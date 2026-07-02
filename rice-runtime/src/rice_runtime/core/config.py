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
    # First-run gate: False until the user picks & connects a model in the
    # settings panel and saves (PUT /settings). The app shows the mandatory
    # model-onboarding flow while this is False, then trusts the saved provider.
    configured: bool = False


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
    # 백그라운드 curator 주기(초). 0 이면 비활성(수동 POST /skills/curate 만).
    # 기본 1시간 — 데스크톱 앱이 떠 있는 동안 stale/archive 전이를 자동 반영.
    curator_interval_seconds: int = 3600
    # review()(LLM 기반 스킬 저작 리뷰, /learning-review) 호출 여부 게이트
    # (learning/review/gate.py::should_review) — 매 턴 LLM을 부르지 않기 위한
    # 비용 통제. 이 턴의 tool_calls_made가 이 값 이상이면 리뷰를 시도한다.
    review_min_tool_calls: int = 3
    # 명확한 신호(교정/실패/스킬화 의도)가 없어도, 세션당 이만큼 턴이 지나면 그냥
    # 한 번 리뷰를 시도한다(Hermes의 nudge-interval 카운터와 같은 목적 — 서서히
    # 쌓이는 학습 기회를 놓치지 않기 위한 폴백).
    review_nudge_turns: int = 8
    # Phase D 하드닝: 스킬 본문 상한(자) — 폭주한 LLM 응답에 대한 마지막 방어선.
    # 넉넉하게 잡음(실제 스킬은 이 근처도 안 감).
    skill_max_body_chars: int = 20_000
    # Phase B(curator 통합): 통합 분석 1회 호출에 담을 후보 스킬 전체 본문 총합
    # 상한(자) — 수동 트리거+저빈도라 review()보다 훨씬 관대하게 잡는다(전체
    # 본문을 보여줘야 진짜 중복/유사 판단이 가능하므로). 넘으면 use 하위 스킬부터
    # 후보에서 제외.
    curator_candidate_budget_chars: int = 300_000


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
