"""Skills + learning-loop management (CONTRACTS §1.3, LEARNING_LOOP §5).

  GET   /skills                 list + usage stats (MySkillsScreen)
  GET   /skills/{id}            detail
  POST  /skills/curate          manual "스킬 정리" trigger
  PATCH /skills/{id}/state      archive / activate
  POST  /skills/{id}/patch      answer a skill_patch_proposed (approve/reject/edit)
  GET   /skills/{id}/history    version history (diff · rollback UI)
  POST  /skills/{id}/rollback   restore a prior version

Reads/writes go through `ILearningService`; the patch answer resolves the parked
approval the orchestrator is awaiting (shared HITL mechanism, §5.1).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ...core.errors import NotFoundError
from ...core.types import ApprovalResponse
from ..composition_root import Container
from .deps import get_container

router = APIRouter(prefix="/skills", tags=["skills"])


class StateIn(BaseModel):
    state: str  # active | stale | archived


class PatchIn(BaseModel):
    approval_id: str
    decision: str  # approve | reject
    edited_body: str | None = None


class RollbackIn(BaseModel):
    version: int


@router.get("")
async def list_skills(c: Container = Depends(get_container)):
    return {"success": True, "data": [s.__dict__ for s in c.learning.list_skills()]}


@router.get("/{skill_id}")
async def get_skill(skill_id: str, c: Container = Depends(get_container)):
    detail = c.learning.get_skill(skill_id)
    if detail is None:
        raise NotFoundError(f"skill {skill_id} not found")
    return {"success": True, "data": detail.__dict__}


@router.post("/curate")
async def curate(c: Container = Depends(get_container)):
    result = c.learning.curate()
    return {"success": True, "data": {"transitioned": list(result.transitioned)}}


@router.patch("/{skill_id}/state")
async def set_state(skill_id: str, body: StateIn, c: Container = Depends(get_container)):
    c.learning.set_state(skill_id, body.state)  # type: ignore[arg-type]
    return {"success": True}


@router.post("/{skill_id}/patch")
async def patch(skill_id: str, body: PatchIn, c: Container = Depends(get_container)):
    # Resolve the parked patch approval; the orchestrator applies it on approve.
    c.approvals.resolve(
        body.approval_id,
        ApprovalResponse(approval_id=body.approval_id, decision=body.decision, edited_body=body.edited_body),  # type: ignore[arg-type]
    )
    return {"success": True}


@router.get("/{skill_id}/history")
async def history(skill_id: str, c: Container = Depends(get_container)):
    return {"success": True, "data": [v.__dict__ for v in c.learning.history(skill_id)]}


@router.post("/{skill_id}/rollback")
async def rollback(skill_id: str, body: RollbackIn, c: Container = Depends(get_container)):
    version = c.learning.rollback(skill_id, body.version)
    return {"success": True, "data": {"version": version}}
