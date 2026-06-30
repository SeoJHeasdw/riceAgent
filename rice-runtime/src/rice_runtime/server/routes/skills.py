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

from ...core.errors import ConflictError, NotFoundError, ValidationError
from ...core.types import ApprovalResponse
from ..composition_root import Container
from .deps import get_container

router = APIRouter(prefix="/skills", tags=["skills"])


class StateIn(BaseModel):
    state: str  # active | stale | archived


class CreateSkillIn(BaseModel):
    name: str  # kebab-case
    body: str
    description: str = ""
    tags: list[str] = []


class UpdateSkillIn(BaseModel):
    body: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    state: str | None = None  # active | stale | archived


class PatchIn(BaseModel):
    approval_id: str
    decision: str  # approve | reject
    edited_body: str | None = None


class RollbackIn(BaseModel):
    version: int


@router.get("")
async def list_skills(c: Container = Depends(get_container)):
    return {"success": True, "data": [s.__dict__ for s in c.learning.list_skills()]}


@router.post("")
async def create_skill(body: CreateSkillIn, c: Container = Depends(get_container)):
    """사용자 직접 스킬 생성 (CONTRACTS §1.3)."""
    detail = c.learning.create_skill(
        name=body.name, body=body.body, description=body.description, tags=tuple(body.tags)
    )
    return {"success": True, "data": detail.__dict__}


@router.get("/{skill_id}")
async def get_skill(skill_id: str, c: Container = Depends(get_container)):
    detail = c.learning.get_skill(skill_id)
    if detail is None:
        raise NotFoundError(f"skill {skill_id} not found")
    return {"success": True, "data": detail.__dict__}


@router.put("/{skill_id}")
async def update_skill(skill_id: str, body: UpdateSkillIn, c: Container = Depends(get_container)):
    """스킬 본문/메타 직접 편집 (CONTRACTS §1.3). 본문 변경 시 version+1·history 보관."""
    detail = c.learning.update_skill(
        skill_id,
        body=body.body,
        description=body.description,
        tags=tuple(body.tags) if body.tags is not None else None,
        state=body.state,  # type: ignore[arg-type]
    )
    return {"success": True, "data": detail.__dict__}


@router.delete("/{skill_id}")
async def delete_skill(skill_id: str, c: Container = Depends(get_container)):
    """스킬 삭제 (CONTRACTS §1.3)."""
    c.learning.delete_skill(skill_id)
    return {"success": True}


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
    # Cross-check that approval_id is a skill_patch parked for THIS skill before
    # resolving (v1 버그#12: any approval_id worked on any skill endpoint).
    parked = c.approvals.get(body.approval_id)
    if parked is None:
        raise ConflictError(f"approval {body.approval_id} already resolved or expired")
    if parked.kind != "skill_patch" or parked.payload.get("skill_id") != skill_id:
        raise ValidationError(
            f"approval {body.approval_id} does not belong to skill {skill_id}"
        )
    # Resolve the parked patch approval; the orchestrator applies it on approve.
    ok = c.approvals.resolve(
        body.approval_id,
        ApprovalResponse(approval_id=body.approval_id, decision=body.decision, edited_body=body.edited_body),  # type: ignore[arg-type]
    )
    if not ok:
        raise ConflictError(f"approval {body.approval_id} already resolved or expired")
    return {"success": True}


@router.get("/{skill_id}/history")
async def history(skill_id: str, c: Container = Depends(get_container)):
    return {"success": True, "data": [v.__dict__ for v in c.learning.history(skill_id)]}


@router.post("/{skill_id}/rollback")
async def rollback(skill_id: str, body: RollbackIn, c: Container = Depends(get_container)):
    version = c.learning.rollback(skill_id, body.version)
    return {"success": True, "data": {"version": version}}
