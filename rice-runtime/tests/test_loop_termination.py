"""안전장치 상호작용 종료 보장 테스트 (2026-07-10).

루프에는 여러 방어 장치가 공존한다 — 빈 응답 넛지(×2), 계획 reconcile 넛지(×1),
서사 넛지(프로파일 상한), drift guard, 반복 상한. 이 테스트들은 장치들이 서로
꼬이는 최악의 모델 행동에서도 턴이 **유한하게, 결정론적으로** 끝남을 못박는다:

  1. 계획 활성 + 영원한 빈 응답  → 넛지 예산 소진 후 런타임이 계획을 강제 종결
  2. 빈 응답 ↔ 도구 호출 교대     → 넛지 카운터는 리셋되지 않고 소진된다
  3. 영원한 침묵 도구 호출        → 서사 넛지 상한 후 반복 상한이 최종 방벽
  4. 매 반복 transient 에러 + 침묵 도구 호출 → 재시도 예산이 반복마다 갱신돼도
     반복 상한이 총 provider 호출 수를 (상한 × (1+재시도))로 묶는다
  5. 답 완료 후 reconcile 넛지 → update_plan 정리 → 추가 LLM 라운드 없이 종료
     (이미 스트리밍된 답이 canonical final — 재질의로 인한 중복 답 재생성 차단)
  6. reconcile 넛지에 답을 재작성 → 재작성 본문은 억제되고 원래 답이 최종,
     계획은 런타임이 강제 종결
  7. reconcile 넛지에 실제 도구 호출 재개 → 억제했던 서사를 방출하고 정상 복귀,
     이후의 진짜 최종 답이 최종
  8. reconcile 넛지에 추론을 남김 → 억제 라운드의 추론은 라이브·리로드 양쪽에서
     보이지 않는다(런타임이 주입한 STOP을 사용자 탓으로 서술하는 독백 차단)

새 방어 장치를 추가할 때는 반드시 여기에 상호작용 시나리오를 추가할 것 —
"장치 하나가 더해질 때마다 종료 증명을 다시 한다"가 이 파일의 계약이다.
"""

from __future__ import annotations

import asyncio

import pytest

from rice_runtime.agent.loop import AgentLoop, LoopResult
from rice_runtime.agent.profiles import LOCAL_PROFILE
from rice_runtime.agent.tool_dispatch import ToolDispatcher
from rice_runtime.agent.providers.interfaces import ProviderCapabilities, ProviderChunk
from rice_runtime.core.types import Message, ToolCall, ToolSpec, TurnContext
from rice_runtime.server.approval import ApprovalRegistry


def _dispatcher() -> ToolDispatcher:
    async def echo(args):
        return {"ok": True}

    return ToolDispatcher(
        connectors=None,  # never reached — echo is builtin
        plugins=None,
        approvals=ApprovalRegistry(),
        approval_timeout_s=1,
        builtins={"echo": echo},
    )


def _ctx(max_iterations: int = 8) -> TurnContext:
    return TurnContext(
        session_id="s1",
        system_prompt="sys",
        history=(Message(role="user", content="go"),),
        tools=(ToolSpec(name="echo", description="d", parameters={"type": "object"}, source="builtin"),),
        max_iterations=max_iterations,
    )


class _CountingProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self):
        return ProviderCapabilities(native_tool_calling=True)


class _PlanThenForeverEmpty(_CountingProvider):
    """1라운드에 start_plan(2단계)으로 계획을 세운 뒤, 영원히 빈 응답만 낸다.

    빈 응답 넛지(×2)와 reconcile 넛지(×1)가 순차 소진되고, 그 뒤 런타임이
    미종결 단계를 skipped로 강제 종결해야 한다 — 넛지끼리 핑퐁하며 반복 상한까지
    돌면 실패."""

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
        self.calls += 1
        if self.calls == 1:
            yield ProviderChunk(
                kind="tool_call",
                tool_call=ToolCall(
                    tool_name="start_plan",
                    arguments={"goal": "do the thing", "steps": [{"title": "a"}, {"title": "b"}]},
                    id="c1",
                ),
            )
            yield ProviderChunk(kind="done", stop_reason="tool_use")
            return
        yield ProviderChunk(kind="done", stop_reason="end_turn")  # 영원한 빈 응답


