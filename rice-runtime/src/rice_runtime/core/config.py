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

import logging
import os
from dataclasses import asdict, dataclass, field, fields

from .paths import RicePaths

log = logging.getLogger("rice.core.config")


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
    # 메인 ReAct 루프의 기본 온도. 0.7은 도구 인자(PPT deck spec 같은 깊은 JSON)
    # 생성에서 불필요한 변동성을 키운다 — 재시도 편차·괄호/인용부호 실수의 온상.
    # RICE의 자기개선은 온도의 무작위성이 아니라 스킬·메모리·도구 피드백에서
    # 나오므로 기본을 낮춰도 제품 정체성은 그대로다. 0.5는 도구 정확도와 (로컬
    # thinking 모델의 저온 반복 루프를 피하는) 안정성의 균형점이다(2026-07-15).
    # 창작이 명시적으로 필요한 요청은 사용자가 설정에서 올릴 수 있다. 참고:
    # 구조화 JSON 생성/편집 전용 호출은 이 값과 **무관하게** 0.1로 고정된다
    # (prompts/skill_edit.py·analysis.py — 사용자 온도와 독립).
    temperature: float = 0.5
    base_url: str | None = None  # Ollama only
    # Per-generation OUTPUT cap for the main agent loop (Claude `max_tokens` /
    # Ollama `num_predict`). Without this the providers fall back to their own
    # tiny defaults — Claude's is 4096, which silently truncated long answers
    # (a full slide outline) mid-sentence with stop_reason=max_tokens. Generous
    # enough for long structured text AND large tool-call JSON (a 10-slide deck
    # spec is emitted as output tokens), while still capping runaway generation.
    max_output_tokens: int = 16_384
    # Ollama HTTP 타임아웃(초). 로컬 추론 모델은 긴 생성(PPT 구성안, 코드 등)에서
    # 60초를 쉽게 넘긴다 — 기본 300초로 설정해 ReadTimeout을 방지한다.
    # SaaS 프로바이더(Claude/OpenAI)는 이 값을 사용하지 않는다.
    timeout_seconds: int = 600
    # Ollama 요청 컨텍스트 창(num_ctx). 미지정 시 Ollama 서버 기본값(대개 4096)이
    # 적용돼 긴 턴에서 프롬프트 앞부분(시스템 프롬프트·도구 스키마)이 **소리 없이
    # 잘린다** — 도구 호출 실패·지시 무시의 근본 원인. 로컬 프로파일은 이 값에서
    # 컨텍스트 예산을 역산해 클램프한다(잘림 대신 지능적 압축). qwen3 27b/gpt-oss
    # 20b급이 무리 없이 감당하는 32K를 기본으로 한다. SaaS는 사용하지 않는다.
    num_ctx: int = 32_768
    # Ollama request keep_alive — 비정상 종료로 명시적 unload를 못 보냈을 때도
    # Ollama가 스스로 회수하는 crash-safety 상한. 정상 흐름은 아래 idle 유예 뒤
    # keep_alive=0 unload를 보내므로 이 시간만큼 계속 상주하지 않는다.
    keep_alive: str = "5m"
    # 마지막 활성 lease 후 title/review 같은 즉시 후처리가 이미 로드된 모델을
    # 재사용할 짧은 유예. 만료 시 /api/generate keep_alive=0으로 명시 해제한다.
    idle_unload_seconds: float = 60.0
    # First-run gate: False until the user picks & connects a model in the
    # settings panel and saves (PUT /settings). The app shows the mandatory
    # model-onboarding flow while this is False, then trusts the saved provider.
    configured: bool = False
    # Local LLM 전용: 모델의 reasoning(thinking) 트레이스를 대화 스트림으로
    # 내보낼지. False면 AgentLoop이 thinking SSE 이벤트와 assistant 메타데이터
    # 트레이스를 모두 억제한다 — 모델은 계속 추론하되(소형 모델 안정성 유지)
    # 클라이언트로는 한 조각도 흘려보내지 않는다. SaaS provider(Claude 등)에는
    # 적용하지 않는다(항상 노출): 게이트는 orchestrator가 kind로 판별한다.
    show_thinking: bool = True


