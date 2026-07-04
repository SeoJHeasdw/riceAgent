"""Plan-and-execute × ReAct (RICE_AGENT_EXECUTION.md §3): planner triage,
plan state machine, and the loop's plan-driven execution."""

from __future__ import annotations

import asyncio
import json

import pytest

from rice_runtime.agent.loop import AgentLoop, LoopResult
from rice_runtime.agent.orchestrator import Orchestrator
from rice_runtime.agent.planner import UPDATE_PLAN_SPEC, PlanOutcome, PlanState, TurnPlanner
from rice_runtime.agent.providers.interfaces import ProviderCapabilities, ProviderChunk
from rice_runtime.agent.tool_dispatch import ToolDispatcher
from rice_runtime.core.config import AgentConfig, RuntimeArgs
from rice_runtime.core.model_json import extract_json_object
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.types import (
    EventType,
    Message,
    PlanStep,
    ToolCall,
    ToolSpec,
    TurnContext,
    TurnPlan,
)
from rice_runtime.server.approval import ApprovalRegistry
from rice_runtime.server.composition_root import build_container


class _NoopService:
    async def invoke(self, call):
        raise AssertionError(f"unexpected invoke: {call}")


def _spec(name: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} test tool",
        parameters={"type": "object", "properties": {}},
        source="builtin",
    )


class _ScriptedProvider:
    """Pops one scripted chunk-list per stream() call; records every call."""

    name = "fake"

    def __init__(self, rounds: list[list[ProviderChunk]]) -> None:
        self._rounds = list(rounds)
        self.calls: list[dict] = []

    def capabilities(self):
        return ProviderCapabilities(native_tool_calling=True)

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **knobs):
        self.calls.append(
            {"system": system, "messages": tuple(messages), "tools": tuple(tools), "temperature": temperature, **knobs}
        )
        assert self._rounds, "provider called more times than scripted"
        for chunk in self._rounds.pop(0):
            yield chunk
        yield ProviderChunk(kind="done", stop_reason="end_turn")


def _text(payload) -> list[ProviderChunk]:
    raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return [ProviderChunk(kind="text", text=raw)]


_PLAN_JSON = {
    "plan_needed": True,
    "goal": "테스트 목표",
    "steps": [
        {"title": "step one", "detail": "use echo"},
        {"title": "step two", "detail": "verify"},
    ],
}


def _ctx(tools: tuple[ToolSpec, ...]) -> TurnContext:
    return TurnContext(
        session_id="s1",
        system_prompt="sys",
        history=(Message(role="user", content="복잡한 작업 해줘"),),
        tools=tools,
        max_iterations=10,
    )


def _dispatcher(builtins: dict) -> ToolDispatcher:
    return ToolDispatcher(
        connectors=_NoopService(),
        plugins=_NoopService(),
        approvals=ApprovalRegistry(),
        approval_timeout_s=1,
        builtins=builtins,
    )


async def _run_planner(provider, ctx, user_message="복잡한 작업 해줘"):
    planner = TurnPlanner(provider=provider, config=AgentConfig())
    outcome = PlanOutcome()
    events = [
        ev async for ev in planner.run(ctx, user_message, outcome, asyncio.Event())
    ]
    return outcome.plan, events


# ── model_json ───────────────────────────────────────────────────────────────


def test_extract_json_object_accepts_fence_and_chatter():
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('the plan is {"a": 1} thanks') == {"a": 1}
    assert extract_json_object("no json here") is None
    assert extract_json_object('["not", "an", "object"]') is None


