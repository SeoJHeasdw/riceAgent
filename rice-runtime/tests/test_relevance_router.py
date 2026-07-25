"""test_relevance_router.py — 결정론적 관련도 라우팅 (2026-07-07 적대적 리뷰 반영).

시멘틱(임베딩) 검색은 최종적으로 채택하지 않는다 — 8GB 노트북이 바닥 사양인
로컬 퍼스트 제품에 아키텍처 세금이 과하다. 대신 이 파일이 고정하는 결정론 사다리:

  1. explicit mention — 사용자가 스킬/도구 이름을 그대로 말하면 정확 라우팅
  2. name/tag/description/body lexical 매칭 (exact → substring → bigram fuzzy)
  3. 소형 global ko→en intent table ("만들어줘"→create, "메일"→mail)
  4. negative trigger (`not:` 태그) 감점

그리고 그 사다리를 타는 두 소비자:
  - plugin SKILL.md 번들 → learning.prefetch(extra_skills=...) 와 skill_view
    (P0: 전량 주입 우회 제거 — index → top-K 전문 → 온디맨드)
  - tool_search 랭킹 + fold_tools_for_budget 의 관련도 스포트라이트
    (P0: "많으면 전부 접기"가 아니라 "관련한 것만 펴기")
"""

from __future__ import annotations

import json
import tempfile

import pytest
from unittest.mock import AsyncMock, MagicMock

from rice_runtime.agent.tool_dispatch import (
    BRIDGE_TOOL_SPECS,
    ToolDispatcher,
    _score_tool,
    _tool_term_weights,
    fold_tools_for_budget,
)
from rice_runtime.core import lexical
from rice_runtime.core.config import LearningConfig
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.core.types import InjectedSkill, ToolCall, ToolSpec
from rice_runtime.execution.plugin.loader import PluginLoader
from rice_runtime.learning.service import LearningService
from rice_runtime.learning.skill.relevance import (
    AUTO_INJECT_THRESHOLD,
    auto_inject_eligible,
    score_skills,
    term_weights,
)
from rice_runtime.learning.skill.store import SkillData, SkillStore


# ── helpers ───────────────────────────────────────────────────────────────────

def _fresh_paths() -> RicePaths:
    return RicePaths.from_env(tempfile.mkdtemp(prefix="rice-router-test-")).ensure()


def _skill(name: str, *, tags=(), description="", body="steps"):
    return SkillData(
        name=name, version=1, description=description, state="active",
        body=body, tags=list(tags), source=None, created_at=None, updated_at=None,
    )


def _tool(name: str, description: str = "", *, big=False) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description or (("x" * 500) if big else f"{name} tool"),
        parameters={"type": "object", "properties": {"a": {"type": "string"}}},
        source="connector",
        owner_id="c1",
    )


# ── lexical ladder ────────────────────────────────────────────────────────────

