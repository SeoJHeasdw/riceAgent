"""Sub-agent v2 테스트 — 스킬 주입 · 도구 화이트리스트 주입 · scratch 아티팩트 채널.

원칙 검증:
  - 스킬은 인덱스(이름+한 줄)로만 프롬프트에 실리고, 전문은 skill_view로 온디맨드.
  - 화이트리스트는 도구 표면을 좁히되, 오타/공집합이면 전체 셋 폴백(fail-soft).
  - write_scratch는 thread 격리 영역(.subagent/<thread>/) 밖으로 절대 못 쓴다.
  - 아티팩트는 모델 자기신고가 아니라 러너의 관찰로 수집된다.
"""

from __future__ import annotations

import json

import pytest

from rice_runtime.agent.builtins import build_subagent_toolset, scratch_tool_factory
from rice_runtime.agent.providers.interfaces import ProviderCapabilities, ProviderChunk
from rice_runtime.agent.subagents import SubAgentRunner
from rice_runtime.core.config import ContextConfig
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.types import PluginAgent, ToolCall


class _EmptyTranscript:
    def history(self, session_id):
        return ()


class _FakeLearning:
    """search_skills/view_skill_body만 구현한 얕은 학습 서비스 더블."""

    def __init__(self) -> None:
        self.viewed: list[tuple[str, str]] = []

    def search_skills(self, query: str, limit: int = 8):
        return [{"name": "deploy-checklist", "description": "배포 전 점검 절차", "score": 3.2}]

    def view_skill_body(self, session_id: str, skill_id: str):
        self.viewed.append((session_id, skill_id))
        return {"name": skill_id, "version": 2, "body": "1. check\n2. ship"}


class _ScriptedProvider:
    """각 호출에서 미리 정해둔 (text, calls)를 재생하는 provider 더블."""

    name = "fake"

    def __init__(self, script) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def capabilities(self):
        return ProviderCapabilities(native_tool_calling=True)

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
        self.calls.append({"system": system, "messages": tuple(messages), "tools": tuple(tools)})
        text, tool_calls = self.script.pop(0) if self.script else ("", [])
        for call in tool_calls:
            yield ProviderChunk(kind="tool_call", tool_call=call)
        if text:
            yield ProviderChunk(kind="text", text=text)
        yield ProviderChunk(kind="done", stop_reason="end_turn")


def _report(**overrides) -> str:
    base = {
        "findings": ["done"],
        "evidence": ["seen"],
        "confidence": "medium",
        "open_questions": [],
        "recommended_next_action": "none",
    }
    base.update(overrides)
    return json.dumps(base)


def _runner(tmp_path, provider, *, learning=None, with_scratch=True, agents=None) -> SubAgentRunner:
    paths = RicePaths.from_env(tmp_path)
    return SubAgentRunner(
        resolve_provider=lambda: provider,
        transcript=_EmptyTranscript(),
        context_config=ContextConfig(),
        tools=build_subagent_toolset(paths, learning=learning),
        learning=learning,
        scratch_tool_factory=scratch_tool_factory(paths) if with_scratch else None,
        resolve_agents=(lambda: agents) if agents is not None else None,
    )


# ── toolset assembly ──────────────────────────────────────────────────────────


def test_subagent_toolset_gains_skill_tools_with_learning(tmp_path):
    paths = RicePaths.from_env(tmp_path)
    base = {spec.name for spec, _ in build_subagent_toolset(paths)}
    assert base == {"read_file", "list_dir", "grep", "search_files", "fetch_url", "web_search"}

    with_learning = {spec.name for spec, _ in build_subagent_toolset(paths, learning=_FakeLearning())}
    # skill_resource: 플러그인 스킬 번들 파일(references/ 등) 온디맨드 읽기 —
    # 워커도 메인 루프와 같은 하이브리드 스킬 파이프라인을 쓴다(2026-07-12).
    assert with_learning == base | {"skill_search", "skill_view", "skill_resource"}
    # propose_skill(사용자 가시 효과 스테이징)은 워커에 실리지 않는다
    assert "propose_skill" not in with_learning


# ── skill injection ───────────────────────────────────────────────────────────


