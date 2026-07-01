"""ConnectorService — IConnectorService 구현 파사드 (RUNTIME_DESIGN §9, CONTRACTS §4).

두 표면을 조합한다:
  1. 도구 표면 (orchestrator/dispatcher 용):
       list_tools()    → 연결된 **모든** connector 의 도구(ToolSpec) 합집합
       invoke(call)    → 도구를 소유한 connector 의 원격 MCP `tools/call` 위임
       check_permission(plugin_id, connector_type) → 권한 검증
  2. 관리 (routes/connectors.py 용):
       list/get/status/begin_connect/complete_oauth/delete
       sync_tools / check_updates / apply_update (도구 카탈로그 동기화·업데이트)

핵심 원칙:
  - **도구를 하드코딩하지 않는다.** 도구는 원격 MCP `tools/list` 를 ToolSpec 으로 변환한 것.
  - **gmail 을 하드코딩하지 않는다.** 타입별 차이는 registry.ConnectorDescriptor 에서 온다.
    새 커넥터는 레지스트리에 디스크립터만 추가하면 되고, 이 파일은 제네릭하게 동작한다.
  - **카탈로그는 연결 메타데이터.** list_tools() 는 sync 라 네트워크를 못 하므로, 카탈로그를
    연결 시점에 DB(tools_json)에 저장하고 list_tools() 는 로컬 DB 만 읽는다. 부팅·매 턴
    네트워크 0. 원격 변경 감지는 온디맨드 check_updates() 로 pending_tools_json 에 담는다.

composition_root.py 에서 RicePaths 가 추가로 주입된다 (DI 허용 — CONVENTIONS §0.3).
"""
from __future__ import annotations

import json
import logging

from ..core.errors import NotFoundError
from ..core.paths import RicePaths
from ..core.storage.sqlite import Database
from ..core.types import ToolCall, ToolResult, ToolSpec, now_iso
from .credential.keychain import KeychainStore
from .interfaces import ConnectorInfo, ConnectorStatus, IConnectorService, OAuthStart
from .manager import ConnectorManager, _row_to_info
from .mcp.client import RemoteMcpClient
from .oauth.flow import OAuthFlow
from .permission.checker import PermissionChecker
from .registry import descriptors, get_descriptor

log = logging.getLogger("rice.connector.service")


