"""ILLMProvider — the model-swap seam (RUNTIME_DESIGN §6).

This Protocol is the boundary that absorbs provider differences so the agent loop
never branches on "is this Claude or Ollama". It normalizes three things:

  - tool calling  : Claude native `tool_use` ↔ Ollama function-calling (or prompt
                    fallback) → one `ToolCall` shape (core.types).
  - streaming     : each provider's stream → a single `ProviderChunk` sequence the
                    loop turns into SSE events (9-event contract, CONTRACTS §1.3).
  - prompt caching: Claude `cache_control` ↔ Ollama keep-alive — expressed as a
                    capability flag; the loop/provider handle it, callers don't.

**FROZEN.** New providers implement this; they do not widen it. Adding a capability
means adding a field to `ProviderCapabilities`, never changing `stream`'s signature.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Literal, Protocol, Sequence, runtime_checkable

from ...core.types import Message, ToolCall, ToolSpec


@dataclass(frozen=True)
class ProviderCapabilities:
    native_tool_calling: bool = False
    streaming: bool = True
    prompt_caching: bool = False
    vision: bool = False


@dataclass(frozen=True)
class ProviderChunk:
    """One normalized unit of a provider's streamed response.

    kind:
      "thinking"  → `text` is a reasoning delta (→ SSE `thinking`)
      "text"      → `text` is an answer delta   (→ SSE `message` delta)
      "tool_call" → `tool_call` is a fully-formed request (→ SSE `tool_call`)
      "done"      → end of this assistant turn; `stop_reason` explains why
    """

    kind: Literal["thinking", "text", "tool_call", "done"]
    text: str | None = None
    tool_call: ToolCall | None = None
    stop_reason: Literal["end_turn", "tool_use", "max_tokens", "error"] | None = None
    # Actual token usage, populated on the "done" chunk when the provider reports
    # it (Claude does; echo/ollama may not). Feeds `TokenCalibrator` so the CJK
    # budget estimate self-corrects from real numbers. None = unknown.
    input_tokens: int | None = None
    output_tokens: int | None = None


@runtime_checkable
class ILLMProvider(Protocol):
    """A model provider. Implementations live in `agent/providers/` and are chosen
    by `registry.py` from `Config.provider` — never imported directly by the loop."""

    name: str

    def capabilities(self) -> ProviderCapabilities: ...

    def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        temperature: float = 0.7,
    ) -> AsyncIterator[ProviderChunk]:
        """Stream one assistant turn. Yields `ProviderChunk`s and ends with a
        single `kind="done"` chunk. Implementations that don't support native tool
        calling synthesize `tool_call` chunks from a prompt-based fallback so the
        loop sees a uniform stream regardless of provider."""
        ...


@runtime_checkable
class IProviderRegistry(Protocol):
    """Resolves a provider by config. The only place that maps `kind` → impl."""

    def get(self, kind: str) -> ILLMProvider: ...
    def available(self) -> list[str]: ...
