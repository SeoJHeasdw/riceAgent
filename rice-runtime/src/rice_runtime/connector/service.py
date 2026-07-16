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

import asyncio
import json
import logging

from ..core.errors import ConflictError, NotFoundError, ValidationError
from ..core.paths import RicePaths
from ..core.storage.sqlite import Database
from ..core.types import ToolCall, ToolResult, ToolSpec, now_iso
from .credential.keychain import KeychainStore
from .custom_registry import CustomConnectorStore, resolve_descriptor
from .discovery import DiscoveredOAuthConfig, discover_oauth_config, register_dynamic_client
from .interfaces import ConnectorInfo, ConnectorStatus, IConnectorService, OAuthStart
from .local.config import (
    LocalMcpServer,
    LocalMcpServerExistsError,
    add_local_mcp_server,
    connector_id_to_name,
    load_local_mcp_servers,
    remove_local_mcp_server,
)
from .manager import ConnectorManager, _row_to_info
from .mcp.client import RemoteMcpClient
from .mcp.stdio_client import StdioMcpClient
from .oauth.flow import OAuthFlow, default_redirect_uri
from .permission.checker import PermissionChecker
from .registry import descriptors

log = logging.getLogger("rice.connector.service")

# 도구 호출 정책 (dispatcher 가 강제). connector_tool_permissions.permission 값.
_PERMISSIONS = frozenset({"allow", "approval", "block"})

# 도구명 접두사로 읽기 성격을 추정 → 저장값이 없을 때 기본 정책 계산에 쓴다
# (프론트 toolToScope 의 READ_PREFIXES 와 의도를 맞춘다: 읽기=allow, 그 외=approval).
_READ_PREFIXES = ("get", "list", "search", "read", "fetch", "find", "view")


def _default_permission(tool_name: str) -> str:
    """저장값이 없는 도구의 기본 정책. 읽기 성격이면 allow, 그 외엔 approval(외부 접근)."""
    lowered = tool_name.lower()
    return "allow" if lowered.startswith(_READ_PREFIXES) else "approval"


