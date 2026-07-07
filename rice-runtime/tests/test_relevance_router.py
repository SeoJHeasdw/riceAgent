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
    fold_tools_for_budget,
)
from rice_runtime.core import lexical
from rice_runtime.core.config import LearningConfig
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.core.types import InjectedSkill, ToolCall, ToolSpec
from rice_runtime.execution.plugin.loader import PluginLoader
from rice_runtime.learning.service import LearningService
from rice_runtime.learning.skill.relevance import AUTO_INJECT_THRESHOLD, score_skills
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

    def test_bigram_fuzzy_bridges_typos(self):
        assert lexical.field_score({"workbok"}, "create workbook") > 0

    def test_expansion_never_fabricates_explicit_mention(self):
        """intent 확장어로는 explicit-mention 보너스가 발동하지 않아야 한다."""
        ranked = score_skills("뭔가 만들어줘", [_skill("create")])
        assert ranked[0].score < 2 * AUTO_INJECT_THRESHOLD  # 보너스(9.0) 미발동


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
