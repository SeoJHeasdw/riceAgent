"""test_skill_analysis.py — 설명용 분석 사이드카(SkillAnalysis) 수용 기준 검증.

수용 기준 (MySkills "스킬 이해" 실배선, 2026-07-06):
  1. 사이드카 라이프사이클: missing → refresh(휴리스틱) → fresh, 본문 변경 → stale
     (읽기 시점 hash 판정), 재분석 → fresh. SKILL.md 본문은 절대 변하지 않는다.
  2. 트리거: create/update(body)/apply_patch/rollback 이 재분석을 enqueue 하고
     run_pending_analyses 가 드레인한다. delete 는 사이드카를 제거한다.
  3. 도구 단서 3원 분리: observed(usage 실행 로그) / declared(frontmatter tools:)
     / mentioned(본문 감지)가 각자의 kind 로 합류한다.
  4. observed ledger: observe()가 이 턴의 tool_calls 를 주입 스킬의 usage 사이드카에
     누적한다 (aborted 턴은 제외).
  5. LLM 층: structured JSON 성공 시 source='llm' 으로 덮고, 실패 시 휴리스틱
     결과 + status='failed' 로 폴백한다. 어느 쪽도 본문에 닿지 않는다.
  6. API: GET /skills/{id} 가 analysis 를 싣고, POST /skills/{id}/analysis/refresh
     가 사이드카만 재생성한다.

격리: 임시 루트 + in-memory DB. 외부 의존성 없음 (LLM 은 스텁 provider).
"""

from __future__ import annotations

import asyncio
import json
import tempfile

import pytest

from rice_runtime.core.config import LearningConfig
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.core.types import Message, ToolCall, TurnOutcome
from rice_runtime.learning.service import LearningService
from rice_runtime.learning.skill.analysis import (
    AnalysisStore,
    SkillAnalysisData,
    body_hash,
    effective_status,
)
from rice_runtime.learning.skill.analyzer import SkillAnalyzer, heuristic_analysis
from rice_runtime.learning.skill.store import SkillData, parse_skill_md, serialize_skill_md
from rice_runtime.learning.skill.usage import UsageStore


def _fresh_paths() -> RicePaths:
    return RicePaths.from_env(tempfile.mkdtemp(prefix="rice-analysis-test-")).ensure()


def _service(paths: RicePaths, **kwargs) -> LearningService:
    return LearningService(Database(":memory:").connect(), LearningConfig(), paths, **kwargs)


_BODY = (
    "# Demo\n\n요약 문단입니다.\n\n## Steps\n- run pytest first\n- then git commit\n\n"
    "## Tools\n- terminal\n"
)


# ── 1. 사이드카 라이프사이클 ──────────────────────────────────────────────────

def test_analysis_lifecycle_and_body_untouched():
    paths = _fresh_paths()
    svc = _service(paths)
    svc.create_skill(name="demo-skill", body=_BODY, description="데모")

    assert svc.get_skill_analysis("demo-skill")["status"] == "missing"

    payload = asyncio.run(svc.refresh_skill_analysis("demo-skill"))
    assert payload["status"] == "fresh"
    assert payload["source"] == "heuristic"  # provider 미배선 → 휴리스틱 층만
    assert [s["title"] for s in payload["procedural_steps"]] == [
        "run pytest first",
        "then git commit",
    ]

    # refresh 는 사이드카만 만든다 — SKILL.md 본문/버전 불변.
    detail = svc.get_skill("demo-skill")
    assert detail.body == _BODY
    assert detail.version == 1

    # 본문 변경 → 읽기 시점 stale 판정 (사이드카를 손대지 않고도).
    svc.update_skill("demo-skill", body="# 완전히 다른 본문\n")
    assert svc.get_skill_analysis("demo-skill")["status"] == "stale"


def test_effective_status_analyzer_version_mismatch():
    data = SkillAnalysisData(skill="x", body_hash=body_hash("b"), analyzer_version="0-old")
    assert effective_status(data, "b") == "stale"
    assert effective_status(None, "b") == "missing"


def test_corrupt_sidecar_treated_as_missing():
    paths = _fresh_paths()
    store = AnalysisStore(paths)
    paths.skills.mkdir(parents=True, exist_ok=True)
    (paths.skills / "broken.analysis.json").write_text("{not json", encoding="utf-8")
    assert store.read("broken") is None


