"""커스텀 원격 커넥터 — OAuth 자동 디스커버리(RFC 9728/8414) + 동적 등록(RFC 7591) +
CustomConnectorStore(DB+키체인) + ConnectorService.add_custom_connector 종단.

discovery.py 는 `transport=httpx.MockTransport(...)` 로 실제 네트워크 없이 다단계
프로토콜 로직(401 파싱 → well-known 폴백 → issuer 스위칭 → OIDC 폴백)을 실제 왕복으로
검증한다 — RemoteMcpClient 를 실제 Google 엔드포인트로 수동 검증한 것과 같은 철학이되,
CI 에서 반복 가능하도록 MockTransport 로 대체한 것.
"""
from __future__ import annotations

import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from rice_runtime.connector.discovery import discover_oauth_config, register_dynamic_client
from rice_runtime.connector.credential.keychain import KeychainStore
from rice_runtime.connector.custom_registry import (
    CustomConnectorStore,
    resolve_descriptor,
    resolve_supported_types,
    slugify_connector_type,
)
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database


def _fresh_db() -> Database:
    return Database(":memory:").connect()


def _fresh_paths() -> RicePaths:
    return RicePaths.from_env(tempfile.mkdtemp(prefix="rice-custom-conn-")).ensure()


def _transport(routes: dict[str, httpx.Response]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(str(request.url), httpx.Response(404))

    return httpx.MockTransport(handler)


# ── discovery: RFC 9728 (protected resource) + RFC 8414 (authorization server) ──

class TestDiscoverOAuthConfig:
    @pytest.mark.asyncio
    async def test_full_chain_via_401_challenge(self):
        """401 WWW-Authenticate → resource metadata → authorization_servers[0] → AS metadata."""
        transport = _transport({
            "https://mcp.example.com/mcp": httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": (
                        'Bearer resource_metadata='
                        '"https://mcp.example.com/.well-known/oauth-protected-resource"'
                    )
                },
            ),
            "https://mcp.example.com/.well-known/oauth-protected-resource": httpx.Response(
                200, json={"authorization_servers": ["https://auth.example.com"]}
            ),
            "https://auth.example.com/.well-known/oauth-authorization-server": httpx.Response(
                200,
                json={
                    "authorization_endpoint": "https://auth.example.com/authorize",
                    "token_endpoint": "https://auth.example.com/token",
                    "registration_endpoint": "https://auth.example.com/register",
                    "scopes_supported": ["read", "write"],
                },
            ),
        })
        cfg = await discover_oauth_config("https://mcp.example.com/mcp", transport=transport)
        assert cfg.ok is True
        assert cfg.auth_url == "https://auth.example.com/authorize"
        assert cfg.token_url == "https://auth.example.com/token"
        assert cfg.registration_endpoint == "https://auth.example.com/register"
        assert cfg.scopes == ("read", "write")
        assert cfg.issuer == "https://auth.example.com"

    @pytest.mark.asyncio
    async def test_falls_back_to_well_known_guess_and_same_origin_issuer(self):
        """WWW-Authenticate 가 없으면 well-known 경로를 추정하고, 그마저 없으면 MCP 서버
        자신의 origin 을 issuer 로 보고 AS metadata 를 바로 찾는다(같은 도메인에 호스팅되는
        흔한 배치)."""
        transport = _transport({
            "https://mcp.example.com/mcp": httpx.Response(200, json={"ok": True}),  # no challenge
            # .well-known/oauth-protected-resource 는 404(라우트에 없음) → resource_meta=None
            "https://mcp.example.com/.well-known/oauth-authorization-server": httpx.Response(
                200,
                json={
                    "authorization_endpoint": "https://mcp.example.com/authorize",
                    "token_endpoint": "https://mcp.example.com/token",
                },
            ),
        })
        cfg = await discover_oauth_config("https://mcp.example.com/mcp", transport=transport)
        assert cfg.ok is True
        assert cfg.issuer == "https://mcp.example.com"
        assert cfg.registration_endpoint is None  # 이 서버는 동적 등록 미지원

    @pytest.mark.asyncio
    async def test_oidc_discovery_fallback_when_oauth_well_known_missing(self):
        transport = _transport({
            "https://mcp.example.com/mcp": httpx.Response(200),
            "https://mcp.example.com/.well-known/openid-configuration": httpx.Response(
                200,
                json={
                    "authorization_endpoint": "https://mcp.example.com/oidc/authorize",
                    "token_endpoint": "https://mcp.example.com/oidc/token",
                },
            ),
        })
        cfg = await discover_oauth_config("https://mcp.example.com/mcp", transport=transport)
        assert cfg.ok is True
        assert cfg.auth_url == "https://mcp.example.com/oidc/authorize"

    @pytest.mark.asyncio
    async def test_total_failure_returns_not_ok(self):
        transport = _transport({})  # everything 404s
        cfg = await discover_oauth_config("https://mcp.example.com/mcp", transport=transport)
        assert cfg.ok is False
        assert cfg.auth_url is None and cfg.token_url is None