class TestLexicalLadder:
    def test_korean_intent_expansion_bridges_to_english_index(self):
        """"발표자료 만들어줘" → presentation/create 로 확장돼 영어 스킬을 찾는다
        (per-skill alias 관리 없이, 소형 global intent table 만으로)."""
        ranked = score_skills(
            "발표자료 만들어줘",
            [_skill("presentation-builder", tags=("ppt", "slides")), _skill("db-migration")],
        )
        assert ranked[0].name == "presentation-builder"
        assert ranked[0].score >= AUTO_INJECT_THRESHOLD

    def test_explicit_mention_routes_exactly(self):
        """사용자가 스킬 이름을 그대로 말하면 다른 텍스트와 무관하게 임계를 넘는다."""
        ranked = score_skills(
            "use business-planning for this",
            [_skill("business-planning"), _skill("business-analytics")],
        )
        assert ranked[0].name == "business-planning"
        assert ranked[0].score > AUTO_INJECT_THRESHOLD

    def test_negative_trigger_penalizes(self):
        """`not:excel` 태그는 excel 쿼리에서 감점 — 알려진 오탐 이웃을 저자가 깎는다."""
        plain = score_skills("excel report", [_skill("report-writer", tags=("report",))])[0]
        negated = score_skills(
            "excel report", [_skill("report-writer", tags=("report", "not:excel"))]
        )[0]
        assert negated.score < plain.score

    def test_substring_credit_requires_both_sides_to_be_distinctive(self):
        """부분문자열 credit 은 양쪽 모두 길어야 준다. 예전엔 쿼리어 길이만 봐서,
        MIN_TOKEN_LEN(2) 짜리 불용어가 긴 쿼리어의 부분문자열이라는 이유로 0.5 를
        받았다 — "invoice" 가 "items **in** gmail" 을 담은 문서에 전부 걸려 도구
        카탈로그에서 df 가 1/193 → 73/193 으로 뭉개졌다(판별력 신호가 죽는다)."""
        q = lexical.prepare_query("invoice")
        assert lexical.field_score(q, "list items in gmail") == 0.0   # 'in' 은 더 이상 못 건다
        assert lexical.field_score(q, "download an invoice pdf") == 1.0  # 진짜 매칭은 그대로
        # 긴 필드 토큰이 쿼리어를 포함/피포함하는 정상 경로는 유지된다
        assert lexical.field_score(lexical.prepare_query("workbooks"), "create workbook") > 0

    def test_bigram_fuzzy_bridges_typos(self):
        assert lexical.field_score(lexical.prepare_query("workbok"), "create workbook") > 0

    def test_expansion_never_fabricates_explicit_mention(self):
        """intent 확장어로는 explicit-mention 보너스가 발동하지 않아야 한다."""
        ranked = score_skills("뭔가 만들어줘", [_skill("create")])
        assert ranked[0].score < 2 * AUTO_INJECT_THRESHOLD  # 보너스(9.0) 미발동

    def test_expansion_cannot_manufacture_independent_evidence(self):
        """한 단어의 intent 확장이 정밀도 게이트(MIN_STRONG_HITS)를 스스로 통과시키면
        안 된다. "생성해줘"는 사용자가 친 단어 **하나**이므로 author-signal 도 하나다 —
        예전엔 {create, new, 생성해줘} 로 평평하게 펼쳐져 strong=2.0 으로 게이트를
        통과했고(영어 "create it"은 정상적으로 막혔다), 게이트가 한국어에서만 꺼져
        있었다. term 단위 최고점 규칙이 그 비대칭을 없앤다."""
        skill = _skill("note-taker", tags=("create", "new"), description="Take a note")
        ko = score_skills("생성해줘", [skill])[0]
        en = score_skills("create it", [skill])[0]
        assert ko.strong == en.strong == 1.0  # 양쪽 다 author-signal 1개
        assert not auto_inject_eligible(ko) and not auto_inject_eligible(en)
        assert ko.score > 0  # 리콜은 유지 — 인덱스에 남아 skill_view 로 열린다

    def test_mixed_korean_english_query_is_symmetric(self):
        """혼용 쿼리에서 게이트가 언어에 따라 다르게 열리면 안 된다.

        비교 기준을 맞추는 게 핵심이다: "엑셀"은 확장으로 name(xlsx)·tag(excel)을
        모두 덮으므로, 대응하는 영어는 같은 두 필드를 덮는 "xlsx"다("excel"이
        아니다 — tag 하나만 덮으므로 정책상 인덱스행이 맞다). strong 은 name/tag
        **필드 커버리지**를 세므로 둘은 같은 값이어야 한다."""
        xl = _skill("xlsx-builder", tags=("excel", "xlsx", "spreadsheet"),
                    description="Build an Excel workbook")
        ko = score_skills("엑셀", [xl])[0]
        en = score_skills("xlsx", [xl])[0]
        assert ko.strong == en.strong == 2.0
        assert auto_inject_eligible(ko) == auto_inject_eligible(en)
        # 한 필드만 덮는 단어는 양쪽 언어 모두 인덱스행
        assert not auto_inject_eligible(score_skills("excel", [xl])[0])

    def test_mixed_script_query_scores_each_word_once(self):
        """ko/en 을 섞어 쓴 쿼리에서도 한 단어는 한 번만 세어진다 — 동의어를 나란히
        쓴 한국어+영어 쿼리와 순수 영어 등가물의 strong 이 같아야 한다."""
        mail = _skill("mail-sender", tags=("mail", "email", "send"), description="Send email")
        assert score_skills("메일 email 보내줘", [mail])[0].strong == \
               score_skills("mail email send", [mail])[0].strong

    def test_field_index_cache_cannot_go_stale(self):
        """필드 인덱스 캐시는 필드 **내용**으로 키를 잡는다 — 스킬 본문이 바뀌면
        키도 바뀌므로 무효화 훅 없이 자동으로 새 인덱스를 탄다."""
        before = score_skills("deploy", [_skill("s", body="nothing relevant here")])[0]
        after = score_skills("deploy", [_skill("s", body="run the deploy script")])[0]
        assert after.score > before.score

    def test_multi_signal_korean_request_still_auto_injects(self):
        """게이트 강화가 진짜 다신호 한국어 요청까지 막으면 안 된다."""
        scored = score_skills(
            "투자 사업계획서 발표자료 만들어줘",
            [_skill("business-planning", tags=("investor", "pitch", "presentation"),
                    description="Build an investor pitch deck")],
        )[0]
        assert auto_inject_eligible(scored)


