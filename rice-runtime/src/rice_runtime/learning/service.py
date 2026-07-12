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

import logging
from collections.abc import Sequence
from dataclasses import replace
from typing import Callable

from ..agent.providers.interfaces import ILLMProvider
from ..core import lexical
from ..core.config import LearningConfig
from ..core.errors import ConflictError, NotFoundError, ValidationError
from ..core.paths import RicePaths
from ..core.storage.sqlite import Database
from ..core.tokens import estimate_tokens as _cjk_estimate_tokens
from ..core.types import InjectedSkill, Message, MemoryItem, TurnOutcome, now_iso
from ..prompts.discovery import SessionDigest
from ..prompts.system import (
    SKILL_GUIDANCE,
    SKILL_INDEX_HEADER,
    SKILL_INDEX_NOTE,
    SKILL_PRECEDENCE_NOTE,
    SKILL_TRUST_NOTE,
)
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
from .review.context import LearningContextManager, ReviewContextConfig
from .review.engine import ReviewEngine
from .review.gate import ReviewGate
from .signals import has_correction_signal, has_failure_signal
from .skill import guard as skill_guard
from .skill.verifier import VerificationReport, tool_call_refs, verify_candidate
from .skill.analysis import AnalysisStore, SkillAnalysisData, effective_status
from .skill.analyzer import SkillAnalyzer
from .skill.builtin import BuiltinSkillCatalog
from .skill.lifecycle import SkillLifecycle
from .skill.relevance import AUTO_INJECT_THRESHOLD, auto_inject_eligible, score_skills
from .skill.store import SkillData, SkillStore
from .skill.usage import UsageStore
from .user_model.profile import UserProfile

log = logging.getLogger("rice.learning.service")


# 토큰 추정: CJK 인식 휴리스틱(core.tokens) — 이전 len//4는 한국어 예산을 2~4배 과소평가했다.
# 프리페치 스킬 예산에도 같은 추정을 쓴다.
def _estimate_tokens(text: str) -> int:
    return _cjk_estimate_tokens(text)


def _turn_tool_names(history: tuple[Message, ...]) -> list[str]:
    """이 턴에서 호출된 도구 이름들 — 마지막 user 메시지 이후의 assistant
    tool_calls 를 수집한다 (observed 도구 ledger 의 원천)."""
    names: list[str] = []
    for m in reversed(history):
        if m.role == "user":
            break
        for tc in m.tool_calls or ():
            names.append(tc.tool_name)
    names.reverse()
    return names


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
    """Auto-injected 스킬 본문을 system prompt 블록으로 렌더링한다 (LEARNING_LOOP §7).
    이제 '전부'가 아니라 이번 턴에 관련도가 높아 전문 주입된 것만 실린다.
    스킬은 신뢰 경계가 아니라 입력 데이터이므로 SKILL_TRUST_NOTE를, 사용자의
    이번 요청이 스킬 절차보다 항상 우선하므로 SKILL_PRECEDENCE_NOTE를 함께
    싣는다 — 위험 행동은 스킬 지시와 무관하게 승인을 거친다."""
    if not skills:
        return ""
    lines = [
        # "already loaded" 명시 — 소형 모델이 인덱스의 skill_view 지시와 헷갈려
        # 이미 주입된 본문을 다시 로딩하는 중복 호출(view 인플레이션)을 막는다.
        "## Available Skills (full procedures below — already loaded, do NOT call "
        "skill_view for these; follow them when relevant)",
        SKILL_PRECEDENCE_NOTE,
        SKILL_TRUST_NOTE,
    ]
    for sk in skills:
        # provenance 를 헤딩에 남긴다 — "왜 이게 프롬프트에 있었나"가 transcript
        # 만으로 감사 가능해야 한다(plugin 스킬도 같은 파이프라인을 타므로).
        origin = f", {sk.source}" if sk.source != "learned" else ""
        lines.append(f"\n### [{sk.name}] (v{sk.version}{origin})\n{sk.body}")
    return "\n".join(lines)


_INDEX_DESC_MAX = 140  # one-liner cap per index row — keep the always-on index cheap
_INDEX_MAX_ROWS = 30  # 인덱스도 무한하지 않다 — 초과분은 skill_search 안내 한 줄로