@dataclass(frozen=True)
class LearningConfig:
    """Learning-loop knobs (LEARNING_LOOP §5.1, §6; decisions LL-4)."""

    max_iterations: int = 25  # agent loop cap (RUNTIME_DESIGN §2)
    prefetch_recent_facts: int = 8  # N recent facts (LL-4 default, tunable)
    prefetch_fts_top_k: int = 5  # K FTS-matched memories
    skill_inject_token_budget: int = 24_000  # 전문 자동 주입 총 토큰 예산 (§7)
    # 한 턴에 전문 자동 주입하는 스킬 상한. 기본 1 — 최고 득점 하나만 본문으로
    # 들어가고 나머지는 인덱스(+skill_view 온디맨드)로 밀려난다. 프론티어
    # 프로파일에서 컨텍스트가 넉넉하면 2로 올릴 수 있다.
    skill_max_auto_inject: int = 1
    # 자동 주입 점착(sticky) 턴 수 — 한 번 전문 주입된 스킬은 이후 이 턴 수만큼
    # 재매칭 없이도 계속 주입된다("그거 이어서 해줘"가 절차를 잃지 않게). 재매칭
    # 시 카운터가 갱신되고, 0이면 비활성.
    skill_sticky_turns: int = 2
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
    # ── Self-Improvement v3 (learning ledger + suggestion inbox, 2026-07-03) ──
    # 반복 거부 쿨다운: 같은 스킬 제안이 이 시간창 안에서 이 횟수 이상 거부되면
    # 리뷰/발굴이 그 스킬에 대한 제안을 만들지 않는다(설계문서 §5.2.4의 "(향후)
    # 반복 거부 시 제안 빈도 하향" 구현). 최근 거부 목록은 프롬프트에도 실린다.
    review_rejection_cooldown_hours: float = 24.0
    review_max_rejections: int = 2
    # 크로스세션 발굴 주기(초). 0 이면 백그라운드 비활성(수동 POST /skills/discover만).
    # 발굴은 LLM 1회 호출이라 curator 규칙 전이보다 비싸다 — 기본 꺼짐.
    discovery_interval_seconds: int = 0
    # 발굴 1회에 넣을 최근 세션 수 / 세션당 다이제스트 길이(자).
    discovery_max_sessions: int = 8
    discovery_digest_chars: int = 2_000
    # 제안함(pending) 상한 — 발굴/타임아웃이 쌓여도 인박스가 무한히 늘지 않게.
    suggestion_pending_cap: int = 30
    # ── 리뷰 전용 컨텍스트 ───────────────────────────
    # 리뷰 엔진은 메인 턴과 다른 렌더러(learning/review/context.py)를 탄다:
    # 최근 N개 메시지는 전문(과대 도구 출력만 증류), 그 이전은 message id 라벨이
    # 달린 구조화 요약 한 블록. 긴 세션에서도 리뷰 호출이 bounded가 된다.
    review_context_recent_messages: int = 12
    review_context_max_tokens: int = 24_000
    review_context_tool_output_tokens: int = 600
    review_context_message_tokens: int = 2_000
    review_context_summary_tokens: int = 2_000
    # history/catalog/system/tool schema를 모두 합친 **한 요청**의 입력 상한.
    # 개별 24K 예산 둘을 독립 합산해 기본 Ollama 32K 창을 넘기지 않게 한다.
    review_request_max_tokens: int = 28_000
    # review는 full replacement body를 tool argument로 낼 수 있어 8K를 예약한다.
    # composition root가 로컬 num_ctx가 더 작으면 둘을 함께 비례 클램프한다.
    review_output_max_tokens: int = 8_192
    # ── 서버 소유 리뷰 잡 ────────────────────────────
    # 턴 종료 시 observe()가 남긴 pending 리뷰 잡을 클라이언트가 이만큼(초) 지나도
    # claim하지 않으면(탭 종료 등) 스케줄러 sweep이 백그라운드로 실행해 제안함으로
    # 라우팅한다. 0 이면 sweep 비활성(잡은 pending으로 남는다).
    review_job_stale_seconds: int = 900
    # 리뷰 LLM 호출(provider/context) 실패 시 잡을 pending으로 되돌려 재시도하는
    # 총 시도 상한 — 이전엔 실패가 done으로 마감돼 학습 기회가 조용히 소실됐다.
    # 상한 도달 시 failed로 마감(감사 가능, 무한 재시도 방지).
    review_job_max_attempts: int = 3
    # ── 설명용 스킬 분석 사이드카 (learning/skill/analysis.py) ──
    # 본문 변경 경로들이 enqueue 한 재분석 잡을 스케줄러가 이 주기(초)로 드레인.
    # 0 이면 백그라운드 비활성 (수동 POST /skills/{id}/analysis/refresh 만).
    # LLM 1회 호출이지만 트리거가 전부 사용자 승인 이벤트라 빈도가 낮다 — 기본 켜짐.
    analysis_interval_seconds: int = 180
    analysis_max_per_tick: int = 2


