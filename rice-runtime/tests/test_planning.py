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
    async def invoke(self, call, owner_id=None):
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
    # think=False + 하드코딩 breadcrumb 금지(2026-07-07) → 이벤트는 흐르지 않는다.
    # 플래너 호출 동안의 진행 표시는 프론트엔드(생각중 애니메이션)가 담당한다.
    assert events == []
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
    assert events == []  # fail-soft — 하드코딩 breadcrumb 도 없다


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
            [  # round 1: report s1+s2 done + run a real tool (plan enforcement:
                # 미종결 단계가 남으면 final answer가 차단되므로 정직하게 종결한다)
                ProviderChunk(
                    kind="tool_call",
                    tool_call=ToolCall(
                        tool_name="update_plan",
                        arguments={
                            "updates": [
                                {"step_id": "s1", "status": "done", "note": "echoed"},
                                {"step_id": "s2", "status": "done"},
                            ]
                        },
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

    # 정적 작업 지침은 시스템에 1회, 살아있는 계획 상태는 렌더된 히스토리의
    # **마지막 메시지**(ephemeral tail)에 실린다 — 시스템 프롬프트는 턴 내 불변
    # (프리픽스 캐시 불변식: Ollama KV·Claude cache_control이 반복 간 유지된다).
    assert "## Active Plan mode" in provider.calls[1]["system"]
    assert provider.calls[1]["system"] == provider.calls[2]["system"]
    tail1 = provider.calls[1]["messages"][-1]
    tail2 = provider.calls[2]["messages"][-1]
    assert tail1.metadata.get("synthetic") == "plan_status"
    # 첫 스텝은 런타임이 in_progress로 만든다(실행 시작은 런타임이 아는 사실)
    assert "[>] s1. step one" in tail1.content
    assert "[v] s1. step one" in tail2.content  # ReAct: 관찰이 반영된 상태
    # ephemeral tail은 durable 히스토리에 남지 않는다
    assert not any(m.metadata.get("synthetic") == "plan_status" for m in result.history)

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
    # 계획이 없으면 update_plan이 아니라 start_plan(escalation trigger)을 광고한다.
    # 계획 블록이 없으니 시스템 프롬프트는 그대로다.
    assert [t.name for t in provider.calls[1]["tools"]] == ["echo", "start_plan"]
    assert provider.calls[1]["system"] == "sys"
    assert result.final_message == "바로 답변"


@pytest.mark.asyncio
async def test_web_tool_result_is_marked_as_external_content():
    provider = _ScriptedProvider(
        [
            [ProviderChunk(
                kind="tool_call",
                tool_call=ToolCall(tool_name="fetch_url", arguments={}, id="call_web"),
            )],
            _text("웹 내용을 정리했습니다."),
        ]
    )

    async def fetch_url(_args):
        return {"text": "untrusted page content"}

    spec = ToolSpec(
        name="fetch_url",
        description="test web tool",
        parameters={"type": "object", "properties": {}},
        source="builtin",  # 실행 라우팅은 builtin
        is_web_tool=True,  # 데이터 provenance는 external web
    )
    loop = AgentLoop(provider=provider, dispatcher=_dispatcher({"fetch_url": fetch_url}))
    result = LoopResult()
    _events = [
        ev async for ev in loop.run(_ctx((spec,)), "s1", result, asyncio.Event())
    ]

    tool_message = next(m for m in result.history if m.metadata.get("tool_name") == "fetch_url")
    assert tool_message.metadata["tool_source"] == "web"


@pytest.mark.asyncio
async def test_loop_escalates_to_plan_via_start_plan():
    """#2 lazy escalation: 단순으로 판정돼 ReAct로 출발하지만, 실행 중 복잡도가
    드러나면 모델이 start_plan으로 그 자리에서 계획으로 승격한다."""
    provider = _ScriptedProvider(
        [
            _text({"plan_needed": False}),  # 플래너: 단순으로 판정 → ReAct 출발
            [  # round 1 (ReAct): 복잡도(분해 너비) 발견 → start_plan 승격
                ProviderChunk(
                    kind="tool_call",
                    tool_call=ToolCall(
                        tool_name="start_plan",
                        arguments={"goal": "두 갈래 작업", "steps": [{"title": "수집"}, {"title": "분석"}]},
                        id="call_sp1",
                    ),
                ),
            ],
            [  # round 2: 계획 종결 보고 (enforcement: 미종결 final 차단)
                ProviderChunk(
                    kind="tool_call",
                    tool_call=ToolCall(
                        tool_name="update_plan",
                        arguments={
                            "updates": [
                                {"step_id": "s1", "status": "done"},
                                {"step_id": "s2", "status": "done"},
                            ]
                        },
                        id="call_up2",
                    ),
                ),
            ],
            _text("최종 답변"),  # round 3: 계획 모드에서 마무리
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

    # 승격 순간 계획 스냅샷(rev1) + 종결 보고(rev2)가 흐른다
    plan_events = [e for e in events if e.type == EventType.PLAN_UPDATED]
    assert [e.data["revision"] for e in plan_events] == [1, 2]
    assert [s["title"] for s in plan_events[0].data["steps"]] == ["수집", "분석"]

    # round 1(ReAct)엔 start_plan이, 승격 후 round 2엔 update_plan이 광고된다(상호 배타)
    r1_tools = [t.name for t in provider.calls[1]["tools"]]
    r2_tools = [t.name for t in provider.calls[2]["tools"]]
    assert "start_plan" in r1_tools and "update_plan" not in r1_tools
    assert "update_plan" in r2_tools and "start_plan" not in r2_tools

    # 승격 후에야 시스템 프롬프트에 살아있는 계획이 렌더된다
    assert "## Active Plan" not in provider.calls[1]["system"]
    assert "## Active Plan" in provider.calls[2]["system"]

    # start_plan은 감사 가능(tool_call)하고, 스냅샷이 tool 메시지 metadata로 영속화된다
    assert "start_plan" in [e.data["tool_name"] for e in events if e.type == EventType.TOOL_CALL]
    sp_msg = next(m for m in result.history if m.metadata.get("tool_name") == "start_plan")
    assert sp_msg.metadata["plan"]["revision"] == 1
    assert result.final_message == "최종 답변"


@pytest.mark.asyncio
async def test_loop_start_plan_rejects_single_step_and_stays_react():
    """반복 깊이(단일 산출물 재작업)는 계획이 아니다 — 1단계 승격 시도는 '그냥 계속
    일하라' 넛지로 반려되고 턴은 순수 ReAct로 남는다(over-planning 방지의 코드 강제)."""
    provider = _ScriptedProvider(
        [
            _text({"plan_needed": False}),  # 플래너: 단순
            [  # round 1: 깊이(1단계)로 잘못 승격 시도 → 반려
                ProviderChunk(
                    kind="tool_call",
                    tool_call=ToolCall(
                        tool_name="start_plan",
                        arguments={"goal": "한 산출물", "steps": [{"title": "다시 만들기"}]},
                        id="call_sp0",
                    ),
                ),
            ],
            _text("계속해서 마무리"),  # round 2: 반려 후 계속
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

    # 계획은 서지 않는다
    assert not [e for e in events if e.type == EventType.PLAN_UPDATED]
    # 반려는 ok=False tool_result로 감사되고, 아직 계획이 없으니 여전히 start_plan을 광고
    assert any(e.type == EventType.TOOL_RESULT and e.data.get("ok") is False for e in events)
    assert "start_plan" in [t.name for t in provider.calls[2]["tools"]]
    assert result.final_message == "계속해서 마무리"


@pytest.mark.asyncio
async def test_loop_drift_guard_forces_reassessment():
    provider = _ScriptedProvider(
        [
            _text(_PLAN_JSON),  # planner
            [ProviderChunk(kind="tool_call", tool_call=ToolCall(tool_name="echo", arguments={}, id="c1"))],
            [ProviderChunk(kind="tool_call", tool_call=ToolCall(tool_name="echo", arguments={}, id="c2"))],
            _text("끝"),  # 미종결 final 시도 → reconcile 넛지
            _text("끝"),  # 넛지도 무시 → 런타임이 auto-reconcile 후 final 수용
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
    # — 경고는 시스템이 아니라 ephemeral tail(마지막 메시지)에 실린다(캐시 불변식).
    assert "REASSESS NOW" not in provider.calls[2]["messages"][-1].content
    assert "REASSESS NOW" in provider.calls[3]["messages"][-1].content
    assert "REASSESS NOW" not in provider.calls[3]["system"]


@pytest.mark.asyncio
async def test_loop_blocks_final_answer_while_plan_unfinished():
    """P1 plan enforcement: 미종결 단계가 남은 채 tool call 없이 답하면 final 로
    받지 않고 reconcile 을 강제한다. 이미 스트리밍된 답이 canonical final — 모델의
    update_plan 종결을 소비한 뒤 런타임이 그 답으로 턴을 닫고, 답을 재질의하지
    않는다(중복 재생성 차단)."""
    provider = _ScriptedProvider(
        [
            _text(_PLAN_JSON),  # planner
            _text("다 됐습니다"),  # 미종결 상태의 성급한 final 시도 → 넛지
            [  # 넛지 후: 계획 종결 보고 — 여기서 턴이 끝난다(추가 라운드 없음)
                ProviderChunk(
                    kind="tool_call",
                    tool_call=ToolCall(
                        tool_name="update_plan",
                        arguments={
                            "updates": [
                                {"step_id": "s1", "status": "done"},
                                {"step_id": "s2", "status": "skipped", "note": "불필요"},
                            ]
                        },
                        id="call_rec",
                    ),
                ),
            ],
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

    # 넛지가 synthetic user 메시지로 감사 가능하게 남는다
    assert any(m.metadata.get("synthetic") == "plan_reconcile" for m in result.history)
    plan_events = [e for e in events if e.type == EventType.PLAN_UPDATED]
    assert [e.data["revision"] for e in plan_events] == [1, 2]
    assert plan_events[-1].data["status"] == "completed"
    # 정리 라운드 뒤 재질의가 없다: planner + 답 + update_plan = 3회
    assert len(provider.calls) == 3
    assert result.final_message == "다 됐습니다"


@pytest.mark.asyncio
async def test_loop_auto_reconciles_abandoned_plan():
    """넛지까지 무시되면 런타임이 미종결 단계를 skipped 로 닫는다 — 계획 카드가
    영원히 열린 채 남지 않는 것은 프롬프트 희망사항이 아니라 런타임 불변식.
    이때 최종 답은 이미 확정된 첫 답("끝1")이고, 넛지에 대한 재작성("끝2")은
    사용자에게 흘러가지 않는다."""
    provider = _ScriptedProvider(
        [
            _text(_PLAN_JSON),  # planner
            _text("끝1"),  # 미종결 final 시도 → 넛지
            _text("끝2"),  # 넛지도 무시하고 답 재작성 → 억제, auto-reconcile
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

    plan_events = [e for e in events if e.type == EventType.PLAN_UPDATED]
    final_plan = plan_events[-1].data
    assert final_plan["status"] == "completed"
    assert all(s["status"] == "skipped" for s in final_plan["steps"])  # 성공 위조 금지
    assert result.final_message == "끝1"
    # 재작성 본문은 message 이벤트로 새 나가지 않고 metadata로만 남는다
    msg_events = [e for e in events if e.type == EventType.MESSAGE]
    assert "끝2" not in "".join(e.data.get("delta", "") + str(e.data.get("content", "")) for e in msg_events)
    last_assistant = [m for m in result.history if m.role == "assistant"][-1]
    assert last_assistant.metadata["suppressed_text"] == "끝2"


@pytest.mark.asyncio
async def test_loop_streams_no_synthetic_text_on_silent_tool_rounds():
    """사용자에게 보이는 서사는 모델의 몫(NARRATE 프롬프트) — 모델이 서사 없이
    tool call 만 해도 런타임이 하드코딩 문장을 합성해 스트림하지 않는다
    (2026-07-07 결정: 침묵 라운드의 진행 표시는 프론트엔드가 담당)."""
    provider = _ScriptedProvider(
        [
            _text({"plan_needed": False}),  # planner: 단순
            [ProviderChunk(kind="tool_call", tool_call=ToolCall(tool_name="echo", arguments={}, id="c1"))],
            _text("답변"),
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
    result = LoopResult()
    events = [ev async for ev in loop.run(_ctx((_spec("echo"),)), "s1", result, asyncio.Event())]

    deltas = [e.data.get("delta") for e in events if e.type == EventType.MESSAGE and e.data.get("delta")]
    assert deltas == ["답변"]  # 모델이 직접 쓴 텍스트만 — 합성 preamble 없음
    assert result.final_message == "답변"
    assert not any(m.metadata.get("synthetic_preamble") for m in result.history)


def test_completed_plan_nudges_skill_capture():
    state = PlanState(
        TurnPlan(
            goal="g",
            steps=(PlanStep(id="s1", title="a", status="done"), PlanStep(id="s2", title="b", status="done")),
            status="completed",
        )
    )
    loop = AgentLoop(provider=None, dispatcher=None, agent_config=AgentConfig())  # type: ignore[arg-type]
    # 승격 유도는 동적 상태의 일부 — ephemeral tail 메시지에 실린다(시스템은 불변).
    with_nudge = loop._plan_status_message(state, tool_calls_made=5).content
    without_nudge = loop._plan_status_message(state, tool_calls_made=1).content
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
                        arguments={
                            "updates": [
                                {"step_id": "s1", "status": "done"},
                                {"step_id": "s2", "status": "done"},
                            ]
                        },
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
