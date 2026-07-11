"""Agentic runtime upgrades: context rendering, tool scheduling, sub-agents."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from rice_runtime.agent.context_manager import ConversationContextManager
from rice_runtime.agent.loop import AgentLoop, LoopResult
from rice_runtime.agent.providers.interfaces import ProviderCapabilities, ProviderChunk
from rice_runtime.agent.subagents import SubAgentRunner
from rice_runtime.agent.tool_dispatch import ToolDispatcher
from rice_runtime.agent.tool_scheduler import ScheduledToolRequest, ToolBatchResult, ToolScheduler
from rice_runtime.core.config import ContextConfig
from rice_runtime.core.types import Message, ToolCall, ToolSpec, TurnContext
from rice_runtime.server.approval import ApprovalRegistry


class _NoopService:
    async def invoke(self, call, owner_id=None):
        raise AssertionError(f"unexpected invoke: {call}")


def _spec(name: str, *, requires_approval: bool = False) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} test tool",
        parameters={"type": "object", "properties": {}},
        source="builtin",
        requires_approval=requires_approval,
    )


def test_context_manager_masks_distills_and_compresses():
    cfg = ContextConfig(
        max_tokens=180,
        retained_tokens=100,
        recent_messages=2,
        summary_tokens=80,
        tool_output_threshold_tokens=20,
        tool_output_retained_tokens=8,
        message_threshold_tokens=80,
        message_retained_tokens=20,
    )
    call = ToolCall(tool_name="lookup", arguments={}, id="call_1")
    messages = (
        Message(role="user", content="old token=sk-abc12345678901234567890 " + ("old " * 400)),
        Message(role="assistant", content="", tool_calls=(call,)),
        Message(role="tool", content="tool-output " + ("x" * 800), metadata={"tool_call_id": "call_1"}),
        Message(role="user", content="recent question"),
    )

    rendered = ConversationContextManager(cfg).render("sys", messages)

    assert rendered.stats.did_apply_management is True
    assert rendered.stats.omitted_messages > 0
    assert rendered.stats.distilled_tool_outputs >= 1
    # 압축 요약은 시스템이 아니라 히스토리 선두의 synthetic 메시지다(프리픽스
    # 캐시 불변식) — 시스템 프롬프트는 렌더가 절대 건드리지 않는다.
    assert rendered.system_prompt == "sys"
    summary = rendered.history[0]
    assert summary.metadata.get("synthetic") == "context_summary"
    assert "Compressed Conversation Context" in summary.content
    assert "[REDACTED]" in summary.content
    assert rendered.history[-1].content == "recent question"
    assert all(m.role != "tool" for m in rendered.history[:1])  # no orphaned tool_result prefix


def test_context_manager_skips_oversized_middle_message_without_breaking_scan():
    cfg = ContextConfig(
        max_tokens=120,
        retained_tokens=60,
        recent_messages=1,
        summary_tokens=80,
        message_threshold_tokens=10_000,
    )
    messages = (
        Message(role="user", content="older but still useful"),
        Message(role="user", content="H" * 2_000),
        Message(role="user", content="latest"),
    )

    rendered = ConversationContextManager(cfg).render("sys", messages)

    real = [m for m in rendered.history if m.metadata.get("synthetic") != "context_summary"]
    assert [m.content for m in real] == ["older but still useful", "latest"]
    assert "Compressed Conversation Context" in rendered.history[0].content


def test_context_manager_does_not_summarize_wire_repair_drops():
    cfg = ContextConfig(
        max_tokens=25,
        retained_tokens=15,
        recent_messages=1,
        summary_tokens=80,
        message_threshold_tokens=10_000,
        tool_output_threshold_tokens=10_000,
    )
    call = ToolCall(tool_name="lookup", arguments={}, id="call_1")
    messages = (
        Message(role="assistant", content="", tool_calls=(call,)),
        Message(role="tool", content="small result", metadata={"tool_call_id": "call_1"}),
        Message(role="user", content="latest"),
    )

    rendered = ConversationContextManager(cfg).render("", messages)

    real = [m for m in rendered.history if m.metadata.get("synthetic") != "context_summary"]
    assert [m.role for m in real] == ["user"]
    # wire 수리로 떨어진 도구 결과는 요약에 실리지 않는다(생략과 삭제는 다른 사건).
    assert all("tool result call_1" not in m.content for m in rendered.history)
    assert "tool result call_1" not in rendered.system_prompt


# ── 프리픽스 캐시 불변식 (context_manager ★, 2026-07-11) ─────────────────────


def _filler(i: int) -> Message:
    return Message(role="user", content=f"filler message {i} " + "pad " * 40)


_PREFIX_CFG = ContextConfig(
    max_tokens=600,
    retained_tokens=200,
    recent_messages=2,
    summary_tokens=80,
    message_threshold_tokens=10_000,
    tool_output_threshold_tokens=10_000,
)


def test_render_prefix_stable_across_appended_rounds():
    from rice_runtime.agent.context_manager import ContextRenderState

    state = ContextRenderState()
    base = [_filler(i) for i in range(20)]  # ≈980tok > 600 → 압축 발동
    r1 = ConversationContextManager(_PREFIX_CFG, state=state).render("sys", tuple(base))
    assert r1.history[0].metadata.get("synthetic") == "context_summary"

    # 다음 라운드/턴: 같은 세션 상태, 새 매니저(턴 경계 시뮬레이션) + 라운드 append
    call = ToolCall(tool_name="lookup", arguments={}, id="call_n")
    appended = base + [
        Message(role="assistant", content="", tool_calls=(call,)),
        Message(role="tool", content="ok", metadata={"tool_call_id": "call_n"}),
    ]
    r2 = ConversationContextManager(_PREFIX_CFG, state=state).render("sys", tuple(appended))

    # 프리픽스 불변: r2는 r1의 렌더 결과 그대로로 시작한다(id·본문 동일) — 이게
    # Ollama KV/Claude prompt cache가 히트하는 조건이다. 요약 재계산 금지.
    prefix = r2.history[: len(r1.history)]
    assert [(m.id, m.content) for m in prefix] == [(m.id, m.content) for m in r1.history]
    assert [m.id for m in r2.history[len(r1.history) :]] == [m.id for m in appended[-2:]]


def test_rendered_form_frozen_when_recency_window_slides():
    from rice_runtime.agent.context_manager import ContextRenderState

    cfg = ContextConfig(
        max_tokens=100_000,
        recent_messages=2,
        tool_output_threshold_tokens=50,
        tool_output_retained_tokens=30,
    )
    state = ContextRenderState()
    mid = "y" * 400  # 임계(50tok) 초과, 최근 3배 완화(150tok) 안
    call = ToolCall(tool_name="lookup", arguments={}, id="c1")
    asst = Message(role="assistant", content="", tool_calls=(call,))
    tool_msg = Message(role="tool", content=mid, metadata={"tool_call_id": "c1"})
    tail = Message(role="user", content="check")
    r1 = ConversationContextManager(cfg, state=state).render("sys", (asst, tool_msg, tail))
    assert tool_msg.id not in r1.stats.distilled_message_ids  # 최근 → 원문 유지

    # recency 창 밖으로 밀려나도 동결된 원문 형태가 유지된다(창 슬라이딩이
    # 라운드마다 프리픽스를 깨던 요인 제거 — hysteresis).
    more = (asst, tool_msg, tail, Message(role="user", content="a"), Message(role="user", content="b"))
    r2 = ConversationContextManager(cfg, state=state).render("sys", more)
    assert tool_msg.id not in r2.stats.distilled_message_ids
    assert r2.history[1].content == mid

    # 대조군: 상태를 공유하지 않는 새 세션이면 같은 입력이 증류된다
    r3 = ConversationContextManager(cfg).render("sys", more)
    assert tool_msg.id in r3.stats.distilled_message_ids


def test_checkpoint_refreshes_only_when_budget_reexceeded():
    from rice_runtime.agent.context_manager import ContextRenderState

    state = ContextRenderState()
    base = [_filler(i) for i in range(20)]
    cm = ConversationContextManager(_PREFIX_CFG, state=state)
    r1 = cm.render("sys", tuple(base))
    summary1 = r1.history[0]
    assert summary1.metadata.get("synthetic") == "context_summary"

    # 예산 안의 append는 동결 스냅샷 유지 — 요약 id·본문이 그대로다
    grown = base + [Message(role="user", content="small follow-up")]
    r2 = cm.render("sys", tuple(grown))
    assert (r2.history[0].id, r2.history[0].content) == (summary1.id, summary1.content)

    # 예산을 다시 넘기면 그때 딱 한 번 새 체크포인트로 갈아탄다(새 요약 id)
    grown2 = grown + [_filler(100 + i) for i in range(10)]
    r3 = cm.render("sys", tuple(grown2))
    assert r3.history[0].metadata.get("synthetic") == "context_summary"
    assert r3.history[0].id != summary1.id


@pytest.mark.asyncio
async def test_tool_scheduler_parallelizes_safe_calls():
    started: list[str] = []

    async def slow_a(args):
        started.append("a")
        await asyncio.sleep(0.15)
        return {"a": True}

    async def slow_b(args):
        started.append("b")
        await asyncio.sleep(0.15)
        return {"b": True}

    dispatcher = ToolDispatcher(
        connectors=_NoopService(),
        plugins=_NoopService(),
        approvals=ApprovalRegistry(),
        approval_timeout_s=1,
        builtins={"slow_a": slow_a, "slow_b": slow_b},
    )
    scheduler = ToolScheduler(dispatcher)
    batch = ToolBatchResult()
    requests = (
        ScheduledToolRequest(ToolCall(tool_name="slow_a", arguments={}, id="a"), _spec("slow_a")),
        ScheduledToolRequest(ToolCall(tool_name="slow_b", arguments={}, id="b"), _spec("slow_b")),
    )

    begin = time.perf_counter()
    events = [event async for event in scheduler.schedule("s1", requests, batch, asyncio.Event())]
    elapsed = time.perf_counter() - begin

    assert elapsed < 0.25  # sequential would be roughly 0.30s
    assert set(started) == {"a", "b"}
    assert batch.results["a"].ok is True and batch.results["b"].ok is True
    assert [event.type.value for event in events].count("tool_call") == 2


@pytest.mark.asyncio
async def test_tool_scheduler_parallel_batch_observes_abort_while_waiting():
    class HangingDispatcher:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def dispatch(self, session_id, call, spec):
            self.started.set()
            while True:
                await asyncio.sleep(1)
                if False:
                    yield None

        async def result_for(self, call):
            return None

    dispatcher = HangingDispatcher()
    scheduler = ToolScheduler(dispatcher)
    batch = ToolBatchResult()
    requests = (
        ScheduledToolRequest(ToolCall(tool_name="slow_a", arguments={}, id="a"), _spec("slow_a")),
        ScheduledToolRequest(ToolCall(tool_name="slow_b", arguments={}, id="b"), _spec("slow_b")),
    )
    abort = asyncio.Event()

    async def consume():
        return [event async for event in scheduler.schedule("s1", requests, batch, abort)]

    task = asyncio.create_task(consume())
    await dispatcher.started.wait()
    abort.set()
    await asyncio.wait_for(task, timeout=0.25)

    assert batch.results["a"].error == "tool cancelled"
    assert batch.results["b"].error == "tool cancelled"


class _ToolThenFinalProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Message, ...]]] = []

    def capabilities(self):
        return ProviderCapabilities(native_tool_calling=True)

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
        self.calls.append((system, tuple(messages)))
        if len(self.calls) == 1:
            yield ProviderChunk(kind="tool_call", tool_call=ToolCall(tool_name="huge", arguments={}, id="call_huge"))
            yield ProviderChunk(kind="done", stop_reason="tool_use")
            return
        yield ProviderChunk(kind="text", text="final")
        yield ProviderChunk(kind="done", stop_reason="end_turn")


@pytest.mark.asyncio
async def test_agent_loop_renders_managed_context_without_mutating_transcript():
    provider = _ToolThenFinalProvider()

    async def huge(args):
        return {"blob": "x" * 2_000}

    dispatcher = ToolDispatcher(
        connectors=_NoopService(),
        plugins=_NoopService(),
        approvals=ApprovalRegistry(),
        approval_timeout_s=1,
        builtins={"huge": huge},
    )
    loop = AgentLoop(
        provider=provider,
        dispatcher=dispatcher,
        context_manager=ConversationContextManager(
            ContextConfig(
                max_tokens=10_000,
                tool_output_threshold_tokens=40,
                tool_output_retained_tokens=10,
            )
        ),
    )
    ctx = TurnContext(
        session_id="s1",
        system_prompt="sys",
        history=(Message(role="user", content="go"),),
        tools=(_spec("huge"),),
        max_iterations=3,
    )
    result = LoopResult()
    _ = [event async for event in loop.run(ctx, "s1", result, asyncio.Event())]

    assert len(provider.calls) == 2
    second_messages = provider.calls[1][1]
    rendered_tool = next(m for m in second_messages if m.role == "tool")
    assert "Context management distilled this tool output" in rendered_tool.content
    persisted_tool = next(m for m in result.history if m.role == "tool")
    assert "x" * 1_000 in persisted_tool.content  # durable transcript remains full


class _TextProvider:
    name = "fake"

    def __init__(self) -> None:
        self.messages = ()

    def capabilities(self):
        return ProviderCapabilities()

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
        self.messages = tuple(messages)
        yield ProviderChunk(kind="text", text="sub report")
        yield ProviderChunk(kind="done", stop_reason="end_turn")


class _Transcript:
    def __init__(self, messages):
        self._messages = tuple(messages)

    def append(self, session_id: str, message: Message) -> None:
        raise AssertionError("subagent runner should not mutate transcript")

    def history(self, session_id: str) -> tuple[Message, ...]:
        return self._messages


@pytest.mark.asyncio
async def test_subagent_runner_returns_structured_summary():
    provider = _TextProvider()
    runner = SubAgentRunner(
        resolve_provider=lambda: provider,
        transcript=_Transcript([
            Message(role="user", content="old context"),
            Message(role="user", content="new context"),
        ]),
        context_config=ContextConfig(subagent_history_messages=1),
    )

    result = await runner.run(session_id="s1", task="review the plan", context="facts")

    assert result.thread_id.startswith("sub_")
    assert result.summary == "sub report"
    assert result.to_dict()["task"] == "review the plan"
    prompt = provider.messages[0].content
    assert "new context" in prompt
    assert "old context" not in prompt


def test_delegate_subagent_builtin_is_registered():
    from rice_runtime.agent.builtins.subagent_tools import tools

    runner = MagicMock()
    spec, handler = tools(runner)[0]

    assert spec.name == "delegate_subagent"
    assert spec.source == "builtin"
    assert getattr(handler, "needs_session") is True
