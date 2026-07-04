"""EchoProvider — the walking-skeleton model (no network, no API key).

It implements `ILLMProvider` by streaming back a canned, deterministic response
derived from the last user message. Its entire job is to prove the seam end to end:
composition_root → orchestrator → loop → provider → SSE, with zero external deps.

Real providers (`claude.py`, `ollama.py`) drop in behind the same Protocol; nothing
upstream changes. The skeleton ships with this selected so `provider.kind="echo"`
boots offline.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Sequence

from ...core.types import Message, ToolSpec
from .interfaces import ILLMProvider, ProviderCapabilities, ProviderChunk


class EchoProvider(ILLMProvider):
    name = "echo"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(native_tool_calling=False, streaming=True, prompt_caching=False)

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        temperature: float = 0.7,
        response_schema: dict | None = None,  # noqa: ARG002 — skeleton ignores per-call knobs
        think: bool | None = None,  # noqa: ARG002
        max_tokens: int | None = None,  # noqa: ARG002
    ) -> AsyncIterator[ProviderChunk]:
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        yield ProviderChunk(kind="thinking", text="(echo) composing a reply…")
        await asyncio.sleep(0)  # cooperative: let the event loop interleave
        reply = f"echo: {last_user}" if last_user else "echo: (no input)"
        for token in reply.split(" "):
            yield ProviderChunk(kind="text", text=token + " ")
            await asyncio.sleep(0)
        yield ProviderChunk(kind="done", stop_reason="end_turn")
