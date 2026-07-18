"""agent/effect_surfacing.py — LearningEffect → SSE (+HITL gate) — extracted seam.

WHY extracted (Phase A.3, 2026-07-02): both `orchestrator._drive_turn` (the main
turn's `observe()` effects) and the standalone `/learning-review`·`/learn` routes
(the LLM-backed `review()`/`learn()` effects, learning/review/engine.py) need to
turn a `LearningEffect` into SSE events, gating any `PatchProposal` (skill create
*or* patch — Phase A.1 unified the two) through the same `IApprovalGate` HITL dance
before calling `apply_patch`. This used to live only as `Orchestrator._surface_effect`;
pulling it out means both call sites share one implementation instead of drifting
(regression-guarded by a parametrized test in test_learning_t4.py).
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from ..core.errors import ConflictError
from ..core.types import (
    ApprovalRequest,
    SSEEvent,
    ev_error,
    ev_memory_written,
    ev_skill_candidate_staged,
    ev_skill_patch_proposed,
    ev_skill_patched,
)
from ..learning.interfaces import (
    CandidateStaged,
    ILearningService,
    LearningEffect,
    MemoryWritten,
    PatchProposal,
)
from .interfaces import IApprovalGate

log = logging.getLogger("rice.agent.effect_surfacing")


async def surface_learning_effect(
    *,
    session_id: str,
    effect: LearningEffect,
    learning: ILearningService,
    approvals: IApprovalGate,
    approval_timeout_s: int,
) -> AsyncIterator[SSEEvent]:
    """Turn one `LearningEffect` into its SSE event(s).

    `MemoryWritten` is an already-applied notification (memory writes are never
    HITL-gated — RICE_LEARNING_LOOP_DESIGN.md §4/§5.2.1 only require confirmation
    for skills). `PatchProposal` (skill create-or-patch, discriminated by
    `from_version == 0`) parks an approval and hands the verdict to
    `learning.resolve_proposal` (Self-Improvement v3): approve → apply + ledger,
    reject → ledger (feeds the rejection cooldown), timeout → the proposal moves
    to the persistent suggestion inbox instead of being discarded. In every
    non-approve case the skill store is untouched.
    """
    if isinstance(effect, MemoryWritten):
        yield ev_memory_written(
            effect.memory_type,
            effect.content,
            status=effect.status,
            memory_id=effect.memory_id,
            derived_from_external=effect.derived_from_external,
        )
    elif isinstance(effect, PatchProposal):
        metadata_payload: dict[str, object] = {}
        if effect.from_version == 0:
            # Creation metadata is persisted together with the v1 body.  Include
            # empty values as well: omitting them would make the approval card
            # unable to distinguish "empty by proposal" from "wire data lost".
            metadata_payload["description"] = effect.description
            metadata_payload["tags"] = list(effect.tags)
        if effect.new_description is not None:
            metadata_payload["new_description"] = effect.new_description
        if effect.new_tags is not None:
            metadata_payload["new_tags"] = list(effect.new_tags)
        req = ApprovalRequest(
            kind="skill_patch",
            payload={
                "skill_id": effect.skill_id,
                "changelog": effect.changelog,
                "diff": effect.diff,
                "trigger": effect.trigger,
                "from_version": effect.from_version,
                "to_version": effect.to_version,
                # Reconnect must restore the exact same safety/approval surface
                # as the live SSE card.  A diff is not an authoritative body and
                # warnings/provenance must not disappear merely because the tab
                # reconnected while the approval was parked.
                "proposed_body": effect.proposed_body,
                "warnings": list(effect.warnings),
                "derived_from_external": effect.derived_from_external,
                "evidence_message_ids": list(effect.evidence_message_ids),
                **metadata_payload,
            },
        )
        yield ev_skill_patch_proposed(
            req.approval_id,
            effect.skill_id,
            effect.changelog,
            effect.diff,
            effect.trigger,
            effect.from_version,
            effect.to_version,
            proposed_body=effect.proposed_body,
            description=effect.description if effect.from_version == 0 else None,
            tags=effect.tags if effect.from_version == 0 else None,
            new_description=effect.new_description,
            new_tags=effect.new_tags,
            warnings=effect.warnings,
            derived_from_external=effect.derived_from_external,
            evidence_message_ids=effect.evidence_message_ids,
        )
        try:
            response = await approvals.request(session_id, req, timeout_s=approval_timeout_s)
        except (asyncio.CancelledError, GeneratorExit):
            # 클라이언트가 카드에 답하기 전에 SSE 스트림이 끊겼다(탭 종료/새로고침
            # → uvicorn이 이 제너레이터를 취소한다). 무응답은 거부 의사표시가
            # 아니므로 timeout과 동일하게 제안을 **제안함으로 보존**한다 — 이전엔
            # 여기서 제안이 ledger에도 inbox에도 남지 않고 증발했다(2026-07-03
            # 적대 리뷰 High#1). 명시적 턴 abort(cancel_session)는 이 경로가 아니라
            # decision="cancelled"로 정상 반환되어 여전히 폐기된다(사용자 의사).
            try:
                learning.resolve_proposal(effect, session_id=session_id, decision="disconnected")
            except Exception:  # noqa: BLE001 — 보존 실패가 취소 전파를 막으면 안 된다
                log.warning("failed to preserve proposal on disconnect (%s)", effect.skill_id, exc_info=True)
            raise
        try:
            new_version = learning.resolve_proposal(
                effect,
                session_id=session_id,
                decision=response.decision,
                edited_body=response.edited_body,
            )
        except ConflictError as exc:
            # Optimistic-concurrency miss: the skill moved past the
            # proposal's base version while the card sat open. Never silently
            # overwrite — tell the user to get a fresh proposal instead.
            yield ev_error("skill_patch_conflict", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            # 적용 실패(손상된 SKILL.md, 권한/IO 오류 — apply_patch 가 이제 삼키지
            # 않고 전파한다)는 명시적 에러 이벤트로 사용자에게 보여준다. 스트림
            # 전체를 turn_failed 로 죽이지 않고 이 효과만 실패로 마감한다.
            log.exception("skill patch apply failed for %s", effect.skill_id)
            yield ev_error("skill_patch_failed", f"스킬 '{effect.skill_id}' 적용 실패: {exc}")
            return
        if response.decision == "approve" and new_version is not None:
            if isinstance(new_version, CandidateStaged):
                # §10.6: 승인이 즉시 적용이 아니라 candidate 격리로 귀결됐다 —
                # "적용됨"(skill_patched)이라고 말하면 거짓이 된다. 승격/폐기는
                # 백그라운드 평가 후의 일이고 이 스트림은 그때 이미 없다.
                yield ev_skill_candidate_staged(
                    new_version.skill_id,
                    new_version.candidate_id,
                    new_version.baseline_version,
                )
            else:
                yield ev_skill_patched(effect.skill_id, new_version)
    else:  # pragma: no cover - effect union is closed
        log.warning("unknown learning effect: %r", effect)
