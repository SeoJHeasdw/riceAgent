"""커스텀 원격 커넥터 저장소 — registry.py 의 정적 디스크립터를 DB 로 확장한다.

사용자가 "커스텀 추가"로 등록한 원격 MCP 커넥터는 코드에 정적으로 박혀 있는 게 아니라
런타임에 생기므로 DB(+키체인)에 저장한다. 비민감 필드는 `custom_connector_descriptors`
테이블에, client_secret 은 키체인에 둔다(§10.4 — DB/로그에 비밀 금지).

이 스토어가 만들어내는 것도 registry.ConnectorDescriptor 그 자체다 — manager/service 는
"정적이냐 커스텀이냐"를 몰라도 되도록, resolve_descriptor/resolve_supported_types 로
두 출처를 합류시킨다.
"""
from __future__ import annotations

import json
import logging
import re

from ..core.storage.sqlite import Database
from .credential.keychain import KeychainStore
from .registry import ConnectorDescriptor, get_descriptor, supported_types

log = logging.getLogger("rice.connector.custom_registry")


def _secret_key(connector_type: str) -> str:
    return f"custom_client:{connector_type}"


def slugify_connector_type(display_name: str) -> str:
    """표시명 → connector_type 슬러그. "custom_" 접두사로 정적 레지스트리와 네임스페이스 분리."""
    base = re.sub(r"[^a-z0-9]+", "_", display_name.strip().lower()).strip("_")
    return f"custom_{base or 'connector'}"


class CustomConnectorStore:
    """커스텀 connector_type ↔ ConnectorDescriptor 영속화 (DB + 키체인)."""

    def __init__(self, db: Database, keychain: KeychainStore) -> None:
        self._db = db
        self._keychain = keychain

    def unique_type(self, display_name: str) -> str:
        """정적+커스텀 어느 쪽과도 겹치지 않는 connector_type 을 만든다."""
        base = slugify_connector_type(display_name)
        candidate = base
        existing = supported_types() | self.types()
        n = 2
        while candidate in existing:
            candidate = f"{base}_{n}"
            n += 1
        return candidate

    def add(
        self,
        *,
        connector_type: str,
        display_name: str,
        auth_url: str,
        token_url: str,
        mcp_endpoint: str,
        scopes: tuple[str, ...],
        client_id: str,
        client_secret: str | None,
        registration_endpoint: str | None,
    ) -> ConnectorDescriptor:
        self._db.execute(
            "INSERT INTO custom_connector_descriptors"
            "(connector_type, display_name, auth_url, token_url, mcp_endpoint, scopes_json, "
            " client_id, registration_endpoint) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                connector_type,
                display_name,
                auth_url,
                token_url,
                mcp_endpoint,
                json.dumps(list(scopes)),
                client_id,
                registration_endpoint,
            ),
        )
        if client_secret:
            self._keychain.set_secret(_secret_key(connector_type), client_secret)
        return _to_descriptor(
            connector_type=connector_type,
            display_name=display_name,
            auth_url=auth_url,
            token_url=token_url,
            mcp_endpoint=mcp_endpoint,
            scopes=scopes,
        )

    def get(self, connector_type: str) -> ConnectorDescriptor | None:
        row = self._db.query_one(
            "SELECT * FROM custom_connector_descriptors WHERE connector_type=?", (connector_type,)
        )
        if row is None:
            return None
        return _to_descriptor(
            connector_type=row["connector_type"],
            display_name=row["display_name"],
            auth_url=row["auth_url"],
            token_url=row["token_url"],
            mcp_endpoint=row["mcp_endpoint"],
            scopes=tuple(json.loads(row["scopes_json"] or "[]")),
        )

    def types(self) -> frozenset[str]:
        rows = self._db.query_all("SELECT connector_type FROM custom_connector_descriptors")
        return frozenset(r["connector_type"] for r in rows)

    def list_descriptors(self) -> list[ConnectorDescriptor]:
        rows = self._db.query_all("SELECT connector_type FROM custom_connector_descriptors")
        out: list[ConnectorDescriptor] = []
        for r in rows:
            d = self.get(r["connector_type"])
            if d is not None:
                out.append(d)
        return out

    def client_id_for(self, connector_type: str) -> str | None:
        row = self._db.query_one(
            "SELECT client_id FROM custom_connector_descriptors WHERE connector_type=?", (connector_type,)
        )
        return row["client_id"] if row else None

    def client_secret_for(self, connector_type: str) -> str | None:
        return self._keychain.get_secret(_secret_key(connector_type))

    def delete(self, connector_type: str) -> None:
        self._db.execute(
            "DELETE FROM custom_connector_descriptors WHERE connector_type=?", (connector_type,)
        )
        self._keychain.delete_secret(_secret_key(connector_type))


def _to_descriptor(
    *,
    connector_type: str,
    display_name: str,
    auth_url: str,
    token_url: str,
    mcp_endpoint: str,
    scopes: tuple[str, ...],
) -> ConnectorDescriptor:
    """커스텀 행 → ConnectorDescriptor. client_id/secret 은 여기 담기지 않는다 — OAuthFlow
    생성 시 CustomConnectorStore.client_id_for/client_secret_for 로 별도 주입한다
    (정적 디스크립터의 env/file 자격증명 해석 경로와 분리하기 위해)."""
    return ConnectorDescriptor(
        connector_type=connector_type,
        display_name=display_name,
        auth_url=auth_url,
        token_url=token_url,
        scopes=scopes,
        mcp_endpoint=mcp_endpoint,
        env_prefix=f"RICE_CUSTOM_{connector_type.upper()}",
        client_secret_filename=f"{connector_type}_client_secret.json",
        is_custom=True,
    )


# ── 정적(registry.py) + 커스텀(DB) 병합 조회 — manager/service 가 쓴다 ─────────────
def resolve_descriptor(custom: "CustomConnectorStore | None", connector_type: str) -> ConnectorDescriptor | None:
    return get_descriptor(connector_type) or (custom.get(connector_type) if custom else None)


def resolve_supported_types(custom: "CustomConnectorStore | None") -> frozenset[str]:
    return supported_types() | (custom.types() if custom else frozenset())