# ── plugin skills ride the same relevance pipeline (P0) ──────────────────────

class TestPluginSkillPipeline:
    def _svc(self) -> LearningService:
        return LearningService(Database(":memory:").connect(), LearningConfig(), _fresh_paths())

    def _plugin_skill(self, name="deploy-checklist", body="1. run tests\n2. deploy") -> InjectedSkill:
        return InjectedSkill(
            skill_id=f"plugin:plugin:demo:{name}", name=name, version=1, body=body,
            description="Deployment checklist", tags=("deploy",), source="plugin",
        )

    def test_relevant_plugin_skill_is_injected_with_provenance(self):
        svc = self._svc()
        pf = svc.prefetch("s1", "배포 체크리스트대로 진행해줘", extra_skills=(self._plugin_skill(),))
        assert [s.name for s in pf.skills] == ["deploy-checklist"]
        assert pf.skills[0].source == "plugin"
        # 주입 렌더에 provenance 가 남아 감사 가능해야 한다
        assert "plugin" in pf.system_prompt_additions

    def test_irrelevant_plugin_skill_costs_one_index_line_not_its_body(self):
        svc = self._svc()
        pf = svc.prefetch("s1", "hello", extra_skills=(self._plugin_skill(),))
        assert pf.skills == ()  # 전문 주입 없음 — 전량 주입 우회가 닫혔다
        assert "deploy-checklist" in pf.system_prompt_additions  # 인덱스 한 줄
        assert "run tests" not in pf.system_prompt_additions  # 본문은 없다

    def test_indexed_plugin_skill_is_viewable_on_demand(self):
        """인덱스가 광고한 plugin 스킬은 skill_view 로 실제로 열려야 한다."""
        svc = self._svc()
        svc.prefetch("s1", "hello", extra_skills=(self._plugin_skill(),))
        body = svc.view_skill_body("s1", "deploy-checklist")
        assert "run tests" in body["body"]
        results = svc.search_skills("deploy")
        assert results[0]["name"] == "deploy-checklist"

    def test_learned_skill_of_same_name_wins(self):
        svc = self._svc()
        SkillStore(svc._paths).write(_skill("deploy-checklist", body="LEARNED BODY", tags=("deploy",)))
        pf = svc.prefetch("s1", "배포 체크리스트대로 진행해줘", extra_skills=(self._plugin_skill(),))
        assert [s.name for s in pf.skills] == ["deploy-checklist"]
        assert pf.skills[0].source == "learned"
        assert "LEARNED BODY" in pf.system_prompt_additions


