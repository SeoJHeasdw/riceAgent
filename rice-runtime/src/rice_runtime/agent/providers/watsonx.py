"""IBM watsonx.ai provider adapter (RUNTIME_DESIGN §6).

watsonx is not "just an API key": it needs an IAM API key (exchanged for a short-
lived bearer token, ~1h), a `project_id`, and a region endpoint (base_url, e.g.
https://us-south.ml.cloud.ibm.com). Its chat API mirrors the OpenAI chat shape, so
message/tool mapping + streaming assembly are reused from `_openai_compat`; this
file owns the IAM token exchange (cached) and the watsonx HTTP call.

Both network edges (`_iam_token`, `_stream_raw`) are isolated so unit tests run
offline.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterable, AsyncIterator, Sequence
from typing import Any

from ...core.config import ProviderConfig
from ...core.errors import ProviderError, classify_provider_error
from ...core.types import Message, ToolSpec
from . import _openai_compat as oai
from .interfaces import ILLMProvider, ProviderCapabilities, ProviderChunk

_IAM_URL = "https://iam.cloud.ibm.com/identity/token"
_API_VERSION = "2023-05-29"  # watsonx ml text/chat API version


class WatsonxProvider(ILLMProvider):
    name = "watsonx"

    def __init__(self, cfg: ProviderConfig, *, api_key: str, project_id: str, base_url: str) -> None:
        self._cfg = cfg
        self._api_key = api_key
        self._project_id = project_id
        self._base_url = base_url.rstrip("/")
        self._token: str | None = None
        self._token_exp: float = 0.0  # monotonic deadline

    def capabilities(self) -> ProviderCapabilities:
        # tool calling is model-dependent on watsonx; advertise it and let the
        # model fall back to text if it doesn't support functions.
        return ProviderCapabilities(native_tool_calling=True, streaming=True, prompt_caching=False, vision=False)

    async def _iam_token(self) -> str:
        """Exchange the IAM API key for a bearer token; cache until ~1min before expiry."""
        if self._token and time.monotonic() < self._token_exp:
            return self._token
        import httpx  # noqa: PLC0415 - optional dependency by design

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                _IAM_URL,
                data={
                    "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                    "apikey": self._api_key,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            body = resp.json()
        token = body.get("access_token")
        if not token:
            raise ProviderError("watsonx IAM token exchange returned no access_token")
        self._token = token
        self._token_exp = time.monotonic() + max(60, int(body.get("expires_in", 3600)) - 60)
        return token

    async def _stream_raw(self, payload: dict[str, Any], token: str) -> AsyncIterable[dict[str, Any]]:
        """POST /ml/v1/text/chat_stream (SSE). Yields parsed chunk dicts."""
        import httpx  # noqa: PLC0415 - optional dependency by design

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        params = {"version": _API_VERSION}
        async with httpx.AsyncClient(base_url=self._base_url, timeout=120.0) as client:
            async with client.stream(
                "POST", "/ml/v1/text/chat_stream", params=params, json=payload, headers=headers
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if not data or data == "[DONE]":
                        continue
                    yield json.loads(data)

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        temperature: float = 0.7,
        response_schema: dict | None = None,  # noqa: ARG002 — watsonx chat has no schema-constrained decoding
        think: bool | None = None,  # noqa: ARG002
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        payload: dict[str, Any] = {
            "model_id": self._cfg.model,
            "project_id": self._project_id,
            "messages": oai.to_oai_messages(system, messages),
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = oai.to_oai_tools(tools)

        try:
            token = await self._iam_token()
            async for chunk in oai.assemble_stream(self._stream_raw(payload, token)):
                yield chunk
        except ProviderError:
            raise
        except Exception as exc:  # pragma: no cover - exercised via mocks in tests
            raise classify_provider_error(exc, self.name) from exc