async def test_worker_gets_skill_index_and_loads_body_on_demand(tmp_path):
    learning = _FakeLearning()
    provider = _ScriptedProvider([
        ("", [ToolCall(tool_name="skill_view", arguments={"name": "deploy-checklist"}, id="c1")]),
        (_report(findings=["followed the checklist"]), []),
    ])
    runner = _runner(tmp_path, provider, learning=learning)

    result = await runner.run(session_id="s1", task="deploy the service")

    # (a) 인덱스는 프롬프트에 — 전문은 아니다
    first_prompt = provider.calls[0]["messages"][0].content
    assert "deploy-checklist" in first_prompt and "배포 전 점검 절차" in first_prompt
    assert "1. check" not in first_prompt
    # (b) 전문은 skill_view로 온디맨드 로딩됐고 session_id가 전달됐다(needs_session)
    assert learning.viewed == [("s1", "deploy-checklist")]
    tool_msg = next(m for m in provider.calls[1]["messages"] if m.role == "tool")
    assert "1. check" in tool_msg.content
    # (c) 시스템 프롬프트에 스킬 지시가 실렸다
    assert "skill_view" in provider.calls[0]["system"]
    assert result.tools_used == ("skill_view",)


async def test_worker_without_learning_has_no_skill_prompt(tmp_path):
    provider = _ScriptedProvider([(_report(), [])])
    runner = _runner(tmp_path, provider, learning=None)
    await runner.run(session_id="s1", task="anything")
    assert "skill_view" not in provider.calls[0]["system"]
    assert "Saved skills" not in provider.calls[0]["messages"][0].content


# ── tool whitelist injection ─────────────────────────────────────────────────


async def test_tool_whitelist_narrows_worker_surface(tmp_path):
    provider = _ScriptedProvider([(_report(), [])])
    runner = _runner(tmp_path, provider)
    await runner.run(session_id="s1", task="web only", tool_names=("fetch_url",))
    assert {t.name for t in provider.calls[0]["tools"]} == {"fetch_url"}
    # write_scratch가 빠졌으니 시스템 프롬프트도 그 지시를 싣지 않는다
    assert "write_scratch" not in provider.calls[0]["system"]


async def test_tool_whitelist_of_unknown_names_falls_back_to_full_set(tmp_path):
    provider = _ScriptedProvider([(_report(), [])])
    runner = _runner(tmp_path, provider)
    await runner.run(session_id="s1", task="typo", tool_names=("no_such_tool",))
    names = {t.name for t in provider.calls[0]["tools"]}
    assert "read_file" in names and "fetch_url" in names and "write_scratch" in names


# ── scratch artifact channel ─────────────────────────────────────────────────


async def test_worker_scratch_write_is_tracked_and_isolated(tmp_path):
    provider = _ScriptedProvider([
        ("", [ToolCall(tool_name="write_scratch", arguments={"path": "draft.md", "content": "# long draft"}, id="c1")]),
        (_report(findings=["draft saved"]), []),
    ])
    runner = _runner(tmp_path, provider)

    result = await runner.run(session_id="s1", task="write a draft")

    assert len(result.artifacts) == 1
    rel = result.artifacts[0]
    assert rel.startswith(".subagent/sub_") and rel.endswith("/draft.md")
    paths = RicePaths.from_env(tmp_path)
    assert (paths.workspace / rel).read_text(encoding="utf-8") == "# long draft"


async def test_scratch_write_cannot_escape_thread_dir(tmp_path):
    paths = RicePaths.from_env(tmp_path)
    factory = scratch_tool_factory(paths)
    _, handler = factory("sub_test")

    for bad in ("../../outside.md", "../other_thread/x.md"):
        with pytest.raises(ValueError):
            await handler({"path": bad, "content": "x"})
    # 워크스페이스 밖은 물론, 다른 thread 디렉토리에도 쓸 수 없다
    assert not (paths.workspace / "outside.md").exists()


async def test_scratch_artifacts_survive_timeout(tmp_path, monkeypatch):
    """timeout으로 죽은 워커도 이미 남긴 아티팩트는 보고한다(부분 성과 회수)."""

    class _SlowProvider(_ScriptedProvider):
        async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
            self.calls.append({"system": system, "messages": tuple(messages), "tools": tuple(tools)})
            if len(self.calls) == 1:
                yield ProviderChunk(
                    kind="tool_call",
                    tool_call=ToolCall(tool_name="write_scratch", arguments={"path": "p.md", "content": "partial"}, id="c1"),
                )
                yield ProviderChunk(kind="done", stop_reason="tool_use")
                return
            import asyncio

            await asyncio.sleep(60)  # 두 번째 호출에서 시간 초과 유도
            yield ProviderChunk(kind="done", stop_reason="end_turn")

    provider = _SlowProvider([])
    paths = RicePaths.from_env(tmp_path)
    runner = SubAgentRunner(
        resolve_provider=lambda: provider,
        transcript=_EmptyTranscript(),
        context_config=ContextConfig(),
        tools=build_subagent_toolset(paths),
        scratch_tool_factory=scratch_tool_factory(paths),
    )
    # config의 timeout 하한이 10초라 실제로 기다릴 수 없다 — 러너의 timeout
    # 경로만 짧게 패치해 TimeoutError 분기를 탄다.
    import asyncio

    real_timeout = asyncio.timeout
    monkeypatch.setattr("rice_runtime.agent.subagents.asyncio.timeout", lambda s: real_timeout(0.2))

    result = await runner.run(session_id="s1", task="slow task")
    assert result.truncated and "timed out" in result.summary
    assert result.artifacts and result.artifacts[0].endswith("/p.md")


