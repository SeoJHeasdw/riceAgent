"""ConnectorManager — connector lifecycle (연결·해제) 관리 (RUNTIME_DESIGN §9, connector.md #10).

책임:
  - begin_connect: pending connector 레코드 생성 + OAuth URL 구성
  - complete_oauth: code 교환 → 키체인 저장 → status='connected' 갱신
  - delete: connector 레코드 삭제 + 키체인 항목 정리

DB 접근은 직접 하고, OAuth flow·keychain 은 주입된 collaborator 에 위임한다.
(단일 책임 원칙 — manager 는 lifecycle 상태 머신만 가짐.)
"""
from __future__ import annotations

import json
import logging
from datetime import timezone, datetime

from ..core.errors import NotFoundError, ValidationError
from ..core.storage.sqlite import Database
from ..core.types import new_id, now_iso
from .credential.keychain import KeychainStore
from .interfaces import ConnectorInfo, ConnectorStatus, OAuthStart
from .oauth.flow import GmailOAuthFlow

log = logging.getLogger("rice.connector.manager")

# 지원하는 connector_type 목록 (MVP = gmail 하나 — connector.md)
_SUPPORTED_TYPES = frozenset({"gmail"})

# Gmail connector display name
_DISPLAY_NAMES: dict[str, str] = {
    "gmail": "Gmail",
}


class ConnectorManager:
    """ConnectorService 에서 분리된 lifecycle 담당 컴포넌트.

    service.py 는 이 manager 에 begin_connect / complete_oauth / delete 를 위임한다.
    """

    def __init__(self, db: Database, keychain: KeychainStore, gmail_flow: GmailOAuthFlow | None = None) -> None:
        self._db = db
        self._keychain = keychain
        # NOTE(agent): GmailOAuthFlow 는 env > JSON(~/.rice/connectors) 순으로 자격증명을
        # 읽는다. 미설정 시 begin_connect 시점에 OAuthConfigError 가 발생한다.
        # ConnectorService 가 paths/port 를 아는 flow 를 주입한다(공유). 없으면 자체 생성.
        self._gmail_flow = gmail_flow or GmailOAuthFlow()

        # state → connector_id 매핑 — OAuth callback 에서 state 로 connector 를 찾을 수 있게
        # 인메모리로 관리 (단일 활성 세션 전제 — RUNTIME_DESIGN §2).
        # 런타임 재시작 시 진행 중이던 OAuth 는 소실되지만 데모 용도상 허용.
        self._pending_states: dict[str, str] = {}  # state -> connector_id

    def begin_connect(self, connector_type: str) -> OAuthStart:
        """OAuth 시작: pending 레코드 생성 + authorization URL 반환.

        connector_type 이 지원되지 않으면 ValidationError (RUNTIME_DESIGN §9.1).
        """
        if connector_type not in _SUPPORTED_TYPES:
            raise ValidationError(
                f"unsupported connector_type={connector_type!r}. "
                f"Supported: {sorted(_SUPPORTED_TYPES)}"
            )

        connector_id = new_id("conn")
        display_name = _DISPLAY_NAMES.get(connector_type, connector_type)

        # pending 레코드 생성 (status='disconnected') — complete_oauth 가 'connected' 로 갱신
        self._db.execute(
            "INSERT INTO connectors(id, connector_type, display_name, status) "
            "VALUES (?, ?, ?, 'disconnected')",
            (connector_id, connector_type, display_name),
        )

        oauth_url, state = self._gmail_flow.build_auth_url()
        self._pending_states[state] = connector_id

        return OAuthStart(
            connector_id=connector_id,
            oauth_url=oauth_url,
            state=state,
        )

    async def complete_oauth(self, *, code: str, state: str) -> ConnectorInfo:
        """OAuth callback: state 검증 → code 교환 → 키체인 저장 → status 갱신.

        state 불일치 시 ValidationError (CSRF 방어).
        """
        connector_id = self._pending_states.pop(state, None)
        if connector_id is None:
            raise ValidationError(
                f"unknown or expired OAuth state={state!r} — possible CSRF attempt"
            )

        # connector 레코드 존재 확인
        row = self._db.query_one("SELECT * FROM connectors WHERE id = ?", (connector_id,))
        if row is None:
            raise NotFoundError(f"connector {connector_id} not found (state race?)")

        # code → tokens (GmailOAuthFlow.exchange_code 가 state 재검증)
        # state 는 이미 위에서 검증했으므로 expected_state = state 로 전달
        token = await self._gmail_flow.exchange_code(
            code=code, state=state, expected_state=state
        )

        # 토큰을 OS 키체인에 저장 (DB/로그 금지 — RUNTIME_DESIGN §10.4)
        self._keychain.save(
            connector_id,
            access_token=token.access_token,
            refresh_token=token.refresh_token,
            expires_in=token.expires_in,
        )

        # DB: status='connected' + connector_credentials 메타(토큰 본문 제외)
        connected_at = now_iso()
        self._db.execute(
            "UPDATE connectors SET status='connected', connected_at=?, last_verified_at=? WHERE id=?",
            (connected_at, connected_at, connector_id),
        )

        # scopes JSON 저장 (token_expires_at + scopes 만 — 토큰 본문 X)
        expires_at_iso: str | None = None
        if token.expires_in:
            from datetime import timedelta
            dt = datetime.now(timezone.utc) + timedelta(seconds=token.expires_in)
            expires_at_iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        scopes_json = json.dumps(token.scope.split()) if token.scope else "[]"
        self._db.execute(
            "INSERT INTO connector_credentials(connector_id, token_expires_at, scopes) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(connector_id) DO UPDATE SET token_expires_at=excluded.token_expires_at, "
            "scopes=excluded.scopes",
            (connector_id, expires_at_iso, scopes_json),
        )

        # 갱신된 connector 정보 반환
        updated = self._db.query_one(
            "SELECT c.*, cc.scopes FROM connectors c "
            "LEFT JOIN connector_credentials cc ON c.id=cc.connector_id "
            "WHERE c.id=?",
            (connector_id,),
        )
        return _row_to_info(updated)

    async def delete(self, connector_id: str) -> None:
        """connector 레코드 삭제 + 키체인 항목 정리."""
        self._keychain.delete(connector_id)
        self._db.execute("DELETE FROM connectors WHERE id = ?", (connector_id,))


def _row_to_info(row) -> ConnectorInfo:
    """DB Row → ConnectorInfo DTO 변환."""
    scopes: tuple[str, ...] = ()
    if row is not None and "scopes" in row.keys() and row["scopes"]:
        try:
            scopes = tuple(json.loads(row["scopes"]))
        except (json.JSONDecodeError, TypeError):
            scopes = ()

    return ConnectorInfo(
        id=row["id"],
        connector_type=row["connector_type"],
        display_name=row["display_name"] or row["connector_type"],
        status=row["status"],
        scopes=scopes,
        connected_at=row["connected_at"],
        last_verified_at=row["last_verified_at"],
    )
