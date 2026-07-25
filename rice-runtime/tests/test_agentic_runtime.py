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
from rice_runtime.core.types import ApprovalResponse, Message, ToolCall, ToolSpec, TurnContext
from rice_runtime.server.approval import ApprovalRegistry


class _NoopService:
    async def invoke(self, call, owner_id=None):
        raise AssertionError(f"unexpected invoke: {call}")


class _AutoApproveGate:
    def __init__(self) -> None:
        self.requests = []

    async def request(self, session_id, request, timeout_s):
        self.requests.append((session_id, request))
        return ApprovalResponse(approval_id=request.approval_id, decision="approve")

    def cancel_session(self, session_id):
        return None


def _spec(name: str, *, requires_approval: bool = False) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} test tool",
        parameters={"type": "object", "properties": {}},
        source="builtin",
        requires_approval=requires_approval,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expects_prompt"),
    [("ask", True), ("approve_for_me", False), ("full_access", False)],
)
async def test_approval_modes_for_routine_workspace_write(mode, expects_prompt):
    gate = _AutoApproveGate()
    executed = []

    async def write_file(args):
        executed.append(args)
        return {"written": True}

    dispatcher = ToolDispatcher(
        connectors=_NoopService(),
        plugins=_NoopService(),
        approvals=gate,
        approval_timeout_s=1,
        builtins={"write_file": write_file},
        approval_mode=mode,
    )
    call = ToolCall(tool_name="write_file", arguments={"path": "ok.txt"}, id="write_1")
    events = [event async for event in dispatcher.dispatch("s1", call, _spec("write_file", requires_approval=True))]

    assert ("tool_approval_required" in [event.type.value for event in events]) is expects_prompt
    assert bool(gate.requests) is expects_prompt
    assert executed == [{"path": "ok.txt"}]


@pytest.mark.asyncio
async def test_approve_for_me_still_prompts_for_potentially_unsafe_terminal():
    gate = _AutoApproveGate()

    async def terminal(args):
        return {"ok": True}

    dispatcher = ToolDispatcher(
        connectors=_NoopService(),
        plugins=_NoopService(),
        approvals=gate,
        approval_timeout_s=1,
        builtins={"terminal": terminal},
        approval_mode="approve_for_me",
    )
    call = ToolCall(tool_name="terminal", arguments={"command": "rm -rf build"}, id="shell_1")
    events = [event async for event in dispatcher.dispatch("s1", call, _spec("terminal", requires_approval=True))]

    assert "tool_approval_required" in [event.type.value for event in events]
    assert len(gate.requests) == 1


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


def test_context_manager_summarizes_wire_repair_drops():
    """wire 수리로 떨어질 메시지도 요약에 실린다(2026-07-25 방침 전환).

    이전 방침은 "생략과 삭제는 다른 사건"이라 수리 삭제분을 요약에서 제외했다.
    그 결과가 조용한 유실이다: greedy 예산 선택이 retained 로 담은 메시지를 수리가
    나중에 지우면, 그 내용은 **요약에도 없고 wire 에도 없다** — 모델이 도구 관찰
    자체를 통째로 잊는다. 지금은 `_build_checkpoint`가 수리를 미리 돌려 삭제될
    메시지를 생략 집합으로 넘기므로 최소한 요약에는 남는다. 감사(stats) 는 그대로
    유지된다 — 무엇이 왜 사라졌는지는 여전히 ledger 로 추적 가능해야 한다."""
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
        # loop.py `_tool_message` 가 싣는 실제 metadata 모양
        Message(role="tool", content="small result",
                metadata={"tool_call_id": "call_1", "tool_name": "lookup", "ok": True}),
        Message(role="user", content="latest"),
    )

    rendered = ConversationContextManager(cfg).render("", messages)

    real = [m for m in rendered.history if m.metadata.get("synthetic") != "context_summary"]
    assert [m.role for m in real] == ["user"]  # wire 상 결과는 이전과 동일
    summary = "".join(
        m.content for m in rendered.history if m.metadata.get("synthetic") == "context_summary"
    )
    assert "small result" in summary  # 더 이상 조용히 사라지지 않는다
    # 요약 머리말은 모델이 읽는 문장이다 — 도구 이름/성패를 쓰고, 프로바이더 내부
    # call id 는 절대 노출하지 않는다(소형 모델이 없는 id 를 되짚으려 든다).
    assert "lookup result ok" in summary
    assert "call_1" not in summary
    # 수리가 렌더 시점에 또 지울 것은 남아있지 않다 — 체크포인트가 이미 흡수했다.
    assert rendered.stats.dropped_message_ids == ()


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


