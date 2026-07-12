"""test_skill_injection_v2.py — 하이브리드 스킬 주입 v2 (2026-07-11 고도화) 고정.

이 파일이 고정하는 계약:
  1. 정밀도 게이트: 태그 한 개 스치는 오탐(strong<2)은 임계 점수를 넘어도 전문
     주입되지 않는다 — 인덱스에 남는다. 명시적 언급/이중 강신호는 주입된다.
  2. 개수 상한: skill_max_auto_inject(기본 1) — "N개 주입"의 교정. 상위 1개만.
  3. 점착(sticky): 한 번 주입된 스킬은 skill_sticky_turns 동안 재매칭 없이 유지되고,
     그 후 만료된다. 유지 턴은 카운터를 리셋하지 않는다(무한 자기연장 방지).
  4. 인덱스: 관련도순 정렬 + 상한(_INDEX_MAX_ROWS) + 오버플로 안내.
  5. skill_view: 같은 턴 반복 조회에 note, 라우터 미스 수동 로딩은 routing_miss.
  6. 발견성 자기개선: propose_skill_patch 의 new_description/new_tags 가
     make_patch_proposal → apply_patch 로 관통한다(메타데이터-only 패치 성립).
  7. 리뷰/발굴의 빌트인 맹점 제거: 빌트인 이름 create 제안은 조기 드랍.
"""

from __future__ import annotations

import tempfile

import pytest

from rice_runtime.core.config import LearningConfig
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.core.types import ToolCall, TurnOutcome
from rice_runtime.learning.proposals import make_patch_proposal
from rice_runtime.learning.review.engine import ReviewEngine
from rice_runtime.learning.service import LearningService, _INDEX_MAX_ROWS
from rice_runtime.learning.skill.relevance import (
    AUTO_INJECT_THRESHOLD,
    auto_inject_eligible,
    score_skills,
)
from rice_runtime.learning.skill.store import SkillData, SkillStore
from rice_runtime.learning.skill.usage import UsageStore


def _fresh_paths() -> RicePaths:
    return RicePaths.from_env(tempfile.mkdtemp(prefix="rice-inject-v2-")).ensure()


def _db() -> Database:
    return Database(":memory:").connect()


def _skill(name: str, *, tags=(), description="", body="## Steps\n1. do the thing\n") -> SkillData:
    return SkillData(
        name=name, version=1, description=description, state="active",
        body=body, tags=list(tags), source=None, created_at=None, updated_at=None,
    )


def _service(paths: RicePaths, **cfg) -> LearningService:
    return LearningService(_db(), LearningConfig(**cfg), paths)


# ── 1. 정밀도 게이트 ──────────────────────────────────────────────────────────

class TestPrecisionGate:
    def test_single_tag_hit_is_not_eligible(self):
        """태그 한 개 겹침(strong=1.0)은 점수 임계(3.0)를 넘어도 오탐일 수 있다 —
        인덱스행이지 본문 주입감이 아니다."""
        sk = _skill("business-planning", tags=["business", "pptx", "xlsx"],
                    description="investor deliverables")
        scored = score_skills("엑셀 정리 좀 해줘", [sk])[0]  # 엑셀→xlsx 태그 1개 히트
        assert scored.score >= AUTO_INJECT_THRESHOLD
        assert not auto_inject_eligible(scored)

    def test_two_name_token_hits_are_eligible(self):
        sk = _skill("presentation-create", description="PowerPoint 발표 자료 생성")
        scored = score_skills("피피티 만들어줘", [sk])[0]  # presentation+create 확장 히트
        assert auto_inject_eligible(scored)

    def test_explicit_mention_is_always_eligible(self):
        sk = _skill("obscure-thing", description="niche")
        scored = score_skills("use the obscure-thing skill now", [sk])[0]
        assert scored.explicit and auto_inject_eligible(scored)


# ── 2+3. 개수 상한 + 점착 ─────────────────────────────────────────────────────

