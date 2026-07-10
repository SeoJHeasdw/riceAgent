"""Provider registry — the one place `Config.provider.kind` maps to an impl.

Implements `IProviderRegistry`. The composition root asks it for the configured
provider; the agent loop never names a concrete provider. New providers register
here (a one-line factory entry), keeping the swap seam closed (RUNTIME_DESIGN §6).

Real providers are lazily imported so the skeleton runs with only `echo` available
(no anthropic/httpx deps required to boot).

Credentials (API key, custom base_url, watsonx project_id) are NOT in config.yaml —
they live in `state.db` (provider_models). The registry takes a `credentials`
lookup (model_id → ProviderCredentials) so it can build a provider with the active
model's secret at resolve time (re-evaluated each turn, C-1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ...core.config import ConfigStore, ProviderConfig
from ...core.errors import ProviderError
from .echo import EchoProvider
from .interfaces import ILLMProvider, IProviderRegistry


@dataclass(frozen=True)
class ProviderCredentials:
    """Per-model secret/connection info, sourced from provider_models (state.db)."""

    api_key: str | None = None
    base_url: str | None = None  # openai-compatible custom endpoint / watsonx region
    project_id: str | None = None  # watsonx only


CredentialLookup = Callable[[str], "ProviderCredentials | None"]


class ProviderRegistry(IProviderRegistry):
    def __init__(self, config_store: ConfigStore, credentials: CredentialLookup | None = None) -> None:
        # Holds the store (not a snapshot) so `get` builds providers with the
        # *current* provider config after a runtime settings change (C-1).
        self._config_store = config_store
        self._credentials = credentials
        self._factories: dict[str, Callable[[ProviderConfig, ProviderCredentials | None], ILLMProvider]] = {
            "echo": lambda _cfg, _creds: EchoProvider(),
            "claude": self._make_claude,
            "ollama": self._make_ollama,
            "openai": self._make_openai,
            "watsonx": self._make_watsonx,
        }
        # 단일 슬롯 인스턴스 캐시 — 같은 설정이면 같은 provider를 돌려준다.
        # 매 턴 새 인스턴스를 만들면 provider가 보유한 HTTP 커넥션 풀/SDK
        # 클라이언트가 매번 버려진다(ReAct 반복마다 TCP 재수립). 시그니처가
        # 바뀌면(모델 스위치 등) 새로 만든다 — C-1(라이브 설정 반영)은 유지된다.
        self._cache: tuple[tuple, ILLMProvider] | None = None

    def available(self) -> list[str]:
        return list(self._factories.keys())

    def get(self, kind: str) -> ILLMProvider:
        factory = self._factories.get(kind)
        if factory is None:
            raise ProviderError(f"unknown provider kind: {kind}")
        cfg = self._config_store.config.provider
        creds = self._credentials(cfg.model) if self._credentials else None
        signature = (
            kind, cfg.model, cfg.base_url, cfg.timeout_seconds,
            getattr(cfg, "num_ctx", None), getattr(cfg, "keep_alive", None),
            (creds.api_key, creds.base_url, creds.project_id) if creds else None,
        )
        if self._cache is not None and self._cache[0] == signature:
            return self._cache[1]
        provider = factory(cfg, creds)
        # 이전 인스턴스는 참조만 놓는다 — in-flight 스트림이 아직 쓰고 있을 수
        # 있으므로(설정 변경은 다음 턴부터) 강제로 닫지 않고 GC에 맡긴다.
        self._cache = (signature, provider)
        return provider

    # ── lazy real providers (not needed for the skeleton) ────────────────────
    def _make_claude(self, cfg: ProviderConfig, creds: ProviderCredentials | None) -> ILLMProvider:
        try:
            from .claude import ClaudeProvider  # noqa: PLC0415 - lazy by design
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("claude provider not available (install anthropic)") from exc
        return ClaudeProvider(cfg, api_key=creds.api_key if creds else None)

    def _make_ollama(self, cfg: ProviderConfig, _creds: ProviderCredentials | None) -> ILLMProvider:
        try:
            from .ollama import OllamaProvider  # noqa: PLC0415 - lazy by design
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("ollama provider not available") from exc
        return OllamaProvider(cfg)

    def _make_openai(self, cfg: ProviderConfig, creds: ProviderCredentials | None) -> ILLMProvider:
        if creds is None or not creds.api_key:
            raise ProviderError("openai provider needs an API key (register the model with a key)")
        from .openai import OpenAIProvider  # noqa: PLC0415 - lazy by design

        return OpenAIProvider(cfg, api_key=creds.api_key, base_url=creds.base_url)

    def _make_watsonx(self, cfg: ProviderConfig, creds: ProviderCredentials | None) -> ILLMProvider:
        if creds is None or not creds.api_key or not creds.project_id or not creds.base_url:
            raise ProviderError("watsonx provider needs api_key, project_id and base_url (region endpoint)")
        from .watsonx import WatsonxProvider  # noqa: PLC0415 - lazy by design

        return WatsonxProvider(cfg, api_key=creds.api_key, project_id=creds.project_id, base_url=creds.base_url)
