"""Runtime configuration (RUNTIME_DESIGN §6, §7; LEARNING_LOOP §6).

Two concerns, deliberately separated:

  RuntimeArgs   — process-launch params injected by Electron on spawn
                  (`--port` dynamic, `--token` one-shot). Security boundary
                  (RUNTIME_DESIGN §10.1): the token gates all local API access.

  Config        — user-facing settings backed by `~/.rice/config.yaml`
                  (model/provider, language, theme, learning-loop knobs).
                  Surfaced via GET/PUT /settings.

Both are plain dataclasses with explicit defaults so the walking skeleton boots
with zero config files present. YAML load/save is intentionally lazy (PyYAML is a
packaging dep) — `load_config` falls back to defaults if the file or parser is
absent, which keeps `core` import-clean.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .paths import RicePaths


@dataclass(frozen=True)
class RuntimeArgs:
    """Injected by the Electron supervisor at spawn (CONTRACTS §1.1)."""

    port: int = 0  # 0 → OS picks a free port (the supervisor normally injects one)
    token: str = ""  # one-shot bearer; empty = auth disabled (dev only)
    host: str = "127.0.0.1"  # localhost-only; never bind 0.0.0.0


@dataclass(frozen=True)
class ProviderConfig:
    """Active LLM provider (RUNTIME_DESIGN §6). Keys are NOT stored here — the
    Claude API key lives in the OS keychain; this only names the selection."""

    kind: str = "claude"  # "claude" (SaaS) | "ollama" (on-prem) | "echo" (skeleton)
    model: str = "claude-sonnet-4-6"
    temperature: float = 0.7
    base_url: str | None = None  # Ollama only


@dataclass(frozen=True)
class LearningConfig:
    """Learning-loop knobs (LEARNING_LOOP §5.1, §6; decisions LL-4)."""

    max_iterations: int = 25  # agent loop cap (RUNTIME_DESIGN §2)
    prefetch_recent_facts: int = 8  # N recent facts (LL-4 default, tunable)
    prefetch_fts_top_k: int = 5  # K FTS-matched memories
    skill_inject_token_budget: int = 24_000  # inject-all-active-under-budget (§7)
    stale_after_days: int = 3  # active → stale (§5.1)
    archive_after_days: int = 7  # stale → archived
    approval_timeout_seconds: int = 300  # HITL safe-default reject (§5.1)


@dataclass(frozen=True)
class UIConfig:
    language: str = "ko"
    theme: str = "system"  # light | dark | system
    font: str = "default"


@dataclass(frozen=True)
class Config:
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)
    ui: UIConfig = field(default_factory=UIConfig)

    def to_dict(self) -> dict:
        return asdict(self)


class ConfigStore:
    """A mutable holder for the live `Config`.

    Settings are user-editable at runtime (PUT /settings — model/provider toggle is
    a product feature, RUNTIME_DESIGN §6). Components that must observe the *current*
    config (provider registry, orchestrator) hold this store, not a captured `Config`
    snapshot, so an update is visible everywhere without re-wiring the graph.
    """

    def __init__(self, config: Config) -> None:
        self._config = config

    @property
    def config(self) -> Config:
        return self._config

    def update(self, config: Config) -> None:
        self._config = config


def load_config(paths: RicePaths) -> Config:
    """Load `config.yaml` over the defaults. Missing file or missing PyYAML →
    defaults (keeps the skeleton runnable with no deps)."""
    cfg_path = paths.config
    if not cfg_path.exists():
        return Config()
    try:
        import yaml  # optional dependency
    except ImportError:
        return Config()
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return _merge(Config(), raw)


def save_config(paths: RicePaths, config: Config) -> None:
    """Persist to `config.yaml`. No-op if PyYAML is unavailable."""
    try:
        import yaml
    except ImportError:
        return
    paths.ensure()
    paths.config.write_text(yaml.safe_dump(config.to_dict(), allow_unicode=True), encoding="utf-8")


def _merge(base: Config, raw: dict) -> Config:
    """Shallow-merge a parsed dict onto defaults, section by section."""
    prov = {**asdict(base.provider), **(raw.get("provider") or {})}
    learn = {**asdict(base.learning), **(raw.get("learning") or {})}
    ui = {**asdict(base.ui), **(raw.get("ui") or {})}
    return Config(
        provider=ProviderConfig(**prov),
        learning=LearningConfig(**learn),
        ui=UIConfig(**ui),
    )