@dataclass(frozen=True)
class ContextConfig:
    """Provider-facing context rendering knobs.

    Inspired by Gemini CLI's context manager, but intentionally deterministic for
    RICE's local runtime: no extra model calls are required just to fit a turn.
    Durable session history stays full; these limits only affect what is rendered
    into each LLM request.
    """

    enabled: bool = True
    max_tokens: int = 96_000
    retained_tokens: int = 64_000
    recent_messages: int = 8
    subagent_history_messages: int = 24
    # ── sub-agent 실행 한도 ──────────────────────────
    # 이전엔 timeout이 approval_timeout_seconds를 빌려 썼다(무관한 커플링) —
    # 전용 노브로 분리. max_concurrent는 한 라운드에 병렬 스폰되는 sub-agent
    # 폭주(토큰/비용)를 막는 세마포어 상한이다.
    # timeout 300: sub-agent가 read-only 도구로 파일을 실제로 읽게 된 뒤로는
    # (agent/subagents.py 미니 ReAct 루프) 90초가 자주 모자란다 — "30분짜리
    # 작업도 OK"가 제품 방향이므로 심부름꾼의 시간도 그에 맞춘다.
    subagent_timeout_seconds: int = 300
    subagent_max_concurrent: int = 3
    # sub-agent 미니 루프의 도구 라운드 상한 — 라운드당 여러 도구를 병렬로
    # 부를 수 있으므로 6이면 수십 파일 분석까지 커버된다.
    subagent_max_tool_rounds: int = 6
    summary_tokens: int = 1_600
    tool_output_threshold_tokens: int = 4_000
    tool_output_retained_tokens: int = 1_200
    message_threshold_tokens: int = 12_000
    message_retained_tokens: int = 3_000


@dataclass(frozen=True)
class AgentConfig:
    """Plan-and-execute × ReAct 노브 (RICE_AGENT_EXECUTION.md §3).

    로컬 LLM이 메인 모델인 배포에서 "토큰 절약"이 아니라 "수행 품질"을 사는
    지점들이다: 계획 수립에 한 번 더 LLM을 부르고, 매 반복 계획 상태를 프롬프트에
    다시 실으며, 계획이 크면 반복 상한을 늘린다."""

    # 턴 시작 시 *eager* 플래너 호출 여부(의도가 명백히 복합일 때 미리 계획 수립).
    # False면 턴 시작에 계획을 세우지 않는다 — 단, 실행 중 lazy 에스컬레이션은 이
    # 플래그와 무관하게 항상 가능하다(loop.py의 start_plan): 순수 ReAct로 돌다가
    # 분해 너비가 드러나면 모델이 그 자리에서 계획으로 승격한다. 즉 True=eager+lazy,
    # False="기본 ReAct, 복잡도가 오르면 lazy 승격"(플래너 LLM 호출 세금 없음).
    planning_enabled: bool = True
    # 플래너에게 보여줄 최근 대화 메시지 수(의도 파악용 문맥).
    planner_recent_messages: int = 12
    # 플래너 1회 호출 타임아웃(초). 초과 시 계획 없이 진행한다(fail-soft).
    # think=False + 디코딩 수준 토큰 상한이 걸린 뒤로는(planner.py) 정상 호출이
    # 이 근처에 갈 일이 없다 — 240은 무한 thinking 루프에 시간을 2배 더 주던
    # 증상 치료였고, 원인(루프)을 디코딩에서 끊었으므로 90으로 되돌린다.
    planner_timeout_seconds: int = 90
    # 플래너가 세울 수 있는 단계 수 상한(프롬프트 지시 + 코드 클램프).
    plan_max_steps: int = 8
    # 계획이 있는 턴의 반복 상한 — max(learning.max_iterations, 12 + 6×단계수)를
    # 이 값으로 캡한다. 계획이 없으면 기존 max_iterations 그대로.
    plan_max_iterations: int = 60
    # 이만큼 연속 도구 라운드 동안 update_plan이 없으면 시스템 프롬프트에
    # 재평가(ReAct reflection) 지시를 주입한다.
    plan_drift_guard_rounds: int = 4
    # 오케스트레이션 프로파일(agent/profiles.py): "auto" | "local" | "frontier".
    # auto는 provider에서 유추한다(ollama/로컬 base_url → local, SaaS → frontier).
    # local은 소형 모델 보정 장치(액션 규칙 에필로그·서사 넛지·num_ctx 예산
    # 클램프)를 켠다 — LM Studio 등 openai 호환 로컬 서버는 "local"로 강제한다.
    orchestration_profile: str = "auto"


