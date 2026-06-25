"""LearningService — skeleton implementation of `ILearningService`.

⚠️ STUB. This is the wiring placeholder so the seam is exercisable end to end. The
real learning loop lives in the sibling modules (`loop.py`, `memory/`, `skill/`,
`curator/`, `user_model/`) and replaces the bodies here — *behind this same
Protocol*, so nothing upstream changes (RUNTIME_DESIGN §3 upgrade seam).

What's intentionally inert today:
  prefetch → injects only the skill-creation guidance (no retrieval yet).
  observe  → returns no effects (no skill creation / memory / patch yet).
  apply_patch / management → empty, honest no-ops.

What each will own when implemented is noted inline as TODO(module).
"""

from __future__ import annotations

from ..core.config import LearningConfig
from ..core.storage.sqlite import Database
from ..core.types import MemoryItem, TurnOutcome
from .interfaces import (
    CurateResult,
    ILearningService,
    LearningEffect,
    PrefetchResult,
    SkillDetail,
    SkillState,
    SkillSummary,
    SkillVersion,
)

_SKILL_GUIDANCE = (
    "If you complete a reusable, multi-step procedure (e.g. 5+ tool calls), save it "
    "as a SKILL.md — capture repeatable *methods*, not declarative facts."
)


class LearningService(ILearningService):
    def __init__(self, db: Database, config: LearningConfig) -> None:
        self._db = db
        self._config = config

    # ── turn-critical ─────────────────────────────────────────────────────────
    def prefetch(self, session_id: str, user_message: str) -> PrefetchResult:
        # TODO(learning/memory): inject recent facts (N) + FTS top-K memories.
        # TODO(learning/skill): inject all active skills under token budget (§7).
        return PrefetchResult(system_prompt_additions=_SKILL_GUIDANCE)

    async def observe(self, outcome: TurnOutcome) -> list[LearningEffect]:
        # TODO(learning/loop): decide skill creation (HITL), memory writes,
        # and patch proposals (T1/T3) from the finished turn.
        return []

    def apply_patch(self, skill_id: str, *, approved: bool, edited_body: str | None = None) -> int | None:
        # TODO(learning/skill/patch): archive prior body → .history/, write new
        # body, version += 1, usage.patch += 1; return new version.
        return None

    # ── management ────────────────────────────────────────────────────────────
    def list_skills(self) -> list[SkillSummary]:
        return []  # TODO(learning/skill/store): read ~/.rice/skills/*.md + .usage.json

    def get_skill(self, skill_id: str) -> SkillDetail | None:
        return None

    def curate(self) -> CurateResult:
        return CurateResult()  # TODO(learning/curator): time-rule transitions

    def set_state(self, skill_id: str, state: SkillState) -> None:
        return None

    def history(self, skill_id: str) -> list[SkillVersion]:
        return []  # TODO(learning/skill): read <name>/.history/v*.md

    def rollback(self, skill_id: str, version: int) -> int:
        raise NotImplementedError("skill rollback not implemented yet")

    # ── memory ────────────────────────────────────────────────────────────────
    def list_memories(self) -> list[MemoryItem]:
        rows = self._db.query_all("SELECT kind, content, id FROM memories ORDER BY created_at DESC")
        return [MemoryItem(kind=r["kind"], content=r["content"], id=r["id"]) for r in rows]
