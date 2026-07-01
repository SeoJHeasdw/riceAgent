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
  4. Emit `tool_call` / `tool_result` SSE events around execution.

It holds NO conversation state and NO module impls beyond the injected Protocols.
Results are stashed per-call so the loop can read them back into history.
"""

from __future__ import annotations

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
    ) -> None:
        self._connectors = connectors
        self._plugins = plugins
        self._approvals = approvals
        self._approval_timeout_s = approval_timeout_s
        self._builtins = builtins or {}
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
