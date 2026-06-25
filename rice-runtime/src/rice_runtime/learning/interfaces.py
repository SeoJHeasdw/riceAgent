"""ILearningService — the learning-loop separation seam (RUNTIME_DIRECTORY §5).

This is *the* boundary that lets the learning loop (memory #3, skill #4, curator
#5, loop #6, user model #9) evolve — and one day be swapped for a smarter
retrieval backend — without the agent or server knowing (RUNTIME_DESIGN §3,
"upgrade seam"). The agent loop sees only this Protocol.

Two surfaces:
  1. TURN-CRITICAL (called by the orchestrator every turn):
       prefetch(...)  → context to inject before the LLM call
       observe(...)   → effects to surface after the turn (skill/memory/patch)
       apply_patch(...) → commit a patch once HITL approval is decided
     Note the **approval dance is NOT here**: learning *proposes*; the orchestrator
     owns the `IApprovalGate` + SSE stream and calls `apply_patch` with the verdict.
     This keeps learning free of transport/HITL concerns (single responsibility).

  2. MANAGEMENT (called by `routes/skills.py`, `routes/...` for the UI):
       list/get skills, curate, set_state, history, rollback, list_memories.

**FROZEN.** Implementations (`learning/loop.py`, `skill/`, `memory/`, `curator/`)
sit behind this. Returns use small DTOs defined here so routes don't reach inside.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from ..core.types import InjectedSkill, MemoryItem, TurnOutcome

SkillState = Literal["active", "stale", "archived"]
PatchTrigger = Literal["T1a", "T1b", "T1c", "T3"]  # LEARNING_LOOP §5.2.2


# ── prefetch output (LEARNING_LOOP §6, §7) ───────────────────────────────────
@dataclass(frozen=True)
class PrefetchResult:
    """What to inject before the LLM call. `system_prompt_additions` carries the
    skill-creation guidance + active-skill bodies + recent facts, already rendered;
    the structured lists are also returned so the orchestrator can build the
    `TurnContext` and emit telemetry (e.g. which skills were injected → `use++`)."""

    system_prompt_additions: str = ""
    skills: tuple[InjectedSkill, ...] = ()
    memories: tuple[MemoryItem, ...] = ()


# ── effects produced by observing a finished turn (LEARNING_LOOP §1) ──────────
@dataclass(frozen=True)
class SkillCreated:
    skill_id: str
    title: str
    version: int


@dataclass(frozen=True)
class MemoryWritten:
    memory_type: Literal["fact", "preference"]
    content: str


@dataclass(frozen=True)
class PatchProposal:
    """A proposed SKILL.md improvement awaiting HITL approval (LEARNING_LOOP
    §5.2.3). `diff` matches the SkillDiff shape in types.ts (UI renders it)."""

    skill_id: str
    changelog: str
    diff: dict  # {"lines": [{"type": "add|remove|context", "text": str}, ...]}
    trigger: PatchTrigger
    proposed_body: str
    from_version: int
    to_version: int


# An effect is either an immediate notification (skill/memory) or a proposal that
# the orchestrator must gate through HITL (patch). PEP 695 alias (3.12+).
type LearningEffect = SkillCreated | MemoryWritten | PatchProposal


# ── management DTOs (UI-facing, served by routes) ────────────────────────────
@dataclass(frozen=True)
class SkillSummary:
    id: str
    name: str
    version: int
    description: str
    state: SkillState
    tags: tuple[str, ...] = ()
    source: str | None = None
    use: int = 0
    view: int = 0
    patch: int = 0
    last_used_at: str | None = None


@dataclass(frozen=True)
class SkillDetail(SkillSummary):
    body: str = ""


@dataclass(frozen=True)
class SkillVersion:
    version: int
    updated_at: str
    body: str


@dataclass(frozen=True)
class CurateResult:
    transitioned: tuple[tuple[str, SkillState], ...] = ()  # (skill_id, new_state)


@runtime_checkable
class ILearningService(Protocol):
    # ── turn-critical ─────────────────────────────────────────────────────────
    def prefetch(self, session_id: str, user_message: str) -> PrefetchResult:
        """Assemble injectable context: active skills (all under token budget),
        recent facts + FTS-matched memories, and skill-creation guidance."""
        ...

    async def observe(self, outcome: TurnOutcome) -> list[LearningEffect]:
        """Inspect a finished turn and decide what to persist/propose. Skill
        creation + memory writes are applied immediately and returned as
        notifications; patch proposals are returned *un-applied* for the
        orchestrator to gate (then `apply_patch`)."""
        ...

    def apply_patch(self, skill_id: str, *, approved: bool, edited_body: str | None = None) -> int | None:
        """Commit (or discard) a previously proposed patch after HITL. On approve:
        archive prior body to `.history/`, write new body, version += 1, usage
        patch += 1 → returns the new version. On reject/timeout: returns None."""
        ...

    # ── management (routes/skills.py) ────────────────────────────────────────
    def list_skills(self) -> list[SkillSummary]: ...
    def get_skill(self, skill_id: str) -> SkillDetail | None: ...
    def curate(self) -> CurateResult: ...
    def set_state(self, skill_id: str, state: SkillState) -> None: ...
    def history(self, skill_id: str) -> list[SkillVersion]: ...
    def rollback(self, skill_id: str, version: int) -> int: ...

    # ── memory (routes/memories) ─────────────────────────────────────────────
    def list_memories(self) -> list[MemoryItem]: ...