class TestCapAndSticky:
    def setup_method(self):
        self._paths = _fresh_paths()

    def _write(self, svc_paths, *skills):
        store = SkillStore(svc_paths)
        for s in skills:
            store.write(s)

    def test_only_top_scorer_injected_under_default_cap(self):
        svc = _service(self._paths, skill_max_auto_inject=1, skill_sticky_turns=0)
        self._write(
            self._paths,
            _skill("deck-builder", tags=["presentation", "slides", "deck"],
                   description="slide deck builder"),
            _skill("deck-review", tags=["presentation", "deck", "review"],
                   description="deck review checklist"),
        )
        pf = svc.prefetch("s1", "presentation deck 만들어줘")
        injected = [s.skill_id for s in pf.skills]
        assert len(injected) == 1  # N개 주입 금지 — 상위 1개만
        # 탈락한 쪽은 인덱스에 남아 있어야 한다(온디맨드 경로 유지).
        other = ({"deck-builder", "deck-review"} - set(injected)).pop()
        assert other in pf.system_prompt_additions

    def test_sticky_keeps_skill_across_unmatched_turns_then_expires(self):
        svc = _service(self._paths, skill_max_auto_inject=1, skill_sticky_turns=2)
        self._write(self._paths, _skill("deck-builder",
                                        tags=["presentation", "slides", "deck"],
                                        description="slide deck builder"))
        pf1 = svc.prefetch("s1", "presentation deck 만들어줘")
        assert [s.skill_id for s in pf1.skills] == ["deck-builder"]
        # 후속 지시는 라우터에 안 걸리지만 점착으로 유지된다.
        pf2 = svc.prefetch("s1", "3번째 것 파란색으로 바꿔줘")
        assert [s.skill_id for s in pf2.skills] == ["deck-builder"]
        pf3 = svc.prefetch("s1", "좋아 계속")
        assert [s.skill_id for s in pf3.skills] == []  # 2턴 뒤 만료(리셋 없음)

    def test_sticky_is_per_session(self):
        svc = _service(self._paths, skill_max_auto_inject=1, skill_sticky_turns=2)
        self._write(self._paths, _skill("deck-builder",
                                        tags=["presentation", "slides", "deck"],
                                        description="slide deck builder"))
        svc.prefetch("s1", "presentation deck 만들어줘")
        pf_other = svc.prefetch("s2", "안녕")
        assert pf_other.skills == ()


# ── 2.5 창 인식 주입 예산 (skill_token_budget) ────────────────────────────────

class TestWindowAwareBudget:
    """orchestrator가 모델 창에서 역산해 넘기는 skill_token_budget이 config 기본
    예산(24K)을 좁게 클램프한다 — 로컬 창에서 스킬 본문이 대화 히스토리를
    밀어내지 않게. 예산 밖 스킬은 인덱스로 강등될 뿐 사라지지 않는다."""

    def setup_method(self):
        self._paths = _fresh_paths()
        SkillStore(self._paths).write(
            _skill(
                "deck-builder",
                tags=["presentation", "slides", "deck"],
                description="slide deck builder",
                body="## Steps\n" + "1. a fairly long procedure line\n" * 40,
            )
        )

    def test_tight_budget_demotes_to_index(self):
        svc = _service(self._paths, skill_max_auto_inject=1, skill_sticky_turns=0)
        pf = svc.prefetch("s1", "presentation deck 만들어줘", skill_token_budget=10)
        assert pf.skills == ()  # 본문은 예산 밖 — 주입 금지
        assert "deck-builder" in pf.system_prompt_additions  # 인덱스에는 남는다

    def test_generous_budget_injects_and_none_keeps_config_budget(self):
        svc = _service(self._paths, skill_max_auto_inject=1, skill_sticky_turns=0)
        pf = svc.prefetch("s1", "presentation deck 만들어줘", skill_token_budget=8_000)
        assert [s.skill_id for s in pf.skills] == ["deck-builder"]
        # None(하위호환) → config 예산 그대로.
        pf2 = svc.prefetch("s1", "presentation deck 만들어줘")
        assert [s.skill_id for s in pf2.skills] == ["deck-builder"]

    def test_injected_block_carries_precedence_note(self):
        """전문 주입 블록에는 '사용자의 이번 요청 > 스킬' 서열(PRECEDENCE)이 함께
        실린다 — 스킬은 default이지 명령이 아니다. 주입이 없으면 실리지 않는다."""
        from rice_runtime.prompts.system import SKILL_PRECEDENCE_NOTE

        svc = _service(self._paths, skill_max_auto_inject=1, skill_sticky_turns=0)
        pf = svc.prefetch("s1", "presentation deck 만들어줘")
        assert SKILL_PRECEDENCE_NOTE in pf.system_prompt_additions
        pf_plain = svc.prefetch("s2", "안녕")
        assert SKILL_PRECEDENCE_NOTE not in pf_plain.system_prompt_additions


# ── 4. 인덱스 상한/오버플로 ───────────────────────────────────────────────────