@pytest.mark.asyncio
async def test_plan_plus_forever_empty_terminates_via_forced_reconcile():
    provider = _PlanThenForeverEmpty()
    result = LoopResult()
    events = [
        ev
        async for ev in AgentLoop(provider=provider, dispatcher=_dispatcher()).run(
            _ctx(), "s1", result, asyncio.Event()
        )
    ]

    # 유한 종료: start_plan 1 + 빈 넛지 2 + reconcile 넛지 1 + 강제 종결 1 = 5회
    assert provider.calls == 5
    # 반복 상한 에러 없이 정상 경로로 끝났다
    assert not any(ev.type.value == "error" for ev in events)
    # 마지막 plan_updated 스냅샷은 전 단계 종결(skipped) 상태다
    plan_events = [ev for ev in events if ev.type.value == "plan_updated"]
    final_steps = plan_events[-1].data["steps"]
    assert all(s["status"] in ("done", "failed", "skipped") for s in final_steps)
    assert plan_events[-1].data["status"] == "completed"


class _EmptyToolPingPong(_CountingProvider):
    """빈 응답 ↔ 도구 호출을 교대 — 빈 응답 넛지 카운터가 라운드 사이에 리셋되지
    않아야 유한하다(리셋되면 이 패턴은 반복 상한까지 돈다)."""

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
        self.calls += 1
        if self.calls % 2 == 1:
            yield ProviderChunk(
                kind="tool_call",
                tool_call=ToolCall(tool_name="echo", arguments={}, id=f"c{self.calls}"),
            )
            yield ProviderChunk(kind="done", stop_reason="tool_use")
            return
        yield ProviderChunk(kind="done", stop_reason="end_turn")  # 빈 응답


@pytest.mark.asyncio
async def test_empty_tool_pingpong_exhausts_nudges_then_ends():
    provider = _EmptyToolPingPong()
    result = LoopResult()
    events = [
        ev
        async for ev in AgentLoop(provider=provider, dispatcher=_dispatcher()).run(
            _ctx(), "s1", result, asyncio.Event()
        )
    ]

    # 도구1·빈1(넛지1)·도구2·빈2(넛지2)·도구3·빈3(예산 소진 → 빈 최종답 확정) = 6회
    assert provider.calls == 6
    assert result.final_message == ""
    assert not any(ev.type.value == "error" for ev in events)
    nudges = [m for m in result.history if m.metadata.get("synthetic") == "empty_nudge"]
    assert len(nudges) == 2  # _EMPTY_RESPONSE_RETRIES 상한 그대로


class _ForeverSilentToolCaller(_CountingProvider):
    """본문 없이 영원히 도구만 부른다 — 서사 넛지(로컬 프로파일, 상한 2)가 전부
    소진된 뒤에는 반복 상한이 최종 방벽으로 턴을 끝내야 한다."""

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
        self.calls += 1
        yield ProviderChunk(
            kind="tool_call",
            tool_call=ToolCall(tool_name="echo", arguments={}, id=f"c{self.calls}"),
        )
        yield ProviderChunk(kind="done", stop_reason="tool_use")


@pytest.mark.asyncio
async def test_forever_silent_tool_calls_hit_iteration_cap_exactly_once():
    provider = _ForeverSilentToolCaller()
    result = LoopResult()
    events = [
        ev
        async for ev in AgentLoop(
            provider=provider, dispatcher=_dispatcher(), profile=LOCAL_PROFILE
        ).run(_ctx(max_iterations=6), "s1", result, asyncio.Event())
    ]

    assert provider.calls == 6  # 상한에서 정확히 멈춘다 — 그 이상 절대 없음
    errors = [ev for ev in events if ev.type.value == "error"]
    assert len(errors) == 1 and errors[0].data["error_code"] == "max_iterations"
    # 서사 넛지는 프로파일 상한(2)까지만 — 넛지 자체가 폭주하지 않는다
    nudges = [m for m in result.history if m.metadata.get("synthetic") == "narration_nudge"]
    assert len(nudges) == LOCAL_PROFILE.narration_nudge_max


class _FlakyThenSilentToolCaller(_CountingProvider):
    """매 반복: 첫 스트림 진입은 transient 에러(과부하), 재시도에선 침묵 도구 호출.

    transient 재시도(반복당 예산 — loop._TRANSIENT_RETRY_DELAYS)와 서사 넛지,
    반복 상한이 겹치는 최악의 조합. 재시도 예산이 반복마다 갱신되더라도 반복
    상한이 최종 방벽으로 남아 총 호출 수가 유한해야 한다."""

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0  # 스트림 진입 시도(에러 포함 — calls는 성공 응답만)

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
        self.attempts += 1
        if self.attempts % 2 == 1:
            raise RuntimeError("Error code: 529 - overloaded_error")
        self.calls += 1
        yield ProviderChunk(
            kind="tool_call",
            tool_call=ToolCall(tool_name="echo", arguments={}, id=f"c{self.attempts}"),
        )
        yield ProviderChunk(kind="done", stop_reason="tool_use")


