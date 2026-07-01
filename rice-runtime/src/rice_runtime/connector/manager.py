"""ConnectorManager — connector lifecycle (연결·해제) 관리 (RUNTIME_DESIGN §9, connector.md #10).

책임:
  - begin_connect: pending connector 레코드 생성 + OAuth URL 구성
  - complete_oauth: code 교환 → 키체인 저장 → status='connected' 갱신
  - delete: connector 레코드 삭제 + 키체인 항목 정리

타입별 차이(지원 타입·표시명·OAuth 엔드포인트)는 registry.ConnectorDescriptor 에서 오고,
매니저는 그 위에서 제네릭하게 동작한다(하드코딩 없음). OAuth flow 는 타입별로
주입된 flows 딕셔너리에 위임한다. 정적(registry.py) 타입 외에 사용자가 런타임에 추가한
커스텀 타입(custom_registry.CustomConnectorStore, DB)도 같은 방식으로 지원한다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..core.errors import NotFoundError, ValidationError
from ..core.storage.sqlite import Database
from ..core.types import new_id, now_iso
from .credential.keychain import KeychainStore
from .custom_registry import CustomConnectorStore, resolve_descriptor, resolve_supported_types
from .interfaces import ConnectorInfo, OAuthStart
from .oauth.flow import OAuthFlow
from .registry import descriptors

log = logging.getLogger("rice.connector.manager")


class ConnectorManager:
    """ConnectorService 에서 분리된 lifecycle 담당 컴포넌트.

    service.py 는 이 manager 에 begin_connect / complete_oauth / delete 를 위임한다.
    flows: connector_type → OAuthFlow. 미주입 시 레지스트리에서 기본 flow 를 만든다
    (자격증명 미해석 — 골격/테스트용). custom 미주입 시 커스텀 타입은 지원하지 않는다
    (기존 테스트가 flows 만 넘기던 생성자 호환 유지).
    """

    def __init__(
        self,
        db: Database,
        keychain: KeychainStore,
        flows: dict[str, OAuthFlow] | None = None,
        custom: CustomConnectorStore | None = None,
    ) -> None:
        self._db = db
        self._keychain = keychain
        # NOTE(agent): OAuthFlow 는 env > JSON(~/.rice/connectors) 순으로 자격증명을 읽는다.
        # ConnectorService 가 paths/port 를 아는 flow 들을 주입한다(공유). 없으면 자체 생성.
        self._flows: dict[str, OAuthFlow] = flows or {d.connector_type: OAuthFlow(d) for d in descriptors()}
        self._custom = custom

        # state → connector_id 매핑 — OAuth callback 에서 state 로 connector 를 찾을 수 있게
        # 인메모리로 관리 (단일 활성 세션 전제 — RUNTIME_DESIGN §2).
        self._pending_states: dict[str, str] = {}  # state -> connector_id
        # state → PKCE code_verifier(RFC 7636) — exchange_code 에 되돌려줘야 하므로 state 와
        # 같은 생명주기(단일 활성 세션 전제)로 함께 보관한다.
        self._pending_verifiers: dict[str, str] = {}  # state -> code_verifier

    def _flow_for(self, connector_type: str) -> OAuthFlow:
        flow = self._flows.get(connector_type)
        if flow is None:
            raise ValidationError(f"no OAuth flow registered for connector_type={connector_type!r}")
        return flow

    def begin_connect(self, connector_type: str) -> OAuthStart:
        """OAuth 시작: pending 레코드 생성 + authorization URL 반환.

        connector_type 이 지원되지 않으면(정적+커스텀 어느 쪽에도 없음) ValidationError
        (RUNTIME_DESIGN §9.1). 같은 타입의 disconnected pending 레코드가 있으면 재사용한다.
        """
        known = resolve_supported_types(self._custom)
        if connector_type not in known:
            raise ValidationError(
                f"unsupported connector_type={connector_type!r}. Supported: {sorted(known)}"
            )

        descriptor = resolve_descriptor(self._custom, connector_type)
        display_name = descriptor.display_name if descriptor else connector_type

        # 같은 타입의 재사용 가능한 레코드(연결 전 pending 또는 자격증명 오류로 재연결 대상)
        # 를 재사용 — 없으면 새로 생성. error 상태도 재사용해 재연결 시 중복 레코드를 막는다.
        existing = self._db.query_one(
            "SELECT id FROM connectors WHERE connector_type=? AND status IN ('disconnected','error') LIMIT 1",
            (connector_type,),
        )
        if existing:
            connector_id = existing["id"]
        else:
            connector_id = new_id("conn")
            self._db.execute(
                "INSERT INTO connectors(id, connector_type, display_name, status) "
                "VALUES (?, ?, ?, 'disconnected')",
                (connector_id, connector_type, display_name),
            )

        oauth_url, state, code_verifier = self._flow_for(connector_type).build_auth_url()
        self._pending_states[state] = connector_id
        self._pending_verifiers[state] = code_verifier

        return OAuthStart(connector_id=connector_id, oauth_url=oauth_url, state=state)

    async def complete_oauth(self, *, code: str, state: str) -> ConnectorInfo:
        """OAuth callback: state 검증 → code 교환 → 키체인 저장 → status 갱신."""
        connector_id = self._pending_states.pop(state, None)
        code_verifier = self._pending_verifiers.pop(state, None)
        if connector_id is None:
            raise ValidationError(
                f"unknown or expired OAuth state={state!r} — possible CSRF attempt"
            )

        row = self._db.query_one("SELECT * FROM connectors WHERE id = ?", (connector_id,))
        if row is None:
            raise NotFoundError(f"connector {connector_id} not found (state race?)")

        # 이 connector 의 타입에 맞는 flow 로 code 를 교환한다(PKCE code_verifier 동봉).
        token = await self._flow_for(row["connector_type"]).exchange_code(
            code=code, state=state, expected_state=state, code_verifier=code_verifier
        )

        # 토큰을 OS 키체인에 저장 (DB/로그 금지 — RUNTIME_DESIGN §10.4)
        self._keychain.save(
            connector_id,
            access_token=token.access_token,
            refresh_token=token.refresh_token,
            expires_in=token.expires_in,
        )

        connected_at = now_iso()
        self._db.execute(
            "UPDATE connectors SET status='connected', connected_at=?, last_verified_at=? WHERE id=?",
            (connected_at, connected_at, connector_id),
        )

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

        updated = self._db.query_one(
            "SELECT c.*, cc.scopes FROM connectors c "
            "LEFT JOIN connector_credentials cc ON c.id=cc.connector_id "
            "WHERE c.id=?",
            (connector_id,),
        )
        return _row_to_info(updated)

    async def delete(self, connector_id: str) -> None:
        """connector 레코드 삭제 + 키체인 항목 정리 + 저장된 도구 권한 정리.

        connector_tool_permissions 는 FK 가 없어(로컬 MCP 도 같은 테이블을 쓰기 위해
        의도적으로 뺐다 — 0008 참고) cascade 삭제가 안 되므로 여기서 명시적으로 정리한다.
        """
        self._keychain.delete(connector_id)
        self._db.execute("DELETE FROM connectors WHERE id = ?", (connector_id,))
        self._db.execute("DELETE FROM connector_tool_permissions WHERE connector_id = ?", (connector_id,))


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