# ── tool routing: ranked search + relevance spotlight (P0) ────────────────────

class TestToolRouting:
    def _dispatcher(self, folded):
        return ToolDispatcher(
            connectors=MagicMock(), plugins=MagicMock(), approvals=MagicMock(),
            approval_timeout_s=1, folded_catalog={t.name: t for t in folded},
        )

    @pytest.mark.asyncio
    async def test_tool_search_ranks_korean_query_against_english_names(self):
        folded = [
            _tool("gmail_search_threads", "search email threads"),
            _tool("calendar_list_events", "list calendar events"),
        ]
        disp = self._dispatcher(folded)
        call = ToolCall(tool_name="tool_search", arguments={"query": "메일 검색"}, id="s1")
        _ = [ev async for ev in disp.dispatch("sess", call, BRIDGE_TOOL_SPECS[0])]
        res = await disp.result_for(call)
        assert res.result["matches"][0]["name"] == "gmail_search_threads"
        assert all(m["name"] != "calendar_list_events" for m in res.result["matches"])

    def test_over_budget_fold_spotlights_relevant_tools(self):
        """예산 초과 시 전부 접지 않는다 — 브릿지 3개 + 이 턴과 관련한 top-K 만 편다."""
        core = (_tool("builtin_echo"),)
        noise = tuple(_tool(f"big_{i}", big=True) for i in range(200))
        target = _tool("gmail_send", "send an email message")
        result = fold_tools_for_budget(core, noise + (target,), query="회의록을 메일로 보내줘")
        names = {t.name for t in result}
        assert {"tool_search", "tool_describe", "tool_call", "gmail_send"} <= names
        assert not any(n.startswith("big_") for n in names)  # 무관 도구는 접힌 채

    def test_over_budget_fold_without_query_keeps_bridges_only(self):
        core = (_tool("builtin_echo"),)
        noise = tuple(_tool(f"big_{i}", big=True) for i in range(200))
        result = fold_tools_for_budget(core, noise)
        assert result == core + BRIDGE_TOOL_SPECS

    # ── ToolSpec.keywords: 도구의 자기 트리거 어휘 (2026-07-25) ──────────────────
    # 스킬의 tags와 같은 역할. 접힌 도구의 이름/설명은 대개 **벤더가 쓴 영어**라
    # 한국어 도메인어가 닿지 않는데, 전역 intent 테이블에 도메인어를 넣는 것은
    # core/lexical.py가 금지한 anti-pattern이라 도구가 직접 들고 다녀야 한다.

    @staticmethod
    def _spotlit(target: ToolSpec, query: str) -> set[str]:
        """예산 초과로 접힌 상태에서 스포트라이트에 오른 도구 이름들."""
        core = (_tool("builtin_echo"),)
        noise = tuple(_tool(f"big_{i}", big=True) for i in range(200))
        return {t.name for t in fold_tools_for_budget(core, noise + (target,), query=query)}

    def test_keywords_surface_a_tool_its_english_text_cannot_reach(self):
        """'받은편지함'은 intent 테이블에 없고 영어 이름/설명에도 없다 — keywords가
        유일한 다리다. 없으면 브릿지 뒤에 숨어 소형 모델이 도구를 못 찾는다."""
        query = "받은편지함 확인해줘"
        without = _tool("gmail_search_messages", "Search Gmail messages by query")
        with_kw = ToolSpec(
            name="gmail_search_messages", description="Search Gmail messages by query",
            parameters=without.parameters, source="connector", owner_id="c1",
            keywords=("받은편지함", "메일", "지메일"),
        )
        assert "gmail_search_messages" not in self._spotlit(without, query)
        assert "gmail_search_messages" in self._spotlit(with_kw, query)

    def test_idf_lifts_the_one_relevant_tool_out_of_a_large_catalog(self):
        """도구는 스킬보다 훨씬 많고(커넥터/플러그인마다 수십 개), 카탈로그가
        get/list/search 같은 편재 동사로 뒤덮여 있다. 그 단어들이 판별력 없이 만점을
        받으면 스포트라이트 8칸이 generic 도구로 차고, 로컬 모델은 정작 필요한 도구를
        못 본 채 판단해야 한다 — 도구 수가 늘수록 심해지는 실패다."""
        catalog = []
        for svc in ["gmail", "calendar", "drive", "slack", "notion", "jira"]:
            for verb in ["list", "get", "search", "create"]:
                catalog.append(_tool(f"{svc}_{verb}_items",
                                     f"{verb.capitalize()} items in {svc}. Search and list records."))
        target = _tool("stripe_invoice_pdf", "Download an invoice PDF for a charge")
        specs = tuple(catalog) + (target,)

        q = lexical.prepare_query("invoice search")
        w = _tool_term_weights(q, specs)
        assert w is not None
        # 'invoice'(1/25)가 'search'(편재)보다 확실히 무거워야 한다
        assert w[list(t.variants[0][0] for t in q.terms).index("invoice")] > \
               w[list(t.variants[0][0] for t in q.terms).index("search")]

        ranked = sorted(specs, key=lambda s: _score_tool(q, s, w), reverse=True)
        assert ranked[0].name == "stripe_invoice_pdf"
        # 가중 없이는 generic search 도구들이 이겼다(회귀 방지)
        unweighted = sorted(specs, key=lambda s: _score_tool(q, s, None), reverse=True)
        assert unweighted[0].name != "stripe_invoice_pdf"

    def test_keywords_do_not_change_english_routing(self):
        """영어 요청의 점수는 keywords 유무와 무관해야 한다(순수 추가 신호)."""
        from rice_runtime.agent.tool_dispatch import _score_tool
        from rice_runtime.core import lexical
        q = lexical.prepare_query("search my email")
        plain = _tool("gmail_search_messages", "Search Gmail messages")
        kw = ToolSpec(
            name=plain.name, description=plain.description, parameters=plain.parameters,
            source="connector", owner_id="c1", keywords=("받은편지함", "지메일"),
        )
        assert _score_tool(q, plain) == _score_tool(q, kw)

    def test_keywords_never_reach_the_model_schema(self):
        """라우팅 전용이라는 계약 — provider 어댑터는 name/description/parameters만
        직렬화하므로 keywords는 wire에 실리지 않고 토큰을 쓰지 않는다."""
        from rice_runtime.agent.providers.claude import _to_claude_tools
        spec = ToolSpec(
            name="t", description="d", parameters={"type": "object", "properties": {}},
            source="connector", owner_id="c1", keywords=("비밀어",),
        )
        assert "비밀어" not in json.dumps(_to_claude_tools((spec,)), ensure_ascii=False)