class ConnectorService(IConnectorService):
    """IConnectorService 의 실제 구현 — OAuth + 키체인 + 권한 + 원격 MCP (제네릭)."""

    def __init__(self, db: Database, paths: RicePaths | None = None, *, port: int | None = None) -> None:
        self._db = db
        self._paths = paths
        self._keychain = KeychainStore()
        connectors_dir = paths.connectors if paths else None
        # 타입별 OAuth flow 를 레지스트리에서 구성해 manager 와 공유(자격증명·redirect 일관).
        self._flows: dict[str, OAuthFlow] = {
            d.connector_type: OAuthFlow(d, connectors_dir=connectors_dir, port=port)
            for d in descriptors()
        }
        self._manager = ConnectorManager(db, self._keychain, self._flows)
        self._permission = PermissionChecker(paths.plugins if paths else None)
        # 원격 MCP 클라이언트 — connector_id 당 하나(엔드포인트는 타입 디스크립터에서).
        self._clients: dict[str, RemoteMcpClient] = {}

    # ── tool surface ──────────────────────────────────────────────────────────
    def list_tools(self) -> list[ToolSpec]:
        """연결된 **모든** connector 의 저장된 도구 카탈로그 합집합 (로컬 DB, 네트워크 없음)."""
        tools: list[ToolSpec] = []
        for row in self._connected_connectors():
            tools.extend(self.tools_for(row["id"]))
        return tools

    def tools_for(self, connector_id: str) -> list[ToolSpec]:
        """특정 connector 의 활성 도구 카탈로그 (로컬 DB 읽기). 없으면 []."""
        row = self._db.query_one(
            "SELECT tools_json FROM connector_credentials WHERE connector_id=?",
            (connector_id,),
        )
        if row is None or not row["tools_json"]:
            return []
        return _specs_from_json(connector_id, row["tools_json"])

    async def invoke(self, call: ToolCall) -> ToolResult:
        """connector 도구 실행 (권한 + HITL 승인은 dispatcher 가 이미 처리).

        도구명을 소유한 connector 를 찾아 그 원격 MCP `tools/call` 로 위임한다.
        실패는 예외가 아니라 ToolResult(ok=False) 로 반환한다.
        """
        try:
            owner = self._owner_of_tool(call.tool_name)
            if owner is None:
                return ToolResult(call.id, ok=False, error=f"no connected connector owns tool {call.tool_name!r}")
            connector_id, connector_type = owner
            access_token = await self._access_token(connector_id, connector_type)
            if access_token is None:
                return ToolResult(
                    call.id, ok=False,
                    error=f"connector {connector_id} needs reconnection (no valid credentials)",
                )
            client = self._client_for(connector_id, connector_type)
            return await client.call_tool(
                tool_name=call.tool_name,
                arguments=call.arguments,
                access_token=access_token,
                call_id=call.id,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("connector invoke failed: %s", exc)
            return ToolResult(call.id, ok=False, error=str(exc))

    def check_permission(self, plugin_id: str, connector_type: str) -> bool:
        """plugin manifest 의 requires.connectors 기반 권한 검증 (RUNTIME_DESIGN §9.4)."""
        if self._permission is None:
            return False  # paths 미주입(테스트/골격) → fail-closed (CONVENTIONS §7)
        return self._permission.check(plugin_id, connector_type)

    def connected_types(self) -> set[str]:
        """현재 connected 상태인 connector_type 집합 (CONTRACTS §4.4 — plugin 활성화 게이트)."""
        rows = self._db.query_all(
            "SELECT DISTINCT connector_type FROM connectors WHERE status='connected'"
        )
        return {r["connector_type"] for r in rows}

    # ── tool catalog sync / update detection ────────────────────────────────────
    async def sync_tools(self, connector_id: str) -> int:
        """원격 MCP `tools/list` 를 받아 활성 카탈로그를 강제 갱신한다(overwrite + pending 소거).

        연결 시점(complete_oauth)과 "도구 새로고침"(강제)에서 호출. 반환값은 저장된 도구 수.
        토큰/네트워크 실패 시 0 (카탈로그 미변경).
        """
        specs = await self._fetch_remote_tools(connector_id)
        if specs is None:
            return 0
        self._store_active(connector_id, specs)
        log.info("connector tools synced: %d tool(s) for %s", len(specs), connector_id)
        return len(specs)

    async def check_updates(self, connector_id: str) -> dict:
        """원격 카탈로그를 받아 활성본과 diff 한다(적용하지 않음).

        변경이 있으면 pending_tools_json 에 담고 diff 를, 없으면 pending 을 비우고
        changed=False 를 반환한다. 온디맨드(커넥터 화면 진입 시)로 호출해 "업데이트 있음"
        배지를 실제 데이터로 띄운다 — 부팅/백그라운드 원격 호출은 하지 않는다.
        """
        specs = await self._fetch_remote_tools(connector_id)
        if specs is None:
            return {"changed": False, "added": [], "removed": []}
        active = self.tools_for(connector_id)
        added, removed = _diff_names(active, specs)
        if not added and not removed:
            self._clear_pending(connector_id)
            return {"changed": False, "added": [], "removed": []}
        self._store_pending(connector_id, specs)
        return {"changed": True, "added": added, "removed": removed}

    async def apply_update(self, connector_id: str) -> int:
        """대기 중(pending) 카탈로그를 활성으로 승격하고 pending 을 비운다. 반환: 도구 수."""
        row = self._db.query_one(
            "SELECT pending_tools_json FROM connector_credentials WHERE connector_id=?",
            (connector_id,),
        )
        if row is None or not row["pending_tools_json"]:
            return 0
        specs = _specs_from_json(connector_id, row["pending_tools_json"])
        self._store_active(connector_id, specs)
        log.info("connector tools update applied: %d tool(s) for %s", len(specs), connector_id)
        return len(specs)

    def pending_update(self, connector_id: str) -> dict | None:
        """대기 중 업데이트의 diff(added/removed/added_tools). 없으면 None (GET payload 용)."""
        row = self._db.query_one(
            "SELECT tools_json, pending_tools_json FROM connector_credentials WHERE connector_id=?",
            (connector_id,),
        )
        if row is None or not row["pending_tools_json"]:
            return None
        active = _specs_from_json(connector_id, row["tools_json"] or "[]")
        pending = _specs_from_json(connector_id, row["pending_tools_json"])
        added, removed = _diff_names(active, pending)
        if not added and not removed:
            return None
        added_set = set(added)
        return {
            "added": added,
            "removed": removed,
            "added_tools": [
                {"name": s.name, "requires_approval": s.requires_approval}
                for s in pending
                if s.name in added_set
            ],
        }

    # ── management ────────────────────────────────────────────────────────────
    def list_connectors(self) -> list[ConnectorInfo]:
        rows = self._db.query_all(
            "SELECT c.*, cc.scopes FROM connectors c "
            "LEFT JOIN connector_credentials cc ON c.id=cc.connector_id "
            "ORDER BY c.connected_at DESC"
        )
        return [_row_to_info(r) for r in rows]

    def get_connector(self, connector_id: str) -> ConnectorInfo | None:
        row = self._db.query_one(
            "SELECT c.*, cc.scopes FROM connectors c "
            "LEFT JOIN connector_credentials cc ON c.id=cc.connector_id "
            "WHERE c.id=?",
            (connector_id,),
        )
        return _row_to_info(row) if row else None

    def status(self, connector_id: str) -> ConnectorStatus:
        info = self.get_connector(connector_id)
        if info is None:
            raise NotFoundError(f"connector {connector_id} not found")
        return info.status

    async def begin_connect(self, connector_type: str) -> OAuthStart:
        """OAuth 시작: state 토큰 생성 + pending 레코드 생성 + authorization URL 반환."""
        return self._manager.begin_connect(connector_type)

    async def complete_oauth(self, *, code: str, state: str) -> ConnectorInfo:
        """OAuth callback: state 검증 → 토큰 교환 → 키체인 저장 → status='connected'.

        연결 직후 원격 MCP 도구 카탈로그를 한 번 받아 DB 에 저장한다(sync_tools).
        """
        info = await self._manager.complete_oauth(code=code, state=state)
        try:
            await self.sync_tools(info.id)
        except Exception as exc:  # noqa: BLE001 — 도구 동기화 실패가 연결을 막지 않는다
            log.warning("tool sync after connect failed: %s", exc)
        return info

    async def delete(self, connector_id: str) -> None:
        """connector 레코드 삭제 + 키체인 항목 정리 (카탈로그는 cascade 삭제)."""
        await self._manager.delete(connector_id)
        self._clients.pop(connector_id, None)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _connected_connectors(self) -> list:
        """connected 상태인 모든 connector 행(id, connector_type)."""
        return self._db.query_all(
            "SELECT id, connector_type FROM connectors WHERE status='connected'"
        )

    def _owner_of_tool(self, tool_name: str) -> tuple[str, str] | None:
        """도구명을 노출하는 connected connector 의 (id, type). 없으면 None."""
        for row in self._connected_connectors():
            if any(s.name == tool_name for s in self.tools_for(row["id"])):
                return row["id"], row["connector_type"]
        return None

    def _client_for(self, connector_id: str, connector_type: str) -> RemoteMcpClient:
        """connector 전용 RemoteMcpClient (엔드포인트는 타입 디스크립터에서). 캐시한다."""
        client = self._clients.get(connector_id)
        if client is None:
            descriptor = get_descriptor(connector_type)
            endpoint = descriptor.resolved_mcp_endpoint() if descriptor else ""
            client = RemoteMcpClient(endpoint)
            client.set_owner(connector_id)
            self._clients[connector_id] = client
        return client

    async def _fetch_remote_tools(self, connector_id: str) -> list[ToolSpec] | None:
        """connector 의 원격 MCP `tools/list` 결과. 연결/토큰/네트워크 실패 시 None."""
        row = self._db.query_one(
            "SELECT connector_type FROM connectors WHERE id=? AND status='connected'",
            (connector_id,),
        )
        if row is None:
            return None
        connector_type = row["connector_type"]
        access_token = await self._access_token(connector_id, connector_type)
        if access_token is None:
            return None
        client = self._client_for(connector_id, connector_type)
        try:
            return await client.list_tools(access_token)
        except Exception as exc:  # noqa: BLE001 — 도구 목록 실패는 치명적이지 않다
            log.warning("mcp tools/list failed for %s: %s", connector_id, exc)
            return None

    async def _access_token(self, connector_id: str, connector_type: str) -> str | None:
        """유효한 access_token 을 반환한다. 만료 임박 시 refresh (§9.3).

        갱신 성공 시 error→connected 로 자가치유하고, 갱신/자격증명 실패 시 status='error'
        로 표시한 뒤 None 을 반환한다(UI 가 "재연결 필요"를 노출) — #4 토큰 만료 UX.
        """
        credential = self._keychain.load(connector_id)
        if credential is None:
            self._mark_error(connector_id)
            return None

        if not credential.is_expired():
            self._mark_healthy(connector_id)
            return credential.access_token

        if not credential.refresh_token:
            log.warning("connector %s token expired and no refresh_token", connector_id)
            self._mark_error(connector_id)
            return None

        flow = self._flows.get(connector_type)
        if flow is None:
            self._mark_error(connector_id)
            return None
        try:
            refreshed = await flow.refresh_access_token(credential.refresh_token)
        except Exception as exc:  # noqa: BLE001
            log.warning("token refresh failed for %s: %s", connector_id, exc)
            self._mark_error(connector_id)
            return None
        self._keychain.update_access_token(
            connector_id,
            access_token=refreshed.access_token,
            expires_in=refreshed.expires_in,
        )
        self._mark_healthy(connector_id)
        return refreshed.access_token

    def _mark_error(self, connector_id: str) -> None:
        self._db.execute(
            "UPDATE connectors SET status='error' WHERE id=? AND status='connected'",
            (connector_id,),
        )

    def _mark_healthy(self, connector_id: str) -> None:
        self._db.execute(
            "UPDATE connectors SET status='connected', last_verified_at=? WHERE id=? AND status='error'",
            (now_iso(), connector_id),
        )

    # ── catalog persistence ─────────────────────────────────────────────────────
    def _store_active(self, connector_id: str, specs: list[ToolSpec]) -> None:
        """활성 카탈로그를 저장하고 pending 을 비운다(강제 적용)."""
        self._db.execute(
            "INSERT INTO connector_credentials(connector_id, tools_json, pending_tools_json) "
            "VALUES (?, ?, NULL) "
            "ON CONFLICT(connector_id) DO UPDATE SET tools_json=excluded.tools_json, "
            "pending_tools_json=NULL",
            (connector_id, _serialize_specs(specs)),
        )

    def _store_pending(self, connector_id: str, specs: list[ToolSpec]) -> None:
        self._db.execute(
            "INSERT INTO connector_credentials(connector_id, pending_tools_json) VALUES (?, ?) "
            "ON CONFLICT(connector_id) DO UPDATE SET pending_tools_json=excluded.pending_tools_json",
            (connector_id, _serialize_specs(specs)),
        )

    def _clear_pending(self, connector_id: str) -> None:
        self._db.execute(
            "UPDATE connector_credentials SET pending_tools_json=NULL WHERE connector_id=?",
            (connector_id,),
        )


# ── tool catalog (de)serialization + diff — DB json ↔ ToolSpec ─────────────────
def _serialize_specs(specs: list[ToolSpec]) -> str:
    """ToolSpec 목록을 DB 저장용 JSON 으로 직렬화한다.

    source(항상 "connector")·owner_id(로드 시 connector_id 로 재설정)는 저장하지 않는다.
    """
    return json.dumps(
        [
            {
                "name": s.name,
                "description": s.description,
                "parameters": s.parameters,
                "requires_approval": s.requires_approval,
            }
            for s in specs
        ],
        ensure_ascii=False,
    )


def _specs_from_json(connector_id: str, tools_json: str) -> list[ToolSpec]:
    """DB json → ToolSpec 목록. owner_id 는 현재 connector_id 로 설정한다.

    파싱 실패는 [] (fail-safe — 카탈로그 손상이 채팅을 막지 않는다).
    """
    try:
        raw = json.loads(tools_json)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(raw, list):
        return []
    specs: list[ToolSpec] = []
    for t in raw:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        specs.append(
            ToolSpec(
                name=t["name"],
                description=t.get("description", ""),
                parameters=t.get("parameters") or {"type": "object", "properties": {}},
                source="connector",
                owner_id=connector_id,
                requires_approval=bool(t.get("requires_approval", True)),
            )
        )
    return specs


def _diff_names(active: list[ToolSpec], incoming: list[ToolSpec]) -> tuple[list[str], list[str]]:
    """(added, removed) 도구명 목록. active→incoming 기준."""
    a = {s.name for s in active}
    b = {s.name for s in incoming}
    return sorted(b - a), sorted(a - b)
