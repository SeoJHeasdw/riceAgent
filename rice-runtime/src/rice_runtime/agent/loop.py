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
from dataclasses import dataclass, field

from ..core.types import (
    Message,
    SSEEvent,
    ToolResult,
    TurnContext,
    ev_error,
    ev_message,
    ev_thinking,
)
from .context_manager import ConversationContextManager
from .interfaces import IToolDispatcher
from .providers.interfaces import ILLMProvider
from .tool_scheduler import ScheduledToolRequest, ToolBatchResult, ToolScheduler


@dataclass
class LoopResult:
    final_message: str = ""
    history: tuple[Message, ...] = ()
    tool_calls_made: int = 0
    aborted: bool = False


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
    ) -> None:
        self._provider = provider
        self._dispatcher = dispatcher
        self._temperature = temperature
        self._context_manager = context_manager

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

            rendered_context = (
                self._context_manager.render(ctx.system_prompt, tuple(messages))
                if self._context_manager is not None
                else None
            )
            provider_system = rendered_context.system_prompt if rendered_context is not None else ctx.system_prompt
            provider_messages = rendered_context.history if rendered_context is not None else tuple(messages)

            async for chunk in self._provider.stream(
                system=provider_system,
                messages=provider_messages,
                tools=ctx.tools,
                temperature=self._temperature,
            ):
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
                    res = ToolResult(call.id, ok=False, error=f"unknown tool: {call.tool_name}")
                    messages.append(Message(role="tool", content=_render_tool_result(res)))
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
                    messages.append(
                        Message(role="tool", content=_render_tool_result(res), metadata={"tool_call_id": call.id})
                    )
                    tool_calls_made += 1
            # loop continues: provider sees the tool results next iteration
        else:
            # max_iterations exhausted without a clean stop
            yield ev_error("max_iterations", f"turn exceeded {ctx.max_iterations} iterations")

        result.final_message = final_text
        result.history = tuple(messages)
        result.tool_calls_made = tool_calls_made