class TestDiscriminativeness:
    """판별력 가중(IDF) — 임베딩/리랭커가 대신해주던 신호를 모델 없이 얻는다.

    주의: 대부분의 다른 테스트는 스킬이 1~2개라 `_IDF_MIN_CANDIDATES` 미만이고
    IDF가 **꺼진 채** 통과한다. 이 클래스만 현실적 카탈로그 크기를 쓴다.
    """

    # 'report'는 5개 중 4개에 있어 아무것도 변별하지 못하고, 'payroll'은 1개뿐이다.
    CATALOG = [
        _skill("report-hub", tags=("report", "reporting", "reports"),
               description="Report hub for reports", body="report report report"),
        _skill("payroll-run", tags=("payroll",), description="Run it"),
        _skill("monthly-report", tags=("report",), description="Monthly report"),
        _skill("sales-report", tags=("report",), description="Sales report"),
        _skill("annual-report", tags=("report",), description="Annual report"),
    ]

    def test_rare_term_outranks_a_ubiquitous_one(self):
        """빈도만 보면 'report'를 4개 필드에 반복한 report-hub가 이긴다 — 그게
        IDF 이전의 실제 동작이었고(payroll-run이 꼴찌), 사용자가 payroll을 물었는데
        report 허브를 받는 결과였다."""
        ranked = score_skills("payroll report", self.CATALOG)
        assert ranked[0].name == "payroll-run"
        # 압도적이어야 한다 — 근소한 차이는 카탈로그가 조금만 바뀌어도 다시 뒤집힌다
        assert ranked[0].score > 2 * ranked[1].score

    def test_weights_are_mean_normalized_so_the_score_scale_is_preserved(self):
        """AUTO_INJECT_THRESHOLD는 실측 보정된 절대 기준이다 — IDF가 점수 규모를
        옮기면 그 보정이 무효가 된다. 평균 1.0 정규화가 그걸 막는다."""
        q = lexical.prepare_query("payroll report deploy")
        w = term_weights(q, self.CATALOG)
        assert w is not None
        assert abs(sum(w) / len(w) - 1.0) < 1e-9

    def test_idf_does_not_move_the_injection_gate(self):
        """IDF는 **순수 재랭킹**이다. strong은 무가중이라 게이트 판정이 불변이어야
        한다 — 가중치가 섞이면 MIN_STRONG_HITS의 의미가 조용히 바뀐다."""
        skill = _skill("xlsx-builder", tags=("excel", "xlsx", "spreadsheet"),
                       description="Build an Excel workbook")
        alone = score_skills("엑셀", [skill])[0]              # 후보 1개 → IDF off
        in_catalog = score_skills("엑셀", [skill] + self.CATALOG)[0]  # IDF on
        assert in_catalog.name == "xlsx-builder"
        assert alone.strong == in_catalog.strong             # 게이트 입력 불변
        assert auto_inject_eligible(alone) == auto_inject_eligible(in_catalog)

    def test_idf_is_disabled_for_tiny_candidate_sets(self):
        """후보가 2개면 df 통계가 잡음이다 — 가중을 끄고 결정론적으로 동작한다."""
        assert term_weights(lexical.prepare_query("a b"), self.CATALOG[:2]) is None

    def test_top1_stays_correct_across_a_realistic_catalog(self):
        """IDF가 켜진 상태에서 평범한 요청들이 여전히 옳은 스킬을 1위로 올리는가 —
        재랭킹이 흔한 경우를 망가뜨리지 않았다는 회귀 방지선."""
        catalog = [
            _skill("business-planning", tags=("business", "사업계획서", "재무모델"),
                   description="Investor pitch deck and financial model"),
            _skill("deploy-checklist", tags=("deploy", "배포", "체크리스트"),
                   description="Deployment checklist"),
            _skill("expense-filing", tags=("expense", "경비", "영수증"), description="File expenses"),
            _skill("xlsx-builder", tags=("excel", "엑셀", "xlsx"), description="Build a workbook"),
            _skill("deck-builder", tags=("slides", "발표자료", "ppt"), description="Build slides"),
        ]
        for query, expected in [
            ("사업계획서 재무모델 만들어줘", "business-planning"),
            ("배포 체크리스트대로 진행해줘", "deploy-checklist"),
            ("경비 영수증 정리", "expense-filing"),
            ("엑셀 워크북 만들어줘", "xlsx-builder"),
            ("발표자료 슬라이드 만들어줘", "deck-builder"),
            ("investor pitch deck please", "business-planning"),
        ]:
            assert score_skills(query, catalog)[0].name == expected, query


