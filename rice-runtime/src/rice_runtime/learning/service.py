"""LearningService — ILearningService implementation (facade).

This facade wires the sub-modules together so the orchestrator and routes see only
one object (RUNTIME_DESIGN §3 upgrade seam). External callers touch nothing below
this file; the seam discipline keeps the inner graph swappable.

Implementation status per task batch:
  T1 (done): memory/store.py + memory/search.py — CRUD + FTS5 retrieval.
             prefetch now injects recent facts + FTS-matched memories.
             list_memories() delegates to MemoryStore.
  T2 (done): skill/store.py + skill/usage.py + skill/lifecycle.py
             + curator/curator.py — 스킬 CRUD, usage 카운터, 상태 전이.
             list_skills, get_skill, history, rollback, set_state, curate 구현.
             prefetch: active 스킬 전부 token budget 내에서 주입.
  T3 (superseded by review engine, see below): observe() 는 한때 스킬 생성/patch도
             정규식으로 직접 산출했으나(truncation 본문/note-append 본문 — 품질이 나빴다),
             그 판단력을 통째로 learning/review/engine.py(LLM 기반, 비동기 `/learning-review`
             경로)로 옮겼다. observe()는 이제 memory fact/preference 추출 + use 카운터
             증가만 담당한다(턴 안에서 즉시·저비용으로 끝나야 하므로 LLM 호출 없음).
  T5 (done): user_model/profile.py — preferences → system prompt.
"""

from __future__ import annotations

from typing import Callable

from ..agent.providers.interfaces import ILLMProvider
from ..core.config import LearningConfig
from ..core.errors import NotFoundError, ValidationError
from ..core.paths import RicePaths
from ..core.storage.sqlite import Database
from ..core.types import InjectedSkill, Message, MemoryItem, TurnOutcome, now_iso
from ..prompts.system import SKILL_GUIDANCE
from .curator.consolidate import ConsolidationEngine
from .curator.curator import Curator
from .interfaces import (
    ArchiveProposal,
    ConsolidateResult,
    CurateResult,
    ILearningService,
    LearningEffect,
    MemoryWritten,
    PatchProposal,
    PrefetchResult,
    SkillDetail,
    SkillState,
    SkillSummary,
    SkillVersion,
)
from .memory.search import MemorySearch
from .memory.store import MemoryStore
from .review.engine import ReviewEngine
from .review.gate import ReviewGate
from .signals import FACT_PREFIXES, PREF_PREFIXES
from .skill import guard as skill_guard
from .skill.lifecycle import SkillLifecycle
from .skill.store import SkillData, SkillStore
from .skill.usage import UsageStore
from .user_model.profile import UserProfile

# 토큰 추정: len(text) / 4 (대략적 — 정밀도 불필요, LEARNING_LOOP §7)
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _render_memories(memories: list[MemoryItem]) -> str:
    """Render a list of MemoryItem into a compact system-prompt block.

    WHY: The prefetch result carries both the rendered string (for direct injection
    into the system prompt) and the structured list (for the orchestrator to build
    TurnContext metadata). We produce the string here so the caller doesn't need to
    know the rendering format.
    """
    if not memories:
        return ""
    lines = ["## Known facts about this user / project"]
    for m in memories:
        prefix = "FACT" if m.kind == "fact" else "PREF"
        lines.append(f"- [{prefix}] {m.content}")
    return "\n".join(lines)


def _render_skills(skills: list[InjectedSkill]) -> str:
    """Active 스킬 본문을 system prompt 블록으로 렌더링한다 (LEARNING_LOOP §7)."""
    if not skills:
        return ""
    lines = ["## Available Skills (follow these procedures when relevant)"]
    for sk in skills:
        lines.append(f"\n### [{sk.name}] (v{sk.version})\n{sk.body}")
    return "\n".join(lines)