def test_index_is_capped_with_overflow_hint():
    paths = _fresh_paths()
    svc = _service(paths)
    store = SkillStore(paths)
    for i in range(_INDEX_MAX_ROWS + 5):
        store.write(_skill(f"filler-skill-{i:02d}", description=f"filler {i}"))
    pf = svc.prefetch("s1", "hello")
    assert pf.skills == ()
    assert "more — find them with `skill_search`" in pf.system_prompt_additions


# ── 5. skill_view 반복 note + routing_miss ────────────────────────────────────

class TestSkillView:
    def setup_method(self):
        self._paths = _fresh_paths()
        self._svc = _service(self._paths)
        SkillStore(self._paths).write(
            _skill("git-workflow", tags=["git"], description="git 절차")
        )

    def test_repeat_view_same_turn_gets_note(self):
        first = self._svc.view_skill_body("s1", "git-workflow")
        assert "note" not in first
        second = self._svc.view_skill_body("s1", "git-workflow")
        assert "already loaded" in second.get("note", "")
        assert second["body"]  # 본문은 여전히 반환(sub-agent 컨텍스트 안전)

    @pytest.mark.asyncio
    async def test_routing_miss_counted_when_router_would_not_match(self):
        # prefetch 쿼리는 git 과 무관 → 라우터 미스 상황에서 수동 로딩.
        self._svc.prefetch("s1", "주간 보고서 정리해줘")
        self._svc.view_skill_body("s1", "git-workflow")
        assert UsageStore(self._paths).read("git-workflow").routing_miss == 1

    @pytest.mark.asyncio
    async def test_no_routing_miss_when_router_matched(self):
        self._svc.prefetch("s1", "git-workflow 스킬로 진행해줘")  # 명시적 언급
        self._svc.view_skill_body("s1", "git-workflow")
        assert UsageStore(self._paths).read("git-workflow").routing_miss == 0


# ── 6. 발견성 메타데이터 패치 ─────────────────────────────────────────────────

class TestMetadataPatch:
    def setup_method(self):
        self._paths = _fresh_paths()
        self._svc = _service(self._paths)
        SkillStore(self._paths).write(
            _skill("report-weekly", tags=["report"], description="주간 리포트 절차")
        )

    def test_metadata_only_patch_is_a_valid_proposal(self):
        current = SkillStore(self._paths).read("report-weekly")
        p = make_patch_proposal(
            current=current,
            new_body=current.body,  # 본문 동일 — 이전엔 무조건 None 이던 케이스
            changelog="발견성 개선",
            trigger="T1c",
            new_description="주간 업무 보고서(위클리 리포트) 자동 작성 절차",
            new_tags=("report", "weekly", "보고서"),
        )
        assert p is not None
        assert p.new_description and p.new_tags
        assert any("설명 변경" in w for w in p.warnings)

    def test_apply_patch_applies_metadata(self):
        new_version = self._svc.apply_patch(
            "report-weekly",
            approved=True,
            proposed_body=SkillStore(self._paths).read("report-weekly").body + "\n3. verify\n",
            new_description="주간 업무 보고서 자동 작성 절차",
            new_tags=("report", "weekly", "보고서"),
        )
        assert new_version == 2
        updated = SkillStore(self._paths).read("report-weekly")
        assert updated.description == "주간 업무 보고서 자동 작성 절차"
        assert updated.tags == ["report", "weekly", "보고서"]
        # 발견성 개선이 실제 라우팅에 반영된다 — 자기개선 루프의 목적.
        scored = score_skills("주간 보고서 작성해줘", [updated])[0]
        assert scored.score > 0


# ── 7. 리뷰 엔진의 빌트인 맹점 제거 ───────────────────────────────────────────

class _FakeBuiltinCatalog:
    def __init__(self, names):
        self._names = list(names)

    def names(self):
        return list(self._names)

    def list(self):
        return [
            _skill(n, description=f"builtin {n}") for n in self._names
        ]


def test_review_engine_drops_create_proposal_with_builtin_name():
    paths = _fresh_paths()
    engine = ReviewEngine(
        resolve_provider=lambda: None,  # _skill_create_effect 는 provider 를 안 쓴다
        skill_store=SkillStore(paths),
        usage_store=UsageStore(paths),
        mem_store=None,  # type: ignore[arg-type]
        skill_catalog_token_budget=1000,
        builtin_catalog=_FakeBuiltinCatalog(["business-planning"]),
    )
    effect = engine._skill_create_effect(
        {"name": "business-planning", "description": "dup", "body": "## Steps\n1. x\n"}
    )
    assert effect is None  # 승인 시점 에러 카드가 아니라 조기 드랍