def test_both_skill_creation_paths_share_the_same_tag_guidance():
    """리뷰(T1)와 발굴(T4) 두 생성 경로가 서로 다른 tags 지침을 주면, 스킬 발견성이
    '어느 경로로 태어났는가'에 따라 갈린다 — 애초의 결함이 그 표류였다."""
    from rice_runtime.prompts.discovery import build_discovery_tool_specs
    from rice_runtime.prompts.review import TAGS_PARAM_DESCRIPTION, build_review_tool_specs

    def _tags_desc(specs):
        spec = next(s for s in specs if s.name == "propose_skill_create")
        return spec.parameters["properties"]["tags"]["description"]

    assert _tags_desc(build_review_tool_specs(())) == TAGS_PARAM_DESCRIPTION
    assert _tags_desc(build_discovery_tool_specs()) == TAGS_PARAM_DESCRIPTION
    # 지침의 핵심은 "한국어 트리거도 넣어라" — 이게 빠지면 생성 경로가 다시 영어 전용이 된다
    assert "Korean" in TAGS_PARAM_DESCRIPTION


def test_builtin_skills_carry_korean_triggers():
    """빌트인은 사용자가 못 고치는 스킬이라, 한국어 트리거가 없으면 routing_miss가
    쌓여도 학습 루프가 고칠 수 없다(리뷰는 사용자 스킬만 패치한다)."""
    from rice_runtime.learning.skill.builtin import BuiltinSkillCatalog

    for skill in BuiltinSkillCatalog(_fresh_paths()).list():
        korean = [t for t in skill.tags if any("가" <= ch <= "힣" for ch in t)]
        assert korean, f"builtin skill {skill.name!r} has no Korean trigger tag"


