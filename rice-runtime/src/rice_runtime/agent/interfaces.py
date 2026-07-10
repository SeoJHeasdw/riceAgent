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

from dataclasses import dataclass
from typing import AsyncIterator, Protocol, Sequence, runtime_checkable

from ..core.types import (
    ApprovalRequest,
    ApprovalResponse,
    Message,
    SSEEvent,
    ToolCall,
    ToolResult,
    ToolSpec,
    TurnContext,
    TurnPlan,
)


@dataclass(frozen=True)
class TurnView:
    """이번 턴에서 지금까지 일어난 일의 라이브 스냅샷.

    transcript는 턴 **종료** 시에야 이번 턴을 영속화한다(orchestrator step 5) —
    그래서 진행 중 턴의 도구 관찰·계획을 봐야 하는 빌트인(delegate_subagent)은
    transcript만 읽어서는 장님이다. 루프가 도구 라운드마다 이 뷰를 dispatcher에
    갱신하고, `needs_turn_view = True` 마커를 단 빌트인 핸들러가 kwargs로 받는다
    (needs_session과 같은 opt-in 관례)."""

    user_message: str = ""
    plan: TurnPlan | None = None
    # 이번 턴에 루프가 만든 메시지들(assistant 라운드 + tool 결과) — ctx.history 이후분.
    turn_messages: tuple[Message, ...] = ()


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
class ISessionTitler(Protocol):
    """Names an untitled session from its opening exchange, post-answer (the "name
    this chat" step, ChatGPT/Claude-style). **Consumer-defined here** — the
    orchestrator triggers it after the first turn; implemented by `agent/titler.py`.

    Kept off `ITranscript` on purpose: titling is a session-metadata concern, not
    the message append/read slice the loop needs, so the minimal transcript seam
    stays minimal."""

    async def maybe_title(self, session_id: str, history: Sequence[Message]) -> str | None:
        """If `session_id` has no title yet, generate one from `history`, persist it,
        and return it. Returns None when already titled, on too-little content, or on
        any provider/parse failure (titling must never break a turn)."""
        ...


@runtime_checkable
class IAgent(Protocol):
    """The orchestrator's public face — the server depends on this, not on the
    concrete `Orchestrator`."""

    def run_turn(self, session_id: str, user_message: str) -> AsyncIterator[SSEEvent]:
        """Run one full agent turn for `session_id`, streaming SSE events end to
        end: prefetch → LLM loop (with tool dispatch + HITL) → learning observe →
        `done`. Errors surface as a terminal `error` event, then `done`."""
        ...

    def regenerate(self, session_id: str) -> AsyncIterator[SSEEvent]:
        """Re-answer the last user message, streaming a fresh turn. The caller
        removes the previous assistant turn first (so it's a replace, not append)."""
        ...

    async def abort(self, session_id: str) -> None:
        """Cancel the in-flight turn for `session_id` (POST /sessions/{id}/abort).
        Cancels any parked approvals for that session (RUNTIME_DESIGN §5.1)."""
        ...

    def is_active(self, session_id: str) -> bool:
        """True while a turn is streaming for `session_id`. Routes MUST check this
        before any transcript mutation (delete/regenerate) — 진행 중 턴과 같은
        transcript 를 놓고 경쟁하면 append 가 FK 오류/유실로 끝난다."""
        ...

    async def abort_and_drain(self, session_id: str, timeout_s: float = 10.0) -> bool:
        """Abort the in-flight turn AND wait until it actually finishes (bounded by
        `timeout_s`). Session deletion calls this first so the turn's tail writes
        land before the rows disappear. True = no turn/drained; False = timed out."""
        ...


@runtime_checkable
class IToolDispatcher(Protocol):
    """Routes a normalized `ToolCall` to its executor and yields the resulting
    SSE events. For tools with `requires_approval`, it yields a
    `tool_approval_required` control event and awaits the `IApprovalGate` before
    executing (or skips on reject/timeout).

    Optional extension (duck-typed, NOT part of the frozen Protocol): a concrete
    dispatcher may expose `set_turn_view(view: TurnView) -> None`. The loop calls
    it via getattr-guard before each tool round so turn-aware builtins
    (delegate_subagent) see the in-progress turn; fakes/tests that don't care
    simply omit it."""

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

    def resolve(self, approval_id: str, response: ApprovalResponse) -> bool:
        """Resolve a parked approval. Returns True if a live parked approval was
        resolved, False if the id is unknown/already-resolved/expired — the route
        maps False to 409 (CONTRACTS §1.2, RUNTIME_DESIGN §5.1)."""
        ...

    def get(self, approval_id: str) -> ApprovalRequest | None:
        """The parked request for `approval_id` (or None) — lets a route cross-check
        kind/payload (e.g. skill_id) before resolving."""
        ...

    def pending(self, session_id: str) -> list[ApprovalRequest]:
        """Approvals still awaiting an answer — for GET /pending-approvals."""
        ...

    def cancel_session(self, session_id: str) -> None:
        """Cancel all parked approvals for a session (on abort/shutdown)."""
        ...
