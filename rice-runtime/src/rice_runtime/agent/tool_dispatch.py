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
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from typing import Protocol, TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .background_tasks import BackgroundTaskRegistry, CompletedTask

from ..connector.interfaces import IConnectorService
from ..core import lexical
from ..core.types import (
    ApprovalRequest,
    ApprovalMode,
    SSEEvent,
    ToolCall,
    ToolResult,
    ToolSpec,
    ev_tool_approval_required,
    ev_tool_call,
    ev_tool_result,
    split_image_attachments,
)
from ..execution.interfaces import IPluginLoader
from .arg_preflight import preflight_arguments
from .interfaces import IApprovalGate, TurnView

log = logging.getLogger("rice.agent.dispatch")

# "Approve for me"는 워크스페이스 안의 일반 파일/문서 작성을 자동 승인하되,
# 임의 셸·외부 시스템·파괴/게시 성격의 동작은 계속 사람에게 묻는다. 이름 패턴은
# 새 builtin이 추가돼도 위험 동사가 조용히 자동 승인되지 않게 하는 보수적 안전망이다.
_ELEVATED_TOOL_NAME_RE = re.compile(
    r"(?:^|[._-])(terminal|shell|exec|execute|delete|remove|destroy|drop|truncate|send|publish|deploy|purchase|payment|admin)(?:$|[._-])",
    re.IGNORECASE,
)


def _is_potentially_unsafe(call: ToolCall, spec: ToolSpec) -> bool:
    if spec.source in {"connector", "plugin"}:
        return True
    if _ELEVATED_TOOL_NAME_RE.search(call.tool_name):
        return True
    # terminal은 이름 검사로 잡히지만, 브릿지/별칭 도구가 command를 직접 받을 때도
    # 셸 실행을 고위험으로 취급한다.
    return isinstance(call.arguments.get("command"), str)

class SessionBuiltinTool(Protocol):
    """Builtin callable that needs the current session id.

    Most builtins are plain `async (args) -> object`; session-scoped tools opt in
    by setting `needs_session = True` on the handler. Handlers that must see the
    in-progress turn (live tool observations + active plan — transcript엔 아직
    없다) additionally opt in with `needs_turn_view = True` and receive a
    `turn_view: TurnView | None` kwarg (delegate_subagent가 유일한 소비자).
    """

    needs_session: bool

    def __call__(self, args: dict, *, session_id: str) -> Awaitable[object]: ...


BuiltinTool = Callable[[dict], Awaitable[object]] | SessionBuiltinTool


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
    # The scheduler only sees this wrapper spec, not the folded target spec.
    # Conservatively serialize bridge executions so a folded destructive tool
    # cannot become parallel merely because the bridge itself is read-only.
    parallel_safe=False,
)
BRIDGE_TOOL_SPECS: tuple[ToolSpec, ...] = (_TOOL_SEARCH_SPEC, _TOOL_DESCRIBE_SPEC, _TOOL_CALL_SPEC)


# 접힌 상태에서 사용자 메시지와 관련도 높은 도구를 브릿지 옆에 함께 노출하는
# "스포트라이트" — 전체를 접기만 하면 소형 모델이 tool_search 를 부를 줄 몰라
# 도구를 못 쓰는 실패가 잦다. top-K 만 직접 노출하고 나머지는 브릿지 뒤에 둔다.
_SPOTLIGHT_BUDGET_TOKENS = 4_000
_SPOTLIGHT_MAX_TOOLS = 8


def _score_tool(query: lexical.QueryTerms, spec: ToolSpec) -> float:
    """도구 관련도: 이름(언더스코어/점 분해) 2.0 가중 + 설명 1.0 가중.
    exact/substring/bigram-fuzzy 사다리는 core/lexical.py 하나를 공유한다."""
    name_text = spec.name.replace("_", " ").replace(".", " ")
    return 2.0 * lexical.field_score(query, name_text) + lexical.field_score(
        query, spec.description
    )


