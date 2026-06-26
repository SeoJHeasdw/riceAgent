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

from ..agent.builtins import build_builtin_tools
from ..agent.interfaces import IAgent, IApprovalGate
from ..agent.orchestrator import Orchestrator
from ..agent.providers.interfaces import ILLMProvider, IProviderRegistry
from ..agent.providers.registry import ProviderCredentials, ProviderRegistry
from ..connector.interfaces import IConnectorService
from ..connector.service import ConnectorService
from ..core.config import Config, ConfigStore, RuntimeArgs, load_config
from ..core.errors import ProviderError
from ..core.events import EventBus
from ..core.paths import RicePaths
from ..core.storage.sqlite import Database
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

    @property
    def config(self) -> Config:
        """Convenience read of the live config (routes/health)."""
        return self.config_store.config

    def shutdown(self) -> None:
        """Graceful teardown (POST /shutdown). Cancel parked approvals, close DB."""
        self.db.close()


def build_container(args: RuntimeArgs | None = None, *, paths: RicePaths | None = None) -> Container:
    args = args or RuntimeArgs()
    paths = (paths or RicePaths.from_env()).ensure()
    config_store = ConfigStore(load_config(paths))

    # ── infrastructure ────────────────────────────────────────────────────────
    db = Database(paths.db).connect()
    events = EventBus()

    # ── module services (each behind its interface) ───────────────────────────
    sessions: ISessionStore = SessionManager(db)
    learning: ILearningService = LearningService(db, config_store.config.learning, paths)
    connectors: IConnectorService = ConnectorService(db, paths)
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

    def resolve_provider() -> ILLMProvider:
        """Resolve the *current* provider, falling back to `echo` so the runtime
        always boots (no API key / on-prem offline). Re-evaluated each turn, so a
        PUT /settings model switch takes effect without re-wiring (C-1)."""
        kind = config_store.config.provider.kind
        try:
            return providers.get(kind)
        except ProviderError as exc:
            log.warning("provider %r unavailable (%s) → falling back to echo", kind, exc)
            return providers.get("echo")

    # ── builtin tools (in-process; advertised to the LLM + dispatched directly) ─
    builtin_specs, builtins = build_builtin_tools(paths)

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
    )
