"""ILearningService — the learning-loop separation seam (RUNTIME_DIRECTORY §5).

This is *the* boundary that lets the learning loop (memory #3, skill #4, curator
#5, loop #6, user model #9) evolve — and one day be swapped for a smarter
retrieval backend — without the agent or server knowing (RUNTIME_DESIGN §3,
"upgrade seam"). The agent loop sees only this Protocol.

Two surfaces:
  1. TURN-CRITICAL (called by the orchestrator every turn):
       prefetch(...)  → context to inject before the LLM call
       observe(...)   → *cheap, synchronous* effects (memory fact/pref, use++)
       apply_patch(...) → commit a patch once HITL approval is decided
     Note the **approval dance is NOT here**: learning *proposes*; the orchestrator
     owns the `IApprovalGate` + SSE stream and calls `apply_patch` with the verdict.
     This keeps learning free of transport/HITL concerns (single responsibility).

  1b. POST-TURN, LLM-BACKED (called by the separate `/learning-review`/`/learn`
      routes — NEVER inline in the turn's own SSE stream, same reason titling
      moved to its own endpoint: an LLM call here must not delay `done`):
       review(...) → gated, LLM-authored skill/memory proposals
       learn(...)  → same engine, always-on (explicit user "save this as a skill")

  2. MANAGEMENT (called by `routes/skills.py`, `routes/...` for the UI):
       list/get skills, curate, set_state, history, rollback, list_memories.

**FROZEN.** Implementations (`learning/loop.py`, `skill/`, `memory/`, `curator/`)
sit behind this. Returns use small DTOs defined here so routes don't reach inside.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from ..core.types import InjectedSkill, MemoryItem, Message, TurnOutcome

SkillState = Literal["active", "stale", "archived"]
# LEARNING_LOOP §5.2.2 (T1a/T1b/T1c) + §5.1 (T3, curator). T4 신규(Phase A.1) —
# 신규 스킬 "생성" 제안(patch가 아니라 v0→v1)은 T1a/b/c/T3 중 어느 것도 의미가 안
# 맞아 하나만 추가했다. T1a/b/c는 이제 규칙이 아니라 review 엔진의 LLM이 스스로
# 고른다(propose_skill_patch의 trigger_reason) — T1c(누락 단계/낡은 값 organically
# 발견)는 정규식으론 애초에 판단 불가능해 지금까지 한 번도 실사용되지 않았다.
# T5 신규(Self-Improvement v3): 미드턴 에이전트 발의 — 대화 중 `propose_skill`
# 빌트인 도구로 스테이징된 제안(생성이든 개선이든 발의 주체가 다르므로 별도 코드).
PatchTrigger = Literal["T1a", "T1b", "T1c", "T3", "T4", "T5"]


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
class MemoryWritten:
    memory_type: Literal["fact", "preference"]
    content: str


@dataclass(frozen=True)
class PatchProposal:
    """A proposed SKILL.md improvement OR brand-new skill awaiting HITL approval
    (LEARNING_LOOP §5.2.3). `diff` matches the SkillDiff shape in types.ts (UI
    renders it). Phase A.1 (2026-07-02): skill **creation** is unified into this
    same shape — `from_version=0` means "no skill exists yet", `diff` is then
    `build_diff("", proposed_body)` (a 100%-add diff), and `description`/`tags`
    (otherwise unused — a real patch never touches metadata) carry what the new
    skill needs. This avoids a parallel `skill_create_proposed` event/endpoint
    that would duplicate the whole parking/diff/approve/reject/edit plumbing for
    no behavioral gain. (Retired `SkillCreated`/`skill_created` accordingly — it
    would otherwise be unreachable dead code now that creation is HITL-gated.)"""

    skill_id: str
    changelog: str
    diff: dict  # {"lines": [{"type": "add|remove|context", "text": str}, ...]}
    trigger: PatchTrigger
    proposed_body: str
    from_version: int
    to_version: int
    description: str = ""  # only meaningful when from_version == 0 (creation)
    tags: tuple[str, ...] = ()  # only meaningful when from_version == 0 (creation)
    # Phase D (secret-scrub/size-cap guard) redaction notices, surfaced on the
    # approval card ("N개의 잠재적 민감정보가 자동으로 제거되었습니다"). Empty when
    # the guard found nothing to flag.
    warnings: tuple[str, ...] = ()


# An effect is either an immediate notification (memory) or a proposal that the
# orchestrator must gate through HITL (skill create/patch). PEP 695 alias (3.12+).
type LearningEffect = MemoryWritten | PatchProposal


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


# ── curator consolidation (Phase B) ───────────────────────────────────────────
@dataclass(frozen=True)
class ArchiveProposal:
    """A proposed archive (prune, or absorb-then-archive after a merge) awaiting
    HITL approval. Doesn't fit `PatchProposal`'s shape — no diff, no version bump,
    just a state transition — so it gets its own small DTO and its own approval
    endpoint (`POST /skills/{id}/archive-decision`, no diff card to render)."""

    skill_id: str
    reason: str
    # 병합 흡수 대상 umbrella skill_id. 순수 정리(병합 대상 없음)면 None.
    absorbed_into: str | None = None


@dataclass(frozen=True)
class ConsolidateResult:
    """`consolidate()`(mode=consolidate)의 반환값 — 규칙 기반 전이(curate()와 동일
    소스) + (provider가 배선됐으면) LLM 통합 제안. 제안은 `IApprovalGate` 파킹 없이
    그대로 반환된다 — `/skills/curate`는 SSE 스트림이 아닌 일반 REST라 파킹할
    이유가 없다(프론트가 로컬 상태로 들고 있다가 개별 승인 엔드포인트로 보낸다)."""

    transitioned: tuple[tuple[str, SkillState], ...] = ()
    proposals: tuple[PatchProposal | ArchiveProposal, ...] = ()


@runtime_checkable
class ILearningService(Protocol):
    # ── turn-critical ─────────────────────────────────────────────────────────
    def prefetch(self, session_id: str, user_message: str) -> PrefetchResult:
        """Assemble injectable context: active skills (all under token budget),
        recent facts + FTS-matched memories, and skill-creation guidance."""
        ...

    async def observe(self, outcome: TurnOutcome) -> list[LearningEffect]:
        """Inspect a finished turn *cheaply and synchronously* — memory fact/pref
        writes are applied immediately and returned as notifications. Skill
        creation/patch judgement is NOT here (see `review`/`learn` below) — an
        LLM call must never sit inline in the turn's own SSE stream."""
        ...

    def apply_patch(
        self,
        skill_id: str,
        *,
        approved: bool,
        proposed_body: str | None = None,
        edited_body: str | None = None,
        description: str = "",
        tags: tuple[str, ...] = (),
        source: str | None = None,
    ) -> int | None:
        """Commit (or discard) a previously proposed patch — or brand-new skill
        (`from_version=0`, no skill exists yet under `skill_id`) — after HITL.

        On approve, existing skill: archive prior body to `.history/`, write new
        body (edited_body if the user touched the approval card, else
        proposed_body — the proposal's own text — else the unchanged current
        body), version += 1, usage patch += 1 → returns the new version.

        On approve, no existing skill (creation-as-patch): write a fresh v1
        using `description`/`tags`/`source` (ignored for a real patch — those
        never change on patch, only body/version do), no history/patch-counter
        bump (mirrors `create_skill`'s existing behavior — creating isn't
        "patching"). On reject/timeout: returns None."""
        ...

    # ── mid-turn staging (agent/builtins/skill_tools.py::propose_skill, T5) ───
    def stage_proposal(
        self,
        session_id: str,
        *,
        name: str,
        body: str,
        description: str = "",
        tags: tuple[str, ...] = (),
        changelog: str = "",
    ) -> dict:
        """대화 중 에이전트가 발의한 스킬 승격/개선 제안을 세션별로 스테이징한다
        (Self-Improvement v3). 파일에 닿지 않는다 — observe()가 턴 종료 때 드레인해
        기존 HITL 카드(surface_learning_effect)로 서피싱한다. 반환은 모델에게
        돌려줄 상태 dict({"status": "staged"|"dropped", "message": ...})."""
        ...

    def resolve_proposal(
        self,
        proposal: PatchProposal,
        *,
        session_id: str,
        decision: str,
        edited_body: str | None = None,
    ) -> int | None:
        """파킹된 PatchProposal의 HITL 결정 처리 — apply_patch를 감싸며 (1) 결정을
        ledger에 기록하고(메타학습 원천), (2) timeout이면 폐기 대신 제안함
        (skill_suggestions)으로 옮긴다. approve면 새 버전 번호, 그 외 None."""
        ...

    # ── suggestion inbox (Self-Improvement v3) ───────────────────────────────
    def suggestions(self) -> list[dict]:
        """pending 제안함 목록 (GET /skills/suggestions). payload는 SSE
        skill_patch_proposed와 같은 어휘 — 프론트가 동일 카드로 렌더링."""
        ...

    def decide_suggestion(
        self, suggestion_id: str, *, approved: bool, edited_body: str | None = None
    ) -> dict:
        """제안함 항목 승인/거부 (POST /skills/suggestions/{id}/decision). 승인 시
        kind에 따라 apply_patch(create/patch) 또는 decide_archive(archive)로 적용.
        반환: {"applied": bool, "version": int | None}."""
        ...

    async def discover(self, digests: list) -> list[dict]:
        """크로스세션 반복 패턴 발굴 (POST /skills/discover + 백그라운드 스케줄러).
        산출된 신규 스킬 제안을 제안함에 넣고, 새로 추가된 suggestion 목록을
        반환한다. provider 미배선이면 빈 목록."""
        ...

    def learning_events(self, limit: int = 100) -> list[dict]:
        """학습 감사 로그 (GET /learning/events) — 제안/결정/적용/메모리 기록."""
        ...

    # ── post-turn, LLM-backed (routes/messages.py: /learning-review, /learn) ──
    async def review(
        self, session_id: str, history: list[Message], tool_calls_made: int
    ) -> list[LearningEffect]:
        """Gated LLM review (Phase A.2/A.3). Checks the cheap heuristic gate
        first (`review/gate.py::ReviewGate`) — returns `[]` immediately if the
        turn doesn't warrant an LLM call, or if no provider was wired
        (`resolve_provider=None`, e.g. most unit tests). Otherwise runs one
        `ReviewEngine.review()` call and returns whatever it proposes (memory
        writes already applied; skill effects still awaiting HITL)."""
        ...

    async def learn(
        self, session_id: str, history: list[Message], instruction: str = ""
    ) -> list[LearningEffect]:
        """Explicit user-triggered learning (Phase C, `POST /sessions/{id}/learn`)
        — same engine as `review`, but always runs (no gate) and uses a more
        directive prompt. `instruction` is free text ("focus on the deploy
        steps"); empty falls back to "distill this whole conversation"."""
        ...

    # ── management (routes/skills.py) ────────────────────────────────────────
    def list_skills(self) -> list[SkillSummary]: ...
    def get_skill(self, skill_id: str) -> SkillDetail | None: ...
    def create_skill(
        self,
        *,
        name: str,
        body: str,
        description: str = "",
        tags: tuple[str, ...] = (),
    ) -> SkillDetail:
        """User-authored skill (POST /skills). Validates the name, refuses
        collisions, writes a v1 active SKILL.md (source='user')."""
        ...

    def update_skill(
        self,
        skill_id: str,
        *,
        body: str | None = None,
        description: str | None = None,
        tags: tuple[str, ...] | None = None,
        state: SkillState | None = None,
    ) -> SkillDetail:
        """Edit a skill in place (PUT /skills/{id}). A body change archives the prior
        version to .history/ and bumps version (so diff/rollback still work)."""
        ...

    def delete_skill(self, skill_id: str) -> None:
        """Delete a skill + its usage/history sidecars (DELETE /skills/{id})."""
        ...

    def curate(self) -> CurateResult: ...

    def consolidate(self) -> ConsolidateResult:
        """수동 "스킬 정리"의 심화판(Phase B, `POST /skills/curate?mode=consolidate`).
        규칙 기반 전이(curate()와 동일 로직)를 먼저 실행한 뒤, LLM이 배선되어
        있으면(`resolve_provider` 주입됨) 유사/중복 스킬 묶음을 하나의 class-level
        umbrella로 통합하는 제안을 추가로 산출한다. LLM 미배선이면 proposals가
        빈 튜플이라 순수 curate()와 동일하게 동작(하위호환)."""
        ...

    def decide_archive(
        self, skill_id: str, *, approved: bool, absorbed_into: str | None = None
    ) -> bool:
        """`ArchiveProposal` 승인/거부(`POST /skills/{id}/archive-decision`). 승인
        시 archived로 전이 + `absorbed_into` frontmatter 기록, 거부 시 변경 없음.
        반환: 실제로 전이가 일어났으면 True(스킬이 없거나 이미 archived면 False)."""
        ...

    def set_state(self, skill_id: str, state: SkillState) -> None: ...
    def history(self, skill_id: str) -> list[SkillVersion]: ...
    def rollback(self, skill_id: str, version: int) -> int: ...

    # ── memory (routes/memories) ─────────────────────────────────────────────
    def list_memories(self) -> list[MemoryItem]: ...

    def delete_memory(self, memory_id: str) -> None:
        """메모리 삭제 (DELETE /memories/{id}, Self-Improvement v3). 메모리는
        HITL 없이 즉시 반영되는 유일한 학습 경로라, 사후 제거 수단이 있어야
        '한 번 잘못 들어가면 매 턴 영구 주입'이 되지 않는다."""
        ...
