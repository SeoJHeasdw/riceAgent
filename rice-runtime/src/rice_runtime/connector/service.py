"""ConnectorService — IConnectorService 구현 파사드 (RUNTIME_DESIGN §9, CONTRACTS §4).

두 표면을 조합한다:
  1. 도구 표면 (orchestrator/dispatcher 용):
       list_tools()    → 연결된 connector 가 제공하는 ToolSpec 목록
       invoke(call)    → Gmail API 호출 → ToolResult
       check_permission(plugin_id, connector_type) → 권한 검증
  2. 관리 (routes/connectors.py 용):
       list/get/status/begin_connect/complete_oauth/delete

구현 분해 (파사드 역할):
  - ConnectorManager: lifecycle (begin_connect / complete_oauth / delete)
  - KeychainStore:    OS 키체인 CRUD
  - PermissionChecker: plugin manifest 기반 권한 검증
  - GmailToolset:     Gmail 원자적 도구 정의 + 실행

composition_root.py 에서 RicePaths 가 추가로 주입된다 (DI 허용 — CONVENTIONS §0.3).
"""
from __future__ import annotations

import json
import logging

from ..core.errors import NotFoundError, ValidationError
from ..core.paths import RicePaths
from ..core.storage.sqlite import Database
from ..core.types import ToolCall, ToolResult, ToolSpec
from .credential.keychain import KeychainStore
from .interfaces import ConnectorInfo, ConnectorStatus, IConnectorService, OAuthStart
from .manager import ConnectorManager, _row_to_info
from .oauth.flow import GmailOAuthFlow
from .permission.checker import PermissionChecker

log = logging.getLogger("rice.connector.service")

# ── Gmail 원자적 도구 정의 (RUNTIME_DESIGN §9, connector.md "도구 표면") ──────
# source="connector", requires_approval=True (외부 접근 — RUNTIME_DESIGN §5)
_GMAIL_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="gmail_search",
        description="Gmail 메일함에서 메일을 검색합니다. query 파라미터로 Gmail search syntax 를 사용합니다.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail 검색 쿼리 (예: 'from:boss@company.com subject:report')",
                },
                "max_results": {
                    "type": "integer",
                    "description": "반환할 최대 메일 수 (기본값: 10, 최대: 50)",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
        source="connector",
        requires_approval=True,
    ),
    ToolSpec(
        name="gmail_read",
        description="특정 Gmail 메일의 내용을 읽습니다. message_id 는 gmail_search 결과에서 얻습니다.",
        parameters={
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "읽을 메일의 Gmail message ID",
                },
            },
            "required": ["message_id"],
        },
        source="connector",
        requires_approval=True,
    ),
]


