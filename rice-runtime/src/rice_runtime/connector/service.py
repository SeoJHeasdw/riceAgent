"""ConnectorService — skeleton implementation of `IConnectorService`.

⚠️ STUB. The real OAuth flow (`oauth/`), keychain storage (`credential/keychain.py`),
permission checks (`permission/checker.py`), and MCP tool surfacing replace these
bodies behind the same Protocol (RUNTIME_DESIGN §9, CONTRACTS §4).

Inert today: no tools surfaced, OAuth raises NotImplemented, permission denies by
default (fail-closed — the safe stance for an unimplemented access check).
"""

from __future__ import annotations

from ..core.errors import NotFoundError
from ..core.storage.sqlite import Database
from ..core.types import ToolCall, ToolResult
from .interfaces import ConnectorInfo, ConnectorStatus, IConnectorService, OAuthStart


class ConnectorService(IConnectorService):
    def __init__(self, db: Database) -> None:
        self._db = db

    # ── tool surface ──────────────────────────────────────────────────────────
    def list_tools(self):
        return []  # TODO(connector): surface tools from connected MCP servers

    async def invoke(self, call: ToolCall) -> ToolResult:
        return ToolResult(call.id, ok=False, error="connector tools not implemented")

    def check_permission(self, plugin_id: str, connector_type: str) -> bool:
        return False  # fail-closed until permission/checker.py is implemented

    # ── management ────────────────────────────────────────────────────────────
    def list_connectors(self) -> list[ConnectorInfo]:
        rows = self._db.query_all("SELECT * FROM connectors ORDER BY connected_at DESC")
        return [self._to_info(r) for r in rows]

    def get_connector(self, connector_id: str) -> ConnectorInfo | None:
        row = self._db.query_one("SELECT * FROM connectors WHERE id = ?", (connector_id,))
        return self._to_info(row) if row else None

    def status(self, connector_id: str) -> ConnectorStatus:
        info = self.get_connector(connector_id)
        if info is None:
            raise NotFoundError(f"connector {connector_id} not found")
        return info.status

    async def begin_connect(self, connector_type: str) -> OAuthStart:
        # TODO(connector/oauth): build auth URL with a state token, persist pending.
        raise NotImplementedError("connector OAuth not implemented yet")

    async def complete_oauth(self, *, code: str, state: str) -> ConnectorInfo:
        raise NotImplementedError("connector OAuth callback not implemented yet")

    async def delete(self, connector_id: str) -> None:
        self._db.execute("DELETE FROM connectors WHERE id = ?", (connector_id,))

    # ── mapping ───────────────────────────────────────────────────────────────
    @staticmethod
    def _to_info(row) -> ConnectorInfo:
        import json

        scopes = tuple(json.loads(row["scopes"])) if "scopes" in row.keys() and row["scopes"] else ()
        return ConnectorInfo(
            id=row["id"],
            connector_type=row["connector_type"],
            display_name=row["display_name"] or row["connector_type"],
            status=row["status"],
            scopes=scopes,
            connected_at=row["connected_at"],
            last_verified_at=row["last_verified_at"],
        )