@pytest.mark.asyncio
async def test_transient_retry_plus_silent_tools_still_bounded(monkeypatch):
    monkeypatch.setattr("rice_runtime.agent.loop._TRANSIENT_RETRY_DELAYS", (0.0, 0.0))
    provider = _FlakyThenSilentToolCaller()
    result = LoopResult()
    events = [
        ev
        async for ev in AgentLoop(
            provider=provider, dispatcher=_dispatcher(), profile=LOCAL_PROFILE
        ).run(_ctx(max_iterations=6), "s1", result, asyncio.Event())
    ]

    # 반복당 정확히 (실패 1 + 성공 1) — 재시도가 반복 수를 늘리지 않는다
    assert provider.calls == 6
    assert provider.attempts == 12
    errors = [ev for ev in events if ev.type.value == "error"]
    assert len(errors) == 1 and errors[0].data["error_code"] == "max_iterations"

# ── 시나리오 5·6·7: reconcile 모드 — 확정된 답의 중복 재생성 차단 ────────────────


class _PlanAnswerBase(_CountingProvider):
    """공통 골격: 1라운드 start_plan(2단계) → 2라운드 미종결 계획인 채 텍스트
    최종 답. 3라운드부터의 행동은 서브클래스가 정의한다."""

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
        self.calls += 1
        if self.calls == 1:
            yield ProviderChunk(
                kind="tool_call",
                tool_call=ToolCall(
                    tool_name="start_plan",
                    arguments={"goal": "g", "steps": [{"title": "a"}, {"title": "b"}]},
                    id="c1",
                ),
            )
            yield ProviderChunk(kind="done", stop_reason="tool_use")
            return
        if self.calls == 2:
            yield ProviderChunk(kind="text", text="the answer")
            yield ProviderChunk(kind="done", stop_reason="end_turn")
            return
        async for chunk in self._after_nudge():
            yield chunk

    async def _after_nudge(self):
        raise NotImplementedError
        yield  # pragma: no cover — 제너레이터 표식


class _ReconcileWithUpdatePlan(_PlanAnswerBase):
    """넛지에 순종: update_plan으로 전 단계 종결(본문 없음)."""

    async def _after_nudge(self):
        yield ProviderChunk(
            kind="tool_call",
            tool_call=ToolCall(
                tool_name="update_plan",
                arguments={
                    "updates": [
                        {"step_id": "s1", "status": "done"},
                        {"step_id": "s2", "status": "done"},
                    ]
                },
                id=f"c{self.calls}",
            ),
        )
        yield ProviderChunk(kind="done", stop_reason="tool_use")


@pytest.mark.asyncio
async def test_reconciled_answer_is_not_regenerated():
    provider = _ReconcileWithUpdatePlan()
    result = LoopResult()
    events = [
        ev
        async for ev in AgentLoop(provider=provider, dispatcher=_dispatcher()).run(
            _ctx(), "s1", result, asyncio.Event()
        )
    ]

    # start_plan 1 + 답 1 + update_plan 정리 1 = 3회 — 정리 뒤 재질의 라운드가 없다
    assert provider.calls == 3
    assert result.final_message == "the answer"
    # 사용자에게 간 본문: 델타 스트림 1회분 + 넛지 시점의 확정 content 1회가 전부
    msg_events = [ev for ev in events if ev.type.value == "message"]
    assert [ev.data["content"] for ev in msg_events if "content" in ev.data] == ["the answer"]
    assert "".join(ev.data.get("delta", "") for ev in msg_events) == "the answer"
    plan_events = [ev for ev in events if ev.type.value == "plan_updated"]
    assert plan_events[-1].data["status"] == "completed"
    assert all(s["status"] == "done" for s in plan_events[-1].data["steps"])


class _ReconcileThinksAloud(_PlanAnswerBase):
    """넛지에 순종하되(update_plan) 그 전에 추론을 남긴다 — 소형 모델이 런타임의
    합성 STOP 넛지를 "사용자가 STOP을 지시했다"고 서술하는 실측 행동."""

    async def _after_nudge(self):
        yield ProviderChunk(kind="thinking", text="사용자가 STOP 지시를 내렸습니다. 계획을 정리합니다.")
        yield ProviderChunk(
            kind="tool_call",
            tool_call=ToolCall(
                tool_name="update_plan",
                arguments={
                    "updates": [
                        {"step_id": "s1", "status": "done"},
                        {"step_id": "s2", "status": "done"},
                    ]
                },
                id=f"c{self.calls}",
            ),
        )
        yield ProviderChunk(kind="done", stop_reason="tool_use")


