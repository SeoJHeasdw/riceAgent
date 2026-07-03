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
    ev_skill_patch_proposed,
    ev_skill_patched,
)
from ..learning.interfaces import ILearningService, LearningEffect, MemoryWritten, PatchProposal
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
        yield ev_memory_written(effect.memory_type, effect.content)
    elif isinstance(effect, PatchProposal):
        req = ApprovalRequest(
            kind="skill_patch",
            payload={
                "skill_id": effect.skill_id,
                "changelog": effect.changelog,
                "diff": effect.diff,
                "trigger": effect.trigger,
                "from_version": effect.from_version,
                "to_version": effect.to_version,
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
            warnings=effect.warnings,
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
            # Optimistic-concurrency miss (출품 전 P0): the skill moved past the
            # proposal's base version while the card sat open. Never silently
            # overwrite — tell the user to get a fresh proposal instead.
            yield ev_error("skill_patch_conflict", str(exc))
            return
        if response.decision == "approve" and new_version is not None:
            yield ev_skill_patched(effect.skill_id, new_version)
    else:  # pragma: no cover - effect union is closed
        log.warning("unknown learning effect: %r", effect)