# ── planner triage/parsing ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_planner_builds_plan_for_complex_query():
    provider = _ScriptedProvider([_text(_PLAN_JSON)])
    plan, events = await _run_planner(provider, _ctx((_spec("echo"),)))

    assert plan is not None
    assert plan.goal == "테스트 목표"
    assert [s.id for s in plan.steps] == ["s1", "s2"]
    assert plan.steps[0].detail == "use echo"
    # think=False라 모델 thinking은 없지만, "계획 중" breadcrumb 하나는 흐른다.
    assert [e.type for e in events] == [EventType.THINKING]
    # 플래너 호출은 도구 없이, thinking 억제 + 스키마 제약으로 나간다
    assert provider.calls[0]["tools"] == ()
    assert provider.calls[0]["think"] is False
    assert provider.calls[0]["response_schema"]["required"] == ["plan_needed"]
    assert provider.calls[0]["max_tokens"] is not None
    # 도구 카탈로그·요청이 프롬프트에 실린다
    assert "echo" in provider.calls[0]["messages"][0].content
    assert "복잡한 작업 해줘" in provider.calls[0]["messages"][0].content


@pytest.mark.asyncio
async def test_planner_simple_query_and_garbage_fail_soft():
    for payload in ({"plan_needed": False}, "just chatting, no json"):
        provider = _ScriptedProvider([_text(payload)])
        plan, _ = await _run_planner(provider, _ctx((_spec("echo"),)))
        assert plan is None


@pytest.mark.asyncio
async def test_planner_single_step_plan_is_no_plan():
    provider = _ScriptedProvider(
        [_text({"plan_needed": True, "goal": "g", "steps": [{"title": "only one"}]})]
    )
    plan, _ = await _run_planner(provider, _ctx((_spec("echo"),)))
    assert plan is None


@pytest.mark.asyncio
async def test_planner_provider_error_fails_soft():
    class _Boom:
        name = "fake"

        def capabilities(self):
            return ProviderCapabilities()

        async def stream(self, **kwargs):
            raise RuntimeError("provider down")
            yield  # pragma: no cover

    plan, events = await _run_planner(_Boom(), _ctx((_spec("echo"),)))
    assert plan is None
    assert [e.type for e in events] == [EventType.THINKING]  # breadcrumb만


# ── plan state machine ───────────────────────────────────────────────────────


def test_plan_state_transitions_growth_and_completion():
    state = PlanState(
        TurnPlan(goal="g", steps=(PlanStep(id="s1", title="a"), PlanStep(id="s2", title="b")))
    )
    payload = state.apply(
        {
            "updates": [
                {"step_id": "s1", "status": "done", "note": "ok"},
                {"step_id": "nope", "status": "done"},
                {"step_id": "s2", "status": "bogus-status"},
            ],
            "add_steps": [{"title": "c", "detail": "extra"}],
        }
    )
    assert state.plan.revision == 2
    assert state.plan.steps[0].status == "done" and state.plan.steps[0].note == "ok"
    assert state.plan.steps[1].status == "pending"  # invalid status → untouched
    assert [s.id for s in state.plan.steps] == ["s1", "s2", "s3"]
    assert len(payload["warnings"]) == 2
    assert state.plan.status == "active" and not state.all_terminal

    state.apply({"updates": [{"step_id": "s2", "status": "skipped"}, {"step_id": "s3", "status": "done"}]})
    assert state.all_terminal and state.plan.status == "completed"


def test_plan_state_drift_counter_resets_on_apply():
    state = PlanState(TurnPlan(goal="g", steps=(PlanStep(id="s1", title="a"), PlanStep(id="s2", title="b"))))
    state.note_tool_round()
    state.note_tool_round()
    assert state.rounds_without_update == 2
    state.apply({})
    assert state.rounds_without_update == 0