def test_coerce_tool_keywords_is_defensive():
    """플러그인 매니페스트/MCP 응답은 신뢰할 수 없는 입력이다 — 형식 오류가 도구
    로딩을 깨지 않고, 무제한 리스트가 라우팅 비용을 불리지 않아야 한다."""
    from rice_runtime.core.types import MAX_TOOL_KEYWORDS, coerce_tool_keywords

    assert coerce_tool_keywords(None) == ()
    assert coerce_tool_keywords("not-a-list") == ()
    assert coerce_tool_keywords([1, None, {"a": 1}]) == ()   # 비문자열은 버린다
    assert coerce_tool_keywords(["  메일  ", "", "gmail"]) == ("메일", "gmail")
    assert len(coerce_tool_keywords([f"k{i}" for i in range(100)])) == MAX_TOOL_KEYWORDS


# ── owner-aware plugin dispatch (P1) ──────────────────────────────────────────

class _NoSandbox:
    async def run(self, *, entrypoint, payload, sys_path=None):
        raise AssertionError("not used")


def test_plugin_tool_entry_resolution_is_owner_aware():
    """같은 도구명을 선언한 플러그인이 둘일 때 owner_id 가 오배송을 막는다."""
    paths = _fresh_paths()
    db = Database(":memory:").connect()
    loader = PluginLoader(db=db, sandbox=_NoSandbox(), paths=paths)
    for pid, module in (("plugin:first", "first_mod"), ("plugin:second", "second_mod")):
        d = paths.plugins / pid
        d.mkdir(parents=True, exist_ok=True)
        (d / "rice-artifact.json").write_text(
            json.dumps({
                "schema_version": 1, "type": "plugin", "name": pid.split(":")[1],
                "version": "1.0.0",
                "entrypoint": {"module": module, "tools": [{"name": "dup_tool"}]},
            }),
            encoding="utf-8",
        )
        db.execute(
            "INSERT INTO installed_artifacts (id, type, name, version, source, enabled) "
            "VALUES (?, 'plugin', ?, '1.0.0', 'test', 1)",
            (pid, pid.split(":")[1]),
        )
    entry_default, _ = loader._resolve_tool_entry("dup_tool")
    entry_owned, owned_dir = loader._resolve_tool_entry("dup_tool", owner_id="plugin:second")
    assert entry_default == "first_mod.dup_tool"  # 이름 스캔은 첫 플러그인을 고른다
    assert entry_owned == "second_mod.dup_tool"  # owner 지정 시 그 플러그인으로 고정
    assert owned_dir == paths.plugins / "plugin:second"
