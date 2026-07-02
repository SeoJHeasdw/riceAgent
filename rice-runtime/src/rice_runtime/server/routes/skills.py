"""Skills + learning-loop management (CONTRACTS §1.3, LEARNING_LOOP §5).

  GET   /skills                 list + usage stats (MySkillsScreen)
  GET   /skills/{id}            detail
  POST  /skills/curate          manual "스킬 정리" trigger (mode=rules|consolidate, Phase B)
  PATCH /skills/{id}/state      archive / activate
  POST  /skills/{id}/patch      answer a skill_patch_proposed (approve/reject/edit)
  POST  /skills/{id}/archive-decision      answer a consolidation ArchiveProposal (Phase B)
  POST  /skills/{id}/consolidate-decision  answer a consolidation merge PatchProposal (Phase B)
  GET   /skills/{id}/history    version history (diff · rollback UI)
  POST  /skills/{id}/rollback   restore a prior version

Reads/writes go through `ILearningService`; the patch answer resolves the parked
approval the orchestrator is awaiting (shared HITL mechanism, §5.1).

`POST /skills/curate?mode=consolidate` and `/archive-decision` are Phase B
(curator LLM consolidation) — deliberately NOT SSE/`IApprovalGate`-parked like
`/patch`: `/curate` is a plain non-streaming REST call the user explicitly
triggers (a button), not a live turn the server is suspended inside awaiting an
answer to. Proposals come back synchronously in the JSON body; the client holds
them in local state and answers each one at its own pace via `/patch` (for a
merge, which is patch-shaped) or `/archive-decision` (for a prune, which isn't).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ...core.errors import ConflictError, NotFoundError, ValidationError
from ...core.types import ApprovalResponse
from ...learning.interfaces import ArchiveProposal, PatchProposal
from ..composition_root import Container
from .deps import get_container

router = APIRouter(prefix="/skills", tags=["skills"])


class StateIn(BaseModel):
    state: str  # active | stale | archived


class ArchiveDecisionIn(BaseModel):
    approved: bool
    absorbed_into: str | None = None


class ConsolidateDecisionIn(BaseModel):
    approved: bool
    proposed_body: str | None = None
    edited_body: str | None = None
    description: str = ""
    tags: list[str] = []


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


class SuggestionDecisionIn(BaseModel):
    approved: bool
    edited_body: str | None = None


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


# NOTE: /suggestions·/discover는 GET /{skill_id}보다 먼저 선언해야 한다 —
# FastAPI는 선언 순서대로 매칭하므로 뒤에 두면 "suggestions"가 skill_id로 잡힌다.


@router.get("/suggestions")
async def list_suggestions(c: Container = Depends(get_container)):
    """영속 제안함 (Self-Improvement v3) — 타임아웃된 HITL 제안 + 백그라운드 발굴
    산출물. payload는 SSE skill_patch_proposed와 같은 어휘라 프론트가 동일 diff
    카드로 렌더링한다."""
    return {"success": True, "data": c.learning.suggestions()}


@router.post("/suggestions/{suggestion_id}/decision")
async def decide_suggestion(
    suggestion_id: str, body: SuggestionDecisionIn, c: Container = Depends(get_container)
):
    """제안함 항목 승인/거부. 파킹된 적 없는 제안이라 approval_id가 없다 — 제안함
    id로 직접 결정한다. 승인 시 create/patch는 apply_patch, archive는
    decide_archive로 수렴하고 결정은 ledger에 남는다(반복 거부 쿨다운의 원천)."""
    result = c.learning.decide_suggestion(
        suggestion_id, approved=body.approved, edited_body=body.edited_body
    )
    return {"success": True, "data": result}


@router.post("/discover")
async def discover(c: Container = Depends(get_container)):
    """크로스세션 반복 패턴 발굴(수동 트리거). 최근 세션 다이제스트를 모아 LLM
    1회 호출 — 산출된 신규 스킬 제안은 제안함에 들어가고 여기서도 그대로 반환된다.
    /curate?mode=consolidate처럼 오래 걸릴 수 있는 일반 REST — 프론트는 로딩
    상태를 보여줘야 한다."""
    from ..scheduler import gather_recent_digests  # noqa: PLC0415 — 순환 회피

    cfg = c.config.learning
    digests = gather_recent_digests(
        c, max_sessions=cfg.discovery_max_sessions, digest_chars=cfg.discovery_digest_chars
    )
    added = await c.learning.discover(digests)
    return {"success": True, "data": {"scanned_sessions": len(digests), "suggestions": added}}


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
async def curate(mode: Literal["rules", "consolidate"] = "rules", c: Container = Depends(get_container)):
    """수동 "스킬 정리". mode=rules(기본, 하위호환): 시간 규칙 전이만, 즉시 반환.
    mode=consolidate(Phase B): 규칙 전이 + LLM 통합 제안(provider 미배선이면
    proposals가 그냥 빈 배열 — 하위호환). 스트림이 아닌 일반 REST라 오래 걸릴 수
    있음(스킬 전체 본문을 프롬프트에 태움) — 프론트는 로딩 상태를 보여줘야 한다."""
    if mode == "consolidate":
        result = await c.learning.consolidate()
        proposals = [
            {"kind": "patch", **p.__dict__} if isinstance(p, PatchProposal) else {"kind": "archive", **p.__dict__}
            for p in result.proposals
        ]
        return {
            "success": True,
            "data": {"transitioned": list(result.transitioned), "proposals": proposals},
        }
    result = c.learning.curate()
    return {"success": True, "data": {"transitioned": list(result.transitioned), "proposals": []}}


@router.post("/{skill_id}/archive-decision")
async def archive_decision(skill_id: str, body: ArchiveDecisionIn, c: Container = Depends(get_container)):
    """Curator 통합(Phase B)이 제안한 ArchiveProposal에 대한 승인/거부. `/patch`와
    달리 diff가 없는 상태전이라 `IApprovalGate` 파킹을 쓰지 않는다 — 프론트가 로컬에
    들고 있던 제안을 바로 이 엔드포인트로 보낸다(비동기 승인 대기 없음)."""
    applied = c.learning.decide_archive(
        skill_id, approved=body.approved, absorbed_into=body.absorbed_into
    )
    return {"success": True, "data": {"applied": applied}}


@router.post("/{skill_id}/consolidate-decision")
async def consolidate_decision(skill_id: str, body: ConsolidateDecisionIn, c: Container = Depends(get_container)):
    """Curator 통합(Phase B)이 제안한 병합(PatchProposal 모양 — 신규 umbrella 생성
    또는 기존 umbrella patch)에 대한 승인/거부. `/patch`와 달리 이 제안도
    `IApprovalGate`에 파킹된 적이 없다(스트림 밖, `/curate` 응답에 그대로 실려
    프론트가 들고 있었을 뿐) — approval_id 조회 없이 프론트가 들고 있던
    proposed_body를 그대로 반송하면 바로 apply_patch로 적용한다."""
    if not body.approved:
        return {"success": True, "data": {"version": None}}
    new_version = c.learning.apply_patch(
        skill_id,
        approved=True,
        proposed_body=body.proposed_body,
        edited_body=body.edited_body,
        description=body.description,
        tags=tuple(body.tags),
        source="curator:consolidate",
    )
    return {"success": True, "data": {"version": new_version}}


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
