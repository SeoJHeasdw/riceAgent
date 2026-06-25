"""Claude provider adapter for the normalized `ILLMProvider` seam (RUNTIME_DESIGN §6).

This module keeps Anthropic-specific protocol details behind a provider-local
adapter so the agent loop only ever sees `ProviderChunk`s. History conversion is
split into pure helpers because assistant tool_use ↔ tool_result pairing is the
most fragile part of the seam and needs isolated tests.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from ...core.config import ProviderConfig
from ...core.errors import ProviderError
from ...core.types import Message, ToolCall, ToolSpec
from .interfaces import ILLMProvider, ProviderCapabilities, ProviderChunk

_CLAUDE_KEY_SERVICE = "rice.provider.claude"
_CLAUDE_KEY_USERNAME = "api_key"


@dataclass(frozen=True)
class _ToolUseState:
    id: str
    name: str
    partial_json: str = ""


def _to_claude_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters,
        }
        for tool in tools
    ]


def _claude_system_blocks(system: str) -> list[dict[str, Any]]:
    if not system:
        return []
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def _to_claude_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    native: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "user":
            native.append({"role": "user", "content": message.content})
            continue
        if message.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            for call in message.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.tool_name,
                        "input": call.arguments,
                    }
                )
            native.append({"role": "assistant", "content": blocks or message.content})
            continue
        if message.role == "tool":
            tool_call_id = message.metadata.get("tool_call_id")
            if not tool_call_id:
                raise ProviderError("tool message missing tool_call_id metadata")
            native.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_call_id,
                            "content": message.content,
                        }
                    ],
                }
            )
    return native


def _load_api_key() -> str:
    try:
        import keyring  # noqa: PLC0415 - optional dependency by design
    except ImportError:
        keyring = None

    if keyring is not None:
        key = keyring.get_password(_CLAUDE_KEY_SERVICE, _CLAUDE_KEY_USERNAME)
        if key:
            return key

    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key:
        return env_key
    raise ProviderError("claude API key not found in keychain or ANTHROPIC_API_KEY")


def _map_stop_reason(stop_reason: str | None) -> str:
    return {
        "end_turn": "end_turn",
        "tool_use": "tool_use",
        "max_tokens": "max_tokens",
    }.get(stop_reason or "", "error")


class ClaudeProvider(ILLMProvider):
    name = "claude"

    def __init__(self, cfg: ProviderConfig) -> None:
        self._cfg = cfg
        self._api_key = _load_api_key()

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            streaming=True,
            prompt_caching=True,
            vision=True,
        )

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        temperature: float = 0.7,
    ) -> AsyncIterator[ProviderChunk]:
        from anthropic import AsyncAnthropic  # noqa: PLC0415 - optional dependency by design

        client = AsyncAnthropic(api_key=self._api_key, base_url=self._cfg.base_url)
        tool_states: dict[int, _ToolUseState] = {}
        # Anthropic streams stop_reason on the `message_delta` event, NOT on
        # `message_stop` — capture it there and emit on stop (RUNTIME_DESIGN §6).
        final_stop_reason: str | None = None
        yielded_done = False

        try:
            async with client.messages.stream(
                model=self._cfg.model,
                max_tokens=4096,
                system=_claude_system_blocks(system),
                messages=_to_claude_messages(messages),
                tools=_to_claude_tools(tools),
                temperature=temperature,
            ) as stream:
                async for event in stream:
                    event_type = getattr(event, "type", None)
                    if event_type == "content_block_start":
                        block = event.content_block
                        if getattr(block, "type", None) == "tool_use":
                            tool_states[event.index] = _ToolUseState(id=block.id, name=block.name)
                            if getattr(block, "input", None):
                                yield ProviderChunk(
                                    kind="tool_call",
                                    tool_call=ToolCall(tool_name=block.name, arguments=block.input, id=block.id),
                                )
                        elif getattr(block, "type", None) == "thinking" and getattr(block, "thinking", None):
                            yield ProviderChunk(kind="thinking", text=block.thinking)
                    elif event_type == "content_block_delta":
                        delta = event.delta
                        delta_type = getattr(delta, "type", None)
                        if delta_type == "text_delta":
                            yield ProviderChunk(kind="text", text=delta.text)
                        elif delta_type == "thinking_delta":
                            yield ProviderChunk(kind="thinking", text=delta.thinking)
                        elif delta_type == "input_json_delta":
                            state = tool_states.get(event.index)
                            if state is not None:
                                tool_states[event.index] = _ToolUseState(
                                    id=state.id,
                                    name=state.name,
                                    partial_json=state.partial_json + delta.partial_json,
                                )
                    elif event_type == "content_block_stop":
                        state = tool_states.pop(event.index, None)
                        if state is not None and state.partial_json:
                            yield ProviderChunk(
                                kind="tool_call",
                                tool_call=ToolCall(
                                    tool_name=state.name,
                                    arguments=json.loads(state.partial_json),
                                    id=state.id,
                                ),
                            )
                    elif event_type == "message_delta":
                        stop_reason = getattr(getattr(event, "delta", None), "stop_reason", None)
                        if stop_reason:
                            final_stop_reason = stop_reason
                    elif event_type == "message_stop":
                        yield ProviderChunk(kind="done", stop_reason=_map_stop_reason(final_stop_reason))
                        yielded_done = True
        except ProviderError:
            raise
        except Exception as exc:  # pragma: no cover - exercised via mocks in tests
            raise ProviderError(f"claude stream failed: {exc}") from exc

        if not yielded_done:
            yield ProviderChunk(kind="done", stop_reason="error")
