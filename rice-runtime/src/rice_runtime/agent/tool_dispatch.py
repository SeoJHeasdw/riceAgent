"""Tool dispatch (#2) — route one `ToolCall` to its executor, with HITL.

Responsibilities (and *only* these):
  1. Find the `ToolSpec` for a call (built earlier by the orchestrator).
  2. If the tool `requires_approval`, emit `tool_approval_required` and park on the
     `IApprovalGate` until the user answers (or it times out → safe-default skip).
     This is the tool half of the shared HITL mechanism (RUNTIME_DESIGN §5.1).
  3. Route the (approved) call by `spec.source`:
       builtin   → an in-process callable registry
       connector → IConnectorService.invoke   (after permission is the connector's job)
       plugin    → IPluginLoader.invoke        (runs in the sandbox)
       bridge    → tool_search/tool_describe/tool_call (progressive disclosure, below)
  4. Emit `tool_call` / `tool_result` SSE events around execution.
  5. **Schema assembly + availability filtering** (RUNTIME_DESIGN §2.1 — this module is
     the documented owner): `fold_tools_for_budget` decides, once per turn, whether the
     connector+plugin ("non-core") tool schemas fit the ~15K token budget
     (RICE_LEARNING_LOOP_DESIGN.md §7) as-is, or must be folded behind 3 bridge tools
     (RUNTIME_DESIGN §11, HERMES_TOOL_SYSTEM_REFERENCE.md §6.4) so a turn with many
     connectors/plugins doesn't blow the tool-schema budget. Builtin ("core") tools are
     never folded — they're small, fixed, and always needed.

It holds NO conversation state and NO module impls beyond the injected Protocols.
Results are stashed per-call so the loop can read them back into history.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Awaitable, Callable

from ..connector.interfaces import IConnectorService
from ..core.types import (
    ApprovalRequest,
    SSEEvent,
    ToolCall,
    ToolResult,
    ToolSpec,
    ev_tool_approval_required,
    ev_tool_call,
    ev_tool_result,
)
from ..execution.interfaces import IPluginLoader
from .interfaces import IApprovalGate

log = logging.getLogger("rice.agent.dispatch")

# A builtin tool is a plain async callable: arguments dict → result value.
BuiltinTool = Callable[[dict], Awaitable[object]]


# ── progressive disclosure (RUNTIME_DESIGN §2.1/§11) ────────────────────────────
# 실제 토크나이저 없이 문자수/4 로 근사한다 — learning/service.py 의 스킬 예산 추정과
# 같은 휴리스틱(일관성). 예산은 RICE_LEARNING_LOOP_DESIGN.md §7 표의 "MCP 도구" 항목(~15K).
_CHARS_PER_TOKEN = 4
_TOOL_BUDGET_TOKENS = 15_000


def _estimate_tool_tokens(spec: ToolSpec) -> int:
    size = len(spec.name) + len(spec.description) + len(json.dumps(spec.parameters, ensure_ascii=False))
    return max(1, size // _CHARS_PER_TOKEN)


_TOOL_SEARCH_SPEC = ToolSpec(
    name="tool_search",
    description=(
        "이름·설명으로 지금 쓸 수 있는 도구를 검색합니다. 필요한 도구가 지금 스키마에 "
        "안 보이면(연결된 커넥터·플러그인이 많아 접혀 있을 수 있음) 먼저 이걸로 찾아보고, "
        "tool_describe 로 파라미터를 확인한 뒤 tool_call 로 실행하세요."
    ),
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "도구 이름·기능 키워드"}},
        "required": ["query"],
    },
    source="bridge",
    requires_approval=False,
)
_TOOL_DESCRIBE_SPEC = ToolSpec(
    name="tool_describe",
    description="tool_search 로 찾은 도구의 전체 스키마(파라미터 정의)를 조회합니다.",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "조회할 도구 이름(tool_search 결과)"}},
        "required": ["name"],
    },
    source="bridge",
    requires_approval=False,
)
_TOOL_CALL_SPEC = ToolSpec(
    name="tool_call",
    description=(
        "tool_search/tool_describe 로 확인한 도구를 실제로 호출합니다. 도구 자체의 "
        "승인·차단 정책은 그대로 적용됩니다(이 브릿지가 우회하지 않음)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "호출할 도구 이름"},
            "arguments": {"type": "object", "description": "그 도구가 받는 인자"},
        },
        "required": ["name", "arguments"],
    },
    source="bridge",
    requires_approval=False,
)
BRIDGE_TOOL_SPECS: tuple[ToolSpec, ...] = (_TOOL_SEARCH_SPEC, _TOOL_DESCRIBE_SPEC, _TOOL_CALL_SPEC)


def fold_tools_for_budget(
    core: tuple[ToolSpec, ...], foldable: tuple[ToolSpec, ...]
) -> tuple[ToolSpec, ...]:
    """§2.1/§11 progressive disclosure: `foldable`(connector+plugin, "non-core") 스키마
    합이 예산을 넘지 않으면 그대로 다 노출한다(MVP 는 대개 이 경로 — 커넥터 1~2개론
    안 넘는다). 넘으면 `core`(builtin)는 그대로 두고 `foldable` 은 3개 브릿지 도구로 접는다
    — LLM 은 tool_search/describe/call 로 그 뒤의 실제 도구에 접근한다.
    """
    if sum(_estimate_tool_tokens(t) for t in foldable) <= _TOOL_BUDGET_TOKENS:
        return core + foldable
    return core + BRIDGE_TOOL_SPECS


class ToolDispatcher:
    """Implements `agent.interfaces.IToolDispatcher`."""

    def __init__(
        self,
        *,
        connectors: IConnectorService,
        plugins: IPluginLoader,
        approvals: IApprovalGate,
        approval_timeout_s: int,
        builtins: dict[str, BuiltinTool] | None = None,
        folded_catalog: dict[str, ToolSpec] | None = None,
    ) -> None:
        self._connectors = connectors
        self._plugins = plugins
        self._approvals = approvals
        self._approval_timeout_s = approval_timeout_s
        self._builtins = builtins or {}
        # connector+plugin 도구 전체(접혔든 아니든) — bridge 도구(tool_search/describe/call)가
        # 이름으로 찾는 대상. fold_tools_for_budget 이 실제로 접었을 때만 쓰이게 되지만,
        # 접지 않았을 때도 넣어 둬서 무해하다(LLM 스키마에 브릿지가 없으면 애초에 안 불림).
        self._folded_catalog = folded_catalog or {}
        self._results: dict[str, ToolResult] = {}

    async def dispatch(self, session_id: str, call: ToolCall, spec: ToolSpec) -> AsyncIterator[SSEEvent]:
        yield ev_tool_call(call)

        # ── policy gate (RUNTIME_DESIGN §5.1 + 도구별 권한) ───────────────────
        # 기본 정책: requires_approval 이면 승인, 아니면 허용. connector 도구는 사용자가
        # 저장한 도구별 정책(allow/approval/block)이 이를 오버라이드한다.
        policy = "approval" if spec.requires_approval else "allow"
        if spec.source == "connector" and spec.owner_id:
            perm_fn = getattr(self._connectors, "permission_for", None)
            if perm_fn is not None:
                policy = perm_fn(spec.owner_id, call.tool_name)

        if policy == "block":
            # 차단된 도구는 승인도 묻지 않고 즉시 거부(호출 자체를 막음).
            res = ToolResult(call.id, ok=False, error="tool blocked by policy (사용자가 차단한 도구)")
            self._results[call.id] = res
            yield ev_tool_result(res)
            return

        if policy == "approval":
            req = ApprovalRequest(
                kind="tool_approval",
                payload={"tool_name": call.tool_name, "arguments": call.arguments},
            )
            yield ev_tool_approval_required(req.approval_id, call.tool_name, call.arguments)
            response = await self._approvals.request(session_id, req, timeout_s=self._approval_timeout_s)
            if response.decision != "approve":
                # safe default: do NOT run; surface a skipped result to the loop.
                res = ToolResult(call.id, ok=False, error=f"tool skipped ({response.decision})")
                self._results[call.id] = res
                yield ev_tool_result(res)
                return

        # bridge 는 자체 async-generator 위임(tool_call 이 재귀적으로 dispatch 를 다시 도는
        # 경로가 있어 try/except 아래의 단순 res=await 패턴에 안 맞는다) — 별도 분기.
        if spec.source == "bridge":
            async for ev in self._dispatch_bridge(session_id, call):
                yield ev
            return

        # ── execute by source ────────────────────────────────────────────────
        try:
            if spec.source == "builtin":
                fn = self._builtins.get(call.tool_name)
                if fn is None:
                    raise KeyError(f"unknown builtin tool: {call.tool_name}")
                value = await fn(call.arguments)
                res = ToolResult(call.id, ok=True, result=value)
            elif spec.source == "connector":
                res = await self._connectors.invoke(call)
            elif spec.source == "plugin":
                res = await self._plugins.invoke(call)
            else:  # pragma: no cover - guarded by ToolSpec typing
                raise ValueError(f"unknown tool source: {spec.source}")
        except Exception as exc:  # tool failures are data, not crashes (loop continues)
            log.exception("tool %s failed", call.tool_name)
            res = ToolResult(call.id, ok=False, error=str(exc))

        self._results[call.id] = res
        yield ev_tool_result(res)

    async def result_for(self, call: ToolCall) -> ToolResult:
        return self._results.get(call.id, ToolResult(call.id, ok=False, error="no result"))

    # ── progressive disclosure bridges (tool_search / tool_describe / tool_call) ──
    async def _dispatch_bridge(self, session_id: str, call: ToolCall) -> AsyncIterator[SSEEvent]:
        """tool_search/tool_describe 는 접힌 카탈로그를 읽기만 하는 순수 조회(승인 불필요 —
        위 gate 를 이미 통과했다). tool_call 은 실제 도구를 **재귀적으로 다시 dispatch**해서
        그 도구 자신의 승인/차단 정책을 그대로 적용한다(이 브릿지가 정책을 우회하지 않음 —
        RUNTIME_DESIGN §5 실행 승인은 노출 방식과 무관하게 항상 적용되는 별개의 방어선).
        """
        if call.tool_name == "tool_search":
            query = str(call.arguments.get("query", "")).strip().lower()
            matches = [
                {"name": t.name, "description": t.description}
                for t in self._folded_catalog.values()
                if not query or query in t.name.lower() or query in t.description.lower()
            ]
            res = ToolResult(call.id, ok=True, result={"matches": matches})
        elif call.tool_name == "tool_describe":
            name = str(call.arguments.get("name", ""))
            target = self._folded_catalog.get(name)
            res = (
                ToolResult(call.id, ok=False, error=f"unknown tool: {name}")
                if target is None
                else ToolResult(
                    call.id, ok=True,
                    result={"name": target.name, "description": target.description, "parameters": target.parameters},
                )
            )
        elif call.tool_name == "tool_call":
            name = str(call.arguments.get("name", ""))
            target = self._folded_catalog.get(name)
            if target is None:
                res = ToolResult(call.id, ok=False, error=f"unknown tool: {name}")
            else:
                inner_arguments = call.arguments.get("arguments") or {}
                inner_call = ToolCall(tool_name=name, arguments=inner_arguments, id=f"{call.id}:bridged")
                async for ev in self.dispatch(session_id, inner_call, target):
                    yield ev
                inner_res = await self.result_for(inner_call)
                res = ToolResult(call.id, ok=inner_res.ok, result=inner_res.result, error=inner_res.error)
        else:  # pragma: no cover - only reachable if BRIDGE_TOOL_SPECS grows without a handler here
            res = ToolResult(call.id, ok=False, error=f"unknown bridge tool: {call.tool_name}")

        self._results[call.id] = res
        yield ev_tool_result(res)
