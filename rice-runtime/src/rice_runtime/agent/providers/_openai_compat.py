"""Shared OpenAI-style chat helpers (RUNTIME_DESIGN §6).

Both the OpenAI provider and IBM watsonx.ai speak the OpenAI `chat/completions`
wire shape (system/user/assistant/tool messages, `tools` as function specs, and
SSE `choices[].delta` streaming). This module holds the format mapping + the
streaming-delta assembler so each provider only owns its HTTP + auth.

It is provider-internal plumbing, NOT a widening of the FROZEN `ILLMProvider`
seam — providers still expose the normalized `stream()` and `ProviderChunk`s.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable, AsyncIterator, Sequence
from typing import Any

from ...core.types import Message, ToolCall, ToolSpec
from .interfaces import ProviderChunk


def to_oai_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools
    ]


def to_oai_messages(system: str, messages: Sequence[Message]) -> list[dict[str, Any]]:
    native: list[dict[str, Any]] = []
    if system:
        native.append({"role": "system", "content": system})
    for message in messages:
        if message.role == "system":
            native.append({"role": "system", "content": message.content})
            continue
        if message.role == "user":
            native.append({"role": "user", "content": message.content})
            continue
        if message.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": message.content or None}
            if message.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.tool_name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in message.tool_calls
                ]
            native.append(entry)
            continue
        if message.role == "tool":
            entry = {"role": "tool", "content": message.content}
            tool_call_id = message.metadata.get("tool_call_id")
            if tool_call_id:
                entry["tool_call_id"] = tool_call_id
            native.append(entry)
            continue
        native.append({"role": message.role, "content": message.content})
    return native


_FINISH = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}


async def assemble_stream(chunks: AsyncIterable[dict[str, Any]]) -> AsyncIterator[ProviderChunk]:
    """Turn OpenAI-style streamed `chunk` dicts into normalized `ProviderChunk`s.

    Handles incremental `delta.content` (text), `delta.tool_calls[]` fragments
    (assembled by index, arguments concatenated as JSON text), and `finish_reason`.
    Ends with exactly one `kind="done"`.
    """
    # index → {"id", "name", "args"} accumulated across deltas
    pending: dict[int, dict[str, str]] = {}
    finish: str | None = None
    yielded_done = False

    async for chunk in chunks:
        choices = chunk.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta") or {}

        content = delta.get("content")
        if content:
            yield ProviderChunk(kind="text", text=content)

        # some servers stream reasoning as `reasoning_content`/`reasoning`
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning:
            yield ProviderChunk(kind="thinking", text=reasoning)

        for tc in delta.get("tool_calls") or ():
            idx = tc.get("index", 0)
            slot = pending.setdefault(idx, {"id": "", "name": "", "args": ""})
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            if fn.get("arguments"):
                slot["args"] += fn["arguments"]

        if choice.get("finish_reason"):
            finish = choice["finish_reason"]

    # flush assembled tool calls (in index order) before the done chunk
    for _idx, slot in sorted(pending.items()):
        if not slot["name"]:
            continue
        try:
            args = json.loads(slot["args"]) if slot["args"] else {}
        except json.JSONDecodeError:
            args = {}
        kwargs: dict[str, Any] = {"tool_name": slot["name"], "arguments": args}
        if slot["id"]:
            kwargs["id"] = slot["id"]
        yield ProviderChunk(kind="tool_call", tool_call=ToolCall(**kwargs))

    stop = _FINISH.get(finish or "", "end_turn" if finish is None else "error")
    # if tool calls were produced but finish_reason was absent/odd, prefer tool_use
    if stop != "tool_use" and pending:
        stop = "tool_use"
    yield ProviderChunk(kind="done", stop_reason=stop)
    yielded_done = True

    if not yielded_done:  # pragma: no cover - defensive
        yield ProviderChunk(kind="done", stop_reason="error")
