"""Ollama provider adapter for the normalized `ILLMProvider` seam (RUNTIME_DESIGN §6).

Ollama's `/api/chat` wire format is close to the neutral history shape, so this
adapter mostly translates tool-call metadata and streaming chunks. The HTTP call is
kept behind `_stream_chat` so unit tests can replace network I/O with a small fake.

로컬 LLM 효율 계약 (2026-07-10):
  - `num_ctx`를 항상 명시한다. 미지정 시 Ollama 서버 기본(대개 4096)이 적용돼
    긴 턴에서 프롬프트 **앞**(시스템 프롬프트·도구 스키마)이 소리 없이 잘린다 —
    도구 호출 실패·지시 무시의 근본 원인. 예산 압축은 컨텍스트 매니저의 일이고,
    잘림은 버그다.
  - `keep_alive`는 config에서 온 유한 값(기본 5m)이다. 비정상 종료 시 Ollama
    자체 회수 안전망이며, 정상 흐름에서는 registry 공유 lifecycle이 ReAct 전체를
    lease하고 짧은 title/review 유예 뒤 keep_alive=0으로 즉시 해제한다.
  - HTTP 클라이언트는 이벤트 루프당 1개를 재사용한다(keep-alive 커넥션 풀).
    ReAct 루프는 같은 서버에 수십 번 연쇄 요청한다 — 매 호출 TCP 재수립은 낭비.
  - 프리픽스 KV 캐시는 이 어댑터가 아니라 **렌더링 규율**이 지킨다: 루프가
    시스템 프롬프트를 턴 내 불변으로 유지하고 동적 상태를 tail 메시지로 보내므로,
    Ollama는 직전 요청과의 최장 공통 프리픽스를 재사용한다(re-prefill 최소화).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, AsyncIterable, Sequence
from contextlib import asynccontextmanager
from typing import Any

from ...core.config import ProviderConfig
from ...core.errors import ProviderError
from ...core.types import IMAGES_METADATA_KEY, Message, ToolCall, ToolSpec
from .interfaces import ILLMProvider, ProviderCapabilities, ProviderChunk
from .ollama_lifecycle import OllamaLifecycleManager, OllamaModelKey

# think:false is a request, not a guarantee: harmony-format reasoning models
# (gpt-oss etc.) have no true off switch for their analysis pass and can burn the
# whole num_predict budget on hidden reasoning, leaving nothing for the visible
# answer (200 OK, content ""). The non-streaming branch of `stream` retries once
# at this larger budget when that specific signature is detected.
_REASONING_RETRY_NUM_PREDICT = 200

# ── 이벤트 루프당 공유 httpx 클라이언트 ──────────────────────────────────────
# provider 인스턴스는 턴마다 새로 만들어질 수 있으므로(registry) 커넥션 풀은
# 모듈 수준에 둔다. 이벤트 루프가 바뀌면(테스트 등) 새 클라이언트로 교체 —
# 닫힌 루프의 transport 를 재사용하다 죽는 것을 막는다. 타임아웃은 요청별로 준다.
_client: Any = None
_client_loop_id: int | None = None


def _shared_client() -> Any:
    import httpx  # noqa: PLC0415 - optional dependency by design

    global _client, _client_loop_id
    loop_id = id(asyncio.get_running_loop())
    if _client is None or _client_loop_id != loop_id or getattr(_client, "is_closed", False):
        _client = httpx.AsyncClient(timeout=None)
        _client_loop_id = loop_id
    return _client


async def close_shared_client() -> None:
    """Close/reset the inference connection pool during ASGI shutdown."""

    global _client, _client_loop_id
    client = _client
    _client = None
    _client_loop_id = None
    if client is not None and not getattr(client, "is_closed", False):
        await client.aclose()


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


def _message_images(message: Message) -> list[str]:
    """metadata["images"] 첨부(core.types 계약) → Ollama의 per-message `images`
    배열(bare base64). 멀티모달 모델(qwen-vl/llava 등)이 소비하고, 비전 없는
    모델은 서버가 무시한다."""
    return [
        item["data_b64"]
        for item in (message.metadata.get(IMAGES_METADATA_KEY) or ())
        if isinstance(item, dict) and item.get("data_b64")
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
            images = _message_images(message)
            if images:
                entry["images"] = images
            native.append(entry)
            continue
        entry = {"role": message.role, "content": message.content}
        images = _message_images(message)
        if images:
            entry["images"] = images
        native.append(entry)
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

    def __init__(
        self,
        cfg: ProviderConfig,
        *,
        think: bool = True,
        lifecycle: OllamaLifecycleManager | None = None,
    ) -> None:
        self._cfg = cfg
        self._lifecycle = lifecycle
        self._model_key = OllamaModelKey.create(cfg.base_url, cfg.model)
        # think=False: disable reasoning tokens — used by the session titler so a
        # short title generation doesn't pay for a full reasoning pass (and avoids
        # the httpx stream-drain stall that occurs when the caller breaks early).
        self._think = think

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(native_tool_calling=True, streaming=True, prompt_caching=False, vision=False)

    def _chat_url(self) -> str:
        return (self._cfg.base_url or "http://localhost:11434").rstrip("/") + "/api/chat"

    @property
    def lifecycle(self) -> OllamaLifecycleManager | None:
        """Shared registry-owned lifecycle, reused by the utility/titler variant."""

        return self._lifecycle

    def _idle_unload_seconds(self) -> float:
        try:
            return max(0.0, float(getattr(self._cfg, "idle_unload_seconds", 60.0)))
        except (TypeError, ValueError):
            return 60.0

    @asynccontextmanager
    async def activity(self) -> AsyncIterator[None]:
        """Outer logical-work lease used by the orchestrator's complete ReAct turn."""

        if self._lifecycle is None:
            yield
            return
        async with self._lifecycle.lease(
            self._model_key,
            idle_seconds=self._idle_unload_seconds(),
            touches_model=False,
            request_keep_alive=self._keep_alive(),
        ):
            yield

    @asynccontextmanager
    async def _request_activity(self) -> AsyncIterator[None]:
        """Inner lease marking that an HTTP request may have loaded the model."""

        if self._lifecycle is None:
            yield
            return
        async with self._lifecycle.lease(
            self._model_key,
            idle_seconds=self._idle_unload_seconds(),
            touches_model=True,
            request_keep_alive=self._keep_alive(),
        ):
            yield

    async def _stream_chat(self, payload: dict[str, Any]) -> AsyncIterable[dict[str, Any]]:
        timeout = getattr(self._cfg, "timeout_seconds", 300)
        async with self._request_activity():
            client = _shared_client()
            async with client.stream("POST", self._chat_url(), json=payload, timeout=timeout) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line:
                        yield json.loads(line)

    async def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Single non-streaming POST — used when streaming is unnecessary (titler).
        No iterator, no drain stall: the full response arrives in one round-trip."""
        timeout = getattr(self._cfg, "timeout_seconds", 300)
        async with self._request_activity():
            client = _shared_client()
            response = await client.post(self._chat_url(), json=payload, timeout=timeout)
            response.raise_for_status()
            return response.json()

    def _base_options(self) -> dict[str, Any]:
        """모든 호출에 공통인 options — num_ctx는 항상 명시한다(모듈 docstring 참고)."""
        options: dict[str, Any] = {}
        num_ctx = int(getattr(self._cfg, "num_ctx", 0) or 0)
        if num_ctx > 0:
            options["num_ctx"] = num_ctx
        return options

    def _keep_alive(self) -> str:
        return str(getattr(self._cfg, "keep_alive", "") or "5m")

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
            options: dict[str, Any] = {**self._base_options(), "temperature": 0.0, "num_predict": max_tokens or 20}
            payload = {
                "model": self._cfg.model,
                "messages": ([{"role": "system", "content": system}] if system else []) + _to_ollama_messages(messages),
                "stream": False,
                "think": False,
                "options": options,
                "keep_alive": self._keep_alive(),
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
        options: dict[str, Any] = {**self._base_options(), "temperature": temperature}
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
            "keep_alive": self._keep_alive(),
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
                        # Ollama는 done 메시지에 실측 카운트를 싣는다 — TokenCalibrator가
                        # CJK 휴리스틱을 실제 토크나이저에 맞춰 보정하는 유일한 로컬 신호.
                        input_tokens=item.get("prompt_eval_count"),
                        output_tokens=item.get("eval_count"),
                    )
                    yielded_done = True
                    break
        except ProviderError:
            raise
        except Exception as exc:  # pragma: no cover - exercised via mocks in tests
            raise ProviderError(f"ollama stream failed: {exc}") from exc

        if not yielded_done:
            yield ProviderChunk(kind="done", stop_reason="error")
