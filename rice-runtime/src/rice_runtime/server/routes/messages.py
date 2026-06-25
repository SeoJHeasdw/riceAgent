"""Sessions + the streaming turn + HITL tool approval (CONTRACTS §1.2/§1.3).

This router owns the ChatScreen's hot path:
  POST /sessions                              create
  GET  /sessions, /sessions/{id}              list / detail (history UI)
  GET  /sessions/{id}/messages?cursor=        paginated history
  POST /sessions/{id}/messages                → SSE stream (the turn)
  POST /sessions/{id}/abort                   cancel in-flight turn
  POST /sessions/{id}/tool-approval           answer a tool_approval_required
  GET  /sessions/{id}/pending-approvals       reconnect recovery (§5.1)

The turn endpoint adapts `agent.run_turn(...)` (an SSEEvent async-gen) to an HTTP
SSE response via the emitter. The route is thin: all behavior is in the orchestrator.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ...core.errors import ConflictError, NotFoundError
from ...core.types import ApprovalResponse
from ..composition_root import Container
from ..sse.emitter import to_sse_stream
from .deps import get_container

router = APIRouter(tags=["sessions"])


class MessageIn(BaseModel):
    content: str
    # attachments: list[...] — CONTRACTS decision 1-C (future)


class ToolApprovalIn(BaseModel):
    approval_id: str
    decision: str  # "approve" | "reject"


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


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str, cursor: str | None = None, c: Container = Depends(get_container)):
    page = c.sessions.page(session_id, cursor)
    return {
        "success": True,
        "data": {"messages": [m.__dict__ for m in page.messages], "next_cursor": page.next_cursor},
    }


@router.post("/sessions/{session_id}/messages")
async def post_message(session_id: str, body: MessageIn, c: Container = Depends(get_container)):
    if c.sessions.get(session_id) is None:
        raise NotFoundError(f"session {session_id} not found")
    stream = to_sse_stream(c.agent.run_turn(session_id, body.content))
    return StreamingResponse(stream, media_type="text/event-stream")


@router.post("/sessions/{session_id}/abort")
async def abort(session_id: str, c: Container = Depends(get_container)):
    await c.agent.abort(session_id)
    return {"success": True}


@router.post("/sessions/{session_id}/tool-approval")
async def tool_approval(session_id: str, body: ToolApprovalIn, c: Container = Depends(get_container)):
    # Resolve the parked approval; late/duplicate ids are idempotent (§5.1).
    c.approvals.resolve(
        body.approval_id, ApprovalResponse(approval_id=body.approval_id, decision=body.decision)  # type: ignore[arg-type]
    )
    return {"success": True}


@router.get("/sessions/{session_id}/pending-approvals")
async def pending_approvals(session_id: str, c: Container = Depends(get_container)):
    items = [
        {"approval_id": r.approval_id, "kind": r.kind, "payload": r.payload}
        for r in c.approvals.pending(session_id)
    ]
    return {"success": True, "data": items}