@pytest.mark.asyncio
async def test_tool_scheduler_abort_preserves_completed_tool_result():
    """abort 직전에 이미 완료돼 큐에 실린 결과는 "tool cancelled"로 덮이지 않는다 —
    부작용이 이미 난 도구(파일 쓰기 등)를 취소로 기록하면 다음 턴의 모델이 실제
    상태를 오판한다(적대 리뷰 2026-07-19 C2)."""
    from rice_runtime.core.types import ToolResult, ev_tool_call

    class MixedDispatcher:
        async def dispatch(self, session_id, call, spec):
            if call.id == "slow":
                while True:
                    await asyncio.sleep(1)
            yield ev_tool_call(call)

        async def result_for(self, call):
            return ToolResult(call.id, ok=True, result={"done": True})

    scheduler = ToolScheduler(MixedDispatcher())  # type: ignore[arg-type]
    batch = ToolBatchResult()
    requests = (
        ScheduledToolRequest(ToolCall(tool_name="fast", arguments={}, id="fast"), _spec("fast")),
        ScheduledToolRequest(ToolCall(tool_name="slow", arguments={}, id="slow"), _spec("slow")),
    )
    abort = asyncio.Event()
    gen = scheduler.schedule("s1", requests, batch, abort)
    first = await gen.__anext__()  # fast의 tool_call 이벤트 — 결과는 아직 큐에 있다
    assert first.type.value == "tool_call"
    await asyncio.sleep(0.05)  # 제너레이터가 멈춘 사이 fast 워커가 결과를 큐에 넣는다
    abort.set()
    async for _ in gen:
        pass

    assert batch.results["fast"].ok is True  # 완료된 결과가 유실되지 않았다
    assert batch.results["slow"].error == "tool cancelled"


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


class _FailingToolProvider:
    """1라운드에 실패할 도구 + 미지의 도구를 부르고, 2라운드에 답한다."""

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self):
        return ProviderCapabilities(native_tool_calling=True)

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
        self.calls += 1
        if self.calls == 1:
            yield ProviderChunk(kind="tool_call", tool_call=ToolCall(tool_name="boom", arguments={}, id="c_boom"))
            yield ProviderChunk(kind="tool_call", tool_call=ToolCall(tool_name="nope", arguments={}, id="c_nope"))
            yield ProviderChunk(kind="done", stop_reason="tool_use")
            return
        yield ProviderChunk(kind="text", text="final")
        yield ProviderChunk(kind="done", stop_reason="end_turn")


@pytest.mark.asyncio
async def test_tool_result_success_is_persisted_structurally_not_only_as_error_prefix():
    """성패의 정본은 metadata["ok"]다 — content의 "ERROR: " 접두사를 클라이언트가
    sniffing 하게 두면 리로드가 실패한 도구를 성공 카드로 그린다(2026-07-15 실측:
    chat_1.png, create_presentation 1회차가 완료로 표시됨)."""

    async def boom(args):
        raise RuntimeError("invalid deck spec")

    async def fine(args):
        return {"path": "deck.pptx"}

    dispatcher = ToolDispatcher(
        connectors=_NoopService(),
        plugins=_NoopService(),
        approvals=ApprovalRegistry(),
        approval_timeout_s=1,
        builtins={"boom": boom, "fine": fine},
    )
    ctx = TurnContext(
        session_id="s1",
        system_prompt="sys",
        history=(Message(role="user", content="go"),),
        tools=(_spec("boom"), _spec("fine")),
        max_iterations=3,
    )
    result = LoopResult()
    _ = [
        e
        async for e in AgentLoop(provider=_FailingToolProvider(), dispatcher=dispatcher).run(
            ctx, "s1", result, asyncio.Event()
        )
    ]

    by_id = {m.metadata["tool_call_id"]: m for m in result.history if m.role == "tool"}
    # 디스패치 실패
    assert by_id["c_boom"].metadata["ok"] is False
    assert by_id["c_boom"].content.startswith("ERROR: ")
    # 미지의 도구도 같은 계약을 따른다(예전엔 tool_call_id 외 메타데이터가 없었다)
    assert by_id["c_nope"].metadata["ok"] is False
    assert by_id["c_nope"].metadata["tool_name"] == "nope"


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
