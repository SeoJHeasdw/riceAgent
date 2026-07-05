"""Sessions + the streaming turn + HITL tool approval (CONTRACTS §1.2/§1.3).

This router owns the ChatScreen's hot path:
  POST /sessions                              create
  GET  /sessions, /sessions/{id}              list / detail (history UI)
  GET  /sessions/{id}/messages?cursor=        paginated history
  POST /sessions/{id}/messages                → SSE stream (the turn)
  POST /sessions/{id}/title                   name an untitled session (post-turn)
  POST /sessions/{id}/learning-review         → SSE stream, gated LLM skill/memory
                                                 review (post-turn, Phase A)
  POST /sessions/{id}/learn                   → SSE stream, explicit "save this
                                                 conversation as a skill" (Phase C)
  POST /sessions/{id}/abort                   cancel in-flight turn
  POST /sessions/{id}/tool-approval           answer a tool_approval_required
  GET  /sessions/{id}/pending-approvals       reconnect recovery (§5.1)

The turn endpoint adapts `agent.run_turn(...)` (an SSEEvent async-gen) to an HTTP
SSE response via the emitter. The route is thin: all behavior is in the orchestrator.

`/learning-review` and `/learn` follow the exact same "separate, non-blocking,
post-`done`" shape as `/title` — an LLM call must never sit inline in the main
turn's stream (see agent/orchestrator.py's docstring + learning/interfaces.py
§1b). Unlike `/title`, they return their OWN SSE stream (not a single JSON reply)
because a review can produce a `skill_patch_proposed` that needs the same
park-for-HITL dance the main turn already does — reusing `effect_surfacing`
(agent/effect_surfacing.py) so both places drive that dance identically.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ...agent.effect_surfacing import surface_learning_effect
from ...core.errors import ConflictError, NotFoundError
from ...core.types import ApprovalResponse, SSEEvent, ev_done, ev_error
from ..composition_root import Container
from ..sse.emitter import to_sse_stream
from .deps import get_container

log = logging.getLogger("rice.routes.messages")

router = APIRouter(tags=["sessions"])


class MessageIn(BaseModel):
    content: str
    # attachments: list[...] — CONTRACTS decision 1-C (future)


class ToolApprovalIn(BaseModel):
    approval_id: str
    decision: str  # "approve" | "reject"


class LearningReviewIn(BaseModel):
    # DEPRECATED: 서버가 턴 종료 시 learning_jobs에
    # tool_calls_made를 직접 영속화하므로 프론트 카운트는 더 이상 쓰지 않는다
    # (이벤트 누락/재시도에 취약했다). 구버전 클라이언트 호환을 위해 필드만 남긴다.
    tool_calls_made: int = 0


class LearnIn(BaseModel):
    instruction: str = ""


@router.post("/sessions")
async def create_session(c: Container = Depends(get_container)):
    info = c.sessions.create(model=c.config.provider.model)
    return {"success": True, "data": {"id": info.id}}


@router.get("/sessions")
async def list_sessions(c: Container = Depends(get_container)):
    return {"success": True, "data": [s.__dict__ for s in c.sessions.list()]}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, c: Container = Depends(get_container)):
    info = c.sessions.get(session_id)
    if info is None:
        raise NotFoundError(f"session {session_id} not found")
    return {"success": True, "data": info.__dict__}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, c: Container = Depends(get_container)):
    if c.sessions.get(session_id) is None:
        raise NotFoundError(f"session {session_id} not found")
    # 스트리밍 중 삭제 방어: 진행 중 턴을 abort 하고 실제 종료까지 drain 한 뒤에
    # 지운다 — 안 그러면 턴 후반의 transcript.append/observe 가 삭제된 행에 쓰다
    # FK 오류로 죽는다(orchestrator.abort_and_drain docstring 참고).
    await c.agent.abort_and_drain(session_id)
    c.sessions.delete(session_id)
    return {"success": True}


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str, cursor: str | None = None, c: Container = Depends(get_container)):
    page = c.sessions.page(session_id, cursor)
    return {
        "success": True,
        "data": {"messages": [m.__dict__ for m in page.messages], "next_cursor": page.next_cursor},
    }


@router.post("/sessions/{session_id}/messages")
async def post_message(session_id: str, body: MessageIn, c: Container = Depends(get_container)):
    info = c.sessions.get(session_id)
    if info is None:
        raise NotFoundError(f"session {session_id} not found")
    # Title is generated lazily by POST /sessions/{id}/title, which the client calls
    # after this turn's first `done` — kept off the hot streaming path so a slow
    # title model never delays the answer or holds the stream open.
    stream = to_sse_stream(c.agent.run_turn(session_id, body.content))
    return StreamingResponse(stream, media_type="text/event-stream")


@router.post("/sessions/{session_id}/title")
async def generate_title(session_id: str, c: Container = Depends(get_container)):
    """Name an untitled session from its opening exchange (ChatGPT/Claude-style).
    A separate, non-blocking call the client makes after the first turn's `done`, so
    titling never holds up the message stream. Idempotent: returns the existing title
    untouched if the session is already named (or the model can't summarize)."""
    info = c.sessions.get(session_id)
    if info is None:
        raise NotFoundError(f"session {session_id} not found")
    title = await c.titler.maybe_title(session_id, c.sessions.history(session_id))
    return {"success": True, "data": {"title": title or info.title}}


async def _stream_learning_effects(session_id: str, c: Container, coro) -> AsyncIterator[SSEEvent]:
    """Shared tail for /learning-review and /learn: await the effects list, surface
    each one as SSE (+ HITL park for skill proposals via the same helper the main
    turn uses), then always close with `done` — mirrors orchestrator._drive_turn's
    steps 7/8 exactly, just off the main stream. `coro` is the already-built
    `c.learning.review(...)`/`c.learning.learn(...)` awaitable."""
    try:
        effects = await coro
        timeout_s = c.config.learning.approval_timeout_seconds
        for effect in effects:
            async for event in surface_learning_effect(
                session_id=session_id,
                effect=effect,
                learning=c.learning,
                approvals=c.approvals,
                approval_timeout_s=timeout_s,
            ):
                yield event
    except Exception as exc:  # best-effort — must never leave the stream hanging
        log.exception("learning effect stream failed for session %s", session_id)
        yield ev_error("learning_review_failed", str(exc))
    finally:
        yield ev_done()


@router.post("/sessions/{session_id}/learning-review")
async def learning_review(
    session_id: str, body: LearningReviewIn | None = None, c: Container = Depends(get_container)
):
    """Gated LLM review, called fire-and-forget by the client right after the main
    turn's `done` (same trigger point as /title). Fast no-op (single `done` frame)
    when there is no pending server-side review job (exactly-once:
    observe()가 턴 종료 시 영속화한 learning_jobs를 claim해 실행한다; 중복 호출·
    재시도는 자동으로 no-op) or when `ReviewGate` decides the turn doesn't
    warrant an LLM call. The request body is deprecated and ignored."""
    info = c.sessions.get(session_id)
    if info is None:
        raise NotFoundError(f"session {session_id} not found")
    history = c.sessions.history(session_id)
    coro = c.learning.review(session_id, list(history))
    stream = to_sse_stream(_stream_learning_effects(session_id, c, coro))
    return StreamingResponse(stream, media_type="text/event-stream")


@router.post("/sessions/{session_id}/learn")
async def learn(session_id: str, body: LearnIn, c: Container = Depends(get_container)):
    """Explicit user-triggered learning ("이 대화를 스킬로 저장", Phase C) — same
    engine as /learning-review, always runs (no gate), more directive prompt."""
    info = c.sessions.get(session_id)
    if info is None:
        raise NotFoundError(f"session {session_id} not found")
    history = c.sessions.history(session_id)
    coro = c.learning.learn(session_id, list(history), body.instruction)
    stream = to_sse_stream(_stream_learning_effects(session_id, c, coro))
    return StreamingResponse(stream, media_type="text/event-stream")


@router.post("/sessions/{session_id}/regenerate")
async def regenerate(session_id: str, c: Container = Depends(get_container)):
    if c.sessions.get(session_id) is None:
        raise NotFoundError(f"session {session_id} not found")
    # busy 확인은 delete_after **이전**이어야 한다 — 이전엔 여기서 무조건 지운 뒤
    # 스트림이 session_busy 로 거절돼, 진행 중 턴의 베이스라인 히스토리만 파괴하고
    # 재생성은 하지도 않는 최악의 조합이었다. 아래 검사~delete_after 사이에 await 가
    # 없어 단일 이벤트 루프에서 원자적이다.
    if c.agent.is_active(session_id):
        raise ConflictError("이 세션에서 이미 응답을 생성하고 있습니다. 완료 후 다시 시도하세요.")
    # Drop the stale assistant turn (everything after the last user message) so the
    # re-run replaces it rather than appending a second answer.
    history = c.sessions.history(session_id)
    last_user = next((m for m in reversed(history) if m.role == "user"), None)
    if last_user is None:
        raise ConflictError("재생성할 사용자 메시지가 없습니다.")
    c.sessions.delete_after(session_id, last_user.ts)
    stream = to_sse_stream(c.agent.regenerate(session_id))
    return StreamingResponse(stream, media_type="text/event-stream")


@router.post("/sessions/{session_id}/abort")
async def abort(session_id: str, c: Container = Depends(get_container)):
    await c.agent.abort(session_id)
    return {"success": True}


@router.post("/sessions/{session_id}/tool-approval")
async def tool_approval(session_id: str, body: ToolApprovalIn, c: Container = Depends(get_container)):
    # Resolve the parked approval. A late/duplicate/expired id resolves nothing →
    # 409 Conflict (CONTRACTS §1.2 — no longer a silent 200).
    ok = c.approvals.resolve(
        body.approval_id, ApprovalResponse(approval_id=body.approval_id, decision=body.decision)  # type: ignore[arg-type]
    )
    if not ok:
        raise ConflictError(f"approval {body.approval_id} already resolved or expired")
    return {"success": True}


@router.get("/sessions/{session_id}/pending-approvals")
async def pending_approvals(session_id: str, c: Container = Depends(get_container)):
    items = [
        {"approval_id": r.approval_id, "kind": r.kind, "payload": r.payload}
        for r in c.approvals.pending(session_id)
    ]
    return {"success": True, "data": items}