# ── loop integration ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_plans_executes_and_react_updates():
    provider = _ScriptedProvider(
        [
            _text(_PLAN_JSON),  # planner call
            [  # round 1: report s1 in_progress→done + run a real tool
                ProviderChunk(
                    kind="tool_call",
                    tool_call=ToolCall(
                        tool_name="update_plan",
                        arguments={"updates": [{"step_id": "s1", "status": "done", "note": "echoed"}]},
                        id="call_up1",
                    ),
                ),
                ProviderChunk(
                    kind="tool_call", tool_call=ToolCall(tool_name="echo", arguments={}, id="call_echo")
                ),
            ],
            _text("최종 답변"),  # round 2: final answer
        ]
    )

    async def echo(args):
        return {"ok": True}

    loop = AgentLoop(
        provider=provider,
        dispatcher=_dispatcher({"echo": echo}),
        planner=TurnPlanner(provider=provider, config=AgentConfig()),
        agent_config=AgentConfig(),
    )
    ctx = _ctx((_spec("echo"),))
    result = LoopResult()
    events = [ev async for ev in loop.run(ctx, "s1", result, asyncio.Event())]

    # plan_updated: 생성(rev1) + update_plan 반영(rev2), 전체 스냅샷
    plan_events = [e for e in events if e.type == EventType.PLAN_UPDATED]
    assert [e.data["revision"] for e in plan_events] == [1, 2]
    assert plan_events[1].data["steps"][0]["status"] == "done"
    assert plan_events[1].data["steps"][0]["note"] == "echoed"

    # update_plan도 tool_call/tool_result로 감사 가능해야 한다
    tool_calls = [e.data["tool_name"] for e in events if e.type == EventType.TOOL_CALL]
    assert tool_calls == ["update_plan", "echo"]

    # 실행 라운드에는 update_plan이 광고되고, 플래너 호출에는 도구가 없다
    assert provider.calls[0]["tools"] == ()
    exec_tools = [t.name for t in provider.calls[1]["tools"]]
    assert "update_plan" in exec_tools and "echo" in exec_tools

    # 매 실행 반복의 시스템 프롬프트에 살아있는 계획 상태가 렌더된다
    assert "## Active Plan" in provider.calls[1]["system"]
    assert "[ ] s1. step one" in provider.calls[1]["system"]
    assert "[v] s1. step one" in provider.calls[2]["system"]  # ReAct: 관찰이 반영된 상태

    # 영속화: 계획 생성 스냅샷은 첫 어시스턴트 메시지에, 갱신본은 tool 메시지에
    first_assistant = next(m for m in result.history if m.role == "assistant")
    assert first_assistant.metadata["plan"]["revision"] == 1
    up_tool_msg = next(m for m in result.history if m.metadata.get("tool_name") == "update_plan")
    assert up_tool_msg.metadata["plan"]["revision"] == 2
    assert result.final_message == "최종 답변"
    assert result.tool_calls_made == 2


@pytest.mark.asyncio
async def test_loop_without_plan_stays_pure_react():
    provider = _ScriptedProvider(
        [
            _text({"plan_needed": False}),  # planner: 단순 질의
            _text("바로 답변"),
        ]
    )
    loop = AgentLoop(
        provider=provider,
        dispatcher=_dispatcher({}),
        planner=TurnPlanner(provider=provider, config=AgentConfig()),
        agent_config=AgentConfig(),
    )
    result = LoopResult()
    events = [ev async for ev in loop.run(_ctx((_spec("echo"),)), "s1", result, asyncio.Event())]

    assert not [e for e in events if e.type == EventType.PLAN_UPDATED]
    # 계획이 없으면 update_plan을 광고하지 않고, 시스템 프롬프트도 그대로다
    assert [t.name for t in provider.calls[1]["tools"]] == ["echo"]
    assert provider.calls[1]["system"] == "sys"
    assert result.final_message == "바로 답변"


@pytest.mark.asyncio
async def test_loop_drift_guard_forces_reassessment():
    provider = _ScriptedProvider(
        [
            _text(_PLAN_JSON),  # planner
            [ProviderChunk(kind="tool_call", tool_call=ToolCall(tool_name="echo", arguments={}, id="c1"))],
            [ProviderChunk(kind="tool_call", tool_call=ToolCall(tool_name="echo", arguments={}, id="c2"))],
            _text("끝"),
        ]
    )

    async def echo(args):
        return {"ok": True}

    loop = AgentLoop(
        provider=provider,
        dispatcher=_dispatcher({"echo": echo}),
        planner=TurnPlanner(provider=provider, config=AgentConfig()),
        agent_config=AgentConfig(plan_drift_guard_rounds=2),
    )
    result = LoopResult()
    _ = [ev async for ev in loop.run(_ctx((_spec("echo"),)), "s1", result, asyncio.Event())]

    # round1(무보고) 후엔 아직 아니고, round2(무보고) 후의 세 번째 실행 호출에 경고
    assert "REASSESS NOW" not in provider.calls[2]["system"]
    assert "REASSESS NOW" in provider.calls[3]["system"]