def _render_skill_index(entries: list[tuple[str, str]]) -> str:
    """Active 스킬 중 전문 주입되지 **않은** 것들의 짧은 인덱스(이름 + 한 줄 설명).
    모델은 이걸 보고 `skill_view`로 필요한 전문만 온디맨드로 당긴다 — inject-all-active를
    대체하는 하이브리드 주입의 '항상 켜진 인덱스' 절반이다. 호출자가 관련도순으로
    정렬해 넘긴다(모델은 앞줄에 가장 주의를 쏟는다 — 알파벳순은 그 신호를 버렸다)."""
    if not entries:
        return ""
    lines = [SKILL_INDEX_HEADER, SKILL_INDEX_NOTE]
    for name, desc in entries[:_INDEX_MAX_ROWS]:
        one_line = " ".join((desc or "").split())
        if len(one_line) > _INDEX_DESC_MAX:
            one_line = one_line[: _INDEX_DESC_MAX - 1].rstrip() + "…"
        lines.append(f"- {name} — {one_line}" if one_line else f"- {name}")
    overflow = len(entries) - _INDEX_MAX_ROWS
    if overflow > 0:
        lines.append(f"- …and {overflow} more — find them with `skill_search`.")
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
        resolve_analyzer_provider: Callable[[], ILLMProvider] | None = None,
        resolve_tool_names: Callable[[], set[str]] | None = None,
    ) -> None:
        self._db = db
        self._config = config
        # 승격 검증 harness(skill/verifier.py)의 도구 카탈로그 소스 — 스킬 본문이
        # 호출을 지시하는 도구가 실재하는지 검사한다. None(대부분의 테스트/독립
        # 사용)이면 그 검사만 스킵되고 나머지 검증은 동일하게 돈다.
        self._resolve_tool_names = resolve_tool_names
        # paths가 None이면 기본 경로를 사용한다.
        # NOTE(agent): paths=None은 T1 이전 테스트와의 역호환을 위해 허용한다.
        # composition_root.py에서 paths를 DI로 주입하므로 실제 실행 경로에서는 항상 제공된다.
        self._paths = paths or RicePaths.from_env()
        self._mem_store = MemoryStore(db)
        self._mem_search = MemorySearch(db)
        self._skill_store = SkillStore(self._paths)
        # 앱과 함께 배포되는 읽기 전용 빌트인 스킬 (skill/builtin.py). 사용자 스킬과
        # 이름공간을 공유하되(주입·usage 동일 경로) 쓰기 계열은 전부 거부된다 —
        # 아래 _ensure_not_builtin이 그 단일 관문이다.
        self._builtin_skills = BuiltinSkillCatalog(self._paths)
        self._usage_store = UsageStore(self._paths)
        # 설명용 분석 사이드카 (skill/analysis.py — MySkills "스킬 이해" 패널의
        # 데이터 소스). analyzer 는 기본적으로 메인 provider 를 쓰고 안정화
        # (think=False·스키마 디코딩·max_tokens)는 per-call 로 건다 — provider
        # 미배선이면 휴리스틱 전용으로 동작. resolve_analyzer_provider 는 테스트/
        # 특수 배선용 오버라이드다.
        self._analysis_store = AnalysisStore(self._paths)
        self._analyzer = SkillAnalyzer(
            resolve_provider=resolve_analyzer_provider or resolve_provider
        )
        # 재분석 대기열 — 본문을 바꾼 경로들이 enqueue 하고 스케줄러 틱이
        # run_pending_analyses 로 드레인한다. in-memory 로 충분: 유실돼도
        # staleness 가 body hash 계산이라 다음 refresh/조회가 stale 을 정확히
        # 보고하고, 사용자가 언제든 수동 새로고침으로 복구할 수 있다.
        self._analysis_queue: list[str] = []
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
        # skill_view 로 이번 턴에 전문을 당긴 스킬들 — 세션별. observe()가 드레인해
        # auto-injected 와 합쳐 use 카운터에 귀속한다(온디맨드 로딩도 '사용'이다).
        self._viewed_skills: dict[str, set[str]] = {}
        # 자동 주입 점착(sticky) 상태 — 세션별 {skill_name: 남은 턴 수}. 한 번
        # 전문 주입된 스킬은 후속 턴이 재매칭에 실패해도 이 턴 수만큼 계속
        # 주입된다("3번 슬라이드 파랗게" 같은 후속 지시는 어휘 라우터에 안 걸린다 —
        # 절차가 태스크 중간에 증발하던 무점착 결함의 교정). in-memory 로 충분:
        # 재시작 시 소실돼도 다음 매칭이 다시 세운다(ReviewGate 카운터와 동일 전례).
        self._sticky_skills: dict[str, dict[str, int]] = {}
        # 세션별 마지막 prefetch 쿼리 — skill_view 시점의 라우팅 미스 판정용
        # (라우터가 이 쿼리에 못 맞춘 스킬을 모델이 수동 로딩하면 발견성 결함 신호).
        self._last_query: dict[str, str] = {}
        # 외부 소스(plugin) 스킬 레지스트리 — prefetch(extra_skills=...)가 매 턴
        # 갱신한다. skill_search/skill_view 가 인덱스에 실린 plugin 스킬을 실제로
        # 찾고 열 수 있게 하는 온디맨드 반쪽(store 에는 없는 스킬들이므로).
        self._extra_skills: dict[str, InjectedSkill] = {}
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
                context=LearningContextManager(
                    ReviewContextConfig(
                        recent_messages=config.review_context_recent_messages,
                        max_total_tokens=config.review_context_max_tokens,
                        tool_output_max_tokens=config.review_context_tool_output_tokens,
                        message_max_tokens=config.review_context_message_tokens,
                        older_summary_tokens=config.review_context_summary_tokens,
                    )
                ),
                builtin_catalog=self._builtin_skills,
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
                builtin_catalog=self._builtin_skills,
            )
            if resolve_provider is not None
            else None
        )

    # ── boot recovery ─────────────────────────────────────────────────────────
    def recover_boot_state(self) -> int:
        """부팅 시 1회 — 이전 프로세스가 남긴 고아 'running' 리뷰 잡을 pending 으로
        되돌린다(composition_root 가 호출). 없으면 0. 자세한 근거는
        LearningLedger.requeue_orphan_running_jobs docstring."""
        return self._ledger.requeue_orphan_running_jobs()

    # ── skill collection + relevance (hybrid injection) ───────────────────────
    def _active_skills(self) -> list[SkillData]:
        """Active 스킬 전체(빌트인 + 사용자). 빌트인 이름은 예약이라 동명 사용자
        파일은 숨긴다(list_skills와 동일 규칙). prefetch 와 search_skills 의 공통 원천.

        enabled=False(UI 비활성) 스킬은 state=active여도 주입에서 제외한다."""
        builtin_names = self._builtin_skills.names()
        skills = [s for s in self._builtin_skills.list() if s.state == "active" and s.enabled]
        skills += [
            s
            for s in self._skill_store.list()
            if s.state == "active" and s.enabled and s.name not in builtin_names
        ]
        return skills

    # ── turn-critical ─────────────────────────────────────────────────────────
    def prefetch(
        self,
        session_id: str,
        user_message: str,
        extra_skills: Sequence[InjectedSkill] = (),
        *,
        skill_token_budget: int | None = None,
    ) -> PrefetchResult:
        """Assemble injectable context for the next LLM call (LEARNING_LOOP §6, §7).

        Hybrid skill injection (replaces inject-all-active):
          - **전문 주입은 정밀도 우선**: 명시적 언급이거나, 점수 임계
            (AUTO_INJECT_THRESHOLD)와 strong-hit 게이트(name/tag 신호 ≥ 2)를
            모두 넘는 스킬만, `skill_max_auto_inject`(기본 1)·토큰 예산 내에서
            전문 주입한다. 태그 한 개 스치는 오탐은 인덱스에 남는다.
          - **예산은 창 인식(window-aware)**: `skill_token_budget`(호출자가 이 턴의
            모델 창에서 역산해 넘김 — orchestrator.effective_skill_token_budget)이
            config 기본 예산(24K)을 더 좁게 클램프한다. 로컬 32K 창에서 24K 예산은
            "예산 없음"과 같았다 — 컨텍스트 매니저는 시스템 프롬프트를 압축하지
            않으므로, 과대 주입된 스킬 본문이 대화 히스토리(사용자 의도!)를
            밀어내는 역전을 여기서 끊는다. 예산 밖 스킬은 인덱스로 강등될 뿐
            사라지지 않는다(`skill_view` 온디맨드 — 도구 결과는 히스토리라
            컨텍스트 매니저가 증류할 수 있는 안전한 자리다). None이면 기존
            config 예산 그대로(하위호환).
          - **점착(sticky)**: 한 번 전문 주입된 스킬은 `skill_sticky_turns` 동안
            재매칭 없이도 유지된다 — 후속 지시("이어서 해줘")가 라우터에 안
            걸려도 태스크 중간에 절차가 증발하지 않는다. 상한과 별도로 예산만
            차지한다(신규 주입을 밀어내지 않음).
          - 나머지 active 스킬은 **짧은 인덱스**(이름 + 한 줄 설명, 관련도순)로만
            실려서 모델이 `skill_view` 로 온디맨드로 당길 수 있다.
          - `extra_skills`(plugin SKILL.md 번들)도 **같은 파이프라인**을 탄다 —
            전량 주입이 아니라 스코어링→예산→인덱스→skill_view. 동명 학습
            스킬이 이긴다(사용자 학습이 시장 번들보다 우선).
        그 외 (변화 없음):
          - Recent facts (N = config.prefetch_recent_facts)
          - FTS5-matched memories (K = config.prefetch_fts_top_k) for the query
          - Skill-creation guidance string + user preferences (T5)
        """
        all_active = self._active_skills()
        learned_names = {s.name for s in all_active}
        extras = [s for s in extra_skills if s.name and s.name not in learned_names]
        # 온디맨드 레지스트리 갱신 — 인덱스에 실린 plugin 스킬을 skill_search/
        # skill_view 가 찾을 수 있어야 인덱스가 거짓말이 아니게 된다.
        self._extra_skills = {s.name: s for s in extras}
        self._last_query[session_id] = user_message
        candidates: list = all_active + extras
        by_name = {s.name: s for s in candidates}
        ranked = score_skills(user_message, candidates)
        rank_order = {s.name: i for i, s in enumerate(ranked)}

        def _as_injected(skill) -> InjectedSkill:
            return (
                skill
                if isinstance(skill, InjectedSkill)  # plugin 스킬은 provenance 그대로
                else InjectedSkill(
                    skill_id=skill.name, name=skill.name, version=skill.version,
                    body=skill.body or "",
                )
            )

        # ── ① 신규 자격 매칭: 정밀도 게이트 + 개수 상한 + 예산 ────────────
        injected_skills: list[InjectedSkill] = []
        injected_names: set[str] = set()
        remaining_budget = self._config.skill_inject_token_budget
        if skill_token_budget is not None:
            remaining_budget = min(remaining_budget, max(0, skill_token_budget))
        max_inject = max(0, self._config.skill_max_auto_inject)
        fresh_count = 0
        for scored in ranked:
            if scored.score < AUTO_INJECT_THRESHOLD:
                break  # ranked 는 내림차순 — 이 아래는 전부 임계 미만
            if fresh_count >= max_inject:
                break
            if not auto_inject_eligible(scored):
                continue  # 임계는 넘었지만 강신호 부족 — 인덱스로 (오탐 방지)
            skill = by_name[scored.name]
            tokens = _estimate_tokens(skill.body or "")
            if tokens > remaining_budget:
                continue  # 큰 본문 하나가 더 작은 차순위까지 막지 않게 skip
            injected_skills.append(_as_injected(skill))
            injected_names.add(skill.name)
            remaining_budget -= tokens
            fresh_count += 1

        # ── ② 점착 유지: 직전 턴들에 주입된 스킬을 예산 내에서 계속 싣는다 ──
        sticky = self._sticky_skills.setdefault(session_id, {})
        sticky_turns = max(0, self._config.skill_sticky_turns)
        for name in list(sticky):
            if name in injected_names:
                continue  # 이번 턴 재매칭 — 아래에서 만료 카운터가 리셋된다
            skill = by_name.get(name)
            remaining = sticky[name] - 1
            if skill is None or remaining <= 0:
                sticky.pop(name, None)  # 비활성화/삭제됐거나 점착 만료
                continue
            sticky[name] = remaining  # 유지 턴은 리셋하지 않는다 — 점착은 유한하다
            tokens = _estimate_tokens(skill.body or "")
            if tokens > remaining_budget:
                continue  # 예산 밖이면 이번 턴은 인덱스로 강등(점착은 유지)
            injected_skills.append(_as_injected(skill))
            injected_names.add(name)
            remaining_budget -= tokens
        if sticky_turns > 0:
            # 이번 턴 신선 매칭(재매칭 포함)만 만료 카운터를 리셋한다 — ①에서
            # 앞쪽 fresh_count 개가 신선 매칭이다. 유지 전용 스킬은 리셋하지
            # 않아 점착이 무한히 자기연장되지 않는다.
            for s in injected_skills[:fresh_count]:
                sticky[s.name] = sticky_turns
        if not sticky:
            self._sticky_skills.pop(session_id, None)

        # ── ③ 전문 주입되지 않은 나머지는 짧은 인덱스로 (관련도순) ─────────
        index_entries = [
            (s.name, s.description)
            for s in sorted(candidates, key=lambda s: rank_order.get(s.name, len(rank_order)))
            if s.name not in injected_names
        ]

        # ── 메모리 수집 ────────────────────────────────────────────────────
        recent = self._mem_search.recent(self._config.prefetch_recent_facts)
        fts_hits = self._mem_search.fts_search(user_message, self._config.prefetch_fts_top_k)

        seen: set[str] = {m.id for m in recent}
        extra = [m for m in fts_hits if m.id not in seen]
        memories = recent + extra

        # ── system prompt 렌더링 ──────────────────────────────────────────
        mem_block = _render_memories(memories)
        skill_block = _render_skills(injected_skills)
        index_block = _render_skill_index(index_entries)
        pref_block = self._user_profile.render_preference_block()
        parts = [SKILL_GUIDANCE]
        if skill_block:
            parts.append(skill_block)
        if index_block:
            parts.append(index_block)
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

    # ── on-demand skill loading (skill_search / skill_view 빌트인 백엔드) ───────
    def search_skills(self, query: str, limit: int = 8) -> list[dict]:
        """`skill_search` 빌트인의 백엔드 — active 스킬을 쿼리로 랭킹해 반환한다.
        점수>0 매칭을 우선하되, 하나도 없으면 상위 몇 개를 그대로 보여준다(모델이
        무엇이 있는지라도 알도록). 본문은 싣지 않는다 — 그건 skill_view의 몫.
        plugin 스킬(_extra_skills)도 후보에 포함 — 인덱스에 실렸으면 찾을 수 있어야 한다."""
        learned = self._active_skills()
        learned_names = {s.name for s in learned}
        extras = [s for s in self._extra_skills.values() if s.name not in learned_names]
        ranked = score_skills(query, learned + extras)
        positive = [s for s in ranked if s.score > 0]
        chosen = positive[:limit] if positive else ranked[: min(limit, 5)]
        return [
            {"name": s.name, "description": s.description, "score": round(s.score, 2)}
            for s in chosen
        ]

    def _note_routing_miss(self, session_id: str, skill, *, is_store_skill: bool) -> None:
        """모델이 수동 로딩한 스킬이 이번 턴 쿼리의 라우터에 안 걸렸다면 발견성
        결함으로 계측한다 — usage 사이드카(routing_miss)와 ledger 에 남아 리뷰
        LLM 의 '설명/태그를 보강하라' 힌트의 실측 원천이 된다. best-effort."""
        query = self._last_query.get(session_id, "")
        if not query.strip():
            return
        if skill.name in self._sticky_skills.get(session_id, {}):
            return  # 점착으로 이미 주입돼 있다 — 라우터가 놓친 게 아니다
        try:
            scored = score_skills(query, [skill])[0]
            if auto_inject_eligible(scored):
                return  # 라우터도 맞췄을 매칭 — 미스가 아니다
            if is_store_skill:
                self._usage_store.increment_routing_miss(skill.name)
            self._ledger.record(
                "routing_miss",
                skill_id=skill.name,
                session_id=session_id,
                detail={"score": round(scored.score, 2), "query": query[:120]},
            )
        except Exception:  # noqa: BLE001 — 계측 실패가 skill_view 를 깨면 안 된다
            log.warning("routing-miss telemetry failed for %r", skill.name, exc_info=True)

    _REPEAT_VIEW_NOTE = (
        "NOTE: this skill was already loaded earlier in this turn — its body is "
        "above in your context. Do not call skill_view for it again."
    )

    def view_skill_body(self, session_id: str, skill_id: str) -> dict:
        """`skill_view` 빌트인의 백엔드 — 스킬 전문을 반환하고 view 카운터를 올린다.
        비활성(archived) 스킬은 사용자가 끈 것이므로 로딩을 거부한다(토글 존중).
        이번 턴 조회 집합에 기록해 observe()가 use 카운터에 귀속하게 한다.

        같은 턴 반복 조회에는 note 를 함께 실어(본문은 그대로 반환 — sub-agent
        워커는 자기 컨텍스트에 본문이 없을 수 있다) 소형 모델의 중복 호출 루프를
        끊는다. 라우터가 못 맞춘 수동 로딩은 routing_miss 로 계측한다."""
        skill = self._read_any_skill(skill_id)
        if skill is None or skill.state != "active":
            # store 에 없으면 plugin 스킬 레지스트리 폴백 — 인덱스가 광고한 이름은
            # 항상 열려야 한다. usage sidecar 는 store 스킬 전용이라 건드리지 않고,
            # 조회 귀속(viewed)만 provenance id 로 기록한다.
            extra = self._extra_skills.get(skill_id)
            if extra is None:
                raise NotFoundError(f"skill {skill_id!r} not found or not active")
            viewed = self._viewed_skills.setdefault(session_id, set())
            repeat = extra.skill_id in viewed
            if not repeat:
                self._note_routing_miss(session_id, extra, is_store_skill=False)
            viewed.add(extra.skill_id)
            out = {
                "name": extra.name,
                "version": extra.version,
                "description": extra.description,
                "body": extra.body,
            }
            if repeat:
                out["note"] = self._REPEAT_VIEW_NOTE
            return out
        viewed = self._viewed_skills.setdefault(session_id, set())
        repeat = skill_id in viewed
        self._usage_store.increment_view(skill_id)
        if not repeat:
            self._note_routing_miss(session_id, skill, is_store_skill=True)
        viewed.add(skill_id)
        out = {
            "name": skill.name,
            "version": skill.version,
            "description": skill.description,
            "body": skill.body,
        }
        if repeat:
            out["note"] = self._REPEAT_VIEW_NOTE
        return out

    def _engagement_evidence(
        self,
        skill_id: str,
        *,
        viewed: set[str],
        user_tokens: set[str],
        turn_tools: set[str],
    ) -> bool:
        """이 턴에 스킬이 실제로 **관여**했다는 근거가 있는가.

        노출(주입) ≠ 사용이다. 관여 근거 세 가지 중 하나면 충분하다:
          - 모델이 skill_view 로 직접 당겼다 (명시적 선택)
          - 사용자가 스킬 이름을 그대로 언급했다 (명시적 요청)
          - 주입된 본문이 호출을 지시하는 도구가 이 턴에 실제로 호출됐다
            (절차가 실행에 반영됐다는 실측 단서 — verifier 와 같은 규칙)
        도구 없는 스타일 스킬은 view/언급 외에는 증명 불가 — 정직하게 안 센다."""
        if skill_id in viewed:
            return True
        if user_tokens and lexical.name_mentioned(user_tokens, skill_id):
            return True
        if not turn_tools:
            return False
        body = ""
        skill = self._read_any_skill(skill_id)
        if skill is not None:
            body = skill.body or ""
        else:
            for extra in self._extra_skills.values():
                if extra.skill_id == skill_id:
                    body = extra.body or ""
                    break
        if not body:
            return False
        return bool(set(tool_call_refs(body)) & turn_tools)

    async def observe(self, outcome: TurnOutcome) -> list[LearningEffect]:
        """턴 결과를 관찰해 *즉시·저비용* 학습 효과만 반환한다 (LEARNING_LOOP §1).

        수행 내용 (LLM 호출 없음 — 턴의 SSE 스트림 안에서 `done` 이전에 동기
        실행되므로 여기서 절대 느려져선 안 된다):
          ① 텔레메트리(정직화, 2026-07-11):
             - 전문 주입된 스킬 → injected += 1 (노출 집계, 중단 턴 포함)
             - 관여 근거(view/언급/지시 도구 실제 호출)가 있는 스킬만 use += 1
               + last_used_at 갱신 + observed 도구 ledger. curator 의 신선도와
               리뷰 카탈로그 정렬은 이제 이 정직한 use 를 본다.
             - 관여한 턴에서 교정/실패 신호(signals.py) 감지 시 friction += 1 —
               리뷰 LLM 에게 "이 스킬 절차를 의심하라"는 실측 힌트.
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

        # ① 텔레메트리 — 노출(injected)과 관여(use)를 분리 집계한다.
        viewed = self._viewed_skills.pop(outcome.session_id, set())
        turn_tools = set(_turn_tool_names(outcome.history)) if not outcome.aborted else set()
        user_text = next(
            (m.content for m in reversed(outcome.history) if m.role == "user"), ""
        ) or ""
        user_tokens = lexical.tokens(user_text)
        friction_turn = not outcome.aborted and (
            has_correction_signal(f"{outcome.final_message}\n{user_text}")
            or has_failure_signal(f"{outcome.final_message}\n{user_text}")
        )

        for skill_id in dict.fromkeys(outcome.injected_skill_ids):
            try:
                self._usage_store.increment_injected(skill_id)
            except Exception:  # noqa: BLE001 — 카운터 오류가 턴을 깨지 않게
                pass

        if not outcome.aborted:
            for skill_id in dict.fromkeys((*outcome.injected_skill_ids, *viewed)):
                try:
                    if not self._engagement_evidence(
                        skill_id, viewed=viewed, user_tokens=user_tokens, turn_tools=turn_tools
                    ):
                        continue
                    self._usage_store.increment_use(skill_id)
                    if turn_tools:
                        # 도구 귀속은 여전히 턴 단위 실행 로그다 — 다만 이제
                        # 관여 근거가 있는 스킬에만 남는다(설명 가치가 있는 것만).
                        self._usage_store.record_tools(skill_id, sorted(turn_tools))
                    if friction_turn:
                        self._usage_store.increment_friction(skill_id)
                except Exception:
                    # NOTE(agent): usage increment 실패는 조용히 넘긴다. 카운터 오류가
                    # 턴을 깨지 않도록 (fail-safe 우선).
                    pass

        # ② 미드턴 스테이징 드레인 (T5)
        staged = self._staged.pop(outcome.session_id, [])
        if outcome.aborted:
            # 중단된 턴의 제안은 폐기 (fail-closed). 리뷰 잡도 만들지 않는다 —
            # 사용자가 버린 턴을 학습 리뷰가 다시 들추지 않는다.
            return effects
        effects.extend(staged)

        # ③ 서버 소유 리뷰 잡 영속화: tool_calls_made는 이제 프론트가
        #    세어 보내는 값이 아니라 서버가 턴 종료 시점에 직접 기록하는 값이다.
        #    /learning-review(또는 스케줄러 sweep)가 이 잡을 claim해서 실행한다.
        self._ledger.enqueue_review_job(outcome.session_id, outcome.tool_calls_made)

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
        expected_version: int | None = None,
        new_description: str | None = None,
        new_tags: tuple[str, ...] | None = None,
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

        Optimistic concurrency : `expected_version`은 제안이
        만들어진 당시의 스킬 버전(PatchProposal.from_version). None이면 검사 생략
        (직접 호출 하위호환), 값이 있으면 현재 버전과 불일치 시 ConflictError(409) —
        사용자가 v1 제안 카드를 열어둔 사이 스킬이 v2가 됐을 때 오래된 제안이
        최신 내용을 조용히 덮어쓰는 사고를 막는다.
        """
        if not approved:
            return None
        # 리뷰/발굴 LLM이 빌트인과 같은 이름을 제안했을 수 있다 — 승인 시점에 최종
        # 차단한다(빌트인은 patch 불가·이름 예약, stage_proposal은 이미 걸렀지만
        # 제안함/리뷰 경로는 여기로 직행한다).
        self._ensure_not_builtin(skill_id, "패치/생성")

        try:
            current = self._skill_store.read(skill_id)
        except NotFoundError:
            # 신규 생성 경로 — history에 보관할 이전 버전이 없다.
            if expected_version not in (None, 0):
                # 제안은 기존 스킬 vN의 patch였는데 그 스킬이 사라졌다 — 적용 불가.
                raise ConflictError(
                    f"스킬 '{skill_id}'이(가) 삭제되어 v{expected_version} 기준 제안을 적용할 수 없습니다."
                )
            new_body = edited_body if edited_body is not None else proposed_body
            if new_body is None:
                return None  # 본문이 전혀 없으면 만들 수 없다 (fail-closed)
            skill_guard.validate_content_size(new_body, self._config.skill_max_body_chars)
            new_body, _ = skill_guard.scrub_secrets(new_body)  # 방어적 재적용(대부분 이미 클린)
            # 승격 검증 harness의 **최종 관문** — 어떤 경로(카드 승인, 제안함,
            # 사용자 편집 본문)로 왔든 fail 본문은 디스크에 닿지 못한다.
            self._enforce_verification(
                name=skill_id, body=new_body, description=description, tags=tags, current_body=None
            )
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
            self.request_analysis(skill_id)  # 신규 생성 승인 → 설명 사이드카 생성
            return 1
        # NotFoundError 외 read 실패(손상된 SKILL.md 파싱 오류, 권한/IO 오류)는
        # 삼키지 않고 전파한다 — 이전엔 여기서 return None 으로 삼켜져 "사용자가
        # 승인했는데 조용히 적용 안 됨"으로 끝났다(적대 리뷰 P0#5). 전파된 예외는
        # effect_surfacing 이 skill_patch_failed SSE 로, 라우트는 API 오류로 보여준다.

        if expected_version is not None and expected_version != current.version:
            # 스킬은 존재하지만 제안이 만들어진 이후 버전이 움직였다(다른 제안 승인,
            # 직접 편집, rollback 등). 그대로 쓰면 그 변경을 덮어쓴다 — rebase 필요.
            raise ConflictError(
                f"스킬 '{skill_id}'이(가) v{expected_version} → v{current.version}(으)로 변경되었습니다. "
                "이 제안은 이전 버전 기준이라 적용할 수 없습니다 — 새로 제안을 받아주세요."
            )

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
        # 최종 관문(패치 경로) — 사용자 편집(edited_body)이 본문을 깨뜨린 경우까지 잡는다.
        self._enforce_verification(
            name=skill_id, body=new_body, description=current.description,
            tags=tuple(current.tags), current_body=current.body,
        )
        new_version = current.version + 1
        current.version = new_version
        current.body = new_body
        # 발견성 메타데이터 변경(제안의 new_description/new_tags) — 본문 patch 와
        # 같은 승인 카드에서 함께 승인된 것만 도달한다(warnings 로 명시됨).
        if new_description is not None and new_description.strip():
            current.description = " ".join(new_description.split())
        if new_tags is not None:
            current.tags = [t for t in dict.fromkeys(t.strip() for t in new_tags) if t]
        current.updated_at = now_iso()
        self._skill_store.write(current)

        # ③ usage patch += 1
        self._usage_store.increment_patch(skill_id)
        # 본문이 바뀌었다 — 설명 사이드카 재분석 enqueue (staleness 는 어차피
        # hash 로 판정되지만, 백그라운드가 먼저 갱신해 두면 UI 가 stale 을 볼 틈이 짧다).
        self.request_analysis(skill_id)

        return new_version

    # ── 승격 검증 harness (skill/verifier.py — 리뷰 P1) ───────────────────────
    def _verify_candidate(
        self,
        *,
        name: str,
        body: str,
        description: str = "",
        tags: tuple[str, ...] = (),
        current_body: str | None = None,
    ) -> VerificationReport:
        """verify_candidate에 서비스가 아는 문맥(active 스킬 이름들, 도구 카탈로그)을
        모아 넘기는 어댑터. 카탈로그 조회 실패는 해당 검사 스킵으로 강등한다 —
        검증 인프라 문제가 제안/승격 흐름을 깨면 안 된다."""
        known: set[str] | None = None
        if self._resolve_tool_names is not None:
            try:
                known = set(self._resolve_tool_names())
            except Exception:  # noqa: BLE001
                log.warning("tool catalog resolve failed — skipping tool-existence check", exc_info=True)
        existing = [s.name for s in self._active_skills() if s.name != name]
        return verify_candidate(
            name=name,
            body=body,
            description=description,
            tags=tags,
            current_body=current_body,
            existing_names=existing,
            known_tool_names=known,
        )

    def _enforce_verification(
        self,
        *,
        name: str,
        body: str,
        description: str = "",
        tags: tuple[str, ...] = (),
        current_body: str | None = None,
    ) -> None:
        """쓰기 직전의 fail-closed 강제 — fail 판정이면 ValidationError.

        스크리닝(_screen_proposals/stage_proposal)이 놓칠 수 있는 경로까지 덮는
        백스톱이다: 제안함의 오래된 payload, 카드에서 사용자가 직접 고친
        edited_body, 수동 생성/편집 라우트. warn은 여기서 무시한다 — 카드에서
        이미 보였거나(제안 경로) 사용자가 직접 쓴 본문(수동 경로)이므로 차단할
        권리는 객관적 무결성 위반에만 있다."""
        report = self._verify_candidate(
            name=name, body=body, description=description, tags=tags, current_body=current_body
        )
        if report.failed:
            self._ledger.record(
                "verifier_rejected",
                skill_id=name,
                origin="apply",
                detail=report.to_detail(),
            )
            raise ValidationError(f"스킬 검증 실패: {report.failure_text()}")

    def _screen_proposals(
        self,
        effects: list[LearningEffect],
        *,
        origin: str,
        session_id: str | None = None,
    ) -> list[LearningEffect]:
        """LLM이 낳은 제안(review/learn/sweep/discovery 전부)의 단일 검증 관문.

        fail → 제안 드랍 + ledger `verifier_rejected`(findings 포함) — 이것이
        리뷰가 요구한 quarantine이다: 실패 제안은 증발하지 않고 감사 가능하게
        남되, 카드/제안함/디스크 어디에도 닿지 않는다.
        warn → PatchProposal.warnings에 병합(승인 카드에 그대로 표시).
        MemoryWritten 등 비-제안 효과는 그대로 통과."""
        screened: list[LearningEffect] = []
        for effect in effects:
            if not isinstance(effect, PatchProposal):
                screened.append(effect)
                continue
            current_body: str | None = None
            if effect.from_version > 0:
                try:
                    current_body = self._skill_store.read(effect.skill_id).body
                except Exception:  # noqa: BLE001 — 스킬이 사라졌으면 생성으로 검증
                    current_body = None
            report = self._verify_candidate(
                name=effect.skill_id,
                body=effect.proposed_body,
                description=effect.description,
                tags=effect.tags,
                current_body=current_body,
            )
            if report.failed:
                log.warning(
                    "proposal for %r rejected by verifier (%s): %s",
                    effect.skill_id, origin, report.failure_text(),
                )
                self._ledger.record(
                    "verifier_rejected",
                    skill_id=effect.skill_id,
                    session_id=session_id,
                    origin=origin,
                    trigger=effect.trigger,
                    detail={"changelog": effect.changelog, **report.to_detail()},
                )
                continue
            warning_lines = report.warning_lines()
            if warning_lines:
                effect = replace(effect, warnings=effect.warnings + warning_lines)
            screened.append(effect)
        return screened

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

        if self._builtin_skills.is_builtin(name.strip()):
            return {
                "status": "dropped",
                "message": f"'{name}'은(는) 빌트인 스킬이라 수정/재정의할 수 없습니다. 다른 이름으로 새 스킬을 제안하세요.",
            }

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
                # propose_skill 의 description/tags 인자는 기존 스킬에선 "변경
                # 제안"으로 해석한다(현재 값과 같으면 빌더가 None 으로 접는다) —
                # 발견성(라우팅) 결함을 대화 중에도 고칠 수 있게.
                new_description=description or None,
                new_tags=tags or None,
            )
        if proposal is None:
            return {
                "status": "dropped",
                "message": "제안이 성립하지 않습니다 (빈 본문, 크기 초과, 이름 규약 위반, 또는 기존 본문과 동일).",
            }

        # ── 승격 검증 harness: 성립한 제안만 검증한다(정책 검사 — 이름 규약·
        # plugin 소유·동일 본문 — 가 먼저다). fail이면 스테이징을 거부하고 사유를
        # 도구 결과로 되돌린다 — 모델이 같은 턴에 본문을 고쳐 재제안하는 피드백
        # 루프. (HITL 카드는 '그럴듯하지만 잘린/조작된 본문'을 못 잡는다 — P1.)
        report = self._verify_candidate(
            name=proposal.skill_id,
            body=proposal.proposed_body,
            description=proposal.description or (current.description if current else ""),
            tags=proposal.tags or (tuple(current.tags) if current else ()),
            current_body=current.body if current is not None else None,
        )
        if report.failed:
            self._ledger.record(
                "verifier_rejected",
                skill_id=proposal.skill_id,
                session_id=session_id,
                origin="agent",
                trigger="T5",
                detail={"changelog": proposal.changelog, **report.to_detail()},
            )
            return {
                "status": "rejected",
                "message": (
                    f"검증 실패로 제안이 거부되었습니다: {report.failure_text()} "
                    "— 본문을 고쳐 다시 propose_skill을 호출하세요."
                ),
            }
        # 검증 warn은 차단이 아니라 표시 — 승인 카드의 warnings로 병합해 사용자가
        # 판단할 근거를 준다(예: 참조 도구가 카탈로그에 없음, 절차 단계 없음).
        if report.warning_lines():
            proposal = replace(proposal, warnings=proposal.warnings + report.warning_lines())
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

        **disconnected**(카드 응답전 SSE 스트림 단절 — 탭 종료/새로고침)도 timeout과 동일하게
        제안함으로 옮긴다. 단절은 의사표시가 아니고, 이전엔 이 경로에서 제안이 ledger에도
        inbox에도 남지 않고 증발했다.
        """
        base_detail = {"changelog": proposal.changelog, "trigger": proposal.trigger}
        if decision == "approve":
            try:
                version = self.apply_patch(
                    proposal.skill_id,
                    approved=True,
                    proposed_body=proposal.proposed_body,
                    edited_body=edited_body,
                    description=proposal.description,
                    tags=proposal.tags,
                    source=f"session:{session_id}",
                    expected_version=proposal.from_version,
                    new_description=proposal.new_description,
                    new_tags=proposal.new_tags,
                )
            except ConflictError:
                # 제안 생성 시점과 승인 시점 사이에 스킬 버전이 움직였다 — 기록 후
                # 전파한다(effect_surfacing이 SSE error로 서피싱, 라우트는 409).
                self._ledger.record(
                    "conflict",
                    skill_id=proposal.skill_id,
                    session_id=session_id,
                    trigger=proposal.trigger,
                    detail={**base_detail, "expected_version": proposal.from_version},
                )
                raise
            self._ledger.record(
                "approved",
                skill_id=proposal.skill_id,
                session_id=session_id,
                trigger=proposal.trigger,
                detail={**base_detail, "version": version, "edited": edited_body is not None},
            )
            return version
        if decision in ("timeout", "disconnected"):
            payload = proposal_to_payload(proposal)
            payload["session_id"] = session_id  # 승인 시 source 복원용
            self._ledger.add_suggestion(
                origin=decision,
                kind="create" if proposal.from_version == 0 else "patch",
                skill_id=proposal.skill_id,
                payload=payload,
            )
            self._ledger.record(
                decision, skill_id=proposal.skill_id, session_id=session_id,
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
        try:
            raw_new_tags = payload.get("new_tags")
            version = self.apply_patch(
                sug["skill_id"],
                approved=True,
                proposed_body=payload.get("proposed_body"),
                edited_body=edited_body,
                description=payload.get("description", ""),
                tags=tuple(payload.get("tags") or ()),
                source=source,
                expected_version=payload.get("from_version"),
                new_description=payload.get("new_description"),
                new_tags=tuple(str(t) for t in raw_new_tags) if raw_new_tags is not None else None,
            )
        except ConflictError:
            # 제안함에 머무는 동안 스킬이 다른 경로로 변경됐다. 계속 pending으로
            # 두면 영원히 충돌만 나므로 expired로 옮기고 409를 전파한다 — 사용자는
            # 최신 본문 기준으로 다시 제안을 받아야 한다(rebase).
            self._ledger.decide_suggestion(suggestion_id, "expired")
            self._ledger.record(
                "conflict",
                skill_id=sug["skill_id"],
                origin=sug["origin"],
                detail={
                    "changelog": payload.get("changelog", ""),
                    "suggestion_id": suggestion_id,
                    "expected_version": payload.get("from_version"),
                },
            )
            raise
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
        proposals = [
            p for p in self._screen_proposals(list(proposals), origin="discovery")
            if isinstance(p, PatchProposal)
        ]
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

    def record_context_renders(self, session_id: str, renders: list[dict]) -> None:
        """컨텍스트 렌더 감사 — 턴당 1개 ledger 이벤트로 영속화.
        ledger.record 자체가 best-effort(fail-safe)라 여기서 추가 방어는 불필요."""
        if not renders:
            return
        managed = [r for r in renders if r.get("managed")]
        self._ledger.record(
            "context_rendered",
            session_id=session_id,
            detail={
                "llm_calls": len(renders),
                "managed_calls": len(managed),
                "calls": renders,
            },
        )

    # ── post-turn, LLM-backed (Phase A.3; 서버 소유 잡으로 재구성) ─
    @staticmethod
    def _gate_inputs(history: list[Message]) -> tuple[str, str | None]:
        final_message = next(
            (m.content for m in reversed(history) if m.role == "assistant" and m.content), ""
        )
        user_text = next((m.content for m in reversed(history) if m.role == "user"), None)
        return final_message, user_text

    async def review(
        self, session_id: str, history: list[Message], tool_calls_made: int = 0
    ) -> list[LearningEffect]:
        """게이트 통과 시에만 LLM 1회 호출 — POST /sessions/{id}/learning-review 가
        메인 턴의 done 이후 논블로킹으로 호출한다(§1 아키텍처 결정).

        실행 근거가 **서버가 영속화한 리뷰 잡**으로 바뀌었다.
        observe()가 턴 종료 시 learning_jobs에 pending 잡(+서버가 직접 센
        tool_calls_made)을 남기고, 여기서 그 잡을 원자적으로 claim한다:
          - 잡이 없으면(이미 실행됨/중복 POST/관찰된 턴 없음) 즉시 [] — exactly-once.
          - `tool_calls_made` 파라미터는 하위호환용으로만 남았고 **무시된다** —
            프론트 카운트는 이벤트 누락/재시도에 취약해 신뢰하지 않는다.
        """
        job = self._ledger.claim_review_job(session_id)
        if job is None:
            return []

        if self._review_engine is None:
            # resolve_provider 미배선(대부분의 단위 테스트) — 잡은 소진 처리.
            self._ledger.finish_review_job(job["id"], "done", {"skipped": "no_provider"})
            return []

        final_message, user_text = self._gate_inputs(history)
        if not self._review_gate.should_review(
            session_id,
            tool_calls_made=job["tool_calls_made"],
            final_message=final_message,
            user_text=user_text,
        ):
            self._ledger.finish_review_job(job["id"], "done", {"skipped": "gate"})
            return []
        self._review_gate.mark_reviewed(session_id)
        try:
            effects = await self._review_engine.review(session_id, history)
        except Exception:
            self._ledger.finish_review_job(job["id"], "failed", {"error": "engine"})
            raise
        # 승격 검증 harness — LLM 제안은 카드에 닿기 전에 스크리닝된다.
        effects = self._screen_proposals(effects, origin="review", session_id=session_id)
        self._ledger.finish_review_job(job["id"], "done", {"effects": len(effects)})
        return effects

    async def sweep_review_jobs(
        self, history_for: Callable[[str], list[Message]], *, older_than_seconds: int, limit: int = 3
    ) -> int:
        """탭 종료 등으로 클라이언트가 끝내 트리거하지 않은 pending 리뷰 잡을
        백그라운드로 실행한다 (server/scheduler.py가 주기 호출). 파킹할 SSE 스트림이
        없으므로 PatchProposal은 HITL 카드 대신 **제안함**(skill_suggestions)으로
        라우팅한다 — discovery와 동일한 수신처. 반환: 처리한 잡 수."""
        if older_than_seconds <= 0 or self._review_engine is None:
            return 0
        processed = 0
        for job in self._ledger.stale_pending_review_jobs(
            older_than_seconds=older_than_seconds, limit=limit
        ):
            if not self._ledger.claim_job_by_id(job["id"]):
                continue  # 경합 — 다른 경로가 방금 claim했다
            session_id = job["session_id"]
            try:
                history = list(history_for(session_id))
            except Exception:
                self._ledger.finish_review_job(job["id"], "failed", {"error": "history_unavailable"})
                continue
            final_message, user_text = self._gate_inputs(history)
            if not self._review_gate.should_review(
                session_id,
                tool_calls_made=job["tool_calls_made"],
                final_message=final_message,
                user_text=user_text,
            ):
                self._ledger.finish_review_job(job["id"], "done", {"skipped": "gate", "sweep": True})
                continue
            self._review_gate.mark_reviewed(session_id)
            effects = await self._review_engine.review(session_id, history)
            effects = self._screen_proposals(effects, origin="review_sweep", session_id=session_id)
            suggestions_added = 0
            for effect in effects:
                if not isinstance(effect, PatchProposal):
                    continue  # MemoryWritten은 엔진이 이미 반영했다
                payload = proposal_to_payload(effect)
                payload["session_id"] = session_id
                sid = self._ledger.add_suggestion(
                    origin="review_sweep",
                    kind="create" if effect.from_version == 0 else "patch",
                    skill_id=effect.skill_id,
                    payload=payload,
                )
                if sid is not None:
                    suggestions_added += 1
            self._ledger.finish_review_job(
                job["id"],
                "done",
                {"effects": len(effects), "suggestions": suggestions_added, "sweep": True},
            )
            processed += 1
        return processed

    async def learn(
        self, session_id: str, history: list[Message], instruction: str = ""
    ) -> list[LearningEffect]:
        """명시적 사용자 요청 — 게이트 없이 항상 실행(POST /sessions/{id}/learn, Phase C)."""
        if self._review_engine is None:
            return []
        effects = await self._review_engine.learn(session_id, history, instruction)
        return self._screen_proposals(effects, origin="learn", session_id=session_id)

    # ── management ────────────────────────────────────────────────────────────
    def _ensure_not_builtin(self, skill_id: str, action: str) -> None:
        """빌트인 스킬 쓰기 계열의 단일 관문 — 이름이 예약이면 ValidationError(400).

        빌트인은 활성/비활성 전환(set_state)만 가능하고 편집·삭제·패치·롤백·동명
        생성은 전부 여기서 거부된다. 라우트에 흩뿌리지 않고 서비스 경계 한 곳에
        두는 이유: 새 호출 경로(제안함, 큐레이터, 향후 API)가 생겨도 이 규칙을
        우회할 수 없다."""
        if self._builtin_skills.is_builtin(skill_id):
            raise ValidationError(
                f"'{skill_id}'은(는) 빌트인 스킬입니다 — {action}할 수 없고 활성/비활성 전환만 가능합니다."
            )

    def list_skills(self) -> list[SkillSummary]:
        """빌트인 + ~/.rice/skills/*.md 전체를 SkillSummary DTO 목록으로 반환한다."""
        builtin_names = self._builtin_skills.names()
        skills = self._builtin_skills.list() + [
            # 빌트인 이름은 예약 — 혹시 남아 있는 동명 사용자 파일은 숨긴다(중복 id 방지).
            s for s in self._skill_store.list() if s.name not in builtin_names
        ]
        result = []
        for skill in skills:
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
                    enabled=skill.enabled,
                    injected=usage.injected,
                    friction=usage.friction,
                    routing_miss=usage.routing_miss,
                )
            )
        return result

    def get_skill(self, skill_id: str) -> SkillDetail | None:
        """스킬 상세 조회. view 카운터를 **올리지 않는다** (2026-07-11 정직화) —
        view 는 모델의 skill_view 온디맨드 로딩 신호이고, 사용자가 MySkills 패널을
        여는 것은 다른 사건이다(패널 열람이 view 를 152까지 부풀려 온디맨드
        텔레메트리를 무의미하게 만들었다)."""
        skill = self._builtin_skills.get(skill_id)
        if skill is None:
            try:
                skill = self._skill_store.read(skill_id)
            except Exception:
                return None
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
            enabled=skill.enabled,
            injected=usage.injected,
            friction=usage.friction,
            routing_miss=usage.routing_miss,
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
        self._ensure_not_builtin(name, "같은 이름으로 생성")
        try:
            self._skill_store.read(name)
            raise ValidationError(f"이미 존재하는 스킬입니다: {name!r}")
        except NotFoundError:
            pass

        # Phase D: 사용자 직접 작성 경로도 동일 가드를 거친다(이전엔 크기/시크릿
        # 검증이 전혀 없었다 — 지금 함께 닫는다). 승격 검증 harness도 동일 —
        # 수동 경로만 무결성 검사를 우회하면 관문이 관문이 아니게 된다.
        skill_guard.validate_content_size(body, self._config.skill_max_body_chars)
        body, _ = skill_guard.scrub_secrets(body)
        self._enforce_verification(name=name, body=body, description=description, tags=tags)

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
        self.request_analysis(name)  # 직접 생성 → 설명 사이드카 생성
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
        self._ensure_not_builtin(skill_id, "편집")
        current = self._skill_store.read(skill_id)  # NotFoundError 위임

        if state is not None and state not in ("active", "stale", "archived"):
            raise ValidationError(f"Unknown skill state {state!r}")

        body_changed = body is not None and body != current.body
        if body_changed:
            assert body is not None  # narrows for the type checker (body_changed implies this)
            skill_guard.validate_content_size(body, self._config.skill_max_body_chars)
            body, _ = skill_guard.scrub_secrets(body)
            self._enforce_verification(
                name=skill_id, body=body,
                description=description if description is not None else current.description,
                tags=tuple(tags) if tags is not None else tuple(current.tags),
                current_body=current.body,
            )
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
        if body_changed:
            self.request_analysis(skill_id)  # 직접/AI 편집 저장 → 재분석
        return self._detail_from(current, self._usage_store.read(skill_id))

    def delete_skill(self, skill_id: str) -> None:
        """스킬 삭제 (DELETE /skills/{id}). 본문/usage/history 사이드카 모두 제거."""
        self._ensure_not_builtin(skill_id, "삭제")
        self._skill_store.read(skill_id)  # 없으면 NotFoundError → 404
        self._skill_store.delete(skill_id)
        self._usage_store.delete(skill_id)
        self._analysis_store.delete(skill_id)

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
        if self._builtin_skills.is_builtin(skill_id):
            # 통합(consolidation)은 SkillStore만 스캔하므로 정상 경로로는 도달하지
            # 않는다 — 방어적으로 no-op 처리(absorbed_into는 빌트인에 무의미).
            return False
        try:
            self._lifecycle.archive_with_absorption(skill_id, absorbed_into)
        except NotFoundError:
            return False
        return True

    def set_state(self, skill_id: str, state: SkillState) -> None:
        """스킬 상태를 수동으로 변경한다. 빌트인은 active/archived(=활성/비활성)만."""
        if self._builtin_skills.is_builtin(skill_id):
            self._builtin_skills.set_state(skill_id, state)
            return
        self._lifecycle.set_state(skill_id, state)

    def history(self, skill_id: str) -> list[SkillVersion]:
        """스킬 히스토리 버전 목록을 반환한다 (LEARNING_LOOP §5.2.4).
        빌트인은 히스토리가 없다(본문 불변) — 빈 목록."""
        if self._builtin_skills.is_builtin(skill_id):
            return []
        entries = self._skill_store.read_history(skill_id)
        result = []
        for version, updated_at, body in entries:
            result.append(SkillVersion(version=version, updated_at=updated_at, body=body))
        return result

    def rollback(self, skill_id: str, version: int) -> int:
        """히스토리 버전을 현재로 복원한다. 새 version 번호 반환."""
        self._ensure_not_builtin(skill_id, "롤백")
        new_version = self._lifecycle.rollback(skill_id, version)
        self.request_analysis(skill_id)  # 본문이 과거본으로 교체됨 → 재분석
        return new_version

    # ── explanatory analysis sidecar (MySkills "스킬 이해" 실배선, 2026-07-06) ──
    def _read_any_skill(self, skill_id: str) -> SkillData | None:
        """빌트인 우선 스킬 조회 (get_skill 과 동일 규칙, view 카운터는 안 올림)."""
        skill = self._builtin_skills.get(skill_id)
        if skill is not None:
            return skill
        try:
            return self._skill_store.read(skill_id)
        except Exception:
            return None

    def _analysis_payload(self, skill: SkillData, analysis: SkillAnalysisData | None) -> dict:
        """API 응답용 분석 payload — 사이드카의 mentioned 에 세 출처의 도구 단서를
        합류시킨다:
          observed  = usage 사이드카의 실행 로그 ledger (턴 단위 귀속 실측)
          declared  = SKILL.md frontmatter `tools:` 선언 (manifest/작성자 메타데이터)
          mentioned = 분석 사이드카가 본문에서 감지한 후보 (LLM/휴리스틱)
        status 는 읽기 시점 계산값(missing|stale|failed|fresh)이다."""
        status = effective_status(analysis, skill.body)
        usage = self._usage_store.read(skill.name)
        observed = [
            {
                "name": name,
                "kind": "observed",
                "confidence": "high",
                "basis": "실행 로그 — 이 스킬이 주입된 턴의 도구 호출",
                "evidence": f"{count}회 호출됨",
            }
            for name, count in sorted(usage.tools.items(), key=lambda kv: kv[1], reverse=True)
        ]
        declared = [
            {
                "name": name,
                "kind": "declared",
                "confidence": "high",
                "basis": "SKILL.md frontmatter(tools) 선언",
            }
            for name in skill.tools
        ]
        mentioned = list(analysis.tool_mentions) if analysis else []
        return {
            "status": status,
            "body_hash": analysis.body_hash if analysis else None,
            "analyzer_version": analysis.analyzer_version if analysis else None,
            "analyzed_at": analysis.analyzed_at if analysis else None,
            "source": analysis.source if analysis else None,
            "summary": analysis.summary if analysis else "",
            "confidence": analysis.confidence if analysis else None,
            "procedural_steps": list(analysis.procedural_steps) if analysis else [],
            "tool_mentions": observed + declared + mentioned,
            "warnings": list(analysis.warnings) if analysis else [],
        }

    def get_skill_analysis(self, skill_id: str) -> dict | None:
        skill = self._read_any_skill(skill_id)
        if skill is None:
            return None
        return self._analysis_payload(skill, self._analysis_store.read(skill_id))

    async def refresh_skill_analysis(self, skill_id: str) -> dict:
        """사이드카만 재생성 — SKILL.md 본문·버전·히스토리는 절대 건드리지 않는다."""
        skill = self._read_any_skill(skill_id)
        if skill is None:
            raise NotFoundError(f"skill {skill_id!r} not found")
        analysis = await self._analyzer.analyze(skill)
        self._analysis_store.write(analysis)
        self._ledger.record(
            "analysis_refreshed",
            skill_id=skill_id,
            detail={"source": analysis.source, "status": analysis.status},
        )
        return self._analysis_payload(skill, analysis)

    def request_analysis(self, skill_id: str) -> None:
        """재분석 enqueue (best-effort) — 본문을 바꾼 모든 경로(apply_patch/
        create/update/rollback)가 호출한다. 스케줄러 틱이 드레인."""
        if skill_id not in self._analysis_queue:
            self._analysis_queue.append(skill_id)

    async def run_pending_analyses(self, limit: int = 2) -> int:
        """대기열 드레인 (server/scheduler.py 주기 호출). LLM 호출이 섞이므로
        틱당 limit 으로 비용을 유계로 묶는다. 이미 fresh 면(그 사이 수동 refresh
        등) 건너뛴다."""
        processed = 0
        while self._analysis_queue and processed < limit:
            skill_id = self._analysis_queue.pop(0)
            skill = self._read_any_skill(skill_id)
            if skill is None:
                continue  # 그 사이 삭제됨
            existing = self._analysis_store.read(skill_id)
            if effective_status(existing, skill.body) == "fresh":
                continue
            try:
                await self.refresh_skill_analysis(skill_id)
                processed += 1
            except Exception:  # noqa: BLE001 — 분석 실패가 스케줄러를 깨면 안 된다
                log.warning("background skill analysis failed for %s", skill_id, exc_info=True)
        return processed

    # ── memory ────────────────────────────────────────────────────────────────
    def list_memories(self) -> list[MemoryItem]:
        """Delegate to MemoryStore for consistent access (T1)."""
        return self._mem_store.list()

    def delete_memory(self, memory_id: str) -> None:
        """메모리 삭제 (DELETE /memories/{id}, Self-Improvement v3). 메모리는 HITL
        없이 즉시 반영되는 유일한 경로라 사후 제거 수단이 위생의 핵심이다.
        격리(quarantined) 메모리를 여기로 삭제하면 그것이 곧 '거부'다."""
        self._mem_store.delete(memory_id)  # NotFoundError 위임 → 404
        self._ledger.record("memory_deleted", detail={"memory_id": memory_id})

    def approve_memory(self, memory_id: str) -> None:
        """격리(quarantined) 메모리를 active로 승인한다 (POST /memories/{id}/approve)
        이제부터 prefetch가 주입한다. 없으면 NotFoundError → 404."""
        self._mem_store.set_status(memory_id, "active")
        self._ledger.record("memory_approved", detail={"memory_id": memory_id})


# NOTE(agent): 정규식 기반 fact/preference 추출(`_try_extract_fact`/
# `_try_extract_preference`)은 Self-Improvement v3(2026-07-03)에서 완전히
# 제거됐다. 판단 주체는 이제 리뷰 LLM(`propose_memory_write`) 하나다 — 두 작성자
# 체제는 중복/노이즈 메모리를 만들었고, 메모리는 HITL 없이 매 턴 주입되는 가장
# 위생에 민감한 경로다. signals.py의 교정/실패/스킬화 신호는 여전히
# review/gate.py의 게이트 판단에 쓰인다.
