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
  T3 (done): observe() — 메모리 기록 판단, 스킬 생성 판단, PatchProposal 산출.
             skill/patch.py — diff 산출. user_model/profile.py — 선호 렌더링.
             prefetch: preferences 반영.
  T5 (done): user_model/profile.py — preferences → system prompt.
"""

from __future__ import annotations

import re

from ..core.config import LearningConfig
from ..core.errors import NotFoundError, ValidationError
from ..core.paths import RicePaths
from ..core.storage.sqlite import Database
from ..core.types import InjectedSkill, MemoryItem, TurnOutcome, now_iso
from ..prompts.system import SKILL_GUIDANCE
from .curator.curator import Curator
from .interfaces import (
    CurateResult,
    ILearningService,
    LearningEffect,
    MemoryWritten,
    PatchProposal,
    PrefetchResult,
    SkillCreated,
    SkillDetail,
    SkillState,
    SkillSummary,
    SkillVersion,
)
from .memory.search import MemorySearch
from .memory.store import MemoryStore
from .signals import (
    FACT_PREFIXES,
    PREF_PREFIXES,
    extract_skill_name,
    has_skill_creation_intent,
)
from .skill.lifecycle import SkillLifecycle
from .skill.patch import build_diff, detect_patch_trigger
from .skill.store import SkillData, SkillStore
from .skill.usage import UsageStore
from .user_model.profile import UserProfile

# 토큰 추정: len(text) / 4 (대략적 — 정밀도 불필요, LEARNING_LOOP §7)
_CHARS_PER_TOKEN = 4

# 사용자 스킬 이름 규약: kebab-case ascii, 2~64자 (SKILL.md name 규약과 일치)
_VALID_SKILL_NAME = re.compile(r"^[a-z][a-z0-9-]{1,63}$")


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
    def __init__(self, db: Database, config: LearningConfig, paths: RicePaths | None = None) -> None:
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
        """턴 결과를 관찰해 학습 효과를 반환한다 (LEARNING_LOOP §1).

        수행 순서 (모두 보수적, 데모 안정성 우선):
          ① 주입됐던 스킬 use += 1 & last_used_at 갱신
          ② fact / preference 기록 판단 → MemoryWritten 효과
          ③ 스킬 생성 판단 → SkillCreated 효과 (보수적: tool_calls_made >= 5 조건)
          ④ patch 트리거 판단 → PatchProposal 효과 (미적용)

        NOTE(agent): 메모리/스킬 기록 판단은 LLM 분석 없이 규칙 기반으로만 한다.
        규칙이 너무 느슨하면 노이즈가 생기므로 보수적으로 잡는다. 데모에서는
        직접 memory_store.add()를 호출하는 경로(예: 별도 엔드포인트)가 더 신뢰할 수
        있다. observe는 자동 감지 보조 경로다.
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
            # 중단된 턴에서는 메모리/스킬 판단을 하지 않는다 (fail-closed).
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

        # 직전 사용자 메시지 — patch 트리거(교정/실패)는 보통 사용자가 말한다.
        user_text = next(
            (m.content for m in reversed(outcome.history) if m.role == "user"), None
        )

        # ③ 스킬 생성 판단 (보수적: tool_calls_made >= 5 + 명시적 스킬화 의도)
        # 단, 주입된 스킬이 없을 때만 고려 (이미 있는 절차라면 생성 불필요)
        if (
            not outcome.injected_skill_ids
            and outcome.tool_calls_made >= 5
            and has_skill_creation_intent(msg)
        ):
            skill_effect = _try_create_skill(
                final_message=msg,
                session_id=outcome.session_id,
                tool_calls_made=outcome.tool_calls_made,
                skill_store=self._skill_store,
                usage_store=self._usage_store,
            )
            if skill_effect is not None:
                effects.append(skill_effect)

        # ④ patch 트리거 판단 (보수적: 주입된 스킬 + 명확한 신호가 있을 때만)
        trigger = detect_patch_trigger(
            msg, outcome.tool_calls_made, outcome.injected_skill_ids, user_text=user_text
        )
        if trigger is not None and outcome.injected_skill_ids:
            # 가장 최근에 주입된 스킬에 대한 patch 제안 (첫 번째)
            target_skill_id = outcome.injected_skill_ids[0]
            patch_proposal = _try_build_patch_proposal(
                skill_id=target_skill_id,
                trigger=trigger,
                final_message=msg,
                user_text=user_text,
                skill_store=self._skill_store,
            )
            if patch_proposal is not None:
                effects.append(patch_proposal)

        return effects

    def apply_patch(self, skill_id: str, *, approved: bool, edited_body: str | None = None) -> int | None:
        """HITL 승인 결정대로 패치를 적용하거나 버린다 (LEARNING_LOOP §5.2.4).

        On approve:
          ① 현재 본문 → .history/v<N>.md
          ② 새 본문 기록 (edited_body 있으면 그것), version += 1, updated_at 갱신
          ③ usage patch += 1
          ④ 새 version 반환

        On reject: None 반환, 변경 없음.
        """
        if not approved:
            return None

        try:
            current = self._skill_store.read(skill_id)
        except Exception:
            return None

        # ① 현재 본문 history로 보관
        current_path = self._paths.skills / f"{skill_id}.md"
        if current_path.exists():
            self._skill_store.archive_to_history(skill_id, current.version, current_path.read_text(encoding="utf-8"))

        # ② 새 본문 적용
        new_body = edited_body if edited_body is not None else current.body
        new_version = current.version + 1
        current.version = new_version
        current.body = new_body
        current.updated_at = now_iso()
        self._skill_store.write(current)

        # ③ usage patch += 1
        self._usage_store.increment_patch(skill_id)

        return new_version

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
        if not _VALID_SKILL_NAME.match(name):
            raise ValidationError(
                f"스킬 이름은 kebab-case(소문자/숫자/하이픈, 2~64자)여야 합니다: {name!r}"
            )
        try:
            self._skill_store.read(name)
            raise ValidationError(f"이미 존재하는 스킬입니다: {name!r}")
        except NotFoundError:
            pass

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
            current_path = self._paths.skills / f"{skill_id}.md"
            if current_path.exists():
                self._skill_store.archive_to_history(
                    skill_id, current.version, current_path.read_text(encoding="utf-8")
                )
            current.version += 1
            current.body = body  # type: ignore[assignment]

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
# NL 신호셋(fact/pref 도입구, 스킬화 의도, patch 트리거)은 learning/signals.py 에
# 한국어+영어로 중앙화되어 있다 (버그#8 교정 — v1 은 영어 전용이었다).


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