class LearningService(ILearningService):
    """WHY `resolve_provider` (2026-07-02, Phase A.3): until now this facade was
    deliberately "provider-agnostic" — `observe()` only ever did regex matching.
    That purity is the reason skill-authoring quality was so poor (truncated
    bodies, note-appended patches — see `learning/review/engine.py`'s module
    docstring). Giving `review()`/`learn()` real judgment requires an LLM call,
    so this seam now depends on `ILLMProvider` too. `resolve_provider=None`
    (the default — most existing tests construct `LearningService(db, cfg,
    paths)` with nothing else) disables `review`/`learn` entirely (they return
    `[]`) rather than requiring every call site to wire a provider it doesn't
    care about."""

    def __init__(
        self,
        db: Database,
        config: LearningConfig,
        paths: RicePaths | None = None,
        *,
        resolve_provider: Callable[[], ILLMProvider] | None = None,
    ) -> None:
        self._db = db
        self._config = config
        # paths가 None이면 기본 경로를 사용한다.
        # NOTE(agent): paths=None은 T1 이전 테스트와의 역호환을 위해 허용한다.
        # composition_root.py에서 paths를 DI로 주입하므로 실제 실행 경로에서는 항상 제공된다.
        self._paths = paths or RicePaths.from_env()
        self._mem_store = MemoryStore(db)
        self._mem_search = MemorySearch(db)
        self._skill_store = SkillStore(self._paths)
        self._usage_store = UsageStore(self._paths)
        self._lifecycle = SkillLifecycle(self._skill_store, self._paths)
        self._curator = Curator(self._skill_store, self._lifecycle, config, self._usage_store)
        self._user_profile = UserProfile(self._mem_store)
        self._review_gate = ReviewGate(
            min_tool_calls=config.review_min_tool_calls, nudge_turns=config.review_nudge_turns
        )
        self._review_engine = (
            ReviewEngine(
                resolve_provider=resolve_provider,
                skill_store=self._skill_store,
                usage_store=self._usage_store,
                mem_store=self._mem_store,
                skill_catalog_token_budget=config.skill_inject_token_budget,
                max_body_chars=config.skill_max_body_chars,
            )
            if resolve_provider is not None
            else None
        )
        self._consolidation_engine = (
            ConsolidationEngine(
                resolve_provider=resolve_provider,
                skill_store=self._skill_store,
                usage_store=self._usage_store,
                candidate_budget_chars=config.curator_candidate_budget_chars,
                max_body_chars=config.skill_max_body_chars,
            )
            if resolve_provider is not None
            else None
        )

    # ── turn-critical ─────────────────────────────────────────────────────────
    def prefetch(self, session_id: str, user_message: str) -> PrefetchResult:
        """Assemble injectable context for the next LLM call (LEARNING_LOOP §6, §7).

        Delivers:
          - Active 스킬 전부 token budget 내에서 주입 (inject-all-active-under-budget, §7)
          - Recent facts (N = config.prefetch_recent_facts)
          - FTS5-matched memories (K = config.prefetch_fts_top_k) for the query
          - Skill-creation guidance string
          - User preferences (T5)
        """
        # ── 스킬 수집 (active 전체, budget 내) ──────────────────────────────
        all_active = [s for s in self._skill_store.list() if s.state == "active"]

        injected_skills: list[InjectedSkill] = []
        remaining_budget = self._config.skill_inject_token_budget

        # 예산 내 전부 주입; 초과 시 use 상위 정렬 후 선별 (§7)
        # 먼저 usage 데이터로 use 카운트를 가져온다
        def _use_count(name: str) -> int:
            return self._usage_store.read(name).use

        active_sorted = sorted(all_active, key=lambda s: _use_count(s.name), reverse=True)

        for skill in active_sorted:
            body = skill.body or ""
            tokens = _estimate_tokens(body)
            if tokens > remaining_budget:
                # 예산 초과: 이 스킬 이후는 건너뜀 (이미 use 정렬되어 있으므로)
                break
            injected_skills.append(
                InjectedSkill(
                    skill_id=skill.name,
                    name=skill.name,
                    version=skill.version,
                    body=body,
                )
            )
            remaining_budget -= tokens

        # ── 메모리 수집 ────────────────────────────────────────────────────
        recent = self._mem_search.recent(self._config.prefetch_recent_facts)
        fts_hits = self._mem_search.fts_search(user_message, self._config.prefetch_fts_top_k)

        seen: set[str] = {m.id for m in recent}
        extra = [m for m in fts_hits if m.id not in seen]
        memories = recent + extra

        # ── system prompt 렌더링 ──────────────────────────────────────────
        mem_block = _render_memories(memories)
        skill_block = _render_skills(injected_skills)
        pref_block = self._user_profile.render_preference_block()
        parts = [SKILL_GUIDANCE]
        if skill_block:
            parts.append(skill_block)
        if mem_block:
            parts.append(mem_block)
        if pref_block:
            parts.append(pref_block)
        system_additions = "\n\n".join(parts)

        return PrefetchResult(
            system_prompt_additions=system_additions,
            skills=tuple(injected_skills),
            memories=tuple(memories),
        )

    async def observe(self, outcome: TurnOutcome) -> list[LearningEffect]:
        """턴 결과를 관찰해 *즉시·저비용* 학습 효과만 반환한다 (LEARNING_LOOP §1).

        수행 순서 (모두 보수적, 규칙 기반 — LLM 호출 없음, 턴의 SSE 스트림 안에서
        `done` 이전에 동기 실행되므로 여기서 절대 느려져선 안 된다):
          ① 주입됐던 스킬 use += 1 & last_used_at 갱신
          ② fact / preference 기록 판단 → MemoryWritten 효과

        스킬 생성/patch 제안은 더 이상 여기서 만들지 않는다 — 그 판단은
        `review()`(learning/review/engine.py, LLM 기반)로 완전히 옮겨졌다. 이유:
        규칙 기반 저작은 내용 품질 문제(truncation/note-append)를 규칙을 더 다듬는
        걸로는 해결 못 하고, 두 경로를 동시에 두면 같은 턴에 중복 제안이 뜰 위험이
        있다. `signals.py`의 신호 탐지 함수는 폐기되지 않고 `review/gate.py`의
        `should_review()`가 "LLM을 부를 가치가 있는가"를 판단하는 데 재사용된다.
        """
        effects: list[LearningEffect] = []

        # ① 주입됐던 스킬 use 카운터 올리기
        for skill_id in outcome.injected_skill_ids:
            try:
                self._usage_store.increment_use(skill_id)
            except Exception:
                # NOTE(agent): usage increment 실패는 조용히 넘긴다. 카운터 오류가
                # 턴을 깨지 않도록 (fail-safe 우선).
                pass

        if outcome.aborted:
            # 중단된 턴에서는 메모리 판단을 하지 않는다 (fail-closed).
            return effects

        msg = outcome.final_message or ""

        # ② 메모리 기록 판단 (보수적: 명시적 키워드가 있을 때만)
        # NOTE(agent): 자동 메모리 감지는 false positive 위험이 높다.
        # "the project uses X" / "사용자는 Y를 선호한다" 같은 명시적 문구만 처리.
        fact_item = _try_extract_fact(msg)
        if fact_item:
            try:
                self._mem_store.add("fact", fact_item, source_session_id=outcome.session_id)
                effects.append(MemoryWritten(memory_type="fact", content=fact_item))
            except Exception:
                pass

        pref_item = _try_extract_preference(msg)
        if pref_item:
            try:
                self._mem_store.add("preference", pref_item, source_session_id=outcome.session_id)
                effects.append(MemoryWritten(memory_type="preference", content=pref_item))
            except Exception:
                pass

        return effects

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
        """HITL 승인 결정대로 패치(또는 신규 생성 제안)를 적용하거나 버린다
        (LEARNING_LOOP §5.2.4, Phase A.1: 생성 = v0에서의 patch로 통일).

        두 경로를 하나로 다룬다:
          - **기존 스킬 patch**: ① 현재 본문 → .history/v<N>.md ② 새 본문 기록
            (edited_body > proposed_body > 현재 본문 순 우선) + version += 1
            ③ usage patch += 1.
          - **신규 생성**(디스크에 skill_id가 아직 없음 — "from_version=0" 제안의 승인):
            history에 보관할 이전 버전이 없으므로 그 단계를 건너뛰고 바로 v1을
            새로 쓴다. `description`/`tags`/`source`는 이 경로에서만 쓰인다(기존
            patch는 메타데이터를 건드리지 않는다 — update_skill의 몫). usage는
            patch 카운터를 올리지 않고 0-초기화 사이드카만 만든다(생성은 "패치
            횟수"가 아니므로 — create_skill의 기존 관례와 동일).

        On reject: None 반환, 변경 없음.

        버그#0 교정(2026-07-02): 이전에는 edited_body가 None이면 무조건 current.body로
        되돌아가 — 사용자가 diff 카드를 보고 그냥 "승인"만 누르면(가장 흔한 경로) 실제
        제안 본문이 반영되지 않고 버전만 올라가는 채로 방치되어 있었다. 호출부
        (orchestrator._surface_effect)가 이제 PatchProposal.proposed_body를 항상 넘긴다.
        """
        if not approved:
            return None

        try:
            current = self._skill_store.read(skill_id)
        except NotFoundError:
            # 신규 생성 경로 — history에 보관할 이전 버전이 없다.
            new_body = edited_body if edited_body is not None else proposed_body
            if new_body is None:
                return None  # 본문이 전혀 없으면 만들 수 없다 (fail-closed)
            skill_guard.validate_content_size(new_body, self._config.skill_max_body_chars)
            new_body, _ = skill_guard.scrub_secrets(new_body)  # 방어적 재적용(대부분 이미 클린)
            now = now_iso()
            skill = SkillData(
                name=skill_id,
                version=1,
                description=description,
                state="active",
                body=new_body,
                tags=list(tags),
                source=source,
                created_at=now,
                updated_at=now,
            )
            self._skill_store.write(skill)
            usage = self._usage_store.read(skill_id)  # 0-초기화 사이드카 생성 보장
            self._usage_store._write(usage)  # noqa: SLF001 (create_skill과 동일 관례)
            return 1
        except Exception:
            return None

        # ① 현재 본문 history로 보관
        current_path = self._paths.skills / f"{skill_id}.md"
        if current_path.exists():
            self._skill_store.archive_to_history(skill_id, current.version, current_path.read_text(encoding="utf-8"))

        # ② 새 본문 적용 — edited_body > proposed_body > 현재 본문(둘 다 없을 때만) 순.
        if edited_body is not None:
            new_body = edited_body
        elif proposed_body is not None:
            new_body = proposed_body
        else:
            new_body = current.body
        skill_guard.validate_content_size(new_body, self._config.skill_max_body_chars)
        new_body, _ = skill_guard.scrub_secrets(new_body)
        new_version = current.version + 1
        current.version = new_version
        current.body = new_body
        current.updated_at = now_iso()
        self._skill_store.write(current)

        # ③ usage patch += 1
        self._usage_store.increment_patch(skill_id)

        return new_version

    # ── post-turn, LLM-backed (Phase A.3) ────────────────────────────────────
    async def review(
        self, session_id: str, history: list[Message], tool_calls_made: int
    ) -> list[LearningEffect]:
        """게이트 통과 시에만 LLM 1회 호출 — POST /sessions/{id}/learning-review 가
        메인 턴의 done 이후 논블로킹으로 호출한다(§1 아키텍처 결정)."""
        if self._review_engine is None:
            return []  # resolve_provider 미배선(대부분의 단위 테스트) — 조용히 스킵

        final_message = next(
            (m.content for m in reversed(history) if m.role == "assistant" and m.content), ""
        )
        user_text = next((m.content for m in reversed(history) if m.role == "user"), None)
        if not self._review_gate.should_review(
            session_id, tool_calls_made=tool_calls_made, final_message=final_message, user_text=user_text
        ):
            return []
        self._review_gate.mark_reviewed(session_id)
        return await self._review_engine.review(session_id, history)

    async def learn(
        self, session_id: str, history: list[Message], instruction: str = ""
    ) -> list[LearningEffect]:
        """명시적 사용자 요청 — 게이트 없이 항상 실행(POST /sessions/{id}/learn, Phase C)."""
        if self._review_engine is None:
            return []
        return await self._review_engine.learn(session_id, history, instruction)

    # ── management ────────────────────────────────────────────────────────────
    def list_skills(self) -> list[SkillSummary]:
        """~/.rice/skills/*.md 전체를 SkillSummary DTO 목록으로 반환한다."""
        result = []
        for skill in self._skill_store.list():
            usage = self._usage_store.read(skill.name)
            result.append(
                SkillSummary(
                    id=skill.name,
                    name=skill.name,
                    version=skill.version,
                    description=skill.description,
                    state=skill.state,  # type: ignore[arg-type]
                    tags=tuple(skill.tags),
                    source=skill.source,
                    use=usage.use,
                    view=usage.view,
                    patch=usage.patch,
                    last_used_at=usage.last_used_at,
                )
            )
        return result

    def get_skill(self, skill_id: str) -> SkillDetail | None:
        """스킬 상세 조회. view 카운터를 올린다 (LEARNING_LOOP §3)."""
        try:
            skill = self._skill_store.read(skill_id)
        except Exception:
            return None
        self._usage_store.increment_view(skill_id)
        usage = self._usage_store.read(skill_id)
        return self._detail_from(skill, usage)

    def _detail_from(self, skill: SkillData, usage) -> SkillDetail:
        """SkillData + UsageData → SkillDetail DTO (view 카운터는 건드리지 않음)."""
        return SkillDetail(
            id=skill.name,
            name=skill.name,
            version=skill.version,
            description=skill.description,
            state=skill.state,  # type: ignore[arg-type]
            tags=tuple(skill.tags),
            source=skill.source,
            use=usage.use,
            view=usage.view,
            patch=usage.patch,
            last_used_at=usage.last_used_at,
            body=skill.body,
        )

    def create_skill(
        self,
        *,
        name: str,
        body: str,
        description: str = "",
        tags: tuple[str, ...] = (),
    ) -> SkillDetail:
        """사용자 직접 스킬 생성 (POST /skills, CONTRACTS §1.3)."""
        if not skill_guard.VALID_SKILL_NAME.match(name):
            raise ValidationError(
                f"스킬 이름은 kebab-case(소문자/숫자/하이픈, 2~64자)여야 합니다: {name!r}"
            )
        try:
            self._skill_store.read(name)
            raise ValidationError(f"이미 존재하는 스킬입니다: {name!r}")
        except NotFoundError:
            pass

        # Phase D: 사용자 직접 작성 경로도 동일 가드를 거친다(이전엔 크기/시크릿
        # 검증이 전혀 없었다 — 지금 함께 닫는다).
        skill_guard.validate_content_size(body, self._config.skill_max_body_chars)
        body, _ = skill_guard.scrub_secrets(body)

        now = now_iso()
        skill = SkillData(
            name=name,
            version=1,
            description=description,
            state="active",
            body=body,
            tags=list(tags),
            source="user",
            created_at=now,
            updated_at=now,
        )
        self._skill_store.write(skill)
        usage = self._usage_store.read(name)  # 0-초기화 사이드카 생성 보장
        self._usage_store._write(usage)  # noqa: SLF001 (모듈 내부 일관 사용)
        return self._detail_from(skill, usage)

    def update_skill(
        self,
        skill_id: str,
        *,
        body: str | None = None,
        description: str | None = None,
        tags: tuple[str, ...] | None = None,
        state: SkillState | None = None,
    ) -> SkillDetail:
        """스킬 직접 편집 (PUT /skills/{id}). 본문 변경 시 history 보관 + version+1."""
        current = self._skill_store.read(skill_id)  # NotFoundError 위임

        if state is not None and state not in ("active", "stale", "archived"):
            raise ValidationError(f"Unknown skill state {state!r}")

        body_changed = body is not None and body != current.body
        if body_changed:
            assert body is not None  # narrows for the type checker (body_changed implies this)
            skill_guard.validate_content_size(body, self._config.skill_max_body_chars)
            body, _ = skill_guard.scrub_secrets(body)
            current_path = self._paths.skills / f"{skill_id}.md"
            if current_path.exists():
                self._skill_store.archive_to_history(
                    skill_id, current.version, current_path.read_text(encoding="utf-8")
                )
            current.version += 1
            current.body = body

        if description is not None:
            current.description = description
        if tags is not None:
            current.tags = list(tags)
        if state is not None:
            current.state = state
        current.updated_at = now_iso()
        self._skill_store.write(current)
        return self._detail_from(current, self._usage_store.read(skill_id))

    def delete_skill(self, skill_id: str) -> None:
        """스킬 삭제 (DELETE /skills/{id}). 본문/usage/history 사이드카 모두 제거."""
        self._skill_store.read(skill_id)  # 없으면 NotFoundError → 404
        self._skill_store.delete(skill_id)
        self._usage_store.delete(skill_id)

    def curate(self) -> CurateResult:
        """수동 "스킬 정리": 시간 규칙을 즉시 적용한다 (LEARNING_LOOP §5.1)."""
        return self._curator.curate()

    async def consolidate(self) -> ConsolidateResult:
        """"스킬 정리"의 심화판(Phase B, POST /skills/curate?mode=consolidate).
        규칙 기반 전이를 항상 먼저 실행(Hermes의 2단계 모델과 동일 — LLM 패스는
        보완이지 대체가 아니다)한 뒤, provider가 배선돼 있으면 통합 제안을 더한다.
        게이트 없음 — 수동 트리거는 항상 시도한다(비용은 사용자가 감수)."""
        rule_result = self._curator.curate()
        if self._consolidation_engine is None:
            return ConsolidateResult(transitioned=rule_result.transitioned)
        proposals = await self._consolidation_engine.analyze()
        return ConsolidateResult(transitioned=rule_result.transitioned, proposals=tuple(proposals))

    def decide_archive(
        self, skill_id: str, *, approved: bool, absorbed_into: str | None = None
    ) -> bool:
        """ArchiveProposal 승인/거부. 승인 시 archived 전이 + absorbed_into 기록."""
        if not approved:
            return False
        try:
            self._lifecycle.archive_with_absorption(skill_id, absorbed_into)
        except NotFoundError:
            return False
        return True

    def set_state(self, skill_id: str, state: SkillState) -> None:
        """스킬 상태를 수동으로 변경한다."""
        self._lifecycle.set_state(skill_id, state)

    def history(self, skill_id: str) -> list[SkillVersion]:
        """스킬 히스토리 버전 목록을 반환한다 (LEARNING_LOOP §5.2.4)."""
        entries = self._skill_store.read_history(skill_id)
        result = []
        for version, updated_at, body in entries:
            result.append(SkillVersion(version=version, updated_at=updated_at, body=body))
        return result

    def rollback(self, skill_id: str, version: int) -> int:
        """히스토리 버전을 현재로 복원한다. 새 version 번호 반환."""
        return self._lifecycle.rollback(skill_id, version)

    # ── memory ────────────────────────────────────────────────────────────────
    def list_memories(self) -> list[MemoryItem]:
        """Delegate to MemoryStore for consistent access (T1)."""
        return self._mem_store.list()


