"""Conversation loop (#1) — the LLM call/tool-dispatch cycle (RUNTIME_DESIGN §2).

Structure borrowed from Hermes' `run_conversation` (pattern only, re-implemented).
One iteration = one LLM stream; if it asks for tools, dispatch them, append results,
loop again — capped at `ctx.max_iterations` (default 25) to prevent runaway turns.

The loop is provider- and storage-agnostic: it consumes a `TurnContext` and an
`ILLMProvider`, dispatches via an `IToolDispatcher`, and yields `SSEEvent`s. It owns
no persistence and no learning logic — the orchestrator wraps those around it.

`run(...)` is an async generator of SSE events; the turn's summary is written into
the passed-in `LoopResult` (async generators can't return a value cleanly), which
the orchestrator reads to build the `TurnOutcome` for learning.observe.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Sequence

from ..core.tokens import TokenCalibrator, is_context_length_error
from ..core.types import (
    Message,
    SSEEvent,
    ToolResult,
    TurnContext,
    ev_error,
    ev_message,
    ev_thinking,
    ev_tool_call,
    ev_tool_result,
)
from .context_manager import (
    ContextRenderStats,
    ConversationContextManager,
    estimate_context_tokens as _pure_ctx_tokens,
)
from .interfaces import IToolDispatcher
from .providers.interfaces import ILLMProvider, ProviderChunk
from .tool_scheduler import ScheduledToolRequest, ToolBatchResult, ToolScheduler

log = logging.getLogger("rice.agent.loop")


@dataclass
class LoopResult:
    final_message: str = ""
    history: tuple[Message, ...] = ()
    tool_calls_made: int = 0
    aborted: bool = False
    # 매 LLM 호출의 컨텍스트 렌더 감사 스탯 — orchestrator가 턴 종료 시
    # ledger에 `context_rendered` 이벤트로 영속화한다.
    context_renders: list[ContextRenderStats] = field(default_factory=list)


def _render_tool_result(res: ToolResult) -> str:
    if res.ok:
        return json.dumps(res.result, ensure_ascii=False, default=str)
    return f"ERROR: {res.error}"


class AgentLoop:
    """Implements the #1 conversation loop. Stateless across turns: all per-turn
    state lives in locals + the injected `LoopResult`."""

    def __init__(
        self,
        *,
        provider: ILLMProvider,
        dispatcher: IToolDispatcher,
        temperature: float = 0.7,
        context_manager: ConversationContextManager | None = None,
        token_calibrator: "TokenCalibrator | None" = None,
    ) -> None:
        self._provider = provider
        self._dispatcher = dispatcher
        self._temperature = temperature
        self._context_manager = context_manager
        # 실측 usage 피드백 + 컨텍스트 길이 초과 시 재압축 재시도용
        self._calibrator = token_calibrator

    def _render(self, ctx: TurnContext, messages: list[Message], *, budget_scale: float):
        """이 반복에 보낼 컨텍스트를 렌더한다. budget_scale<1이면 컨텍스트 길이
        초과 후 더 강하게 압축하는 재시도용. context_manager가 없으면 raw 그대로."""
        if self._context_manager is None:
            return None, ctx.system_prompt, tuple(messages)
        cm = self._context_manager
        if budget_scale != 1.0:
            cm = ConversationContextManager(
                cm.config, estimate=cm.estimate_fn, budget_scale=budget_scale
            )
        rendered = cm.render(ctx.system_prompt, tuple(messages))
        return rendered, rendered.system_prompt, rendered.history

    def _calibrate(self, system: str, messages: Sequence[Message], done: ProviderChunk) -> None:
        """provider가 보고한 실제 input_tokens로 토큰 추정 비율을 보정한다.
        보낸 컨텍스트의 *순수* 휴리스틱 추정과 실측을 비교한다.
        best-effort — 캘리브레이션 실패가 턴을 깨선 안 된다."""
        if self._calibrator is None or done.input_tokens is None:
            return
        try:
            pure = _pure_ctx_tokens(system, messages)  # 순수 CJK 휴리스틱 (비보정)
            self._calibrator.observe(pure, done.input_tokens)
        except Exception:  # noqa: BLE001
            log.debug("token calibration observe failed", exc_info=True)

    async def run(self, ctx: TurnContext, session_id: str, result: LoopResult, abort: asyncio.Event):
        tool_index = {t.name: t for t in ctx.tools}
        messages: list[Message] = list(ctx.history)
        tool_calls_made = 0
        final_text = ""

        for _iteration in range(ctx.max_iterations):
            if abort.is_set():
                result.aborted = True
                break

            assistant_parts: list[str] = []
            thinking_parts: list[str] = []
            pending_calls = []
            stop_reason = "end_turn"

            # 컨텍스트 길이 초과 시 더 강한 압축으로 1회 재시도한다.
            # 초과 에러는 요청 진입 시점에 나므로(스트림 델타 전) 한 청크도 내보내기
            # 전에 안전하게 재렌더할 수 있다. 재시도 후에도 실패하면 상위로 전파.
            for attempt, budget_scale in enumerate((1.0, 0.45)):
                rendered_context, provider_system, provider_messages = self._render(
                    ctx, messages, budget_scale=budget_scale
                )
                produced_any = False
                try:
                    async for chunk in self._provider.stream(
                        system=provider_system,
                        messages=provider_messages,
                        tools=ctx.tools,
                        temperature=self._temperature,
                    ):
                        produced_any = True
                        if abort.is_set():
                            # interrupt mid-stream, not only between iterations (C-3)
                            result.aborted = True
                            break
                        if chunk.kind == "thinking" and chunk.text:
                            thinking_parts.append(chunk.text)
                            yield ev_thinking(chunk.text)
                        elif chunk.kind == "text" and chunk.text:
                            assistant_parts.append(chunk.text)
                            yield ev_message(delta=chunk.text)
                        elif chunk.kind == "tool_call" and chunk.tool_call is not None:
                            pending_calls.append(chunk.tool_call)
                        elif chunk.kind == "done":
                            stop_reason = chunk.stop_reason or "end_turn"
                            self._calibrate(provider_system, provider_messages, chunk)
                    break  # stream consumed cleanly
                except Exception as exc:  # noqa: BLE001
                    can_retry = (
                        attempt == 0
                        and not produced_any
                        and self._context_manager is not None
                        and is_context_length_error(exc)
                    )
                    if not can_retry:
                        raise
                    log.warning("context length exceeded — retrying with tighter budget (%s)", exc)
                    assistant_parts.clear()
                    thinking_parts.clear()
                    pending_calls.clear()

            if rendered_context is not None:
                result.context_renders.append(rendered_context.stats)

            if abort.is_set():
                break  # stop without emitting a partial answer as final

            assistant_text = "".join(assistant_parts)
            # Stash this round's reasoning on the assistant message so it persists
            # for trace reconstruction on reload. Providers ignore metadata when
            # assembling history, so the model never re-sees stale thinking.
            thinking_text = "".join(thinking_parts)
            metadata = {"thinking": thinking_text} if thinking_text else {}
            messages.append(
                Message(role="assistant", content=assistant_text, tool_calls=tuple(pending_calls), metadata=metadata)
            )

            # No tool calls → this is the final answer for the turn.
            if not pending_calls:
                final_text = assistant_text
                yield ev_message(content=assistant_text)
                break

            # Dispatch requested tools through the scheduler. Safe independent
            # calls can run in parallel; destructive/approval-gated calls remain
            # ordered. Results are appended in model-call order.
            scheduled: list[ScheduledToolRequest] = []
            for call in pending_calls:
                spec = tool_index.get(call.tool_name)
                if spec is None:
                    # 미지의 도구도 SSE로 서피싱한다(이전엔 모델만 에러를 보고 UI/
                    # 지표에는 보이지 않았다 — 감사 가능성 P0와 같은 원칙). tool
                    # 메시지에 tool_call_id 메타데이터가 없으면 컨텍스트 압축의
                    # wire repair가 이 라운드를 통째로 떨궈버리므로 반드시 싣는다.
                    res = ToolResult(call.id, ok=False, error=f"unknown tool: {call.tool_name}")
                    yield ev_tool_call(call)
                    yield ev_tool_result(res)
                    messages.append(
                        Message(role="tool", content=_render_tool_result(res), metadata={"tool_call_id": call.id})
                    )
                    tool_calls_made += 1
                    continue
                scheduled.append(ScheduledToolRequest(call=call, spec=spec))

            if scheduled:
                batch = ToolBatchResult()
                scheduler = ToolScheduler(self._dispatcher)
                async for ev in scheduler.schedule(session_id, scheduled, batch, abort):
                    yield ev
                for req, res in zip(scheduled, batch.ordered([req.call for req in scheduled]), strict=True):
                    call = req.call
                    # tool_source: 이 결과가 외부 콘텐츠(connector/plugin/bridge =
                    # Gmail·웹·원격 도구)에서 왔는지 표시한다. 학습 리뷰가 메모리를
                    # 격리할지 판단하는 근거(메모리 오염 방지).
                    messages.append(
                        Message(
                            role="tool",
                            content=_render_tool_result(res),
                            metadata={"tool_call_id": call.id, "tool_source": req.spec.source, "tool_name": call.tool_name},
                        )
                    )
                    tool_calls_made += 1
            # loop continues: provider sees the tool results next iteration
        else:
            # max_iterations exhausted without a clean stop
            yield ev_error("max_iterations", f"turn exceeded {ctx.max_iterations} iterations")

        result.final_message = final_text
        result.history = tuple(messages)
        result.tool_calls_made = tool_calls_made
