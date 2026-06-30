"""ApprovalRegistry — HITL parking, the single shared mechanism (RUNTIME_DESIGN §5.1).

Implements `agent.interfaces.IApprovalGate`. Both control flows — tool-exec
approval (`tool_dispatch.py`) and skill-patch approval (`orchestrator.py`) — park
here. The design closes the SSE+POST traps that §5.1 calls out:

  parking   : `request` registers `approval_id → asyncio.Future` and awaits it —
              an async suspend, not a thread block (other I/O keeps flowing).
  resolve   : the REST handler calls `resolve` when the user answers, completing
              the Future → the awaiting turn resumes.
  timeout   : each wait has a deadline; on expiry the Future resolves to the SAFE
              DEFAULT (reject), so an ignored prompt can't wedge a turn.
  reconnect : state lives here, not on the SSE connection → a dropped/reconnected
              stream keeps the Future alive; `pending` feeds GET /pending-approvals.
  abandon   : `cancel_session` resolves every parked Future for a session to
              "cancelled" on abort/shutdown (no orphans).
  duplicate : resolving an unknown/already-resolved id is an idempotent no-op
              (the route surfaces that as 409/410).

In-memory is sufficient: single active session (RUNTIME_DESIGN §2); losing parked
approvals on restart is acceptable for the demo.
"""

from __future__ import annotations

import asyncio
import logging

from ..core.types import ApprovalRequest, ApprovalResponse

log = logging.getLogger("rice.approval")


class _Parked:
    __slots__ = ("session_id", "request", "future")

    def __init__(self, session_id: str, request: ApprovalRequest, future: "asyncio.Future[ApprovalResponse]"):
        self.session_id = session_id
        self.request = request
        self.future = future


class ApprovalRegistry:
    """Implements `IApprovalGate`."""

    def __init__(self) -> None:
        self._parked: dict[str, _Parked] = {}

    async def request(self, session_id: str, req: ApprovalRequest, *, timeout_s: int) -> ApprovalResponse:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ApprovalResponse] = loop.create_future()
        self._parked[req.approval_id] = _Parked(session_id, req, future)
        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        except asyncio.TimeoutError:
            # safe default: reject/cancel (tool not run / patch not applied)
            log.info("approval %s timed out → reject", req.approval_id)
            return ApprovalResponse(approval_id=req.approval_id, decision="timeout")
        finally:
            self._parked.pop(req.approval_id, None)

    def resolve(self, approval_id: str, response: ApprovalResponse) -> bool:
        parked = self._parked.get(approval_id)
        if parked is None or parked.future.done():
            # late/duplicate/unknown/expired — couldn't resolve a live approval.
            # Caller (route) maps False → 409 (CONTRACTS §1.2). Still idempotent.
            return False
        parked.future.set_result(response)
        return True

    def get(self, approval_id: str) -> ApprovalRequest | None:
        """The parked request for `approval_id` (or None) — for route cross-checks."""
        parked = self._parked.get(approval_id)
        return parked.request if parked is not None else None

    def pending(self, session_id: str) -> list[ApprovalRequest]:
        return [p.request for p in self._parked.values() if p.session_id == session_id]

    def cancel_session(self, session_id: str) -> None:
        for approval_id, parked in list(self._parked.items()):
            if parked.session_id == session_id and not parked.future.done():
                parked.future.set_result(
                    ApprovalResponse(approval_id=approval_id, decision="cancelled")
                )
