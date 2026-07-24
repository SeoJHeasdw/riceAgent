"""composition_root — the ONE wiring point (RUNTIME_DIRECTORY §5, invariant #2).

This is the only module allowed to import concrete implementations and bolt them
together. Everywhere else depends on `*.interfaces` Protocols. If you find a
`from ...skill.lifecycle import X` anywhere but here, the invariant is broken.

It builds a `Container`: every collaborator constructed once, injected by Protocol,
and the `Orchestrator` (IAgent) assembled from them. Routes and `app.py` receive the
container and read services off it — they never `new` anything themselves.

Swapping an implementation (e.g. a real retrieval-backed LearningService, the
Claude provider, the child-process sandbox) is a one-line change *here* and nowhere
else — that is the entire point of the seam discipline.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..agent.builtins import build_builtin_tools, build_subagent_toolset, scratch_tool_factory
from ..agent.builtins import subagent_tools
from ..agent.interfaces import IAgent, IApprovalGate, ISessionTitler
from ..agent.orchestrator import Orchestrator
from ..agent.planner import START_PLAN_SPEC, UPDATE_PLAN_SPEC
from ..agent.tool_dispatch import BRIDGE_TOOL_SPECS
from ..agent.subagents import SubAgentRunner
from ..agent.titler import SessionTitler
from ..agent.providers.interfaces import ILLMProvider, IProviderRegistry
from ..agent.providers.registry import ProviderCredentials, ProviderRegistry
from ..connector.interfaces import IConnectorService
from ..connector.service import ConnectorService
from ..core.config import Config, ConfigStore, RuntimeArgs, load_config
from ..core.errors import ProviderError
from ..core.events import EventBus
from ..core.paths import RicePaths, WorkspaceLocation
from ..core.storage.sqlite import Database
from ..core.types import ToolSpec
from ..core.tokens import TokenCalibrator
from ..execution.interfaces import IPluginLoader
from ..execution.service import ChildProcessSandbox, PluginLoaderService
from ..execution.plugin.builtin import BuiltinPluginCatalog
from ..learning.interfaces import ILearningService
from ..learning.service import LearningService
from .approval import ApprovalRegistry
from .session.interfaces import ISessionStore
from .session.session_manager import SessionManager

log = logging.getLogger("rice.composition")


def review_token_budgets(config: Config) -> tuple[int, int]:
    """Return one review call's live (input, output) token limits.

    Ollama shares a physical ``num_ctx`` between prompt and generation.  Keep a
    small estimator margin and reduce the output reservation on 4K/8K models so
    the fixed review prompt/tool schema still has a viable input window.  SaaS
    providers use the independently configured caps.
    """
    learning = config.learning
    input_budget = max(0, int(learning.review_request_max_tokens))
    output_budget = max(1, int(learning.review_output_max_tokens))
    provider = config.provider
    num_ctx = max(0, int(provider.num_ctx or 0))
    if provider.kind != "ollama" or num_ctx <= 0:
        return input_budget, output_budget

    # 32K -> 8K output / 1K margin; 8K -> 2K / 512; 4K -> 1K / 256.
    # Below ~4K the full review protocol may still be impossible; the engine
    # detects that before provider invocation and leaves an auditable failed job.
    output_budget = min(output_budget, max(512, num_ctx // 4))
    margin = min(1_024, max(256, num_ctx // 16))
    input_budget = min(input_budget, max(0, num_ctx - output_budget - margin))
    return input_budget, output_budget


@dataclass
class Container:
    """The wired object graph. Held by `app.py`; passed to routes."""

    args: RuntimeArgs
    paths: RicePaths
    config_store: ConfigStore
    db: Database
    events: EventBus
    sessions: ISessionStore
    learning: ILearningService
    connectors: IConnectorService
    plugins: IPluginLoader
    approvals: IApprovalGate
    providers: IProviderRegistry
    resolve_provider: Callable[[], ILLMProvider]
    agent: IAgent
    titler: ISessionTitler
    token_calibrator: TokenCalibrator
    _shutdown_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _async_shutdown_complete: bool = field(default=False, init=False, repr=False)
    _db_closed: bool = field(default=False, init=False, repr=False)

    @property
    def config(self) -> Config:
        """Convenience read of the live config (routes/health)."""
        return self.config_store.config

    def shutdown(self) -> None:
        """Legacy synchronous teardown used by CLI/tests.

        Production ASGI shutdown uses :meth:`shutdown_async` so Ollama can receive
        its explicit unload command before the process exits.  This fallback still
        aborts work, cancels delayed timers, and relies on finite keep_alive if it
        cannot perform network I/O.
        """
        self._abort_all()
        cancel_pending = getattr(self.providers, "cancel_pending", None)
        if cancel_pending is not None:
            cancel_pending()
        if not self._db_closed:
            self.db.close()
            self._db_closed = True

    def _abort_all(self) -> None:
        abort_all = getattr(self.agent, "abort_all", None)
        if abort_all is not None:
            try:
                abort_all()
            except Exception:  # noqa: BLE001 — teardown 은 어떤 경우에도 진행돼야 한다
                log.warning("abort_all during shutdown failed", exc_info=True)

    async def shutdown_async(self) -> None:
        """Idempotent ASGI teardown: abort, unload local models, close DB."""

        async with self._shutdown_lock:
            if self._async_shutdown_complete:
                return
            self._abort_all()
            shutdown_providers = getattr(self.providers, "shutdown", None)
            if shutdown_providers is not None:
                try:
                    await shutdown_providers()
                except Exception:  # noqa: BLE001 — DB/process teardown must continue
                    log.warning("provider shutdown failed", exc_info=True)
            if not self._db_closed:
                self.db.close()
                self._db_closed = True
            self._async_shutdown_complete = True


def build_container(
    args: RuntimeArgs | None = None,
    *,
    paths: RicePaths | None = None,
    resolve_provider_override: Callable[[], ILLMProvider] | None = None,
) -> Container:
    """`resolve_provider_override` (eval 하네스용): 주어지면 registry
    대신 이 thunk가 메인 루프/learning의 provider가 된다 — eval이 결정적
    ScriptedToolProvider를 꽂아 "스킬 유무에 따라 다른 tool sequence"를 재현하는
    유일한 통로다. 프로덕션 경로에서는 절대 넘기지 않는다(titler는 영향 없음)."""
    args = args or RuntimeArgs()
    paths = (paths or RicePaths.from_env()).ensure()
    config_store = ConfigStore(load_config(paths))

    # Workspace output location is configurable, while state.db/config/skills
    # intentionally remain under RICE_HOME. Resolve it once at startup: builtin
    # tool closures share this fixed sandbox root until the next restart.
    configured_workspace = config_store.config.ui.workspace_path
    workspace_root: Path | None = None
    if configured_workspace:
        candidate = Path(configured_workspace).expanduser()
        if candidate.is_absolute():
            workspace_root = candidate
        else:
            log.warning("ignoring non-absolute workspace_path in config: %r", configured_workspace)
    # Live holder: a folder change (PUT /workspace/location) is queued here and
    # promoted by the orchestrator at a turn boundary, so file tools relocate
    # next turn without a restart. Initial root still comes from config above.
    workspace_location = WorkspaceLocation(workspace_root or paths.root / "workspace")
    paths = RicePaths(root=paths.root, workspace_root=workspace_root, location=workspace_location).ensure()

    # ── infrastructure ────────────────────────────────────────────────────────
    db = Database(paths.db).connect()
    events = EventBus()

    # ── module services (each behind its interface) ───────────────────────────
    sessions: ISessionStore = SessionManager(db)
    # port 를 넘겨 OAuth redirect_uri(http://localhost:{port}/connectors/oauth/callback)를
    # 정확히 구성한다 (CONTRACTS §4.2). args.port 가 0 이면 flow 가 dev 기본(8080)으로 폴백.
    connectors: IConnectorService = ConnectorService(db, paths, port=args.port or None)
    plugins: IPluginLoader = PluginLoaderService(
        db, ChildProcessSandbox(), paths, builtin_plugins=BuiltinPluginCatalog(paths)
    )
    # 부팅 위생: 빌트인으로 승격된 플러그인의 과거 market 설치본 정리 —
    # 빌트인 카탈로그에 가려 UI에 안 보이는데 remove()는 빌트인 id를 거부해
    # 영구히 지울 수 없던 유령 행/디렉토리(learning.recover_boot_state와 같은 자리).
    plugins.reconcile_builtin_shadows()
    # 구버전 market skill은 installed_artifacts 행만 있고 SKILL.md source가 없어
    # 재시작 뒤 학습 스킬처럼 보였다. 설치 DB를 기준으로 source를 영속 보정한다.
    plugins.reconcile_installed_skill_sources()
    approvals: IApprovalGate = ApprovalRegistry()

    # ── provider selection (one mapping point) ────────────────────────────────
    # Registered SaaS credentials live in state.db (provider_models), keyed by the
    # active model id — looked up at resolve time so a model switch picks up its key.
    def credentials_for(model_id: str) -> ProviderCredentials | None:
        import json  # noqa: PLC0415

        row = db.query_one(
            "SELECT api_key, extra FROM provider_models WHERE model_id = ?", (model_id,)
        )
        if row is None:
            return None
        extra = json.loads(row["extra"]) if row["extra"] else {}
        return ProviderCredentials(
            api_key=row["api_key"] or None,
            base_url=extra.get("base_url"),
            project_id=extra.get("project_id"),
        )

    providers: IProviderRegistry = ProviderRegistry(config_store, credentials_for)

    def _resolve_registry_provider() -> ILLMProvider:
        """Resolve the *current* provider, falling back to `echo` so the runtime
        always boots (no API key / on-prem offline). Re-evaluated each turn, so a
        PUT /settings model switch takes effect without re-wiring (C-1)."""
        kind = config_store.config.provider.kind
        try:
            return providers.get(kind)
        except ProviderError as exc:
            log.warning("provider %r unavailable (%s) → falling back to echo", kind, exc)
            return providers.get("echo")

    resolve_provider = resolve_provider_override or _resolve_registry_provider

    def resolve_utility_provider() -> ILLMProvider:
        """Like resolve_provider, but returns an OllamaProvider with think=False.
        For single-shot SHORT answers only (session titling): the instance-level
        think=False routes Ollama to a non-streaming path with a tiny num_predict
        default, which is exactly right for a one-line title and exactly wrong
        for anything longer (the skill analyzer learned this the hard way — it
        uses the main provider with per-call knobs instead). Non-Ollama providers
        are returned as-is. eval 의 resolve_provider_override 는 여기에 적용하지
        않는다 — titler 는 eval 시나리오의 대상이 아니다(기존 계약 유지)."""
        kind = config_store.config.provider.kind
        try:
            provider = providers.get(kind)
        except ProviderError as exc:
            log.warning("provider %r unavailable (%s) → falling back to echo", kind, exc)
            return providers.get("echo")
        if kind == "ollama":
            from ..agent.providers.ollama import OllamaProvider  # noqa: PLC0415
            cfg = config_store.config.provider
            lifecycle_factory = getattr(providers, "ollama_lifecycle", None)
            lifecycle = lifecycle_factory() if lifecycle_factory is not None else None
            return OllamaProvider(cfg, think=False, lifecycle=lifecycle)
        return provider

    # `learning` now depends on `resolve_provider` (Phase A.3 — review()/learn()
    # need real judgment, not just regex) so it's constructed AFTER provider
    # selection, not before it as in the pre-learning-loop-evolution ordering.
    # Reuses the SAME resolve_provider as the main turn loop (not a cheaper/
    # think-disabled variant like the titler's) — review requires the same
    # reasoning quality as the conversation itself, and it's already off the hot
    # path (gated + its own endpoint), so there's no latency reason to downgrade it.
    # The **analysis sidecar** also uses the main provider: its stability knobs
    # (think=False · response_schema · max_tokens) are per-call arguments inside
    # SkillAnalyzer._call — the planner's proven recipe. It must NOT get the
    # utility provider instance: that one's instance-level think=False routes to
    # the titler's non-streaming path whose num_predict defaults to ~20 tokens,
    # which truncates any JSON to nothing (2026-07-06 실측 버그).
    def _known_tool_names() -> set[str]:
        """스킬 승격 검증 harness(learning/skill/verifier.py)용 도구 카탈로그 스냅샷
        — 스킬 본문이 호출을 지시하는 도구가 실재하는지 검사한다. `builtin_specs`는
        이 아래에서 정의되지만 실제 호출은 턴 중(빌드 완료 후)이라 late binding으로
        안전하다. 루프 가상 빌트인(update_plan/start_plan)과 브릿지 3종도 스킬이
        정당하게 참조할 수 있으므로 포함한다."""
        names: set[str] = {t.name for t in BRIDGE_TOOL_SPECS}
        names.update((UPDATE_PLAN_SPEC.name, START_PLAN_SPEC.name))
        names.update(t.name for t in builtin_specs)
        try:
            names.update(t.name for t in connectors.list_tools())
            names.update(t.name for t in plugins.list_tools())
        except Exception:  # noqa: BLE001 — 카탈로그 조회 실패가 검증/턴을 깨면 안 된다
            log.warning("tool catalog enumeration failed for skill verifier", exc_info=True)
        return names

    learning_cfg = config_store.config.learning
    review_input_budget, review_output_budget = review_token_budgets(config_store.config)

    def _resolve_review_token_budgets() -> tuple[int, int]:
        # PUT /settings can switch provider/num_ctx without rebuilding the
        # container. Recompute at each review call just like resolve_provider().
        return review_token_budgets(config_store.config)

    learning_impl = LearningService(
        db,
        learning_cfg,
        paths,
        resolve_provider=resolve_provider,
        resolve_tool_names=_known_tool_names,
        review_request_token_budget=review_input_budget,
        review_output_token_budget=review_output_budget,
        resolve_review_token_budgets=_resolve_review_token_budgets,
    )
    # crash 복구: 이전 프로세스가 'running' 으로 남긴 리뷰 잡을 pending 으로 —
    # 안 하면 그 잡은 영구히 재수확되지 않는다(단일 프로세스라 부팅 시점 running 은
    # 전부 고아라는 판별이 정확한 지점이 여기다). 콘크리트 전용 부팅 메서드라
    # ILearningService seam 에는 올리지 않는다(여기는 콘크리트가 허용되는 유일한 곳).
    learning_impl.recover_boot_state()
    learning: ILearningService = learning_impl

    # ── builtin tools (in-process; advertised to the LLM + dispatched directly) ─
    # learning을 넘겨 propose_skill(미드턴 스킬 승격, Self-Improvement v3)을 함께
    # 등록한다 — 도구는 스테이징만 하고 쓰기는 여전히 HITL 카드 승인 뒤에만 일어난다.
    # 토큰 추정 캘리브레이터 — 메인 루프·서브에이전트가 공유하는
    # 싱글턴. 실측 usage로 CJK 예산 추정을 세션 전반에 걸쳐 보정한다.
    token_calibrator = TokenCalibrator()

    subagents = SubAgentRunner(
        resolve_provider=resolve_provider,
        transcript=sessions,
        context_config=lambda: config_store.config.context,
        token_calibrator=token_calibrator,
        # 격리 워커 도구 — 워커가 직접 읽게 해 메인 컨텍스트를 원문으로 오염시키지
        # 않는다. learning을 주면 skill_search/skill_view가 함께 실린다(스킬 주입).
        tools=build_subagent_toolset(paths, learning=learning),
        # 태스크 관련 스킬 인덱스 렌더용 + thread 격리 아티팩트 채널(write_scratch).
        learning=learning,
        scratch_tool_factory=scratch_tool_factory(paths),
        # 플러그인 agents/*.md 페르소나(2026-07-19) — delegate_subagent(agent=...)
        # 가 워커 시스템 프롬프트로 소비한다. 활성 플러그인 기준 라이브 조회.
        resolve_agents=plugins.injected_agents,
    )
    builtin_specs, builtins = build_builtin_tools(paths, learning=learning, subagents=subagents)

    def live_builtin_specs() -> tuple[ToolSpec, ...]:
        """매 턴 재평가되는 빌트인 카탈로그 — delegate_subagent 스펙만 라이브로
        갈아끼운다(활성 플러그인의 페르소나 광고가 설치/토글을 재시작 없이 따라
        가게). 핸들러는 이름 기준 dict 라 스펙 재생성의 영향을 받지 않는다."""
        return tuple(
            subagent_tools.delegate_spec(subagents) if spec.name == "delegate_subagent" else spec
            for spec in builtin_specs
        )

    # ── session titler (names a new chat from its opening exchange, post-answer) ─
    # Decoupled from the store via two callables, so it binds to neither seam.
    def _session_title(session_id: str) -> str | None:
        info = sessions.get(session_id)
        return info.title if info else None

    titler = SessionTitler(
        resolve_provider=resolve_utility_provider,
        get_title=_session_title,
        set_title=sessions.set_title,
    )

    # ── the convergence point ─────────────────────────────────────────────────
    # `sessions` (a SessionManager) satisfies both ISessionStore (UI) and
    # ITranscript (agent) structurally — one object, two seams (invariant 1/3).
    agent: IAgent = Orchestrator(
        resolve_provider=resolve_provider,
        config_store=config_store,
        learning=learning,
        connectors=connectors,
        plugins=plugins,
        approvals=approvals,
        transcript=sessions,
        builtins=builtins,
        builtin_specs=live_builtin_specs,
        token_calibrator=token_calibrator,
        workspace=workspace_location,
    )

    return Container(
        args=args,
        paths=paths,
        config_store=config_store,
        db=db,
        events=events,
        sessions=sessions,
        learning=learning,
        connectors=connectors,
        plugins=plugins,
        approvals=approvals,
        providers=providers,
        resolve_provider=resolve_provider,
        agent=agent,
        titler=titler,
        token_calibrator=token_calibrator,
    )
