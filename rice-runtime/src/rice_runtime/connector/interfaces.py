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
`permission/`) sit behind this. connector_type is registry-driven (registry.py) —
"gmail" / "calendar" / "drive" today, more via a new ConnectorDescriptor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from ..core.types import ToolCall, ToolResult, ToolSpec

ConnectorStatus = Literal["connected", "disconnected", "verifying", "error"]


@dataclass(frozen=True)
class ConnectorInfo:
    id: str
    connector_type: str  # e.g. "gmail" / "calendar" / "drive" — registry.py
    display_name: str
    status: ConnectorStatus
    scopes: tuple[str, ...] = ()
    connected_at: str | None = None
    last_verified_at: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    setup_url: str | None = None


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

    async def invoke(self, call: ToolCall, owner_id: str | None = None) -> ToolResult:
        """Execute a connector tool. Assumes permission + HITL approval already
        passed (the dispatcher enforces those before calling).

        `owner_id`(= 그 도구 ToolSpec의 owner_id, connector_id)가 주어지면 **그
        커넥터로만** 라우팅한다 — 두 계정/서버가 같은 도구명을 노출할 때 이름
        기반 재탐색이 임의의 첫 커넥터로 보내 계정을 오염시키는 사고 방지.
        None이면 이름으로 소유자를 탐색한다(하위호환)."""
        ...

    def check_permission(self, plugin_id: str, connector_type: str) -> bool:
        """Is `plugin_id` granted access to `connector_type`? (CONTRACTS §4.4)"""
        ...

    def connected_types(self) -> set[str]:
        """connector_type set of currently *connected* connectors. Used to gate
        plugin enablement (CONTRACTS §4.4 step 2: all required connectors linked)."""
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

    async def cancel_oauth(self, connector_id: str) -> bool:
        """Cancel a browser OAuth attempt that has not reached the callback."""
        ...

    async def reject_oauth(self, *, state: str, error: str) -> ConnectorInfo | None:
        """Record an OAuth provider rejection (for example access_denied)."""
        ...

    async def verify_connector(self, connector_id: str) -> dict:
        """Run a safe read-only MCP tool to prove the connector can execute, not
        just discover, tools (tools/list can succeed while tools/call is denied)."""
        ...

    async def delete(self, connector_id: str) -> None: ...