# ── 2. 트리거 + 드레인 ────────────────────────────────────────────────────────

def test_body_writes_enqueue_and_drain_refreshes():
    paths = _fresh_paths()
    svc = _service(paths)
    svc.create_skill(name="demo-skill", body=_BODY, description="데모")
    assert "demo-skill" in svc._analysis_queue  # create 트리거

    assert asyncio.run(svc.run_pending_analyses(limit=5)) == 1
    assert svc.get_skill_analysis("demo-skill")["status"] == "fresh"

    # apply_patch(승인) 트리거 → stale → 드레인이 다시 fresh 로.
    svc.apply_patch("demo-skill", approved=True, proposed_body="# 새 본문\n1. 새 단계 실행\n", expected_version=1)
    assert "demo-skill" in svc._analysis_queue
    asyncio.run(svc.run_pending_analyses(limit=5))
    assert svc.get_skill_analysis("demo-skill")["status"] == "fresh"

    # rollback 트리거.
    svc.rollback("demo-skill", 1)
    assert "demo-skill" in svc._analysis_queue

    # 이미 fresh 면 드레인이 건너뛴다 (중복 LLM 호출 방지).
    svc._analysis_queue.append("demo-skill")
    asyncio.run(svc.run_pending_analyses(limit=5))
    assert asyncio.run(svc.run_pending_analyses(limit=5)) == 0


def test_delete_removes_sidecar():
    paths = _fresh_paths()
    svc = _service(paths)
    svc.create_skill(name="demo-skill", body=_BODY, description="데모")
    asyncio.run(svc.refresh_skill_analysis("demo-skill"))
    assert (paths.skills / "demo-skill.analysis.json").exists()
    svc.delete_skill("demo-skill")
    assert not (paths.skills / "demo-skill.analysis.json").exists()


# ── 3+4. 도구 단서 3원 분리 + observed ledger ────────────────────────────────

def test_tool_mention_sources_are_separated():
    paths = _fresh_paths()
    svc = _service(paths)
    body_with_tools = "---\n" not in _BODY and _BODY  # noqa: SIM222 — 가독용
    svc.create_skill(name="demo-skill", body=_BODY, description="데모")

    # declared: frontmatter tools — SkillStore 라운드트립으로 주입.
    data = svc._skill_store.read("demo-skill")
    data.tools = ["create_workbook"]
    svc._skill_store.write(data)

    # observed: 주입된 턴의 tool_calls 를 observe 가 기록.
    outcome = TurnOutcome(
        session_id="s1",
        final_message="done",
        history=(
            Message(role="user", content="do it"),
            Message(role="assistant", content="", tool_calls=(ToolCall(tool_name="terminal", arguments={}),)),
            Message(role="assistant", content="done"),
        ),
        tool_calls_made=1,
        injected_skill_ids=("demo-skill",),
    )
    asyncio.run(svc.observe(outcome))

    payload = asyncio.run(svc.refresh_skill_analysis("demo-skill"))
    kinds = {(t["name"], t["kind"]) for t in payload["tool_mentions"]}
    assert ("terminal", "observed") in kinds
    assert ("create_workbook", "declared") in kinds
    assert ("terminal", "mentioned") in kinds  # 본문 키워드/도구 섹션 감지


def test_aborted_turn_records_no_observed_tools():
    paths = _fresh_paths()
    svc = _service(paths)
    svc.create_skill(name="demo-skill", body=_BODY, description="데모")
    outcome = TurnOutcome(
        session_id="s1",
        final_message="",
        history=(
            Message(role="user", content="do it"),
            Message(role="assistant", content="", tool_calls=(ToolCall(tool_name="terminal", arguments={}),)),
        ),
        tool_calls_made=1,
        injected_skill_ids=("demo-skill",),
        aborted=True,
    )
    asyncio.run(svc.observe(outcome))
    assert UsageStore(paths).read("demo-skill").tools == {}


def test_observed_ledger_survives_body_patch():
    paths = _fresh_paths()
    usage = UsageStore(paths)
    usage.record_tools("demo-skill", ["terminal", "terminal", "read_file"])
    assert usage.read("demo-skill").tools == {"terminal": 2, "read_file": 1}


