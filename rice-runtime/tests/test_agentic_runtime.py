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
from rice_runtime.core.config import ContextConfig, LearningConfig
from rice_runtime.core.types import Message, ToolCall, ToolSpec, TurnContext
from rice_runtime.server.approval import ApprovalRegistry


class _NoopService:
    async def invoke(self, call):
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
    assert "Compressed Conversation Context" in rendered.system_prompt
    assert "[REDACTED]" in rendered.system_prompt
    assert rendered.history[-1].content == "recent question"
    assert rendered.history[0].role != "tool"  # no orphaned provider tool_result prefix


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

    assert [m.content for m in rendered.history] == ["older but still useful", "latest"]
    assert "Compressed Conversation Context" in rendered.system_prompt


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

    assert [m.role for m in rendered.history] == ["user"]
    assert "tool result call_1" not in rendered.system_prompt


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

    async def stream(self, *, system, messages, tools=(), temperature=0.7):
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

    async def stream(self, *, system, messages, tools=(), temperature=0.7):
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
        learning_config=LearningConfig(approval_timeout_seconds=1),
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
