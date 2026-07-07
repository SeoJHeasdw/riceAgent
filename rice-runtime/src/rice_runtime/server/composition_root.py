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

import logging
from dataclasses import dataclass
from typing import Callable

from ..agent.builtins import build_builtin_tools, build_subagent_toolset
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
from ..core.paths import RicePaths
from ..core.storage.sqlite import Database
from ..core.tokens import TokenCalibrator
from ..execution.interfaces import IPluginLoader
from ..execution.service import ChildProcessSandbox, PluginLoaderService
from ..learning.interfaces import ILearningService
from ..learning.service import LearningService
from .approval import ApprovalRegistry
from .session.interfaces import ISessionStore
from .session.session_manager import SessionManager

log = logging.getLogger("rice.composition")


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

    @property
    def config(self) -> Config:
        """Convenience read of the live config (routes/health)."""
        return self.config_store.config

    def shutdown(self) -> None:
        """Graceful teardown (POST /shutdown): 활성 턴 abort + 파킹된 승인 취소
        → DB close. 이전엔 DB만 닫아서, 파킹된 HITL 승인이 스트림을 계속 물고
        있었고 진행 중 턴은 닫힌 DB를 만졌다. abort_all 은 sync best-effort 다 —
        drain 까지는 하지 않는다(직후 supervisor 가 프로세스를 내린다)."""
        abort_all = getattr(self.agent, "abort_all", None)
        if abort_all is not None:
            try:
                abort_all()
            except Exception:  # noqa: BLE001 — teardown 은 어떤 경우에도 진행돼야 한다
                log.warning("abort_all during shutdown failed", exc_info=True)
        self.db.close()


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

    # ── infrastructure ────────────────────────────────────────────────────────
    db = Database(paths.db).connect()
    events = EventBus()

    # ── module services (each behind its interface) ───────────────────────────
    sessions: ISessionStore = SessionManager(db)
    # port 를 넘겨 OAuth redirect_uri(http://localhost:{port}/connectors/oauth/callback)를
    # 정확히 구성한다 (CONTRACTS §4.2). args.port 가 0 이면 flow 가 dev 기본(8080)으로 폴백.
    connectors: IConnectorService = ConnectorService(db, paths, port=args.port or None)
    plugins: IPluginLoader = PluginLoaderService(db, ChildProcessSandbox(), paths)
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
            return OllamaProvider(cfg, think=False)
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

    learning_impl = LearningService(
        db,
        config_store.config.learning,
        paths,
        resolve_provider=resolve_provider,
        resolve_tool_names=_known_tool_names,
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
        learning_config=lambda: config_store.config.learning,
        context_config=lambda: config_store.config.context,
        token_calibrator=token_calibrator,
        # read-only 워커 도구 — 워커가 직접 읽게 해 메인 컨텍스트를 원문으로
        # 오염시키지 않는다(agent/subagents.py 모듈 docstring 참조).
        tools=build_subagent_toolset(paths),
    )
    builtin_specs, builtins = build_builtin_tools(paths, learning=learning, subagents=subagents)

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
        builtin_specs=builtin_specs,
        token_calibrator=token_calibrator,
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
