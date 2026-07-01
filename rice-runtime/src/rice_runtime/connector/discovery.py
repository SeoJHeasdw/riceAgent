"""OAuth 자동 디스커버리 — 커스텀 원격 MCP 커넥터용 (connector.md "커스텀 추가").

임의의 원격 MCP 서버 URL 하나만으로 OAuth 설정(authorize/token endpoint, scope,
동적 client 등록 가능 여부)을 알아낸다 — Google 처럼 코드에 미리 박아둔 디스크립터
없이도 "추가"가 가능하게 하는 것이 목적. 표준 절차 두 단계 + 별도 등록 단계:

  1. RFC 9728 (OAuth 2.0 Protected Resource Metadata) — MCP 엔드포인트에 인증 없이
     요청 → 401 + `WWW-Authenticate: Bearer resource_metadata="..."` 에서 메타데이터
     URL 을 얻거나, 없으면 `{origin}/.well-known/oauth-protected-resource` 로 추정한다.
     이 문서의 `authorization_servers[0]` 이 다음 단계의 issuer.
  2. RFC 8414 (OAuth 2.0 Authorization Server Metadata) — issuer 의
     `/.well-known/oauth-authorization-server`(없으면 `/.well-known/openid-configuration`)
     에서 authorization_endpoint / token_endpoint / registration_endpoint / scopes_supported.

이 두 단계는 **읽기 전용**(부작용 없음)이라 "미리보기"(POST /connectors/custom/discover)
에서 안전하게 호출한다. 반면 RFC 7591 Dynamic Client Registration(`register_dynamic_client`)
은 원격 서버에 실제 client 를 생성하는 부작용이 있으므로, 사용자가 "추가"를 확정한 시점
(service.add_custom_connector)에서만 별도로 호출한다 — 미리보기 취소 시 낭비되는 등록을
막기 위해서다.

전 구간 best-effort — 어느 단계든 실패하면 그 필드는 비운 채 계속 진행한다(발견 안 된
항목만 모달이 수동 입력받게 하려는 의도). auth_url/token_url 이 끝내 없으면 호출자가
발견 실패로 취급하고 수동 입력을 요구한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

log = logging.getLogger("rice.connector.discovery")

_CLIENT_NAME = "RICE"
_TIMEOUT = 10.0


@dataclass(frozen=True)
class DiscoveredOAuthConfig:
    """발견된(또는 못 찾은) OAuth 설정. 필드가 비어 있으면 모달이 수동 입력을 받는다."""

    auth_url: str | None
    token_url: str | None
    registration_endpoint: str | None
    scopes: tuple[str, ...]
    issuer: str | None
    resource_metadata_url: str | None

    @property
    def ok(self) -> bool:
        """authorize+token endpoint 를 둘 다 찾았으면 수동 입력 없이 등록 가능."""
        return bool(self.auth_url and self.token_url)


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


async def _fetch_json(client, url: str) -> dict | None:
    try:
        resp = await client.get(url, timeout=_TIMEOUT)
        if resp.status_code >= 400:
            return None
        return resp.json()
    except Exception as exc:  # noqa: BLE001 — 발견은 best-effort, 실패해도 계속 진행
        log.debug("discovery GET %s failed: %s", url, exc)
        return None


def _parse_resource_metadata_challenge(header: str) -> str | None:
    """`WWW-Authenticate` 헤더에서 `resource_metadata="..."` 파라미터를 뽑는다."""
    for part in header.split(","):
        part = part.strip()
        if part.lower().startswith("resource_metadata="):
            return part.split("=", 1)[1].strip().strip('"')
    return None


async def _discover_resource_metadata_url(client, mcp_endpoint: str) -> str:
    """RFC 9728 §5.1: 인증 없이 MCP 엔드포인트에 요청해 401 챌린지에서 메타데이터 URL 을
    읽는다. 챌린지가 없거나 요청 자체가 실패하면 표준 well-known 위치로 추정한다."""
    try:
        resp = await client.get(mcp_endpoint, timeout=_TIMEOUT)
        challenge = resp.headers.get("www-authenticate", "")
        found = _parse_resource_metadata_challenge(challenge)
        if found:
            return found
    except Exception as exc:  # noqa: BLE001
        log.debug("resource probe failed for %s: %s", mcp_endpoint, exc)
    return f"{_origin(mcp_endpoint)}/.well-known/oauth-protected-resource"


async def _discover_authorization_server(client, issuer: str) -> dict | None:
    """RFC 8414 §3.1: `{origin}/.well-known/oauth-authorization-server{path}`.
    실패하면 OIDC discovery(`openid-configuration`)로 한 번 더 시도한다."""
    p = urlparse(issuer)
    base = f"{p.scheme}://{p.netloc}"
    path = p.path.rstrip("/")
    meta = await _fetch_json(client, f"{base}/.well-known/oauth-authorization-server{path}")
    if meta is not None:
        return meta
    return await _fetch_json(client, f"{base}/.well-known/openid-configuration{path}")


async def discover_oauth_config(mcp_endpoint: str, *, transport=None) -> DiscoveredOAuthConfig:
    """`mcp_endpoint` 하나로 OAuth 메타데이터를 best-effort 로 알아낸다(부작용 없음).

    `transport` 는 테스트 전용 seam — `httpx.MockTransport` 를 꽂아 실제 네트워크 없이
    RFC 9728/8414 다단계 로직(401 파싱 → well-known 폴백 → issuer 스위칭)을 검증한다.
    운영 코드는 넘기지 않는다(기본 None → 실제 네트워크).
    """
    import httpx  # lazy import (connectors extra)

    async with httpx.AsyncClient(follow_redirects=True, transport=transport) as client:
        resource_metadata_url = await _discover_resource_metadata_url(client, mcp_endpoint)
        issuer = _origin(mcp_endpoint)
        resource_meta = await _fetch_json(client, resource_metadata_url)
        if resource_meta:
            servers = resource_meta.get("authorization_servers") or []
            if servers and isinstance(servers[0], str):
                issuer = servers[0]

        as_meta = await _discover_authorization_server(client, issuer)

    return DiscoveredOAuthConfig(
        auth_url=(as_meta or {}).get("authorization_endpoint"),
        token_url=(as_meta or {}).get("token_endpoint"),
        registration_endpoint=(as_meta or {}).get("registration_endpoint"),
        scopes=tuple((as_meta or {}).get("scopes_supported") or ()),
        issuer=issuer,
        resource_metadata_url=resource_metadata_url,
    )


async def register_dynamic_client(
    registration_endpoint: str, redirect_uri: str, *, transport=None
) -> tuple[str | None, str | None]:
    """RFC 7591 Dynamic Client Registration. 성공 시 (client_id, client_secret).

    client_secret 은 서버가 public client(예: PKCE-only)로 등록하면 없을 수 있다 — 이 경우
    None. 실패하면 (None, None)(호출자가 수동 client_id/secret 입력을 요구하게 된다).
    `transport` 는 discover_oauth_config 와 같은 테스트 seam.
    """
    import httpx  # lazy import (connectors extra)

    try:
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await client.post(
                registration_endpoint,
                json={
                    "client_name": _CLIENT_NAME,
                    "redirect_uris": [redirect_uri],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "client_secret_post",
                },
                timeout=_TIMEOUT,
            )
            if resp.status_code >= 300:
                log.warning("dynamic client registration rejected: %s %s", resp.status_code, resp.text[:200])
                return None, None
            data = resp.json()
            return data.get("client_id"), data.get("client_secret")
    except Exception as exc:  # noqa: BLE001
        log.warning("dynamic client registration failed: %s", exc)
        return None, None