def _try_create_skill(
    final_message: str,
    session_id: str,
    tool_calls_made: int,
    skill_store: SkillStore,
    usage_store: UsageStore,
) -> SkillCreated | None:
    """보수적으로 스킬 생성을 시도한다 (LEARNING_LOOP §4).

    NOTE(agent): 스킬 이름은 final_message에서 추출하거나 세션 ID로 fallback한다.
    이름 충돌 시 생성을 건너뛴다 (fail-closed). 생성 HITL은 계약에 없으므로
    skill_created SSE 이벤트로 알림만 한다 (learning.md T3 주의사항).
    """
    # 스킬 이름 추출 — "save this as a skill: <name>" / "스킬: <name>" (kebab ascii).
    # 신호/추출 로직은 signals.py 에 중앙화(한·영). 없으면 세션 id 기반 fallback.
    skill_name = extract_skill_name(final_message)
    if not skill_name:
        short = session_id[-8:] if session_id else "auto"
        skill_name = f"auto-{short}"

    # 이미 존재하면 생성하지 않는다 (fail-closed, 중복 방지)
    try:
        skill_store.read(skill_name)
        return None  # 이미 존재
    except Exception:
        pass  # 없으면 계속

    # 스킬 본문은 final_message 첫 500자 (내용 없는 것보다는 나음)
    body = final_message[:500] if final_message else "Auto-generated skill.\n"

    skill = SkillData(
        name=skill_name,
        version=1,
        description=f"Auto-created from session {session_id} ({tool_calls_made} tool calls)",
        state="active",
        body=body,
        tags=[],
        source=f"session:{session_id}",
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    try:
        skill_store.write(skill)
    except Exception:
        return None

    return SkillCreated(skill_id=skill_name, title=skill_name, version=1)


def _try_build_patch_proposal(
    skill_id: str,
    trigger: str,
    final_message: str,
    skill_store: SkillStore,
    user_text: str | None = None,
) -> PatchProposal | None:
    """스킬 patch 제안을 생성한다 (LEARNING_LOOP §5.2.3, 미적용 상태로 반환).

    proposed_body 는 기존 본문 끝에 **읽을 수 있는 Markdown 보완 섹션**을 덧붙인다
    (v1 의 HTML 주석 노이즈 제거 — 버그#7). 주입 시 LLM 이 자연스럽게 읽고, HITL 에서
    사용자가 edited_body 로 다듬을 수 있다. 보완 텍스트는 (사용자 교정 > 최종 메시지)
    순으로 가장 의미 있는 신호를 사용한다.

    NOTE(agent): 진짜 개선 본문 생성은 LLM 호출이 이상적이나(LEARNING_LOOP §5.2.3),
    learning 서비스는 provider 를 보지 않는 순수 계층이라 MVP 는 '검토 필요' 메모로
    신호를 전달하고 최종 품질은 HITL 편집에 맡긴다.
    """
    try:
        current = skill_store.read(skill_id)
    except Exception:
        return None

    old_body = (current.body or "").rstrip()

    note = (user_text or final_message or "").strip()
    note = " ".join(note.split())  # 개행/연속공백 정리
    if len(note) > 280:
        note = note[:280].rstrip() + "…"
    if not note:
        return None

    label = "사용자 교정 반영" if trigger == "T1a" else "실패 사례 보완"
    # 기존 본문은 보존하고, 명확히 표시된 Markdown 섹션을 덧붙인다(검토 후 편집 전제).
    proposed_body = (
        f"{old_body}\n\n## 보완 사항 (검토 필요 · {label})\n"
        f"- {note}\n"
    )

    diff = build_diff(old_body, proposed_body)
    new_version = current.version + 1

    # diff가 비어 있으면 제안하지 않는다 (아무 변화가 없음)
    if not diff["lines"]:
        return None

    return PatchProposal(
        skill_id=skill_id,
        changelog=f"{label} 제안 (트리거 {trigger})",
        diff=diff,
        trigger=trigger,  # type: ignore[arg-type]
        proposed_body=proposed_body,
        from_version=current.version,
        to_version=new_version,
    )
