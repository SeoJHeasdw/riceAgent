"""Ollama provider adapter for the normalized `ILLMProvider` seam (RUNTIME_DESIGN §6).

Ollama's `/api/chat` wire format is close to the neutral history shape, so this
adapter mostly translates tool-call metadata and streaming chunks. The HTTP call is
kept behind `_stream_chat` so unit tests can replace network I/O with a small fake.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, AsyncIterable, Sequence
from typing import Any

from ...core.config import ProviderConfig
from ...core.errors import ProviderError
from ...core.types import Message, ToolCall, ToolSpec
from .interfaces import ILLMProvider, ProviderCapabilities, ProviderChunk

# think:false is a request, not a guarantee: harmony-format reasoning models
# (gpt-oss etc.) have no true off switch for their analysis pass and can burn the
# whole num_predict budget on hidden reasoning, leaving nothing for the visible
# answer (200 OK, content ""). The non-streaming branch of `stream` retries once
# at this larger budget when that specific signature is detected.
_REASONING_RETRY_NUM_PREDICT = 200


def _to_ollama_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
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


def _to_ollama_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    native: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            native.append({"role": "system", "content": message.content})
            continue
        if message.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": message.content}
            if message.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.tool_name,
                            "arguments": call.arguments,
                        },
                    }
                    for call in message.tool_calls
                ]
            native.append(entry)
            continue
        if message.role == "tool":
            tool_call_id = message.metadata.get("tool_call_id")
            entry = {"role": "tool", "content": message.content}
            if tool_call_id:
                entry["tool_call_id"] = tool_call_id
            native.append(entry)
            continue
        native.append({"role": message.role, "content": message.content})
    return native


def _extract_tool_calls(message: dict[str, Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for raw_call in message.get("tool_calls") or ():
        function = raw_call.get("function") or {}
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        kwargs: dict[str, Any] = {
            "tool_name": function.get("name", ""),
            "arguments": arguments,
        }
        call_id = raw_call.get("id") or raw_call.get("tool_call_id")
        if call_id:
            kwargs["id"] = call_id
        calls.append(ToolCall(**kwargs))
    return calls


class OllamaProvider(ILLMProvider):
    name = "ollama"

    def __init__(self, cfg: ProviderConfig, *, think: bool = True) -> None:
        self._cfg = cfg
        # think=False: disable reasoning tokens — used by the session titler so a
        # short title generation doesn't pay for a full reasoning pass (and avoids
        # the httpx stream-drain stall that occurs when the caller breaks early).
        self._think = think

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(native_tool_calling=True, streaming=True, prompt_caching=False, vision=False)

    async def _stream_chat(self, payload: dict[str, Any]) -> AsyncIterable[dict[str, Any]]:
        import httpx  # noqa: PLC0415 - optional dependency by design

        base_url = (self._cfg.base_url or "http://localhost:11434").rstrip("/")
        timeout = getattr(self._cfg, "timeout_seconds", 300)
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
            async with client.stream("POST", "/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line:
                        yield json.loads(line)

    async def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Single non-streaming POST — used when streaming is unnecessary (titler).
        No iterator, no drain stall: the full response arrives in one round-trip."""
        import httpx  # noqa: PLC0415 - optional dependency by design

        base_url = (self._cfg.base_url or "http://localhost:11434").rstrip("/")
        timeout = getattr(self._cfg, "timeout_seconds", 300)
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
            response = await client.post("/api/chat", json=payload)
            response.raise_for_status()
            return response.json()

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        temperature: float = 0.7,
        response_schema: dict | None = None,
        think: bool | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        if not self._think:
            # ── non-streaming path (titler) ───────────────────────────────────
            # think=False instances are used for short single-answer requests
            # (e.g. session title). A non-streaming POST avoids the httpx
            # stream-drain stall that happens when the caller breaks out of the
            # async-for early: with stream=False Ollama returns one JSON object,
            # httpx closes the connection cleanly, and no drain is needed.
            options: dict[str, Any] = {"temperature": 0.0, "num_predict": max_tokens or 20}
            payload = {
                "model": self._cfg.model,
                "messages": ([{"role": "system", "content": system}] if system else []) + _to_ollama_messages(messages),
                "stream": False,
                "think": False,
                "options": options,
                "keep_alive": "5m",
            }
            if response_schema is not None:
                payload["format"] = response_schema
            try:
                item = await self._post_chat(payload)
            except Exception as exc:
                raise ProviderError(f"ollama post failed: {exc}") from exc
            content = (item.get("message") or {}).get("content") or ""
            if not content and item.get("done_reason") == "length":
                # Confirmed signature of a reasoning-mandatory model eating the
                # budget (see _REASONING_RETRY_NUM_PREDICT) — retry once with
                # headroom. Models that already honor think:false within the
                # small budget (qwen3 etc.) never take this branch, so they
                # never pay for the extra round-trip.
                options["num_predict"] = _REASONING_RETRY_NUM_PREDICT
                try:
                    item = await self._post_chat(payload)
                except Exception as exc:
                    raise ProviderError(f"ollama post retry failed: {exc}") from exc
                content = (item.get("message") or {}).get("content") or ""
            if content:
                yield ProviderChunk(kind="text", text=content)
            yield ProviderChunk(kind="done", stop_reason="end_turn")
            return

        # ── streaming path (normal chat) ──────────────────────────────────────
        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            # decode-level runaway guard — a looping generation is cut by the
            # sampler, not by burning the caller's wall-clock timeout.
            options["num_predict"] = max_tokens
        payload = {
            "model": self._cfg.model,
            "messages": ([{"role": "system", "content": system}] if system else []) + _to_ollama_messages(messages),
            "stream": True,
            # Surface the model's reasoning as a separate `message.thinking` stream
            # (qwen3.x etc. support this) so the agent loop can emit `thinking`
            # events — the trace the ChatScreen renders. Non-thinking models simply
            # return no `thinking` field; the chunk below is then never yielded.
            # Per-call think=False (planner 등 구조화 1회 호출) overrides this.
            "think": self._think if think is None else think,
            "options": options,
            "keep_alive": "5m",
        }
        if response_schema is not None:
            # grammar-constrained decoding: the model physically cannot emit
            # prose/code fences around the JSON — prompt-level "ONLY JSON" rules
            # (소형 모델 검증-루프의 연료) become unnecessary.
            payload["format"] = response_schema
        if tools:
            payload["tools"] = _to_ollama_tools(tools)

        yielded_done = False
        try:
            async for item in self._stream_chat(payload):
                message = item.get("message") or {}
                thinking = message.get("thinking")
                if thinking:
                    yield ProviderChunk(kind="thinking", text=thinking)
                content = message.get("content")
                if content:
                    yield ProviderChunk(kind="text", text=content)
                for call in _extract_tool_calls(message):
                    yield ProviderChunk(kind="tool_call", tool_call=call)
                if item.get("done"):
                    stop_reason = item.get("done_reason") or ("tool_use" if message.get("tool_calls") else "end_turn")
                    yield ProviderChunk(
                        kind="done",
                        stop_reason={"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}.get(
                            stop_reason, stop_reason if stop_reason in {"end_turn", "tool_use", "max_tokens"} else "error"
                        ),
                    )
                    yielded_done = True
                    break
        except ProviderError:
            raise
        except Exception as exc:  # pragma: no cover - exercised via mocks in tests
            raise ProviderError(f"ollama stream failed: {exc}") from exc

        if not yielded_done:
            yield ProviderChunk(kind="done", stop_reason="error")
