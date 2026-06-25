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
from .interfaces import IToolDispatcher
from .providers.interfaces import ILLMProvider


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

    def __init__(self, *, provider: ILLMProvider, dispatcher: IToolDispatcher, temperature: float = 0.7) -> None:
        self._provider = provider
        self._dispatcher = dispatcher
        self._temperature = temperature

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
            pending_calls = []
            stop_reason = "end_turn"

            async for chunk in self._provider.stream(
                system=ctx.system_prompt,
                messages=messages,
                tools=ctx.tools,
                temperature=self._temperature,
            ):
                if abort.is_set():
                    # interrupt mid-stream, not only between iterations (C-3)
                    result.aborted = True
                    break
                if chunk.kind == "thinking" and chunk.text:
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
            messages.append(Message(role="assistant", content=assistant_text, tool_calls=tuple(pending_calls)))

            # No tool calls → this is the final answer for the turn.
            if not pending_calls:
                final_text = assistant_text
                yield ev_message(content=assistant_text)
                break

            # Dispatch each requested tool, feed results back into the history.
            for call in pending_calls:
                spec = tool_index.get(call.tool_name)
                if spec is None:
                    res = ToolResult(call.id, ok=False, error=f"unknown tool: {call.tool_name}")
                    messages.append(Message(role="tool", content=_render_tool_result(res)))
                    tool_calls_made += 1
                    continue
                async for ev in self._dispatcher.dispatch(session_id, call, spec):
                    yield ev
                res = await self._dispatcher.result_for(call)
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