class ConnectorService(IConnectorService):
    """IConnectorService 의 실제 구현 — OAuth + 키체인 + 권한 + 원격 MCP (제네릭)."""

    def __init__(self, db: Database, paths: RicePaths | None = None, *, port: int | None = None) -> None:
        self._db = db
        self._paths = paths
        self._port = port
        self._keychain = KeychainStore()
        self._connectors_dir = paths.connectors if paths else None
        self._custom = CustomConnectorStore(db, self._keychain)
        # 타입별 OAuth flow 를 레지스트리(정적 + 커스텀)에서 구성해 manager 와 공유
        # (자격증명·redirect 일관). 커스텀은 client_id/secret 을 명시 주입한다(env/file
        # 해석 경로가 아니라 동적 등록·DB·키체인에서 온 값이므로) — OAuthFlow 가 이미 지원.
        self._flows: dict[str, OAuthFlow] = {
            d.connector_type: OAuthFlow(d, connectors_dir=self._connectors_dir, port=port)
            for d in descriptors()
        }
        for d in self._custom.list_descriptors():
            self._flows[d.connector_type] = OAuthFlow(
                d,
                client_id=self._custom.client_id_for(d.connector_type),
                client_secret=self._custom.client_secret_for(d.connector_type),
                connectors_dir=self._connectors_dir,
                port=port,
            )
        self._manager = ConnectorManager(db, self._keychain, self._flows, custom=self._custom)
        self._permission = PermissionChecker(paths.plugins if paths else None)
        # 원격 MCP 클라이언트 — connector_id 당 하나(엔드포인트는 타입 디스크립터에서).
        self._clients: dict[str, RemoteMcpClient] = {}
        # connector 별 토큰 refresh 직렬화 — 병렬 tool call 이 같은 만료 토큰을 보고
        # refresh 를 두 번 돌리면 rotation 서버에서 한쪽이 무효 토큰으로 실패한다.
        self._refresh_locks: dict[str, asyncio.Lock] = {}
        # 로컬 MCP(.mcp.json, stdio) — connector_id("local:<name>") → 캐시된 ToolSpec 목록.
        # list_tools() 는 sync 라 여기 캐시를 읽고, warm_local() 이 async 로 채운다.
        self._mcp_config_path = paths.mcp_config if paths else None
        self._local_specs: dict[str, list[ToolSpec]] = {}

    # ── tool surface ──────────────────────────────────────────────────────────
    def list_tools(self) -> list[ToolSpec]:
        """연결된 원격 connector(DB 카탈로그) + 로컬 MCP(캐시) 도구의 합집합.

        원격은 로컬 DB, 로컬 MCP 는 warm_local() 이 채운 인메모리 캐시에서 읽는다
        (둘 다 이 호출 시점엔 네트워크/spawn 없음).
        """
        tools: list[ToolSpec] = []
        for row in self._connected_connectors():
            tools.extend(self.tools_for(row["id"]))
        for specs in self._local_specs.values():
            tools.extend(specs)
        return tools

    # ── local MCP (.mcp.json, stdio+http) ───────────────────────────────────────
    def _local_servers(self) -> list[LocalMcpServer]:
        """`.mcp.json` 에 선언된 로컬 MCP 서버 목록 (없으면 [])."""
        if self._mcp_config_path is None:
            return []
        return load_local_mcp_servers(self._mcp_config_path)

    def _find_local_server(self, name: str) -> LocalMcpServer | None:
        return next((s for s in self._local_servers() if s.name == name), None)

    def _local_client(self, server: LocalMcpServer) -> StdioMcpClient | RemoteMcpClient:
        """transport 에 맞는 클라이언트를 만든다. stdio 는 spawn-per-op, http 는 로컬
        서버가 보통 인증이 없으므로 access_token="" 로 부른다(원격 OAuth 커넥터와 같은
        RemoteMcpClient 를 재사용 — 프로토콜 로직은 동일하고 인증만 다르다)."""
        if server.transport == "stdio":
            return StdioMcpClient(server.command, server.args, server.env, owner_id=server.connector_id)
        client = RemoteMcpClient(server.url, extra_headers=server.headers)
        client.set_owner(server.connector_id)
        return client

    async def _local_list_tools(self, server: LocalMcpServer) -> list[ToolSpec]:
        client = self._local_client(server)
        if isinstance(client, StdioMcpClient):
            return await client.list_tools()
        return await client.list_tools("")

    async def _local_call_tool(self, server: LocalMcpServer, call: ToolCall) -> ToolResult:
        client = self._local_client(server)
        if isinstance(client, StdioMcpClient):
            return await client.call_tool(call.tool_name, call.arguments, call.id)
        return await client.call_tool(call.tool_name, call.arguments, "", call.id)

    async def warm_local(self) -> int:
        """로컬 MCP 서버(stdio=spawn, http=probe) 를 전부 다시 불러 tools/list 를 캐시한다
        (앱 startup 용). 서버별 실패는 건너뛴다(fail-safe). 반환: 캐시된 총 도구 수.

        서버들을 **병렬로** warm 한다 — 직렬이면 죽은 stdio 서버 하나당 타임아웃(~20초)씩
        기다려서, 죽은 서버 N개면 N×20초가 걸린다. 병렬이면 최악이 타임아웃 1회분이다.
        """
        servers = self._local_servers()
        results = await asyncio.gather(
            *(self._local_list_tools(s) for s in servers), return_exceptions=True
        )
        specs_by_conn: dict[str, list[ToolSpec]] = {}
        total = 0
        for server, result in zip(servers, results):
            if isinstance(result, BaseException):
                log.warning("local mcp %s tools/list failed: %s", server.name, result)
                continue
            specs_by_conn[server.connector_id] = result
            total += len(result)
        self._local_specs = specs_by_conn
        log.info("local mcp warmed: %d server(s), %d tool(s)", len(specs_by_conn), total)
        return total

    def local_server_exists(self, connector_id: str) -> bool:
        """`.mcp.json` 에 선언된 로컬 서버 중 이 connector_id 가 있는지 (permission 라우트 가드용)."""
        return any(s.connector_id == connector_id for s in self._local_servers())

    async def test_local_mcp_server(
        self,
        *,
        transport: str,
        command: str = "",
        args: tuple[str, ...] = (),
        env: dict[str, str] | None = None,
        url: str = "",
        headers: dict[str, str] | None = None,
    ) -> dict:
        """등록 전 연결 확인 — **부작용 없음**(.mcp.json 안 건드림). "등록" 모달의 필수
        게이트: 실제로 spawn(stdio)/probe(http) 해서 tools/list 가 성공하는지 확인한다."""
        probe = LocalMcpServer(
            name="__test__", transport=transport, command=command, args=tuple(args),
            env=env or {}, url=url, headers=headers or {},
        )
        try:
            specs = await self._local_list_tools(probe)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "tool_count": 0, "tools": []}
        return {"ok": True, "error": None, "tool_count": len(specs), "tools": [s.name for s in specs]}

    async def register_local_mcp_server(
        self,
        name: str,
        *,
        transport: str,
        command: str = "",
        args: tuple[str, ...] = (),
        env: dict[str, str] | None = None,
        url: str = "",
        headers: dict[str, str] | None = None,
    ) -> dict:
        """`.mcp.json` 에 실제로 쓰고, 이 서버만 바로 warm 한다(다른 서버는 안 건드림 —
        여러 stdio 서버가 있을 때 등록 하나 때문에 전부 재-spawn 하지 않기 위해).
        warm 실패는 등록 자체를 막지 않는다(새로고침으로 재시도 가능 — warm_local 과 동일 철학).
        """
        if self._mcp_config_path is None:
            raise ValidationError("local MCP is not available (no workspace configured)")
        try:
            add_local_mcp_server(
                self._mcp_config_path, name, transport=transport, command=command,
                args=tuple(args), env=env, url=url, headers=headers,
            )
        except LocalMcpServerExistsError as exc:
            raise ConflictError(str(exc)) from exc
        except ValueError as exc:  # .mcp.json 이 깨져 있거나 transport 값이 잘못됨
            raise ValidationError(str(exc)) from exc
        server = self._find_local_server(name)
        if server is not None:
            try:
                self._local_specs[server.connector_id] = await self._local_list_tools(server)
            except Exception as exc:  # noqa: BLE001
                log.warning("warm after register failed for %s: %s", name, exc)
        info = next((s for s in self.local_servers_info() if s["name"] == name), None)
        if info is None:  # pragma: no cover - add_local_mcp_server 가 방금 썼으니 항상 있어야 함
            raise RuntimeError(f"registered {name!r} but it did not appear in .mcp.json")
        return info

    def unregister_local_mcp_server(self, name: str) -> bool:
        """`.mcp.json` 에서 제거 + 캐시·저장된 도구 권한 정리. 있었으면 True."""
        if self._mcp_config_path is None:
            return False
        removed = remove_local_mcp_server(self._mcp_config_path, name)
        if removed:
            connector_id = f"local:{name}"
            self._local_specs.pop(connector_id, None)
            self._db.execute("DELETE FROM connector_tool_permissions WHERE connector_id=?", (connector_id,))
        return removed

    def local_servers_info(self) -> list[dict]:
        """로컬 MCP 서버 + 캐시된 도구를 UI 용 dict 목록으로 반환한다."""
        out: list[dict] = []
        for server in self._local_servers():
            cid = server.connector_id
            specs = self._local_specs.get(cid, [])
            perm_fn = self.permission_for
            out.append({
                "connector_id": cid,
                "name": server.name,
                "transport": server.transport,
                "command": server.command,
                "args": list(server.args),
                "url": server.url,
                "running": cid in self._local_specs,
                "tools": [
                    {
                        "name": s.name,
                        "description": s.description,
                        "requires_approval": s.requires_approval,
                        "permission": perm_fn(cid, s.name),
                    }
                    for s in specs
                ],
            })
        return out

    def tools_for(self, connector_id: str) -> list[ToolSpec]:
        """특정 connector 의 활성 도구 카탈로그 (로컬 DB 읽기). 없으면 []."""
        row = self._db.query_one(
            "SELECT tools_json FROM connector_credentials WHERE connector_id=?",
            (connector_id,),
        )
        if row is None or not row["tools_json"]:
            return []
        return _specs_from_json(connector_id, row["tools_json"])

    async def invoke(self, call: ToolCall, owner_id: str | None = None) -> ToolResult:
        """connector 도구 실행 (권한 + HITL 승인은 dispatcher 가 이미 처리).

        dispatcher 가 넘긴 `owner_id`(ToolSpec.owner_id)가 있으면 그 connector 로
        직접 라우팅하고, 없을 때만 도구명으로 소유자를 탐색한다 — 같은 도구명을
        노출하는 커넥터가 둘일 때(두 Gmail 계정, search류 커스텀 MCP) 이름 재탐색이
        모델이 고른 것과 다른 커넥터로 호출을 보내는 사고를 막는다.
        실패는 예외가 아니라 ToolResult(ok=False) 로 반환한다.
        """
        try:
            owner = self._resolve_owner(call.tool_name, owner_id)
            if owner is None:
                return ToolResult(call.id, ok=False, error=f"no connected connector owns tool {call.tool_name!r}")
            connector_id, connector_type = owner
            if connector_type == "local":
                return await self._invoke_local(connector_id, call)
            access_token = await self._access_token(connector_id, connector_type)
            if access_token is None:
                return ToolResult(
                    call.id, ok=False,
                    error=f"connector {connector_id} needs reconnection (no valid credentials)",
                )
            client = self._client_for(connector_id, connector_type)
            result = await client.call_tool(
                tool_name=call.tool_name,
                arguments=call.arguments,
                access_token=access_token,
                call_id=call.id,
            )
            if not result.ok and result.error:
                code, detail = self._diagnose_mcp_failure(connector_type, result.error)
                descriptor = resolve_descriptor(self._custom, connector_type)
                # 타입별 read-only probe 자체가 거부되면 단일 리소스 권한 문제가 아니라
                # connector 전체 설정 문제다. connected 오탐을 끊고 UI에 복구 경로를 남긴다.
                if (
                    code
                    and descriptor is not None
                    and call.tool_name == descriptor.health_check_tool
                ):
                    self._mark_error(connector_id, code=code, detail=detail)
                return ToolResult(call.id, ok=False, error=detail)
            return result
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

    # ── per-tool permissions (dispatcher 가 강제 — #도구별 권한 영속화) ──────────
    def permission_for(self, connector_id: str, tool_name: str) -> str:
        """(connector, tool) 의 호출 정책: 'allow' | 'approval' | 'block'.

        저장값이 없으면 도구명 휴리스틱으로 기본값(읽기=allow, 그 외=approval)을 준다.
        """
        row = self._db.query_one(
            "SELECT permission FROM connector_tool_permissions WHERE connector_id=? AND tool_name=?",
            (connector_id, tool_name),
        )
        if row is not None and row["permission"] in _PERMISSIONS:
            return row["permission"]
        return _default_permission(tool_name)

    def set_tool_permission(self, connector_id: str, tool_name: str, permission: str) -> None:
        """(connector, tool) 정책을 저장한다. 잘못된 값은 ValidationError."""
        if permission not in _PERMISSIONS:
            raise ValidationError(f"invalid permission {permission!r}; expected one of {sorted(_PERMISSIONS)}")
        self._db.execute(
            "INSERT INTO connector_tool_permissions(connector_id, tool_name, permission) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(connector_id, tool_name) DO UPDATE SET permission=excluded.permission",
            (connector_id, tool_name, permission),
        )

    def tool_permissions(self, connector_id: str) -> dict[str, str]:
        """저장된 (tool_name → permission) 맵 (미저장 도구는 없음). GET payload·UI 용."""
        rows = self._db.query_all(
            "SELECT tool_name, permission FROM connector_tool_permissions WHERE connector_id=?",
            (connector_id,),
        )
        return {r["tool_name"]: r["permission"] for r in rows if r["permission"] in _PERMISSIONS}

    def effective_tool_permission(self, connector_id: str, tool_name: str) -> str:
        """저장값 또는 휴리스틱 기본값(항상 값이 있는 permission_for 의 별칭)."""
        return self.permission_for(connector_id, tool_name)

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

    # ── 커스텀 원격 커넥터 ("추가" → "커스텀 추가") ──────────────────────────────
    async def discover_custom_connector(self, mcp_endpoint: str) -> DiscoveredOAuthConfig:
        """OAuth 메타데이터 자동 발견(RFC 9728+8414, 부작용 없음) — 모달 미리보기용."""
        return await discover_oauth_config(mcp_endpoint)

    async def add_custom_connector(
        self,
        *,
        display_name: str,
        mcp_endpoint: str,
        auth_url: str,
        token_url: str,
        registration_endpoint: str | None = None,
        scopes: tuple[str, ...] = (),
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> OAuthStart:
        """커스텀 커넥터 등록을 확정하고 즉시 OAuth 를 시작한다.

        client_id 가 이미 주어지면(모달에서 수동 입력) 그대로 쓰고, 없고
        registration_endpoint 가 있으면 여기서 RFC 7591 동적 등록을 실행한다(부작용이
        있는 단계라 미리보기가 아니라 확정 시점에만 수행 — discovery.py 모듈 docstring 참고).

        어느 경로로도 client_id 를 못 얻으면 **저장 전에** fail-closed 한다 — 예전엔
        client_id="" 로 descriptor·connectors 행을 만들어 놓고 begin_connect 에서 죽어서,
        UI 에 연결도 제거도 안 되는 좀비 카드가 남았다.
        """
        if not client_id and registration_endpoint:
            redirect_uri = default_redirect_uri(self._port)
            client_id, client_secret = await register_dynamic_client(registration_endpoint, redirect_uri)
        if not client_id:
            raise ValidationError(
                f"failed to obtain OAuth client ID for {display_name!r} — dynamic "
                "registration (RFC 7591) failed or is unsupported",
                code="connectors.dynamic_registration_failed",
                params={"displayName": display_name},
            )

        connector_type = self._custom.unique_type(display_name)
        descriptor = self._custom.add(
            connector_type=connector_type,
            display_name=display_name,
            auth_url=auth_url,
            token_url=token_url,
            mcp_endpoint=mcp_endpoint,
            scopes=scopes,
            client_id=client_id,
            client_secret=client_secret,
            registration_endpoint=registration_endpoint,
        )
        # 방금 등록한 타입을 공유 flows 캐시에 즉시 반영 — 재시작 없이 바로 begin_connect 가능.
        self._flows[connector_type] = OAuthFlow(
            descriptor,
            client_id=client_id,
            client_secret=client_secret,
            connectors_dir=self._connectors_dir,
            port=self._port,
        )
        return self._manager.begin_connect(connector_type)

    async def complete_oauth(self, *, code: str, state: str) -> ConnectorInfo:
        """OAuth callback: state 검증 → 토큰 교환 → 실제 MCP 검증 → status 확정.

        연결 직후 원격 MCP 도구 카탈로그를 한 번 받아 DB 에 저장한다(sync_tools).
        """
        info = await self._manager.complete_oauth(code=code, state=state)
        try:
            await self.sync_tools(info.id)
        except Exception as exc:  # noqa: BLE001 — 도구 동기화 실패가 연결을 막지 않는다
            log.warning("tool sync after connect failed: %s", exc)
        # tools/list는 Google MCP service가 비활성화돼도 공개 schema를 반환한다. 따라서
        # "도구를 가져왔다"를 연결 성공으로 보지 말고, 안전한 read-only 도구를 한 번 실제
        # 실행해 OAuth + Cloud service + Workspace 권한을 함께 검증한다.
        try:
            await self.verify_connector(info.id)
        except Exception as exc:  # noqa: BLE001 — 검증 crash가 OAuth 성공 자체를 400으로 둔갑시키면 안 된다
            log.warning("connector verify after connect failed: %s", exc)
        return self.get_connector(info.id) or info

    async def cancel_oauth(self, connector_id: str) -> bool:
        """시스템 브라우저 OAuth 대기를 사용자가 취소했을 때 pending state를 폐기한다."""
        return self._manager.cancel_oauth(connector_id)

    async def reject_oauth(self, *, state: str, error: str) -> ConnectorInfo | None:
        """OAuth provider의 error callback을 polling이 관찰 가능한 terminal 상태로 만든다."""
        return self._manager.reject_oauth(state=state, error=error)

    async def verify_connector(self, connector_id: str) -> dict:
        """실제 read-only MCP 호출로 connector가 실행 가능한지 검증한다.

        정적 Google connector는 descriptor.health_check_tool을 사용한다. 커스텀/아직 probe가
        없는 타입은 tools/list까지만 확인한다. 반환값은 관리 API와 진단 스크립트에서 쓴다.
        """
        row = self._db.query_one(
            "SELECT connector_type FROM connectors WHERE id=?", (connector_id,)
        )
        if row is None:
            raise NotFoundError(f"connector {connector_id} not found")
        connector_type = row["connector_type"]
        descriptor = resolve_descriptor(self._custom, connector_type)
        if descriptor is None:
            self._mark_error(
                connector_id,
                code="connectors.descriptor_missing",
                detail=f"No connector descriptor is registered for {connector_type!r}.",
            )
            return {"ok": False, "code": "connectors.descriptor_missing"}

        access_token = await self._access_token(connector_id, connector_type)
        if access_token is None:
            info = self.get_connector(connector_id)
            return {
                "ok": False,
                "code": info.error_code if info else "connectors.oauth_credentials_invalid",
                "error": info.error_detail if info else "No valid OAuth credential.",
            }

        client = self._client_for(connector_id, connector_type)
        if not descriptor.health_check_tool:
            try:
                await client.list_tools(access_token)
            except Exception as exc:  # noqa: BLE001
                detail = f"Remote MCP connection check failed: {exc}"
                self._mark_error(connector_id, code="connectors.mcp_unreachable", detail=detail)
                return {"ok": False, "code": "connectors.mcp_unreachable", "error": detail}
            self._mark_verified(connector_id)
            return {"ok": True}

        try:
            result = await client.call_tool(
                descriptor.health_check_tool,
                dict(descriptor.health_check_arguments),
                access_token,
                f"health-{connector_id}",
            )
        except Exception as exc:  # noqa: BLE001
            # HTTP 4xx 는 예외 문자열에 서버 본문이 실려 온다(client._rpc) —
            # JSON-RPC 오류와 같은 분류기로 복구 가능한 원인인지 판별한다.
            code, detail = self._diagnose_mcp_failure(connector_type, str(exc))
            if code is None:
                code = "connectors.mcp_unreachable"
                detail = f"Remote MCP execution check failed: {exc}"
            self._mark_error(connector_id, code=code, detail=detail)
            return {"ok": False, "code": code, "error": detail}

        if not result.ok:
            code, detail = self._diagnose_mcp_failure(connector_type, result.error or "unknown MCP error")
            code = code or "connectors.mcp_tool_failed"
            self._mark_error(connector_id, code=code, detail=detail)
            return {"ok": False, "code": code, "error": detail}

        self._mark_verified(connector_id)
        return {"ok": True}

    async def delete(self, connector_id: str) -> None:
        """connector 레코드 삭제 + 키체인 항목 정리 (카탈로그는 cascade 삭제).

        커스텀 타입의 마지막 인스턴스였다면 descriptor·client secret·flow 도 함께
        정리한다 — 안 그러면 재등록 때마다 custom_foo_2, _3… 타입이 증식하고 키체인에
        고아 secret 이 쌓인다.
        """
        row = self._db.query_one(
            "SELECT connector_type FROM connectors WHERE id=?", (connector_id,)
        )
        await self._manager.delete(connector_id)
        self._clients.pop(connector_id, None)
        if row is not None:
            self._cleanup_custom_type(row["connector_type"])

    def _cleanup_custom_type(self, connector_type: str) -> None:
        """이 커스텀 타입의 connectors 행이 더 없으면 descriptor(DB)·secret(키체인)·
        flow(캐시)를 정리한다. 정적 타입(registry.py)은 건드리지 않는다."""
        if connector_type not in self._custom.types():
            return
        remaining = self._db.query_one(
            "SELECT id FROM connectors WHERE connector_type=? LIMIT 1", (connector_type,)
        )
        if remaining is not None:
            return
        self._custom.delete(connector_type)
        self._flows.pop(connector_type, None)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _connected_connectors(self) -> list:
        """connected 상태인 모든 connector 행(id, connector_type)."""
        return self._db.query_all(
            "SELECT id, connector_type FROM connectors WHERE status='connected'"
        )

    def _resolve_owner(self, tool_name: str, owner_id: str | None) -> tuple[str, str] | None:
        """invoke 라우팅 대상 (connector_id, type). owner_id 우선, 없으면 이름 탐색.

        owner_id 가 로컬 MCP("local:<name>")면 type="local", 아니면 connectors 행에서
        타입을 찾는다. 지정된 owner 가 더 이상 없으면 이름 폴백 없이 None —
        엉뚱한 커넥터로 흘러가느니 명시적 실패가 낫다(fail-closed).
        """
        if owner_id:
            if owner_id in self._local_specs or owner_id.startswith("local:"):
                return owner_id, "local"
            row = self._db.query_one(
                "SELECT connector_type FROM connectors WHERE id=? AND status='connected'",
                (owner_id,),
            )
            if row is None:
                return None
            return owner_id, row["connector_type"]
        return self._owner_of_tool(tool_name)

    def _owner_of_tool(self, tool_name: str) -> tuple[str, str] | None:
        """도구명을 노출하는 소유자의 (connector_id, type). 로컬 우선, 없으면 None.

        로컬 MCP 는 type="local" 로 표시해 invoke 가 stdio 경로로 라우팅하게 한다.
        """
        for cid, specs in self._local_specs.items():
            if any(s.name == tool_name for s in specs):
                return cid, "local"
        for row in self._connected_connectors():
            if any(s.name == tool_name for s in self.tools_for(row["id"])):
                return row["id"], row["connector_type"]
        return None

    async def _invoke_local(self, connector_id: str, call: ToolCall) -> ToolResult:
        """로컬 MCP(stdio=spawn, http=probe) 도구를 실행한다."""
        name = connector_id_to_name(connector_id)
        server = self._find_local_server(name) if name else None
        if server is None:
            return ToolResult(call.id, ok=False, error=f"local mcp server {name!r} not found in .mcp.json")
        return await self._local_call_tool(server, call)

    def _client_for(self, connector_id: str, connector_type: str) -> RemoteMcpClient:
        """connector 전용 RemoteMcpClient (엔드포인트는 타입 디스크립터에서). 캐시한다."""
        client = self._clients.get(connector_id)
        if client is None:
            descriptor = resolve_descriptor(self._custom, connector_type)
            endpoint = descriptor.resolved_mcp_endpoint() if descriptor else ""
            extra_headers: dict[str, str] | None = None
            # Google MCP 서버는 tools/call 의 IAM(mcp.tools.call)·과금 귀속에
            # x-goog-user-project 를 요구한다(공식 클라이언트 설정과 동일). 헤더가
            # 없으면 tools/list 만 되고 실행은 403 이 난다.
            if descriptor is not None and descriptor.mcp_service_name:
                flow = self._flows.get(connector_type)
                project = flow.quota_project if flow is not None else None
                if project:
                    extra_headers = {"x-goog-user-project": project}
                # 403 진단 시 사용자 콘솔에서 헤더 유무를 확정할 수 있어야 한다.
                log.info(
                    "mcp client %s (%s): x-goog-user-project=%s",
                    connector_id, connector_type, project or "(none — header omitted)",
                )
            client = RemoteMcpClient(endpoint, extra_headers=extra_headers)
            client.set_owner(connector_id)
            self._clients[connector_id] = client
        return client

    async def _fetch_remote_tools(self, connector_id: str) -> list[ToolSpec] | None:
        """connector 의 원격 MCP `tools/list` 결과. 연결/토큰/네트워크 실패 시 None."""
        row = self._db.query_one(
            "SELECT connector_type FROM connectors WHERE id=? "
            "AND status IN ('connected','verifying')",
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

        OAuth 자격증명 오류만 갱신 성공 시 error→connected 로 자가치유한다. MCP service
        설정 오류는 토큰이 유효하다는 이유로 지우지 않는다. 갱신/자격증명 실패 시
        status='error'로 표시한 뒤 None을 반환한다 — #4 토큰 만료 UX.
        """
        credential = self._keychain.load(connector_id)
        if credential is None:
            self._mark_error(
                connector_id,
                code="connectors.oauth_credentials_missing",
                detail="OAuth credentials are missing. Reconnect this connector.",
            )
            return None

        if not credential.is_expired():
            self._mark_credentials_healthy(connector_id)
            return credential.access_token

        # 만료 → refresh 는 connector 별로 직렬화한다. 락 안에서 키체인을 다시 읽어
        # 앞선 병렬 호출이 이미 갱신했으면(rotation 포함) 그 토큰을 그대로 쓴다.
        lock = self._refresh_locks.setdefault(connector_id, asyncio.Lock())
        async with lock:
            credential = self._keychain.load(connector_id)
            if credential is None:
                self._mark_error(
                    connector_id,
                    code="connectors.oauth_credentials_missing",
                    detail="OAuth credentials are missing. Reconnect this connector.",
                )
                return None
            if not credential.is_expired():
                self._mark_credentials_healthy(connector_id)
                return credential.access_token

            if not credential.refresh_token:
                log.warning("connector %s token expired and no refresh_token", connector_id)
                self._mark_error(
                    connector_id,
                    code="connectors.oauth_refresh_token_missing",
                    detail="The OAuth access token expired and no refresh token is available. Reconnect this connector.",
                )
                return None

            flow = self._flows.get(connector_type)
            if flow is None:
                self._mark_error(
                    connector_id,
                    code="connectors.oauth_flow_missing",
                    detail=f"No OAuth flow is registered for {connector_type!r}.",
                )
                return None
            try:
                refreshed = await flow.refresh_access_token(credential.refresh_token)
            except Exception as exc:  # noqa: BLE001
                log.warning("token refresh failed for %s: %s", connector_id, exc)
                self._mark_error(
                    connector_id,
                    code="connectors.oauth_refresh_failed",
                    detail=f"OAuth token refresh failed. Reconnect this connector. ({exc})",
                )
                return None
            # rotation 서버가 새 refresh_token 을 내려줬으면 함께 교체한다 — 이전 것은
            # 서버가 무효화했을 수 있어 그대로 두면 다음 갱신부터 실패한다.
            rotated = (
                refreshed.refresh_token
                if refreshed.refresh_token and refreshed.refresh_token != credential.refresh_token
                else None
            )
            self._keychain.update_access_token(
                connector_id,
                access_token=refreshed.access_token,
                expires_in=refreshed.expires_in,
                refresh_token=rotated,
            )
            # DB의 만료 메타도 실제 키체인과 함께 갱신한다. 기존 구현은 최초 OAuth
            # token_expires_at만 남겨 UI/사후 분석에서 계속 만료된 것처럼 보였다.
            from datetime import datetime, timedelta, timezone
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=refreshed.expires_in)
            self._db.execute(
                "UPDATE connector_credentials SET token_expires_at=? WHERE connector_id=?",
                (expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"), connector_id),
            )
            self._mark_credentials_healthy(connector_id)
            return refreshed.access_token

    def _mark_error(self, connector_id: str, *, code: str, detail: str) -> None:
        self._db.execute(
            "UPDATE connectors SET status='error', error_code=?, error_detail=? WHERE id=?",
            (code, detail, connector_id),
        )

    def _mark_credentials_healthy(self, connector_id: str) -> None:
        """OAuth 계열 오류만 자가치유한다. MCP service/IAM 오류는 verify가 지운다."""
        self._db.execute(
            "UPDATE connectors SET status='connected', last_verified_at=?, "
            "error_code=NULL, error_detail=NULL WHERE id=? AND status='error' "
            "AND (error_code IS NULL OR error_code LIKE 'connectors.oauth_%')",
            (now_iso(), connector_id),
        )

    def _mark_verified(self, connector_id: str) -> None:
        """실제 MCP 실행 성공 후에만 모든 오류를 지우고 connected로 확정한다."""
        self._db.execute(
            "UPDATE connectors SET status='connected', last_verified_at=?, "
            "error_code=NULL, error_detail=NULL WHERE id=?",
            (now_iso(), connector_id),
        )

    def _diagnose_mcp_failure(self, connector_type: str, raw_error: str) -> tuple[str | None, str]:
        """원격 MCP의 축약 오류를 사용자가 복구 가능한 설명으로 바꾼다.

        Google MCP(mcp_service_name 보유) 타입 한정. 분류 불가면 (None, 원문) —
        호출자가 원문을 그대로 노출한다. server said 꼬리는 콘솔 없이 UI만 보는
        사용자를 위한 원문 증거다.
        """
        descriptor = resolve_descriptor(self._custom, connector_type)
        if descriptor is None or not descriptor.mcp_service_name:
            return None, raw_error
        service = descriptor.mcp_service_name
        lowered = raw_error.lower()
        evidence = f" (server said: {raw_error[:200]})"
        if "service_disabled" in lowered or "has not been used in project" in lowered:
            detail = (
                f"The remote MCP service is disabled. Enable {service} in the OAuth client's "
                f"Google Cloud project, wait a few minutes, then verify again.{evidence}"
            )
            return "connectors.mcp_service_disabled", detail
        if "access_token_scope_insufficient" in lowered or "insufficient authentication scopes" in lowered:
            detail = (
                "The OAuth token is missing a scope this tool requires. Reconnect the connector "
                f"and approve every requested permission.{evidence}"
            )
            return "connectors.mcp_scope_insufficient", detail
        if "permission" in lowered and ("denied" in lowered or "does not have" in lowered):
            # 실사례(gemini-cli discussion #26017) 기준 최다 원인은 Workspace Developer
            # Preview 미등록 — IAM(roles/mcp.toolUser)보다 앞에 안내한다.
            detail = (
                "OAuth completed, but the remote MCP server denied tool execution (mcp.tools.call). "
                "Most commonly the Google account is not enrolled in the Google Workspace Developer "
                "Preview Program (https://developers.google.com/workspace/preview). Also grant the "
                "'MCP Tool User' role (roles/mcp.toolUser) on the Google Cloud project that owns the "
                f"OAuth client and keep {service} enabled — IAM changes can take a few minutes to "
                "propagate. Then verify again; reconnecting is not required. Tool discovery can "
                f"succeed even while execution is denied.{evidence}"
            )
            return "connectors.mcp_service_permission_denied", detail
        if "unauthenticated" in lowered or "invalid authentication credentials" in lowered:
            detail = f"The remote MCP server rejected the OAuth credential. Reconnect this connector.{evidence}"
            # connectors.oauth_ 접두 → 토큰 refresh 성공 시 _mark_credentials_healthy 가 자가치유.
            return "connectors.oauth_unauthenticated", detail
        return None, raw_error

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