class TestRegisterDynamicClient:
    @pytest.mark.asyncio
    async def test_success_returns_client_id_and_secret(self):
        transport = _transport({
            "https://auth.example.com/register": httpx.Response(
                201, json={"client_id": "abc123", "client_secret": "s3cr3t"}
            ),
        })
        cid, csecret = await register_dynamic_client(
            "https://auth.example.com/register", "http://localhost:8080/connectors/oauth/callback",
            transport=transport,
        )
        assert cid == "abc123"
        assert csecret == "s3cr3t"

    @pytest.mark.asyncio
    async def test_public_client_has_no_secret(self):
        transport = _transport({
            "https://auth.example.com/register": httpx.Response(201, json={"client_id": "public1"}),
        })
        cid, csecret = await register_dynamic_client(
            "https://auth.example.com/register", "http://localhost:8080/connectors/oauth/callback",
            transport=transport,
        )
        assert cid == "public1"
        assert csecret is None

    @pytest.mark.asyncio
    async def test_rejected_registration_returns_none_none(self):
        transport = _transport({
            "https://auth.example.com/register": httpx.Response(400, json={"error": "invalid_client_metadata"}),
        })
        cid, csecret = await register_dynamic_client(
            "https://auth.example.com/register", "http://localhost:8080/connectors/oauth/callback",
            transport=transport,
        )
        assert cid is None and csecret is None


# ── CustomConnectorStore: DB(비민감) + 키체인(client_secret) ────────────────────