@pytest.mark.asyncio
async def test_reconcile_thinking_is_suppressed_live_and_on_reload():
    """억제된 reconcile 라운드의 추론은 사용자에게도 히스토리에도 남지 않는다 —
    런타임이 주입한 STOP을 사용자 탓으로 돌리는 독백이 답변 뒤에 붙던 버그
    (2026-07-15 실측: chat_2.png)."""
    provider = _ReconcileThinksAloud()
    result = LoopResult()
    events = [
        ev
        async for ev in AgentLoop(provider=provider, dispatcher=_dispatcher()).run(
            _ctx(), "s1", result, asyncio.Event()
        )
    ]

    assert provider.calls == 3
    assert result.final_message == "the answer"
    # 확정된 답 뒤로 thinking 이벤트가 새 나가지 않는다
    thinking_events = [ev for ev in events if ev.type.value == "thinking"]
    assert all("STOP" not in ev.data.get("text", "") for ev in thinking_events)
    # 리로드도 같은 그림: 억제 라운드의 추론은 metadata에도 없다
    last_assistant = [m for m in result.history if m.role == "assistant"][-1]
    assert "thinking" not in last_assistant.metadata


class _ReconcileRewritesAnswer(_PlanAnswerBase):
    """최악 행동: 넛지에 계획 정리 대신 답을 다시 써버린다."""

    async def _after_nudge(self):
        yield ProviderChunk(kind="text", text="the answer, rewritten")
        yield ProviderChunk(kind="done", stop_reason="end_turn")


@pytest.mark.asyncio
async def test_reconcile_rewrite_is_suppressed_and_plan_force_closed():
    provider = _ReconcileRewritesAnswer()
    result = LoopResult()
    events = [
        ev
        async for ev in AgentLoop(provider=provider, dispatcher=_dispatcher()).run(
            _ctx(), "s1", result, asyncio.Event()
        )
    ]

    assert provider.calls == 3
    assert result.final_message == "the answer"
    # 재작성 본문은 어떤 message 이벤트로도 새 나가지 않는다
    msg_events = [ev for ev in events if ev.type.value == "message"]
    leaked = "".join(ev.data.get("delta", "") + str(ev.data.get("content", "")) for ev in msg_events)
    assert "rewritten" not in leaked
    # durable 히스토리에서도 content가 아니라 metadata로만 보관된다(리로드 동일 그림)
    last_assistant = [m for m in result.history if m.role == "assistant"][-1]
    assert last_assistant.content == ""
    assert "rewritten" in last_assistant.metadata["suppressed_text"]
    # 계획은 런타임이 skipped로 강제 종결
    plan_events = [ev for ev in events if ev.type.value == "plan_updated"]
    assert plan_events[-1].data["status"] == "completed"
    assert all(s["status"] in ("done", "failed", "skipped") for s in plan_events[-1].data["steps"])


class _ReconcileResumesRealWork(_PlanAnswerBase):
    """넛지에 실제 도구를 더 부른다("작업이 남아 있었다") — 서사와 함께. 그 다음
    라운드에 진짜 최종 답을 낸다(계획은 여전히 미종결 → 런타임 강제 종결 경로)."""

    async def _after_nudge(self):
        if self.calls == 3:
            yield ProviderChunk(kind="thinking", text="work remains")
            yield ProviderChunk(kind="text", text="continuing")
            yield ProviderChunk(
                kind="tool_call",
                tool_call=ToolCall(tool_name="echo", arguments={}, id="c3"),
            )
            yield ProviderChunk(kind="done", stop_reason="tool_use")
            return
        yield ProviderChunk(kind="text", text="real final")
        yield ProviderChunk(kind="done", stop_reason="end_turn")


@pytest.mark.asyncio
async def test_reconcile_resumed_work_flushes_narration_and_ends_normally():
    provider = _ReconcileResumesRealWork()
    result = LoopResult()
    events = [
        ev
        async for ev in AgentLoop(provider=provider, dispatcher=_dispatcher()).run(
            _ctx(), "s1", result, asyncio.Event()
        )
    ]

    # start_plan 1 + 조기 답 1 + 작업 재개 1 + 진짜 최종 1 = 4회 (넛지 예산 소진
    # 뒤라 두 번째 reconcile은 없다 — 런타임이 강제 종결)
    assert provider.calls == 4
    assert result.final_message == "real final"
    msg_events = [ev for ev in events if ev.type.value == "message"]
    # 억제됐던 재개 서사("continuing")는 방출된다 — 조기 답, 서사, 진짜 답 순서
    assert "".join(ev.data.get("delta", "") for ev in msg_events) == "the answercontinuingreal final"
    # 확정 content는 두 번: 넛지 시점(조기 답) + 정상 종료(진짜 답)
    assert [ev.data["content"] for ev in msg_events if "content" in ev.data] == ["the answer", "real final"]
    # 작업이 재개된 라운드의 추론은 버퍼에서 풀려 방출된다(억제는 재작성 라운드만)
    assert "work remains" in "".join(
        ev.data.get("text", "") for ev in events if ev.type.value == "thinking"
    )
    plan_events = [ev for ev in events if ev.type.value == "plan_updated"]
    assert plan_events[-1].data["status"] == "completed"