def fold_tools_for_budget(
    core: tuple[ToolSpec, ...], foldable: tuple[ToolSpec, ...], query: str = ""
) -> tuple[ToolSpec, ...]:
    """§2.1/§11 progressive disclosure: `foldable`(connector+plugin, "non-core") 스키마
    합이 예산을 넘지 않으면 그대로 다 노출한다(MVP 는 대개 이 경로 — 커넥터 1~2개론
    안 넘는다). 넘으면 `core`(builtin)는 그대로 두고 `foldable` 은 3개 브릿지 도구로
    접는다 — LLM 은 tool_search/describe/call 로 그 뒤의 실제 도구에 접근한다.

    `query`(이 턴의 사용자 메시지)가 있으면 접힌 카탈로그 중 관련도 상위 도구를
    소예산(_SPOTLIGHT_BUDGET_TOKENS)·소개수(_SPOTLIGHT_MAX_TOOLS) 안에서 브릿지와
    **함께** 직접 노출한다 — "많으면 전부 접는다"가 아니라 "관련한 것만 펴고
    나머지를 접는다". 매칭은 결정론적 lexical 라우팅(core/lexical.py — ko→en
    intent 확장 포함)이며, 점수 0 도구는 절대 펴지 않는다.
    """
    if sum(_estimate_tool_tokens(t) for t in foldable) <= _TOOL_BUDGET_TOKENS:
        return core + foldable
    spotlight: list[ToolSpec] = []
    q = lexical.prepare_query(query)
    if q:
        ranked = sorted(
            ((score, spec) for spec in foldable if (score := _score_tool(q, spec)) > 0),
            key=lambda pair: pair[0],
            reverse=True,
        )
        remaining = _SPOTLIGHT_BUDGET_TOKENS
        for _, spec in ranked:
            if len(spotlight) >= _SPOTLIGHT_MAX_TOOLS:
                break
            cost = _estimate_tool_tokens(spec)
            if cost > remaining:
                continue
            spotlight.append(spec)
            remaining -= cost
    return core + BRIDGE_TOOL_SPECS + tuple(spotlight)


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
        approval_mode: ApprovalMode = "ask",
        web_search_enabled: bool = True,
        background_tasks: "BackgroundTaskRegistry | None" = None,
    ) -> None:
        self._connectors = connectors
        self._plugins = plugins
        self._approvals = approvals
        self._approval_timeout_s = approval_timeout_s
        self._approval_mode = approval_mode
        self._web_search_enabled = web_search_enabled
        self._builtins = builtins or {}
        # 백그라운드 작업 완료 큐 — 창 A 드레인(loop 이 매 도구 라운드 직후
        # drain_completed 를 부른다)의 접점. 오케스트레이터가 세션 스코프 레지스트리를
        # 매 턴 주입한다(§11.8). None 이면(테스트/독립) 드레인은 빈 리스트.
        self._background_tasks = background_tasks
        # connector+plugin 도구 전체(접혔든 아니든) — bridge 도구(tool_search/describe/call)가
        # 이름으로 찾는 대상. fold_tools_for_budget 이 실제로 접었을 때만 쓰이게 되지만,
        # 접지 않았을 때도 넣어 둬서 무해하다(LLM 스키마에 브릿지가 없으면 애초에 안 불림).
        self._folded_catalog = folded_catalog or {}
        self._results: dict[str, ToolResult] = {}
        # 진행 중 턴의 라이브 스냅샷 — 루프가 도구 라운드마다 갱신하고,
        # needs_turn_view 빌트인(delegate_subagent)에 kwargs로 주입된다.
        # dispatcher는 턴마다 새로 만들어지므로(C-2) 교차 턴 누수가 없다.
        self._turn_view: TurnView | None = None

    def set_turn_view(self, view: TurnView) -> None:
        self._turn_view = TurnView(
            user_message=view.user_message,
            plan=view.plan,
            turn_messages=view.turn_messages,
            web_search_enabled=self._web_search_enabled,
        )

    async def dispatch(self, session_id: str, call: ToolCall, spec: ToolSpec) -> AsyncIterator[SSEEvent]:
        async for event in self._dispatch(session_id, call, spec, emit_envelope=True):
            yield event

    async def _dispatch(
        self,
        session_id: str,
        call: ToolCall,
        spec: ToolSpec,
        *,
        emit_envelope: bool,
    ) -> AsyncIterator[SSEEvent]:
        """Execute one call, optionally suppressing its public call/result envelope.

        ``tool_call`` uses the suppressed form for its folded target: the wrapper
        owns the single public call id while the target still runs through this
        exact policy/approval path. Control events (notably approval requests)
        remain public, so suppressing the envelope never bypasses HITL.
        """
        # ── argument preflight (step-1 hardening, 2026-07-15) ─────────────────
        # 승인/실행 **전에** 소형 모델의 인자 실수를 교정하거나 정확한 오류로
        # 되돌린다: 배열/객체 인자를 JSON 문자열로 감싼 경우(관찰된 PPT `slides`
        # 실패) json.loads로 salvage 하고, 복구 불가면 승인창을 띄우지 않고 모델에게
        # 정밀 오류를 돌려 정보 있는 재시도를 유도한다. spec.parameters(도구가 이미
        # 선언한 스키마)만으로 판단한다 — 모든 도구 공통, 새 모델 호출 없음.
        coerced_args, arg_error = preflight_arguments(spec, call.arguments)
        if arg_error is not None:
            # 잘못된 호출도 트레이스엔 남긴다(call↔result 짝 유지) — 단 승인은 건너뛴다.
            if emit_envelope:
                yield ev_tool_call(self._public_call(call, spec))
            res = ToolResult(call.id, ok=False, error=arg_error)
            self._results[call.id] = res
            if emit_envelope:
                yield ev_tool_result(res)
            return
        if coerced_args is not call.arguments:
            # salvage됨 — 교정본으로 교체해 이후 표시·승인·실행이 일관되게 본다.
            call = replace(call, arguments=coerced_args)

        if emit_envelope:
            yield ev_tool_call(self._public_call(call, spec))

        # ── policy gate (RUNTIME_DESIGN §5.1 + 도구별 권한) ───────────────────
        # 기본 정책: requires_approval 이면 승인, 아니면 허용. connector 도구는 사용자가
        # 저장한 도구별 정책(allow/approval/block)이 이를 오버라이드한다.
        #
        # 불변식: 이 판정은 **호출 이유와 무관**하다 — 모델이
        # 학습된 스킬의 지시를 따라 이 도구를 부르든, 스스로 판단하든 정책은 동일하게
        # 적용된다. 스킬은 신뢰 경계가 아니라 입력 데이터이므로(prompts/system.py
        # SKILL_TRUST_NOTE), 승인이 필요한 도구는 어떤 스킬이 시켜도 승인을 거친다.
        # 여기 어디에도 "스킬이 승인했으니 통과" 같은 우회로가 없다는 것이 그 보장이다.
        policy = "approval" if spec.requires_approval else "allow"
        if spec.source == "connector" and spec.owner_id:
            perm_fn = getattr(self._connectors, "permission_for", None)
            if perm_fn is not None:
                policy = perm_fn(spec.owner_id, call.tool_name)

        if policy == "block":
            # 차단된 도구는 승인도 묻지 않고 즉시 거부(호출 자체를 막음).
            res = ToolResult(call.id, ok=False, error="tool blocked by policy (the user has blocked this tool)")
            self._results[call.id] = res
            if emit_envelope:
                yield ev_tool_result(res)
            return

        needs_approval = policy == "approval" and (
            self._approval_mode == "ask"
            or (self._approval_mode == "approve_for_me" and _is_potentially_unsafe(call, spec))
        )

        if needs_approval:
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
                if emit_envelope:
                    yield ev_tool_result(res)
                return

        # bridge 는 자체 async-generator 위임(tool_call 이 재귀적으로 dispatch 를 다시 도는
        # 경로가 있어 try/except 아래의 단순 res=await 패턴에 안 맞는다) — 별도 분기.
        if spec.source == "bridge":
            async for ev in self._dispatch_bridge(session_id, call, emit_result=emit_envelope):
                yield ev
            return

        # ── execute by source ────────────────────────────────────────────────
        try:
            if spec.source == "builtin":
                fn = self._builtins.get(call.tool_name)
                if fn is None:
                    raise KeyError(f"unknown builtin tool: {call.tool_name}")
                # 세션 문맥이 필요한 빌트인(예: propose_skill의 세션 단위 스테이징)은
                # `needs_session` 마커로, 진행 중 턴의 라이브 컨텍스트가 필요한
                # 빌트인(delegate_subagent)은 `needs_turn_view` 마커로 opt-in한다 —
                # 시그니처를 전 빌트인에 강제로 넓히는 대신 필요한 도구만 받는다.
                kwargs: dict[str, object] = {}
                if getattr(fn, "needs_session", False):
                    kwargs["session_id"] = session_id
                if getattr(fn, "needs_turn_view", False):
                    kwargs["turn_view"] = self._turn_view
                value = await cast(Callable[..., Awaitable[object]], fn)(call.arguments, **kwargs)
                res = ToolResult(call.id, ok=True, result=value)
            elif spec.source == "connector":
                # spec.owner_id 를 함께 넘긴다 — 같은 도구명을 노출하는 커넥터가
                # 둘일 때 이름 재탐색이 다른 커넥터로 오배송하는 것을 막는다.
                res = await self._connectors.invoke(call, owner_id=spec.owner_id)
            elif spec.source == "plugin":
                # owner_id 를 함께 넘긴다 — 같은 도구명을 선언한 플러그인이 둘일 때
                # 이름 재탐색이 다른 플러그인으로 오배송하는 것을 막는다(connector 와 동일).
                res = await self._plugins.invoke(call, owner_id=spec.owner_id)
            else:  # pragma: no cover - guarded by ToolSpec typing
                raise ValueError(f"unknown tool source: {spec.source}")
        except Exception as exc:  # tool failures are data, not crashes (loop continues)
            log.exception("tool %s failed", call.tool_name)
            res = ToolResult(call.id, ok=False, error=str(exc))

        # 이미지 첨부 분리(core.types 계약): 도구 소스와 무관하게 결과 dict의
        # `_images`를 ToolResult.images로 옮긴다 — base64가 SSE payload와 텍스트
        # 본문(다음 반복의 컨텍스트)으로 새지 않고, provider 어댑터의 이미지
        # 블록으로만 흐른다.
        if res.ok and res.images == ():
            cleaned, images = split_image_attachments(res.result)
            if images:
                res = ToolResult(res.tool_call_id, ok=res.ok, result=cleaned, images=images)

        self._results[call.id] = res
        if emit_envelope:
            yield ev_tool_result(res)

    async def result_for(self, call: ToolCall) -> ToolResult:
        return self._results.get(call.id, ToolResult(call.id, ok=False, error="no result"))

    def drain_completed(self, session_id: str) -> "list[CompletedTask]":
        """창 A: 이 세션의 미확인 백그라운드 완료를 드레인한다(loop 이 매 도구 라운드
        직후 호출 → 완료를 관찰로 주입). 레지스트리 미주입이면 빈 리스트."""
        if self._background_tasks is None:
            return []
        return self._background_tasks.drain_completed(session_id)

    def _public_call(self, call: ToolCall, spec: ToolSpec) -> ToolCall:
        """Resolve a folded execution for display without changing its public id.

        Provider history must retain the wrapper call (the provider actually
        emitted ``tool_call``), but the live trace should describe the action the
        user is approving and observing. The frontend applies the same unwrap to
        persisted wrapper calls when rebuilding a thread.
        """
        if spec.source != "bridge" or call.tool_name != _TOOL_CALL_SPEC.name:
            return call
        raw_name = call.arguments.get("name")
        if not isinstance(raw_name, str) or not raw_name:
            return call
        name = raw_name
        raw_arguments = call.arguments.get("arguments")
        if raw_arguments is not None and not isinstance(raw_arguments, dict):
            return call
        inner_arguments = raw_arguments or {}
        return ToolCall(tool_name=name, arguments=inner_arguments, id=call.id)

    # ── progressive disclosure bridges (tool_search / tool_describe / tool_call) ──
    async def _dispatch_bridge(
        self,
        session_id: str,
        call: ToolCall,
        *,
        emit_result: bool,
    ) -> AsyncIterator[SSEEvent]:
        """tool_search/tool_describe 는 접힌 카탈로그를 읽기만 하는 순수 조회(승인 불필요 —
        위 gate 를 이미 통과했다). tool_call 은 실제 도구를 **재귀적으로 다시 dispatch**해서
        그 도구 자신의 승인/차단 정책을 그대로 적용한다(이 브릿지가 정책을 우회하지 않음 —
        RUNTIME_DESIGN §5 실행 승인은 노출 방식과 무관하게 항상 적용되는 별개의 방어선).
        """
        if call.tool_name == "tool_search":
            # 단순 substring 이 아니라 relevance 랭킹(core/lexical.py 공유 사다리 —
            # exact/substring/fuzzy + ko→en intent 확장)으로 상위 매칭을 돌려준다.
            # 빈 쿼리는 "뭐가 있나" 브라우징이므로 전체 카탈로그를 그대로 반환.
            query = str(call.arguments.get("query", "")).strip()
            q = lexical.prepare_query(query)
            if not q:
                matches = [
                    {"name": t.name, "description": t.description}
                    for t in self._folded_catalog.values()
                ]
            else:
                ranked = sorted(
                    (
                        (score, t)
                        for t in self._folded_catalog.values()
                        if (score := _score_tool(q, t)) > 0
                    ),
                    key=lambda pair: pair[0],
                    reverse=True,
                )
                matches = [
                    {"name": t.name, "description": t.description, "score": round(score, 2)}
                    for score, t in ranked[:12]
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
            raw_name = call.arguments.get("name")
            raw_arguments = call.arguments.get("arguments")
            if not isinstance(raw_name, str) or not raw_name:
                res = ToolResult(call.id, ok=False, error="tool_call name must be a non-empty string")
            elif raw_arguments is not None and not isinstance(raw_arguments, dict):
                res = ToolResult(call.id, ok=False, error="tool_call arguments must be an object")
            elif (target := self._folded_catalog.get(raw_name)) is None:
                res = ToolResult(call.id, ok=False, error=f"unknown tool: {raw_name}")
            else:
                name = raw_name
                inner_arguments = raw_arguments or {}
                inner_call = ToolCall(tool_name=name, arguments=inner_arguments, id=f"{call.id}:bridged")
                # The wrapper already emitted the one public call event using the
                # outer id. Run the target through the same dispatcher while
                # suppressing only its call/result envelope; approval events are
                # intentionally forwarded unchanged.
                async for ev in self._dispatch(
                    session_id, inner_call, target, emit_envelope=False
                ):
                    yield ev
                inner_res = await self.result_for(inner_call)
                # 이미지 첨부도 함께 전파 — 접힌 도구가 vision류여도 계약 유지.
                res = ToolResult(
                    call.id, ok=inner_res.ok, result=inner_res.result,
                    error=inner_res.error, images=inner_res.images,
                )
        else:  # pragma: no cover - only reachable if BRIDGE_TOOL_SPECS grows without a handler here
            res = ToolResult(call.id, ok=False, error=f"unknown bridge tool: {call.tool_name}")

        self._results[call.id] = res
        if emit_result:
            yield ev_tool_result(res)