def test_completed_plan_nudges_skill_capture():
    state = PlanState(
        TurnPlan(
            goal="g",
            steps=(PlanStep(id="s1", title="a", status="done"), PlanStep(id="s2", title="b", status="done")),
            status="completed",
        )
    )
    loop = AgentLoop(provider=None, dispatcher=None, agent_config=AgentConfig())  # type: ignore[arg-type]
    ctx = _ctx(())
    with_nudge = loop._system_prompt_for(ctx, state, tool_calls_made=5)
    without_nudge = loop._system_prompt_for(ctx, state, tool_calls_made=1)
    assert "propose_skill" in with_nudge
    assert "propose_skill" not in without_nudge  # 도구 호출이 적으면 절차랄 게 없다


def test_effective_max_iterations_scales_with_plan_size():
    loop = AgentLoop(provider=None, dispatcher=None, agent_config=AgentConfig(plan_max_iterations=60))  # type: ignore[arg-type]
    ctx = _ctx(())
    assert loop._effective_max_iterations(ctx, None) == ctx.max_iterations
    small = PlanState(TurnPlan(goal="g", steps=tuple(PlanStep(id=f"s{i}", title="t") for i in range(2))))
    big = PlanState(TurnPlan(goal="g", steps=tuple(PlanStep(id=f"s{i}", title="t") for i in range(12))))
    assert loop._effective_max_iterations(ctx, small) == 24  # 12 + 6*2
    assert loop._effective_max_iterations(ctx, big) == 60  # capped


# ── orchestrator wiring (end to end through run_turn) ────────────────────────


@pytest.mark.asyncio
async def test_orchestrator_streams_plan_and_persists_snapshots(tmp_path):
    """run_turn이 플래너를 배선하고, plan_updated가 스트림에 흐르고, 스냅샷이
    metadata로 transcript에 영속화되는지 — 배선 수준의 회귀 방지."""
    c = build_container(RuntimeArgs(), paths=RicePaths.from_env(str(tmp_path)))
    provider = _ScriptedProvider(
        [
            _text(_PLAN_JSON),  # planner
            [
                ProviderChunk(
                    kind="tool_call",
                    tool_call=ToolCall(
                        tool_name="update_plan",
                        arguments={"updates": [{"step_id": "s1", "status": "done"}]},
                        id="call_up",
                    ),
                )
            ],
            _text("끝났습니다"),
        ]
    )

    class Orch(Orchestrator):
        def _builtin_specs(self):
            return (_spec("echo"),)

    async def echo(args):
        return {"ok": True}

    orch = Orch(
        resolve_provider=lambda: provider,
        config_store=c.config_store,
        learning=c.learning,
        connectors=c.connectors,
        plugins=c.plugins,
        approvals=c.approvals,
        transcript=c.sessions,
        builtins={"echo": echo},
    )
    s = c.sessions.create(model="fake")
    events = [e async for e in orch.run_turn(s.id, "폴더 분석해서 메일 보내줘")]

    plan_events = [e for e in events if e.type == EventType.PLAN_UPDATED]
    assert [e.data["revision"] for e in plan_events] == [1, 2]
    assert events[-1].type == EventType.DONE

    history = c.sessions.history(s.id)
    snapshots = [m.metadata["plan"] for m in history if "plan" in m.metadata]
    assert [p["revision"] for p in snapshots] == [1, 2]  # 생성본 + 갱신본이 영속화됐다
    assert snapshots[1]["steps"][0]["status"] == "done"
    c.shutdown()