class TestCustomConnectorStore:
    def setup_method(self):
        self._db = _fresh_db()
        self._store_backing: dict[tuple, str] = {}
        mock_kr = MagicMock()
        mock_kr.set_password.side_effect = lambda s, u, p: self._store_backing.__setitem__((s, u), p)
        mock_kr.get_password.side_effect = lambda s, u: self._store_backing.get((s, u))
        mock_kr.delete_password.side_effect = lambda s, u: self._store_backing.pop((s, u), None)
        self._patcher = patch(
            "rice_runtime.connector.credential.keychain.KeychainStore._keyring", return_value=mock_kr
        )
        self._patcher.start()
        self._custom = CustomConnectorStore(self._db, KeychainStore())

    def teardown_method(self):
        self._patcher.stop()

    def test_slugify_prefixes_custom_and_strips_specials(self):
        assert slugify_connector_type("Notion MCP!!") == "custom_notion_mcp"
        assert slugify_connector_type("  ") == "custom_connector"

    def test_unique_type_avoids_static_and_existing_custom_collisions(self):
        assert self._custom.unique_type("Gmail") != "gmail"  # 정적 타입과 안 겹침(custom_ 접두)
        t1 = self._custom.unique_type("Notion")
        self._custom.add(
            connector_type=t1, display_name="Notion", auth_url="https://n/authorize",
            token_url="https://n/token", mcp_endpoint="https://n/mcp", scopes=(),
            client_id="cid", client_secret=None, registration_endpoint=None,
        )
        t2 = self._custom.unique_type("Notion")
        assert t2 != t1  # 같은 이름으로 또 추가하면 다른 슬러그

    def test_add_get_roundtrip_with_secret_in_keychain_not_db(self):
        d = self._custom.add(
            connector_type="custom_notion",
            display_name="Notion",
            auth_url="https://notion.example/authorize",
            token_url="https://notion.example/token",
            mcp_endpoint="https://notion.example/mcp",
            scopes=("read", "write"),
            client_id="cid_123",
            client_secret="sec_456",
            registration_endpoint="https://notion.example/register",
        )
        assert d.connector_type == "custom_notion"
        assert d.auth_url == "https://notion.example/authorize"
        assert d.scopes == ("read", "write")

        fetched = self._custom.get("custom_notion")
        assert fetched is not None and fetched.display_name == "Notion"
        assert self._custom.client_id_for("custom_notion") == "cid_123"
        assert self._custom.client_secret_for("custom_notion") == "sec_456"

        # client_secret 은 DB 컬럼에 없다(키체인 전용) — DB row 를 직접 봐도 안 보여야 한다.
        row = self._db.query_one(
            "SELECT * FROM custom_connector_descriptors WHERE connector_type='custom_notion'"
        )
        assert "client_secret" not in row.keys()

    def test_list_and_delete(self):
        self._custom.add(
            connector_type="custom_a", display_name="A", auth_url="https://a/authorize",
            token_url="https://a/token", mcp_endpoint="https://a/mcp", scopes=(),
            client_id="cid_a", client_secret="sec_a", registration_endpoint=None,
        )
        assert self._custom.types() == {"custom_a"}
        assert len(self._custom.list_descriptors()) == 1

        self._custom.delete("custom_a")
        assert self._custom.types() == frozenset()
        assert self._custom.get("custom_a") is None
        assert self._custom.client_secret_for("custom_a") is None  # 키체인도 정리됨

    def test_resolve_descriptor_and_supported_types_merge_static_and_custom(self):
        self._custom.add(
            connector_type="custom_b", display_name="B", auth_url="https://b/authorize",
            token_url="https://b/token", mcp_endpoint="https://b/mcp", scopes=(),
            client_id="cid_b", client_secret=None, registration_endpoint=None,
        )
        types = resolve_supported_types(self._custom)
        assert "gmail" in types and "custom_b" in types
        assert resolve_descriptor(self._custom, "gmail") is not None
        assert resolve_descriptor(self._custom, "custom_b").display_name == "B"
        assert resolve_descriptor(self._custom, "no_such_type") is None
        # custom 미주입(None) 이어도 정적 조회는 여전히 동작(manager 기존 테스트 호환).
        assert resolve_descriptor(None, "gmail") is not None
        assert resolve_supported_types(None) == resolve_supported_types(None)  # no crash


# ── ConnectorService.add_custom_connector: 등록 확정 → flows 캐시 → begin_connect ──