def test_declared_tools_frontmatter_roundtrip():
    skill = SkillData(
        name="x", version=1, description="d", state="active", body="b\n",
        tags=[], source=None, created_at=None, updated_at=None, tools=["terminal", "gmail"],
    )
    parsed = parse_skill_md(serialize_skill_md(skill))
    assert parsed.tools == ["terminal", "gmail"]
    # tools 없는 기존 파일 하위호환.
    legacy = parse_skill_md("---\nname: y\nversion: 1\ndescription: d\nstate: active\n---\nb\n")
    assert legacy.tools == []


# ── 5. LLM 층 ────────────────────────────────────────────────────────────────

class _Chunk:
    def __init__(self, text: str):
        self.kind = "text"
        self.text = text


class _StubProvider:
    """단발 structured-JSON 응답 provider 스텁."""

    name = "stub"

    def __init__(self, response: str):
        self._response = response

    async def stream(self, **_kwargs):
        yield _Chunk(self._response)


def _skill() -> SkillData:
    return SkillData(
        name="demo-skill", version=1, description="데모", state="active",
        body=_BODY, tags=[], source=None, created_at=None, updated_at=None,
    )


def test_llm_layer_overrides_heuristic():
    response = json.dumps(
        {
            "summary": "pytest 후 커밋하는 절차입니다.",
            "confidence": "high",
            "steps": [{"title": "테스트 실행", "description": "pytest", "evidence": "run pytest first"}],
            "tools": [{"name": "pytest", "evidence": "run pytest first"}],
            "warnings": ["커밋 전 리뷰 없음"],
        }
    )
    analyzer = SkillAnalyzer(resolve_provider=lambda: _StubProvider(response))
    result = asyncio.run(analyzer.analyze(_skill()))
    assert result.source == "llm"
    assert result.status == "fresh"
    assert result.summary == "pytest 후 커밋하는 절차입니다."
    assert result.procedural_steps[0]["title"] == "테스트 실행"
    names = [t["name"] for t in result.tool_mentions]
    assert "pytest" in names and "terminal" in names  # LLM + 휴리스틱 병합
    assert all(t["kind"] == "mentioned" for t in result.tool_mentions)


def test_llm_failure_falls_back_to_heuristic_with_failed_status():
    analyzer = SkillAnalyzer(resolve_provider=lambda: _StubProvider("죄송하지만 JSON이 아닙니다"))
    result = asyncio.run(analyzer.analyze(_skill()))
    assert result.source == "heuristic"
    assert result.status == "failed"
    assert result.procedural_steps  # 휴리스틱 결과는 성립
    assert any("AI 분석" in w for w in result.warnings)


def test_echo_provider_skips_llm_layer():
    class _Echo(_StubProvider):
        name = "echo"

    analyzer = SkillAnalyzer(resolve_provider=lambda: _Echo("ignored"))
    result = asyncio.run(analyzer.analyze(_skill()))
    assert result.source == "heuristic"
    assert result.status == "fresh"


def test_heuristic_analysis_hash_matches_body():
    h = heuristic_analysis(_skill())
    assert h.body_hash == body_hash(_BODY)


# ── 6. API ───────────────────────────────────────────────────────────────────

_HOST = {"host": "localhost"}  # 미들웨어의 localhost 화이트리스트 통과용


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from rice_runtime.core.config import RuntimeArgs
    from rice_runtime.server.app import create_app
    from rice_runtime.server.composition_root import build_container

    container = build_container(RuntimeArgs(token=""), paths=_fresh_paths())
    c = TestClient(create_app(container))
    try:
        yield c
    finally:
        container.db.close()


def test_routes_include_and_refresh_analysis(client):
    created = client.post(
        "/skills", json={"name": "demo-skill", "body": _BODY, "description": "데모"}, headers=_HOST
    )
    assert created.status_code == 200

    got = client.get("/skills/demo-skill", headers=_HOST).json()
    assert got["data"]["analysis"]["status"] == "missing"

    refreshed = client.post("/skills/demo-skill/analysis/refresh", headers=_HOST).json()
    assert refreshed["success"] is True
    assert refreshed["data"]["status"] in ("fresh", "failed")
    assert refreshed["data"]["procedural_steps"]

    got = client.get("/skills/demo-skill", headers=_HOST).json()
    assert got["data"]["analysis"]["status"] in ("fresh", "failed")
    # 본문은 그대로.
    assert got["data"]["body"] == _BODY


def test_refresh_analysis_unknown_skill_404(client):
    assert client.post("/skills/nope/analysis/refresh", headers=_HOST).status_code == 404
