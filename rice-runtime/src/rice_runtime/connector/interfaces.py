"""IConnectorService — external-connection separation seam (#10/#11/#12).

Owns: connector lifecycle (OAuth connect/disconnect), credential storage (OS
keychain — credentials never cross this seam), permission checks, and the tools a
connected Connector (MCP) exposes to the agent (RUNTIME_DESIGN §9, CONTRACTS §4).

Two surfaces:
  1. TOOL SURFACE (used by the orchestrator/dispatcher):
       list_tools()  → `ToolSpec`s with source="connector" (advertised to the LLM)
       invoke(...)   → run one connector tool *after* permission + HITL approval
       check_permission(...) → plugin → connector_type access (CONTRACTS §4.4)
     Connector tools default to `requires_approval=True` — they reach outside the
     machine (RUNTIME_DESIGN §5). Approval itself is the orchestrator's job.
  2. MANAGEMENT (used by `routes/connectors.py` for ConnectorsScreen):
       list/get, begin_connect (→ OAuth URL), complete_oauth (callback),
       delete, status.

**FROZEN.** OAuth/keychain/permission impls (`oauth/`, `credential/`,
`permission/`) sit behind this. The prototype connector_type is "gmail".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from ..core.types import ToolCall, ToolResult, ToolSpec

ConnectorStatus = Literal["connected", "disconnected", "error"]


@dataclass(frozen=True)
class ConnectorInfo:
    id: str
    connector_type: str  # "gmail" (prototype)
    display_name: str
    status: ConnectorStatus
    scopes: tuple[str, ...] = ()
    connected_at: str | None = None
    last_verified_at: str | None = None


@dataclass(frozen=True)
class OAuthStart:
    """Result of begin_connect — the app opens `oauth_url` in the system browser
    (CONTRACTS §4.2). `state` is the CSRF token echoed back on callback."""

    connector_id: str
    oauth_url: str
    state: str


@runtime_checkable
class IConnectorService(Protocol):
    # ── tool surface (orchestrator/dispatcher) ───────────────────────────────
    def list_tools(self) -> list[ToolSpec]:
        """Tools exposed by all currently-connected connectors (source=connector)."""
        ...

    async def invoke(self, call: ToolCall) -> ToolResult:
        """Execute a connector tool. Assumes permission + HITL approval already
        passed (the dispatcher enforces those before calling)."""
        ...

    def check_permission(self, plugin_id: str, connector_type: str) -> bool:
        """Is `plugin_id` granted access to `connector_type`? (CONTRACTS §4.4)"""
        ...

    # ── management (routes/connectors.py) ────────────────────────────────────
    def list_connectors(self) -> list[ConnectorInfo]: ...
    def get_connector(self, connector_id: str) -> ConnectorInfo | None: ...
    def status(self, connector_id: str) -> ConnectorStatus: ...
    async def begin_connect(self, connector_type: str) -> OAuthStart:
        """Start OAuth: create a pending connector + authorization URL."""
        ...

    async def complete_oauth(self, *, code: str, state: str) -> ConnectorInfo:
        """Handle the OAuth redirect: exchange code → tokens (→ keychain), mark
        connected (CONTRACTS §4.2 steps 3–6)."""
        ...

    async def delete(self, connector_id: str) -> None: ...
