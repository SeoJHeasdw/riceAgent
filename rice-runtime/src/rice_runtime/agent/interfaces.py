"""Agent module seams.

The agent module is the **convergence point**: its orchestrator is the only place
that touches learning + connector + execution together (see `orchestrator.py`).
This file freezes the Protocols at that convergence so the wiring stays a contract,
not a tangle.

Seams defined here:
  IAgent          — what the server calls to run a turn. The whole turn surfaces
                    as an async stream of `SSEEvent`s (CONTRACTS §1.3).
  IToolDispatcher — routes one `ToolCall` to builtin/connector/plugin, applying
                    HITL approval; yields the tool_call/result/approval events.
  IApprovalGate   — **consumer-defined** seam the orchestrator awaits mid-turn for
                    HITL parking. Implemented by `server/approval.py`
                    (RUNTIME_DESIGN §5.1). Defined here, where it is consumed, so
                    the dependency points server → agent (never the reverse).

**FROZEN.** Implementations live behind these; they are not widened casually.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from ..core.types import (
    ApprovalRequest,
    ApprovalResponse,
    Message,
    SSEEvent,
    ToolCall,
    ToolResult,
    ToolSpec,
    TurnContext,
)


@runtime_checkable
class ITranscript(Protocol):
    """The *minimal* slice of session persistence the agent loop needs: append a
    message, read history. **Consumer-defined here** (the orchestrator is the
    consumer) so the dependency points server → agent, never the reverse.

    The full session surface (`ISessionStore`, with CRUD/paging/search for the UI)
    lives in `server/session/interfaces.py`; `SessionManager` structurally
    satisfies both, so the composition root passes one object that serves the UI as
    `ISessionStore` and the orchestrator as `ITranscript`. This is what keeps
    layering clean *and* the orchestrator's collaborator fully typed."""

    def append(self, session_id: str, message: Message) -> None: ...
    def history(self, session_id: str) -> tuple[Message, ...]: ...


@runtime_checkable
class IAgent(Protocol):
    """The orchestrator's public face — the server depends on this, not on the
    concrete `Orchestrator`."""

    def run_turn(self, session_id: str, user_message: str) -> AsyncIterator[SSEEvent]:
        """Run one full agent turn for `session_id`, streaming SSE events end to
        end: prefetch → LLM loop (with tool dispatch + HITL) → learning observe →
        `done`. Errors surface as a terminal `error` event, then `done`."""
        ...

    async def abort(self, session_id: str) -> None:
        """Cancel the in-flight turn for `session_id` (POST /sessions/{id}/abort).
        Cancels any parked approvals for that session (RUNTIME_DESIGN §5.1)."""
        ...


@runtime_checkable
class IToolDispatcher(Protocol):
    """Routes a normalized `ToolCall` to its executor and yields the resulting
    SSE events. For tools with `requires_approval`, it yields a
    `tool_approval_required` control event and awaits the `IApprovalGate` before
    executing (or skips on reject/timeout)."""

    def dispatch(self, session_id: str, call: ToolCall, spec: ToolSpec) -> AsyncIterator[SSEEvent]:
        ...

    async def result_for(self, call: ToolCall) -> ToolResult:
        """The collected result of the most recent `dispatch` of `call` — fed back
        into the conversation history for the next loop iteration."""
        ...


@runtime_checkable
class IApprovalGate(Protocol):
    """HITL parking registry (RUNTIME_DESIGN §5.1). The orchestrator/dispatcher
    `await request(...)`; the server's REST handler calls `resolve(...)` when the
    user answers; reconnect recovery reads `pending(...)`.

    Implemented by `server/approval.py`. One mechanism for *both* tool-exec and
    skill-patch approvals."""

    async def request(self, session_id: str, req: ApprovalRequest, *, timeout_s: int) -> ApprovalResponse:
        """Register `req`, suspend (async) until resolved/timed-out, and return the
        response. On timeout the safe default is reject/cancel."""
        ...

    def resolve(self, approval_id: str, response: ApprovalResponse) -> None:
        """Resolve a parked approval. Late/duplicate ids are idempotent no-ops
        (the route maps that to 409/410)."""
        ...

    def pending(self, session_id: str) -> list[ApprovalRequest]:
        """Approvals still awaiting an answer — for GET /pending-approvals."""
        ...

    def cancel_session(self, session_id: str) -> None:
        """Cancel all parked approvals for a session (on abort/shutdown)."""
        ...