# ── observe 헬퍼 함수들 (모듈 수준, LearningService에서 호출) ─────────────────
# NL 신호셋(fact/pref 도입구)은 learning/signals.py 에 한국어+영어로 중앙화되어
# 있다 (버그#8 교정 — v1 은 영어 전용이었다). 스킬화 의도/patch 트리거 신호는
# 이제 review/gate.py의 should_review()가 재사용한다 (observe()는 fact/pref만).


def _try_extract_fact(message: str) -> str | None:
    """final_message에서 선언적 사실 문구를 추출한다.

    NOTE(agent): 규칙 기반 감지로 false positive 최소화. 명시적 패턴만.
    실제 배포에서는 LLM 기반 추출이 더 정확하겠지만 MVP에서는 보수적으로.
    """
    msg_lower = message.lower()
    for prefix in FACT_PREFIXES:
        idx = msg_lower.find(prefix)
        if idx == -1:
            continue
        # 패턴 이후 첫 문장 (마침표/줄바꿈/80자 제한)
        rest = message[idx:].strip()
        end = min(
            next((i for i, c in enumerate(rest) if c in (".", "\n")), len(rest)),
            80,
        )
        candidate = rest[:end].strip()
        if len(candidate) >= 10:  # 너무 짧은 건 노이즈
            return candidate
    return None


def _try_extract_preference(message: str) -> str | None:
    """final_message에서 사용자 선호 문구를 추출한다."""
    msg_lower = message.lower()
    for prefix in PREF_PREFIXES:
        idx = msg_lower.find(prefix)
        if idx == -1:
            continue
        rest = message[idx:].strip()
        end = min(
            next((i for i, c in enumerate(rest) if c in (".", "\n")), len(rest)),
            80,
        )
        candidate = rest[:end].strip()
        if len(candidate) >= 10:
            return candidate
    return None


    # NOTE(agent): 스킬 생성/patch 제안을 규칙 기반으로 직접 산출하던
    # `_try_create_skill`/`_try_build_patch_proposal`은 여기서 완전히 제거됐다
    # (2026-07-02, Hermes learning_loop 이식 작업). 그 책임은
    # `learning/review/engine.py::ReviewEngine`(LLM 기반, `/learning-review`·`/learn`
    # 에서 호출)로 옮겨졌다 — 저작 품질 문제는 규칙을 다듬는 걸로 해결되지 않고,
    # 두 경로가 공존하면 같은 턴에 중복 제안이 뜰 위험이 있었다.