# ── delegate_subagent passthrough ────────────────────────────────────────────


async def test_delegate_subagent_passes_tool_whitelist(tmp_path):
    from rice_runtime.agent.builtins.subagent_tools import tools as subagent_tools

    captured = {}

    class _RunnerStub:
        def available_personas(self):
            return ()  # delegate_spec 이 스펙 렌더 시 조회한다(페르소나 광고)

        async def run(self, **kwargs):
            captured.update(kwargs)

            class _R:
                @staticmethod
                def to_dict():
                    return {"ok": True}

            return _R()

    _, handler = subagent_tools(_RunnerStub())[0]
    await handler({"task": "t", "tools": ["fetch_url", 3]}, session_id="s9")
    assert captured["tool_names"] == ("fetch_url", "3")
    assert captured["session_id"] == "s9"


# ── plugin agent personas (2026-07-19 — plugins' agents/*.md 소비) ────────────

_REVIEWER = PluginAgent(
    agent_id="plugin:plugin:demo:agent:skill-reviewer",
    name="skill-reviewer",
    description="Reviews skills for quality. Trigger on review requests with long examples.",
    body="You are a meticulous skill reviewer. Check descriptions and steps.",
    plugin_id="plugin:demo",
)


async def test_persona_swaps_worker_role_but_keeps_report_contract(tmp_path):
    provider = _ScriptedProvider([(_report(), [])])
    runner = _runner(tmp_path, provider, agents=[_REVIEWER])

    result = await runner.run(session_id="s1", task="review the deploy skill", agent="skill-reviewer")

    system = provider.calls[0]["system"]
    # 역할부는 페르소나 본문으로 바뀌고,
    assert system.startswith("You are a meticulous skill reviewer.")
    assert "focused RICE sub-agent" not in system
    # 보고 계약(JSON 구조)은 페르소나가 있어도 항상 붙는다(불변식).
    assert '"findings"' in system and '"confidence"' in system
    # 페르소나가 전제하는 타 하니스 도구를 흉내내지 않게 실제 도구 표면을 명시.
    assert "only the tools actually provided" in system
    assert result.persona == "skill-reviewer"
    assert result.open_questions == ()


async def test_unknown_persona_falls_back_and_reports(tmp_path):
    provider = _ScriptedProvider([(_report(), [])])
    runner = _runner(tmp_path, provider, agents=[_REVIEWER])

    result = await runner.run(session_id="s1", task="t", agent="no-such-agent")

    # fail-soft: 기본 역할로 진행하고,
    assert provider.calls[0]["system"].startswith("You are a focused RICE sub-agent.")
    assert result.persona == ""
    # 메인 모델이 이름을 교정할 수 있게 가용 목록을 보고한다.
    assert any("no-such-agent" in q and "skill-reviewer" in q for q in result.open_questions)


async def test_delegate_spec_advertises_live_personas(tmp_path):
    from rice_runtime.agent.builtins.subagent_tools import delegate_spec

    provider = _ScriptedProvider([])
    with_personas = delegate_spec(_runner(tmp_path, provider, agents=[_REVIEWER]))
    agent_param = with_personas.parameters["properties"]["agent"]["description"]
    # 이름 + 첫 문장만 광고된다(긴 트리거 예시 블록은 스펙에 싣지 않는다).
    assert "'skill-reviewer'" in agent_param
    assert "Reviews skills for quality." in agent_param
    assert "Trigger on review requests" not in agent_param

    without = delegate_spec(_runner(tmp_path, provider, agents=[]))
    assert "None are currently available" in without.parameters["properties"]["agent"]["description"]


async def test_delegate_subagent_passes_agent_name(tmp_path):
    from rice_runtime.agent.builtins.subagent_tools import tools as subagent_tools

    captured: dict = {}

    class _RunnerStub:
        def available_personas(self):
            return ()

        async def run(self, **kwargs):
            captured.update(kwargs)

            class _R:
                @staticmethod
                def to_dict():
                    return {"ok": True}

            return _R()

    _, handler = subagent_tools(_RunnerStub())[0]
    await handler({"task": "t", "agent": "skill-reviewer"}, session_id="s1")
    assert captured["agent"] == "skill-reviewer"
