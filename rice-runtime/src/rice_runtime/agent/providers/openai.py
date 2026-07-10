"""OpenAI (and OpenAI-compatible) provider adapter (RUNTIME_DESIGN §6).

Covers two UI cases with one impl:
  - "openai":   default base_url https://api.openai.com/v1
  - "기타" custom OpenAI-compatible endpoint: caller-supplied base_url
    (OpenRouter, groq, vLLM, a corporate gateway, …)

Wire format + streaming-delta assembly live in `_openai_compat`; this file only
owns the HTTP call (httpx) and bearer auth. Network is isolated behind
`_stream_raw` so unit tests replace it with a fake.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable, AsyncIterator, Sequence
from typing import Any

from ...core.config import ProviderConfig
from ...core.errors import ProviderError
from ...core.types import Message, ToolSpec
from . import _openai_compat as oai
from .interfaces import ILLMProvider, ProviderCapabilities, ProviderChunk

_DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIProvider(ILLMProvider):
    name = "openai"

    def __init__(self, cfg: ProviderConfig, *, api_key: str, base_url: str | None = None) -> None:
        self._cfg = cfg
        self._api_key = api_key
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(native_tool_calling=True, streaming=True, prompt_caching=False, vision=False)

    async def _stream_raw(self, payload: dict[str, Any]) -> AsyncIterable[dict[str, Any]]:
        """POST /chat/completions (SSE). Yields parsed chunk dicts; skips `[DONE]`."""
        import httpx  # noqa: PLC0415 - optional dependency by design

        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(base_url=self._base_url, timeout=120.0) as client:
            async with client.stream("POST", "/chat/completions", json=payload, headers=headers) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    yield json.loads(data)

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        temperature: float = 0.7,
        response_schema: dict | None = None,
        think: bool | None = None,  # noqa: ARG002 — no portable wire knob across OpenAI-compatible servers
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        payload: dict[str, Any] = {
            "model": self._cfg.model,
            "messages": oai.to_oai_messages(system, messages),
            "stream": True,
            "temperature": temperature,
        }
        # 공식 API는 stream_options 없이는 usage를 절대 안 보낸다 → 요청해서
        # TokenCalibrator가 실측 보정을 받게 한다. 커스텀 호환 엔드포인트(vLLM/
        # 게이트웨이 등)는 미지원 필드에 400을 내는 서버가 있어 보내지 않는다 —
        # 그런 서버가 usage를 상시 싣는 경우는 assemble_stream이 알아서 줍는다.
        if self._base_url == _DEFAULT_BASE_URL:
            payload["stream_options"] = {"include_usage": True}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": response_schema},
            }
        if tools:
            payload["tools"] = oai.to_oai_tools(tools)

        try:
            async for chunk in oai.assemble_stream(self._stream_raw(payload)):
                yield chunk
        except ProviderError:
            raise
        except Exception as exc:  # pragma: no cover - exercised via mocks in tests
            raise ProviderError(f"openai stream failed: {exc}") from exc
