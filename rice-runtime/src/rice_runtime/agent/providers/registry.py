"""Provider registry — the one place `Config.provider.kind` maps to an impl.

Implements `IProviderRegistry`. The composition root asks it for the configured
provider; the agent loop never names a concrete provider. New providers register
here (a one-line factory entry), keeping the swap seam closed (RUNTIME_DESIGN §6).

`claude` / `ollama` are declared but lazily imported so the skeleton runs with only
`echo` available (no anthropic/httpx deps required to boot).
"""

from __future__ import annotations

from typing import Callable

from ...core.config import ConfigStore, ProviderConfig
from ...core.errors import ProviderError
from .echo import EchoProvider
from .interfaces import ILLMProvider, IProviderRegistry


class ProviderRegistry(IProviderRegistry):
    def __init__(self, config_store: ConfigStore) -> None:
        # Holds the store, not a snapshot, so `get` builds providers with the
        # *current* provider config after a runtime settings change (C-1).
        self._config_store = config_store
        self._factories: dict[str, Callable[[ProviderConfig], ILLMProvider]] = {
            "echo": lambda _cfg: EchoProvider(),
            "claude": self._make_claude,
            "ollama": self._make_ollama,
        }

    def available(self) -> list[str]:
        return list(self._factories.keys())

    def get(self, kind: str) -> ILLMProvider:
        factory = self._factories.get(kind)
        if factory is None:
            raise ProviderError(f"unknown provider kind: {kind}")
        return factory(self._config_store.config.provider)

    # ── lazy real providers (not needed for the skeleton) ────────────────────
    def _make_claude(self, cfg: ProviderConfig) -> ILLMProvider:
        try:
            from .claude import ClaudeProvider  # noqa: PLC0415 - lazy by design
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("claude provider not available (install anthropic)") from exc
        return ClaudeProvider(cfg)

    def _make_ollama(self, cfg: ProviderConfig) -> ILLMProvider:
        try:
            from .ollama import OllamaProvider  # noqa: PLC0415 - lazy by design
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("ollama provider not available") from exc
        return OllamaProvider(cfg)