@dataclass(frozen=True)
class UIConfig:
    language: str = "ko"
    theme: str = "system"  # light | dark | system
    font: str = "default"
    # Absolute output root for agent-created files. None keeps the default
    # ``~/.rice/workspace``. Applied while the runtime starts so every builtin
    # shares the same sandbox boundary for its whole lifetime.
    workspace_path: str | None = None


@dataclass(frozen=True)
class Config:
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
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
    defaults (keeps the skeleton runnable with no deps).

    Fail-soft: 파손된 YAML, dict가 아닌 최상위 값, 구버전의 미지 키 어느 것도
    부팅을 막지 않는다 — 경고를 남기고 기본값(또는 인식 가능한 부분만)으로
    기동한다. 설정 파일 오류로 런타임이 아예 안 뜨는 것이 최악의 상태다.
    """
    cfg_path = paths.config
    if not cfg_path.exists():
        return Config()
    try:
        import yaml  # optional dependency
    except ImportError:
        return Config()
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — yaml.YAMLError + 인코딩/IO 오류
        log.warning("config.yaml is corrupted — booting with defaults", exc_info=True)
        return Config()
    if not isinstance(raw, dict):
        log.warning("config.yaml top level is %s (expected mapping) — booting with defaults", type(raw).__name__)
        return Config()
    return _merge(Config(), raw)


def save_config(paths: RicePaths, config: Config) -> None:
    """Persist to `config.yaml` atomically. No-op if PyYAML is unavailable.

    temp write → fsync → rename: 저장 도중 프로세스가 죽어도 config.yaml은
    이전 버전 전체 또는 새 버전 전체 중 하나다(반쪽 YAML 없음).
    """
    try:
        import yaml
    except ImportError:
        return
    paths.ensure()
    target = paths.config
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(yaml.safe_dump(config.to_dict(), allow_unicode=True))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)


def _known_fields(cls, section: dict) -> dict:
    """dataclass가 모르는 키를 걸러낸다 — 구버전/수동 편집이 남긴 잔여 키가
    TypeError로 전체 로드를 무산시키지 않게(무시된 키는 경고만)."""
    names = {f.name for f in fields(cls)}
    unknown = set(section) - names
    if unknown:
        log.warning("config.yaml: ignoring unknown %s keys: %s", cls.__name__, sorted(unknown))
    return {k: v for k, v in section.items() if k in names}


def _merge(base: Config, raw: dict) -> Config:
    """Shallow-merge a parsed dict onto defaults, section by section."""

    def section(name: str) -> dict:
        value = raw.get(name)
        return value if isinstance(value, dict) else {}

    provider_section = section("provider")
    prov = {**asdict(base.provider), **_known_fields(ProviderConfig, provider_section)}
    # 2026-07 lifecycle migration: the previous release wrote keep_alive=30m and
    # had no explicit-idle field. Treat precisely that old schema/value pair as
    # the legacy default, not as a deliberate 30m user override. Once a config is
    # saved with idle_unload_seconds present, an explicit 30m remains untouched.
    if provider_section.get("keep_alive") == "30m" and "idle_unload_seconds" not in provider_section:
        prov["keep_alive"] = base.provider.keep_alive
    learn = {**asdict(base.learning), **_known_fields(LearningConfig, section("learning"))}
    context = {**asdict(base.context), **_known_fields(ContextConfig, section("context"))}
    agent = {**asdict(base.agent), **_known_fields(AgentConfig, section("agent"))}
    ui = {**asdict(base.ui), **_known_fields(UIConfig, section("ui"))}
    return Config(
        provider=ProviderConfig(**prov),
        learning=LearningConfig(**learn),
        context=ContextConfig(**context),
        agent=AgentConfig(**agent),
        ui=UIConfig(**ui),
    )
