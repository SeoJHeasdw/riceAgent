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
             경로)로 옮겼다. v3(2026-07-03)에서 정규식 fact/pref 추출마저 제거 —
             observe()는 이제 use 카운터 증가 + 미드턴(T5) 스테이징 드레인만 한다.
  T5 (done): user_model/profile.py — preferences → system prompt.
  v3 (done, 2026-07-03): ledger.py(제안→결정 영속 감사 + 반복 거부 쿨다운) +
             제안함(suggestions/decide_suggestion, 타임아웃 이동) + discovery.py
             (크로스세션 발굴) + stage_proposal/resolve_proposal(미드턴 T5) +
             delete_memory(위생). LEARNING_LOOP §9.
"""

from __future__ import annotations

from typing import Callable

from ..agent.providers.interfaces import ILLMProvider
from ..core.config import LearningConfig
from ..core.errors import NotFoundError, ValidationError
from ..core.paths import RicePaths
from ..core.storage.sqlite import Database
from ..core.types import InjectedSkill, Message, MemoryItem, TurnOutcome, now_iso
from ..prompts.discovery import SessionDigest
from ..prompts.system import SKILL_GUIDANCE
from .curator.consolidate import ConsolidationEngine
from .curator.curator import Curator
from .discovery import DiscoveryEngine
from .interfaces import (
    ArchiveProposal,
    ConsolidateResult,
    CurateResult,
    ILearningService,
    LearningEffect,
    PatchProposal,
    PrefetchResult,
    SkillDetail,
    SkillState,
    SkillSummary,
    SkillVersion,
)
from .ledger import LearningLedger
from .memory.search import MemorySearch
from .memory.store import MemoryStore
from .proposals import make_create_proposal, make_patch_proposal, proposal_to_payload
from .review.engine import ReviewEngine
from .review.gate import ReviewGate
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
        # Self-Improvement v3: 제안→결정의 영속 기록 + 제안함. provider 유무와
        # 무관하게 배선한다 — 미드턴 스테이징(T5)·수동 결정 기록은 LLM이 없어도
        # 일어나는 일이므로.
        self._ledger = LearningLedger(db, pending_cap=config.suggestion_pending_cap)
        # 미드턴 propose_skill(T5) 스테이징 — 세션별, observe()가 드레인.
        # in-memory로 충분: 같은 턴 안에서 스테이징→드레인이 끝난다(파킹과 달리
        # 턴을 넘어 살아남을 필요가 없다).
        self._staged: dict[str, list[PatchProposal]] = {}
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
                ledger=self._ledger,
                rejection_cooldown_hours=config.review_rejection_cooldown_hours,
                max_rejections=config.review_max_rejections,
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
        self._discovery_engine = (
            DiscoveryEngine(
                resolve_provider=resolve_provider,
                skill_store=self._skill_store,
                max_body_chars=config.skill_max_body_chars,
                ledger=self._ledger,
                rejection_cooldown_hours=config.review_rejection_cooldown_hours,
                max_rejections=config.review_max_rejections,
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

        수행 내용 (LLM 호출 없음 — 턴의 SSE 스트림 안에서 `done` 이전에 동기
        실행되므로 여기서 절대 느려져선 안 된다):
          ① 주입됐던 스킬 use += 1 & last_used_at 갱신
          ② 미드턴 `propose_skill`(T5) 스테이징 드레인 — 에이전트가 대화 중 발의한
             제안을 이 턴의 스트림에서 HITL 카드로 서피싱하도록 effects에 싣는다.
             중단된 턴은 폐기(fail-closed — 사용자가 멈춘 턴의 제안을 들이밀지 않는다).

        정규식 fact/preference 추출은 v3에서 **제거됐다**: LLM 리뷰 경로
        (`propose_memory_write`)와 이중 작성자였고, "항상"/"저는" 같은 느슨한
        도입구 매칭은 노이즈 메모리를 만들었다 — 잘못 들어간 메모리는 HITL 없이
        매 턴 주입되므로 가장 위생에 민감한 경로다. 이제 메모리 기록의 판단
        주체는 리뷰 LLM 하나뿐이고, 사후 제거는 delete_memory가 담당한다.
        `signals.py`의 교정/실패/스킬화 신호는 여전히 `review/gate.py`가 쓴다.
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

        # ② 미드턴 스테이징 드레인 (T5)
        staged = self._staged.pop(outcome.session_id, [])
        if outcome.aborted:
            # 중단된 턴의 제안은 폐기 (fail-closed).
            return effects
        effects.extend(staged)

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

    # ── mid-turn staging (T5, Self-Improvement v3) ────────────────────────────
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
        """`propose_skill` 빌트인의 스테이징 백엔드. 파일에 닿지 않는다 — 검증
        (가드/diff)을 통과한 PatchProposal을 세션 큐에 넣고, observe()가 턴 종료 때
        드레인해 메인 스트림의 HITL 카드로 서피싱한다.

        반복 거부 쿨다운을 **적용하지 않는다**(리뷰/발굴과 다른 점): 미드턴 발의는
        사용자가 대화에 현전한 상태라 카드를 바로 거부할 수 있고, 무엇보다 사용자가
        방금 명시적으로 "스킬로 저장해줘"라고 말한 경우일 수 있다 — 쿨다운은
        무인(unattended) 재제안을 막는 장치지 명시적 의도를 막는 장치가 아니다.
        """
        queue = self._staged.setdefault(session_id, [])
        if len(queue) >= 3:
            # 카드 스팸 방어 — 한 턴에 제안 3개면 이미 과하다.
            return {"status": "dropped", "message": "이 턴에 이미 3개의 제안이 대기 중입니다. 답변을 마치세요."}

        proposal: PatchProposal | None
        try:
            current = self._skill_store.read(name.strip())
        except NotFoundError:
            current = None
        except Exception:
            return {"status": "dropped", "message": f"스킬 이름이 유효하지 않습니다: {name!r}"}

        if current is None:
            proposal = make_create_proposal(
                name=name,
                body=body,
                description=description,
                tags=tags,
                changelog=changelog,
                trigger="T5",
                max_body_chars=self._config.skill_max_body_chars,
            )
        else:
            if (current.source or "").startswith("plugin:"):
                return {
                    "status": "dropped",
                    "message": f"'{name}'은(는) 플러그인 제공 스킬이라 학습 루프가 수정할 수 없습니다. 다른 이름으로 새 스킬을 제안하세요.",
                }
            proposal = make_patch_proposal(
                current=current,
                new_body=body,
                changelog=changelog or "대화 중 에이전트 발의 개선",
                trigger="T5",
                max_body_chars=self._config.skill_max_body_chars,
            )
        if proposal is None:
            return {
                "status": "dropped",
                "message": "제안이 성립하지 않습니다 (빈 본문, 크기 초과, 이름 규약 위반, 또는 기존 본문과 동일).",
            }
        queue.append(proposal)
        self._ledger.record(
            "proposed",
            skill_id=proposal.skill_id,
            session_id=session_id,
            origin="agent",
            trigger="T5",
            detail={"changelog": proposal.changelog},
        )
        action = "생성" if proposal.from_version == 0 else f"v{proposal.from_version}→v{proposal.to_version} 개선"
        return {
            "status": "staged",
            "message": (
                f"스킬 '{proposal.skill_id}' {action} 제안이 준비되었습니다. 답변이 끝나면 "
                "사용자에게 승인 카드가 표시됩니다 — 답변에서 제안했다고 언급하세요. 저장 여부는 사용자가 결정합니다."
            ),
        }

    def resolve_proposal(
        self,
        proposal: PatchProposal,
        *,
        session_id: str,
        decision: str,
        edited_body: str | None = None,
    ) -> int | None:
        """파킹된 제안의 HITL 결정 처리 (effect_surfacing이 호출).

        v3의 두 가지 변화:
          - 모든 결정이 ledger에 남는다 → 반복 거부 쿨다운·프롬프트 회피 목록의 원천.
          - **timeout은 더 이상 폐기가 아니다** — 제안함(skill_suggestions)으로
            옮긴다. 무응답은 거부 의사표시가 아니라 자리 비움일 수 있다. 적용은
            여전히 하지 않으므로(파일 불변) safe-default는 유지된다. abort로 인한
            cancelled는 그대로 폐기한다(사용자가 턴 자체를 버렸다).
        """
        base_detail = {"changelog": proposal.changelog, "trigger": proposal.trigger}
        if decision == "approve":
            version = self.apply_patch(
                proposal.skill_id,
                approved=True,
                proposed_body=proposal.proposed_body,
                edited_body=edited_body,
                description=proposal.description,
                tags=proposal.tags,
                source=f"session:{session_id}",
            )
            self._ledger.record(
                "approved",
                skill_id=proposal.skill_id,
                session_id=session_id,
                trigger=proposal.trigger,
                detail={**base_detail, "version": version, "edited": edited_body is not None},
            )
            return version
        if decision == "timeout":
            payload = proposal_to_payload(proposal)
            payload["session_id"] = session_id  # 승인 시 source 복원용
            self._ledger.add_suggestion(
                origin="timeout",
                kind="create" if proposal.from_version == 0 else "patch",
                skill_id=proposal.skill_id,
                payload=payload,
            )
            self._ledger.record(
                "timeout", skill_id=proposal.skill_id, session_id=session_id,
                trigger=proposal.trigger, detail=base_detail,
            )
            return None
        if decision == "reject":
            self._ledger.record(
                "rejected", skill_id=proposal.skill_id, session_id=session_id,
                trigger=proposal.trigger, detail=base_detail,
            )
            return None
        # cancelled(턴 abort) 등 — 기록만 남기고 폐기.
        self._ledger.record(
            decision, skill_id=proposal.skill_id, session_id=session_id,
            trigger=proposal.trigger, detail=base_detail,
        )
        return None

    # ── suggestion inbox (Self-Improvement v3) ───────────────────────────────
    def suggestions(self) -> list[dict]:
        return self._ledger.suggestions()

    def decide_suggestion(
        self, suggestion_id: str, *, approved: bool, edited_body: str | None = None
    ) -> dict:
        sug = self._ledger.get_suggestion(suggestion_id)
        if sug is None or sug["status"] != "pending":
            raise NotFoundError(f"suggestion {suggestion_id} not found or already decided")
        payload = sug["payload"]
        if not approved:
            self._ledger.decide_suggestion(suggestion_id, "rejected")
            self._ledger.record(
                "rejected",
                skill_id=sug["skill_id"],
                origin=sug["origin"],
                detail={"changelog": payload.get("changelog", ""), "suggestion_id": suggestion_id},
            )
            return {"applied": False, "version": None}

        if sug["kind"] == "archive":
            applied = self.decide_archive(
                sug["skill_id"], approved=True, absorbed_into=payload.get("absorbed_into")
            )
            self._ledger.decide_suggestion(suggestion_id, "approved")
            self._ledger.record(
                "approved", skill_id=sug["skill_id"], origin=sug["origin"],
                detail={"suggestion_id": suggestion_id, "kind": "archive"},
            )
            return {"applied": applied, "version": None}

        # create/patch — 파킹 경로와 동일하게 apply_patch로 수렴.
        source_session = payload.get("session_id")
        source = f"session:{source_session}" if source_session else sug["origin"]
        version = self.apply_patch(
            sug["skill_id"],
            approved=True,
            proposed_body=payload.get("proposed_body"),
            edited_body=edited_body,
            description=payload.get("description", ""),
            tags=tuple(payload.get("tags") or ()),
            source=source,
        )
        self._ledger.decide_suggestion(suggestion_id, "approved")
        self._ledger.record(
            "approved", skill_id=sug["skill_id"], origin=sug["origin"],
            detail={
                "changelog": payload.get("changelog", ""),
                "suggestion_id": suggestion_id,
                "version": version,
                "edited": edited_body is not None,
            },
        )
        return {"applied": version is not None, "version": version}

    async def discover(self, digests: list[SessionDigest]) -> list[dict]:
        """크로스세션 반복 패턴 발굴 → 제안함. 파킹 없음(백그라운드/수동 트리거라
        붙잡을 스트림이 없다). 반환은 이번 실행으로 **새로** 추가된 suggestion 목록."""
        if self._discovery_engine is None:
            return []
        proposals = await self._discovery_engine.discover(digests)
        added: list[dict] = []
        for p in proposals:
            sid = self._ledger.add_suggestion(
                origin="discovery", kind="create", skill_id=p.skill_id, payload=proposal_to_payload(p)
            )
            if sid is not None:
                sug = self._ledger.get_suggestion(sid)
                if sug is not None:
                    added.append(sug)
        self._ledger.record(
            "discovery_run",
            detail={"digests": len(digests), "proposals": len(proposals), "added": len(added)},
        )
        return added

    def learning_events(self, limit: int = 100) -> list[dict]:
        return self._ledger.events(limit)

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

    def delete_memory(self, memory_id: str) -> None:
        """메모리 삭제 (DELETE /memories/{id}, Self-Improvement v3). 메모리는 HITL
        없이 즉시 반영되는 유일한 경로라 사후 제거 수단이 위생의 핵심이다."""
        self._mem_store.delete(memory_id)  # NotFoundError 위임 → 404
        self._ledger.record("memory_deleted", detail={"memory_id": memory_id})


# NOTE(agent): 정규식 기반 fact/preference 추출(`_try_extract_fact`/
# `_try_extract_preference`)은 Self-Improvement v3(2026-07-03)에서 완전히
# 제거됐다. 판단 주체는 이제 리뷰 LLM(`propose_memory_write`) 하나다 — 두 작성자
# 체제는 중복/노이즈 메모리를 만들었고, 메모리는 HITL 없이 매 턴 주입되는 가장
# 위생에 민감한 경로다. signals.py의 교정/실패/스킬화 신호는 여전히
# review/gate.py의 게이트 판단에 쓰인다.