class TestAddCustomConnectorService:
    def setup_method(self):
        self._store_backing: dict[tuple, str] = {}
        mock_kr = MagicMock()
        mock_kr.set_password.side_effect = lambda s, u, p: self._store_backing.__setitem__((s, u), p)
        mock_kr.get_password.side_effect = lambda s, u: self._store_backing.get((s, u))
        mock_kr.delete_password.side_effect = lambda s, u: self._store_backing.pop((s, u), None)
        self._patcher = patch(
            "rice_runtime.connector.credential.keychain.KeychainStore._keyring", return_value=mock_kr
        )
        self._patcher.start()

        from rice_runtime.connector.service import ConnectorService

        self._db = _fresh_db()
        self._svc = ConnectorService(self._db, _fresh_paths(), port=8080)

    def teardown_method(self):
        self._patcher.stop()

    @pytest.mark.asyncio
    async def test_manual_client_id_skips_dynamic_registration(self):
        with patch(
            "rice_runtime.connector.service.register_dynamic_client", AsyncMock()
        ) as mock_register:
            start = await self._svc.add_custom_connector(
                display_name="Notion",
                mcp_endpoint="https://notion.example/mcp",
                auth_url="https://notion.example/authorize",
                token_url="https://notion.example/token",
                registration_endpoint="https://notion.example/register",
                client_id="manual-cid",
                client_secret="manual-secret",
            )
        mock_register.assert_not_called()  # client_id 가 이미 있으므로 동적 등록 스킵
        assert start.connector_id
        assert "notion.example/authorize" in start.oauth_url
        assert "client_id=manual-cid" in start.oauth_url

        row = self._db.query_one("SELECT * FROM connectors WHERE id=?", (start.connector_id,))
        assert row["connector_type"].startswith("custom_notion")
        assert row["display_name"] == "Notion"

    @pytest.mark.asyncio
    async def test_dynamic_registration_used_when_no_manual_client_id(self):
        with patch(
            "rice_runtime.connector.service.register_dynamic_client",
            AsyncMock(return_value=("dyn-cid", "dyn-secret")),
        ) as mock_register:
            start = await self._svc.add_custom_connector(
                display_name="Linear",
                mcp_endpoint="https://linear.example/mcp",
                auth_url="https://linear.example/authorize",
                token_url="https://linear.example/token",
                registration_endpoint="https://linear.example/register",
            )
        mock_register.assert_awaited_once()
        called_redirect_uri = mock_register.call_args.args[1]
        assert called_redirect_uri == "http://localhost:8080/connectors/oauth/callback"
        assert "client_id=dyn-cid" in start.oauth_url

    @pytest.mark.asyncio
    async def test_dynamic_registration_without_secret_still_connects(self):
        """RFC 7591 동적 등록이 client_secret 없이 client_id 만 돌려주는 public client
        (PKCE-only) 서버도 begin_connect 까지 성공해야 한다 — OAuthConfigError 로 막히면 안 됨."""
        with patch(
            "rice_runtime.connector.service.register_dynamic_client",
            AsyncMock(return_value=("dyn-cid-public", None)),
        ):
            start = await self._svc.add_custom_connector(
                display_name="Public MCP",
                mcp_endpoint="https://public.example/mcp",
                auth_url="https://public.example/authorize",
                token_url="https://public.example/token",
                registration_endpoint="https://public.example/register",
            )
        assert start.connector_id
        assert "client_id=dyn-cid-public" in start.oauth_url
        # PKCE(RFC 7636) 로 client_secret 부재를 대체 — code_challenge 가 실려 있어야 한다.
        assert "code_challenge=" in start.oauth_url and "code_challenge_method=S256" in start.oauth_url

    @pytest.mark.asyncio
    async def test_discover_custom_connector_delegates_to_discovery_module(self):
        with patch(
            "rice_runtime.connector.service.discover_oauth_config", AsyncMock()
        ) as mock_discover:
            mock_discover.return_value = MagicMock(ok=True)
            result = await self._svc.discover_custom_connector("https://x.example/mcp")
        mock_discover.assert_awaited_once_with("https://x.example/mcp")
        assert result.ok is True

    @pytest.mark.asyncio
    async def test_reconnect_reuses_flow_after_restart_via_custom_store_seed(self):
        """재시작(새 ConnectorService 인스턴스) 후에도 DB 에 남은 커스텀 디스크립터로
        begin_connect 가 다시 동작해야 한다(플로우 캐시를 __init__ 에서 재구성)."""
        with patch(
            "rice_runtime.connector.service.register_dynamic_client", AsyncMock()
        ):
            await self._svc.add_custom_connector(
                display_name="Sentry",
                mcp_endpoint="https://sentry.example/mcp",
                auth_url="https://sentry.example/authorize",
                token_url="https://sentry.example/token",
                client_id="cid-sentry",
                client_secret="secret-sentry",
            )

        from rice_runtime.connector.service import ConnectorService

        reloaded = ConnectorService(self._db, _fresh_paths(), port=8080)
        oauth_start = reloaded._manager.begin_connect("custom_sentry")
        assert "sentry.example/authorize" in oauth_start.oauth_url
