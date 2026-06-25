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

        # ── HITL gate (RUNTIME_DESIGN §5.1) ──────────────────────────────────
        if spec.requires_approval:
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