class ConnectorService(IConnectorService):
    """IConnectorService 의 실제 구현 — Gmail OAuth + 키체인 + 권한 검증."""

    def __init__(self, db: Database, paths: RicePaths | None = None) -> None:
        self._db = db
        self._paths = paths
        self._keychain = KeychainStore()
        self._manager = ConnectorManager(db, self._keychain)
        self._permission = PermissionChecker(paths.plugins if paths else None)
        self._gmail_flow = GmailOAuthFlow()

    # ── tool surface ──────────────────────────────────────────────────────────
    def list_tools(self) -> list[ToolSpec]:
        """연결된(connected) Gmail connector 가 있으면 Gmail 도구 목록을 반환한다.

        도구는 source="connector", requires_approval=True 로 노출된다
        (외부 접근 — RUNTIME_DESIGN §5).
        """
        connected = self._db.query_all(
            "SELECT id FROM connectors WHERE connector_type='gmail' AND status='connected'"
        )
        if not connected:
            return []
        # connected connector 가 있으면 모든 Gmail 도구를 owner_id 에 첫 번째 connector_id 로 노출
        owner_id = connected[0]["id"]
        return [
            ToolSpec(
                name=t.name,
                description=t.description,
                parameters=t.parameters,
                source=t.source,
                owner_id=owner_id,
                requires_approval=t.requires_approval,
            )
            for t in _GMAIL_TOOLS
        ]

    async def invoke(self, call: ToolCall) -> ToolResult:
        """Gmail 도구 실행 (권한 + HITL 승인은 dispatcher 가 이미 처리).

        토큰 만료 임박 시 자동 갱신 후 실행한다 (RUNTIME_DESIGN §9.3).
        실패는 예외가 아니라 ToolResult(ok=False) 로 반환 (connector.md 도구 표면).
        """
        try:
            return await self._invoke_gmail(call)
        except Exception as exc:  # noqa: BLE001
            log.warning("connector invoke failed: %s", exc)
            return ToolResult(call.id, ok=False, error=str(exc))

    async def _invoke_gmail(self, call: ToolCall) -> ToolResult:
        """Gmail 도구를 실행한다. keyring/httpx optional extra 가 있어야 한다."""
        # 연결된 Gmail connector 에서 owner_id 결정
        connected = self._db.query_all(
            "SELECT id FROM connectors WHERE connector_type='gmail' AND status='connected'"
        )
        if not connected:
            return ToolResult(call.id, ok=False, error="gmail connector not connected")

        connector_id = call.arguments.get("_connector_id") or connected[0]["id"]
        credential = self._keychain.load(connector_id)
        if credential is None:
            return ToolResult(call.id, ok=False, error="gmail credentials not found in keychain")

        # 만료 임박 시 access_token 갱신 (RUNTIME_DESIGN §9.3)
        access_token = credential.access_token
        if credential.is_expired() and credential.refresh_token:
            try:
                refreshed = await self._gmail_flow.refresh_access_token(credential.refresh_token)
                self._keychain.update_access_token(
                    connector_id,
                    access_token=refreshed.access_token,
                    expires_in=refreshed.expires_in,
                )
                access_token = refreshed.access_token
            except Exception as exc:  # noqa: BLE001
                log.warning("token refresh failed for %s: %s", connector_id, exc)
                # 만료됐더라도 일단 기존 토큰으로 시도 (API 가 reject 하면 error 반환)

        try:
            import httpx  # lazy import (connectors extra)
        except ImportError as exc:
            return ToolResult(call.id, ok=False, error=f"httpx not installed: {exc}")

        headers = {"Authorization": f"Bearer {access_token}"}

        async with httpx.AsyncClient() as client:
            if call.tool_name == "gmail_search":
                result = await _gmail_search(client, headers, call.arguments)
            elif call.tool_name == "gmail_read":
                result = await _gmail_read(client, headers, call.arguments)
            else:
                return ToolResult(call.id, ok=False, error=f"unknown gmail tool: {call.tool_name}")

        return ToolResult(call.id, ok=True, result=result)

    def check_permission(self, plugin_id: str, connector_type: str) -> bool:
        """plugin manifest 의 requires.connectors 기반 권한 검증 (RUNTIME_DESIGN §9.4).

        PermissionChecker 가 없거나 manifest 를 못 찾으면 fail-closed(False) (CONVENTIONS §7).
        """
        if self._permission is None:
            # NOTE(agent): paths 가 None 으로 주입된 경우(테스트/골격) fail-closed
            return False
        return self._permission.check(plugin_id, connector_type)

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
        """OAuth callback: state 검증 → 토큰 교환 → 키체인 저장 → status='connected'."""
        return await self._manager.complete_oauth(code=code, state=state)

    async def delete(self, connector_id: str) -> None:
        """connector 레코드 삭제 + 키체인 항목 정리."""
        await self._manager.delete(connector_id)


# ── Gmail API 호출 헬퍼 ────────────────────────────────────────────────────────

async def _gmail_search(client, headers: dict, args: dict) -> dict:
    """Gmail 메일 검색 (Gmail API v1).

    NOTE(agent): Gmail API 응답 형태는
    https://developers.google.com/gmail/api/reference/rest/v1/users.messages/list
    에서 확인. messages 는 id/threadId 만 포함, 상세는 별도 get 호출 필요.
    """
    query = args.get("query", "")
    max_results = min(int(args.get("max_results", 10)), 50)
    url = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
    resp = await client.get(
        url,
        headers=headers,
        params={"q": query, "maxResults": max_results},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


async def _gmail_read(client, headers: dict, args: dict) -> dict:
    """Gmail 메일 읽기 (Gmail API v1).

    NOTE(agent): 응답 형태는
    https://developers.google.com/gmail/api/reference/rest/v1/users.messages/get
    참조. format=full 로 전체 내용을 가져온다.
    """
    message_id = args.get("message_id", "")
    if not message_id:
        raise ValueError("message_id is required")
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}"
    resp = await client.get(
        url,
        headers=headers,
        params={"format": "full"},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()
