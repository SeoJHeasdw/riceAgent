"""test_connector.py — connector 모듈 수용 기준 검증 (connector.md Acceptance 1-5).

수용 기준:
  1. begin_connect("gmail") 이 state 포함 URL 과 pending 레코드를 만든다 (HTTP 모킹).
  2. complete_oauth 가 state 불일치를 거부하고, 일치 시 키체인 저장 + status=connected
     (keyring·httpx 모킹).
  3. check_plugin_permission: requires.connectors 에 gmail 있으면 True, 없으면 False.
  4. 키/토큰이 로그·DB·반환 JSON 에 노출되지 않음.
  5. walking_skeleton 정상 (별도 test_walking_skeleton.py 가 커버).

격리:
  - 실제 ~/.rice 는 건드리지 않는다 (tempfile.mkdtemp).
  - keyring, httpx 는 unittest.mock 으로 모킹한다.

NOTE(agent): 외부 계약 모킹 주의 사항 (CONVENTIONS §6):
  - keyring.get_password / set_password / delete_password 형태:
    공식 문서 https://keyring.readthedocs.io/en/latest/ 확인.
    service, username 두 위치 인자를 받고 값은 str|None.
  - Google OAuth token endpoint 응답 형태:
    https://developers.google.com/identity/protocols/oauth2/web-server#exchange-authorization-code
    에서 확인: {"access_token":..., "refresh_token":..., "expires_in":..., "scope":..., "token_type":"Bearer"}
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rice_runtime.connector.credential.keychain import KeychainStore, StoredCredential
from rice_runtime.connector.manager import ConnectorManager
from rice_runtime.connector.permission.checker import PermissionChecker
from rice_runtime.connector.service import ConnectorService
from rice_runtime.core.errors import NotFoundError, ValidationError
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.core.types import ToolCall


# ── helpers ───────────────────────────────────────────────────────────────────

def _fresh_db() -> Database:
    return Database(":memory:").connect()


def _fresh_paths() -> RicePaths:
    tmp = tempfile.mkdtemp(prefix="rice-connector-test-")
    return RicePaths.from_env(tmp).ensure()


def _service_with_keychain(db: Database, paths: RicePaths, keychain: KeychainStore) -> ConnectorService:
    """KeychainStore 를 주입한 ConnectorService 인스턴스 생성 (테스트용)."""
    svc = ConnectorService(db, paths)
    svc._keychain = keychain
    svc._manager._keychain = keychain
    return svc


# ── 수용 기준 1: begin_connect ─────────────────────────────────────────────────

class TestBeginConnect:
    def setup_method(self):
        self._db = _fresh_db()
        self._paths = _fresh_paths()
        self._keychain = KeychainStore()
        self._svc = ConnectorService(self._db, self._paths)

    def test_begin_connect_gmail_creates_pending_record(self):
        """begin_connect("gmail") 이 state 포함 URL 과 pending 레코드를 만든다 (AC-1).

        GmailOAuthFlow.build_auth_url 을 모킹해 실제 Google 호출을 피한다.
        """
        with patch(
            "rice_runtime.connector.oauth.flow.GmailOAuthFlow.build_auth_url",
            return_value=("https://accounts.google.com/o/oauth2/v2/auth?state=fake_state", "fake_state"),
        ), patch.dict("os.environ", {
            "RICE_GMAIL_CLIENT_ID": "test-client-id",
            "RICE_GMAIL_CLIENT_SECRET": "test-client-secret",
        }):
            result = self._svc._manager.begin_connect("gmail")

        assert result.state == "fake_state"
        assert "accounts.google.com" in result.oauth_url or "fake_state" in result.oauth_url
        assert result.connector_id

        # DB 에 pending 레코드 확인
        row = self._db.query_one(
            "SELECT * FROM connectors WHERE id=?", (result.connector_id,)
        )
        assert row is not None
        assert row["connector_type"] == "gmail"
        assert row["status"] == "disconnected"

    def test_begin_connect_unsupported_type_raises_validation_error(self):
        """지원하지 않는 connector_type 은 ValidationError 를 발생시킨다."""
        with pytest.raises(ValidationError):
            self._svc._manager.begin_connect("slack")

    @pytest.mark.asyncio
    async def test_begin_connect_via_service(self):
        """ConnectorService.begin_connect 도 정상 동작한다."""
        with patch(
            "rice_runtime.connector.oauth.flow.GmailOAuthFlow.build_auth_url",
            return_value=("https://accounts.google.com/o/oauth2?state=s1", "s1"),
        ), patch.dict("os.environ", {
            "RICE_GMAIL_CLIENT_ID": "cid",
            "RICE_GMAIL_CLIENT_SECRET": "csecret",
        }):
            oauth_start = await self._svc.begin_connect("gmail")

        assert oauth_start.state == "s1"
        assert oauth_start.connector_id


# ── 수용 기준 2: complete_oauth — state 검증 + 키체인 저장 + status=connected ───

class TestCompleteOAuth:
    def setup_method(self):
        self._db = _fresh_db()
        self._paths = _fresh_paths()
        # NOTE(agent): keyring 은 in-memory dict 로 모킹한다.
        # keyring API: set_password(service, username, password), get_password, delete_password
        # 형태는 https://keyring.readthedocs.io/en/latest/ 참조.
        self._fake_keychain: dict[tuple, str] = {}

        mock_keyring = MagicMock()
        mock_keyring.set_password.side_effect = (
            lambda svc, usr, pw: self._fake_keychain.__setitem__((svc, usr), pw)
        )
        mock_keyring.get_password.side_effect = (
            lambda svc, usr: self._fake_keychain.get((svc, usr))
        )
        mock_keyring.delete_password.side_effect = (
            lambda svc, usr: self._fake_keychain.pop((svc, usr), None)
        )
        self._mock_keyring = mock_keyring

    def _build_manager(self) -> ConnectorManager:
        keychain = KeychainStore()
        manager = ConnectorManager(self._db, keychain)
        with patch("rice_runtime.connector.credential.keychain.KeychainStore._keyring", return_value=self._mock_keyring):
            return manager

    @pytest.mark.asyncio
    async def test_complete_oauth_state_mismatch_raises(self):
        """state 불일치 시 ValidationError 가 발생해야 한다 (CSRF 방어, AC-2)."""
        with patch.dict("os.environ", {
            "RICE_GMAIL_CLIENT_ID": "cid",
            "RICE_GMAIL_CLIENT_SECRET": "csecret",
        }):
            svc = ConnectorService(self._db, self._paths)
            # pending_states 에 등록 없이 complete_oauth 호출 → state mismatch
            with pytest.raises(ValidationError, match="state"):
                await svc.complete_oauth(code="auth_code", state="wrong_state")

    @pytest.mark.asyncio
    async def test_complete_oauth_saves_to_keychain_and_marks_connected(self):
        """올바른 state 로 complete_oauth 시 키체인 저장 + status=connected (AC-2).

        NOTE(agent): Google 토큰 응답 형태 (검증된 스키마):
          {"access_token": str, "refresh_token": str, "expires_in": int,
           "scope": str, "token_type": "Bearer"}
        출처: https://developers.google.com/identity/protocols/oauth2/web-server
        """
        mock_token_resp = {
            "access_token": "ya29.test_access_token",
            "refresh_token": "1//test_refresh_token",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send",
            "token_type": "Bearer",
        }

        with patch.dict("os.environ", {
            "RICE_GMAIL_CLIENT_ID": "cid",
            "RICE_GMAIL_CLIENT_SECRET": "csecret",
        }):
            svc = ConnectorService(self._db, self._paths)

            # begin_connect 로 pending 레코드 + state 등록
            with patch(
                "rice_runtime.connector.oauth.flow.GmailOAuthFlow.build_auth_url",
                return_value=("https://accounts.google.com/o/oauth2?state=valid_state", "valid_state"),
            ):
                oauth_start = await svc.begin_connect("gmail")

            assert oauth_start.state == "valid_state"

            # httpx POST 를 모킹 — Google 토큰 엔드포인트 응답
            mock_response = MagicMock()
            mock_response.json.return_value = mock_token_resp
            mock_response.raise_for_status.return_value = None

            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            with patch("rice_runtime.connector.credential.keychain.KeychainStore._keyring", return_value=self._mock_keyring), \
                 patch("rice_runtime.connector.oauth.flow.GmailOAuthFlow._require_credentials"), \
                 patch("httpx.AsyncClient", return_value=mock_client):
                info = await svc.complete_oauth(code="valid_code", state="valid_state")

        # DB 상태 확인
        assert info.status == "connected"
        assert info.id == oauth_start.connector_id
        assert info.connected_at is not None

        # 키체인에 저장됐는지 확인 (AC-4: DB 에는 토큰이 없어야 함)
        access_key = ("rice", f"rice.connector.{oauth_start.connector_id}.access_token")
        assert access_key in self._fake_keychain
        stored_access = self._fake_keychain[access_key]
        assert stored_access == "ya29.test_access_token"

        # DB 에 토큰 본문이 없는지 확인 (AC-4)
        cred_row = self._db.query_one(
            "SELECT * FROM connector_credentials WHERE connector_id=?",
            (oauth_start.connector_id,),
        )
        assert cred_row is not None
        # token_expires_at 과 scopes 만 있어야 함 — 토큰 본문 절대 없음
        assert "ya29.test_access_token" not in str(dict(cred_row))
        assert "1//test_refresh_token" not in str(dict(cred_row))


# ── 수용 기준 3: check_plugin_permission ─────────────────────────────────────

class TestCheckPluginPermission:
    """check_permission 은 순수 로직 — manifest 파일 읽기만 의존한다."""

    def setup_method(self):
        self._paths = _fresh_paths()
        self._checker = PermissionChecker(self._paths.plugins)

    def _create_plugin_manifest(self, plugin_id: str, connectors: list[str]) -> None:
        """테스트용 plugin manifest 를 임시 디렉토리에 생성한다."""
        plugin_dir = self._paths.plugins / plugin_id
        plugin_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 1,
            "type": "plugin",
            "name": plugin_id,
            "version": "1.0.0",
            "requires": {
                "connectors": connectors,
            },
        }
        import yaml
        (plugin_dir / "rice-artifact.yaml").write_text(
            yaml.safe_dump(manifest), encoding="utf-8"
        )

    def test_plugin_with_gmail_permission_returns_true(self):
        """requires.connectors = ["gmail"] 인 plugin 은 gmail 접근 권한 있음 (AC-3)."""
        self._create_plugin_manifest("my-plugin", ["gmail"])
        assert self._checker.check("my-plugin", "gmail") is True

    def test_plugin_without_gmail_permission_returns_false(self):
        """requires.connectors 에 gmail 없으면 False (AC-3)."""
        self._create_plugin_manifest("limited-plugin", ["google_drive"])
        assert self._checker.check("limited-plugin", "gmail") is False

    def test_plugin_with_empty_connectors_returns_false(self):
        """requires.connectors 가 빈 목록이면 False."""
        self._create_plugin_manifest("no-connector-plugin", [])
        assert self._checker.check("no-connector-plugin", "gmail") is False

    def test_missing_plugin_manifest_returns_false(self):
        """manifest 파일이 없으면 fail-closed(False) (CONVENTIONS §7)."""
        assert self._checker.check("nonexistent-plugin", "gmail") is False

    def test_null_plugins_root_returns_false(self):
        """plugins_root=None 이면 fail-closed(False)."""
        checker = PermissionChecker(None)
        assert checker.check("any-plugin", "gmail") is False

    def test_check_permission_via_service_with_manifest(self):
        """ConnectorService.check_permission 도 정상 동작한다."""
        db = _fresh_db()
        self._create_plugin_manifest("svc-plugin", ["gmail"])
        svc = ConnectorService(db, self._paths)
        assert svc.check_permission("svc-plugin", "gmail") is True

    def test_check_permission_via_service_without_permission(self):
        db = _fresh_db()
        self._create_plugin_manifest("restricted-plugin", ["slack"])
        svc = ConnectorService(db, self._paths)
        assert svc.check_permission("restricted-plugin", "gmail") is False


# ── 수용 기준 4: 토큰 비노출 확인 ───────────────────────────────────────────────

class TestTokenNotExposed:
    """토큰이 DB·로그·반환 JSON 에 노출되지 않음 (AC-4)."""

    def setup_method(self):
        self._db = _fresh_db()
        self._paths = _fresh_paths()

    @pytest.mark.asyncio
    async def test_connectorinfo_does_not_contain_tokens(self):
        """ConnectorInfo DTO 에 access_token/refresh_token 필드가 없어야 한다."""
        import dataclasses
        info_fields = {f.name for f in dataclasses.fields(
            __import__("rice_runtime.connector.interfaces", fromlist=["ConnectorInfo"]).ConnectorInfo
        )}
        # 토큰 관련 필드가 없어야 함
        assert "access_token" not in info_fields
        assert "refresh_token" not in info_fields

    def test_db_connector_credentials_has_no_token_columns(self):
        """connector_credentials 테이블에 토큰 본문 컬럼이 없어야 한다 (AC-4)."""
        # DB 스키마에서 connector_credentials 컬럼 확인
        rows = self._db.query_all(
            "PRAGMA table_info(connector_credentials)"
        )
        column_names = {row["name"] for row in rows}
        # 허용: connector_id, token_expires_at, scopes
        assert "access_token" not in column_names
        assert "refresh_token" not in column_names

    def test_list_connectors_returns_no_token_data(self):
        """list_connectors 반환값에 토큰 데이터가 없어야 한다."""
        # connected connector 직접 삽입 (토큰은 키체인에 있다고 가정)
        self._db.execute(
            "INSERT INTO connectors(id, connector_type, display_name, status, connected_at) "
            "VALUES ('conn_test', 'gmail', 'Gmail', 'connected', '2024-01-01T00:00:00Z')"
        )
        self._db.execute(
            "INSERT INTO connector_credentials(connector_id, token_expires_at, scopes) "
            "VALUES ('conn_test', '2024-12-31T00:00:00Z', '[\"https://www.googleapis.com/auth/gmail.readonly\"]')"
        )
        svc = ConnectorService(self._db, self._paths)
        connectors = svc.list_connectors()
        assert len(connectors) == 1
        info = connectors[0]

        # dict/str 변환에 토큰 흔적 없음
        info_str = str(info)
        assert "access_token" not in info_str
        assert "refresh_token" not in info_str
        # scopes 는 노출됨 (메타데이터 — 토큰 아님)
        assert "gmail.readonly" in info_str or info.scopes


# ── 수용 기준 5: list_tools / invoke 기본 동작 ───────────────────────────────

class TestToolSurface:
    def setup_method(self):
        self._db = _fresh_db()
        self._paths = _fresh_paths()
        self._svc = ConnectorService(self._db, self._paths)

    def test_list_tools_empty_when_no_connected_connector(self):
        """연결된 connector 가 없으면 빈 목록."""
        assert self._svc.list_tools() == []

    def test_list_tools_returns_gmail_tools_when_connected(self):
        """connected Gmail connector 가 있으면 Gmail 도구 목록 반환."""
        self._db.execute(
            "INSERT INTO connectors(id, connector_type, display_name, status) "
            "VALUES ('conn_1', 'gmail', 'Gmail', 'connected')"
        )
        tools = self._svc.list_tools()
        assert len(tools) > 0
        assert all(t.source == "connector" for t in tools)
        assert all(t.requires_approval is True for t in tools)
        names = {t.name for t in tools}
        assert "gmail_search" in names
        assert "gmail_read" in names

    @pytest.mark.asyncio
    async def test_invoke_returns_error_when_no_connector(self):
        """연결된 connector 가 없으면 ok=False ToolResult 반환 (예외 아님)."""
        call = ToolCall(tool_name="gmail_search", arguments={"query": "test"})
        result = await self._svc.invoke(call)
        assert result.ok is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_invoke_returns_error_when_no_credential(self):
        """connector 는 있지만 키체인에 credential 이 없으면 ok=False."""
        self._db.execute(
            "INSERT INTO connectors(id, connector_type, display_name, status) "
            "VALUES ('conn_2', 'gmail', 'Gmail', 'connected')"
        )
        # keyring 이 None 반환하도록 모킹
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = None
        with patch("rice_runtime.connector.credential.keychain.KeychainStore._keyring", return_value=mock_kr):
            call = ToolCall(tool_name="gmail_search", arguments={"query": "test"})
            result = await self._svc.invoke(call)
        assert result.ok is False


# ── 키체인 단위 테스트 ─────────────────────────────────────────────────────────

class TestKeychainStore:
    """KeychainStore 단위 테스트 (keyring 모킹)."""

    def setup_method(self):
        self._store: dict[tuple, str] = {}
        mock_kr = MagicMock()
        mock_kr.set_password.side_effect = (
            lambda s, u, p: self._store.__setitem__((s, u), p)
        )
        mock_kr.get_password.side_effect = (
            lambda s, u: self._store.get((s, u))
        )
        mock_kr.delete_password.side_effect = (
            lambda s, u: self._store.pop((s, u), None)
        )
        self._mock_kr = mock_kr
        self._keychain = KeychainStore()

    def test_save_and_load_roundtrip(self):
        """save 후 load 로 동일 credential 복원."""
        with patch("rice_runtime.connector.credential.keychain.KeychainStore._keyring", return_value=self._mock_kr):
            self._keychain.save(
                "conn_abc",
                access_token="access_xyz",
                refresh_token="refresh_xyz",
                expires_in=3600,
            )
            cred = self._keychain.load("conn_abc")

        assert cred is not None
        assert cred.access_token == "access_xyz"
        assert cred.refresh_token == "refresh_xyz"
        assert cred.expires_at is not None

    def test_load_missing_returns_none(self):
        """키체인에 없으면 None 반환."""
        with patch("rice_runtime.connector.credential.keychain.KeychainStore._keyring", return_value=self._mock_kr):
            assert self._keychain.load("no_such_conn") is None

    def test_delete_removes_all_credentials(self):
        """delete 후 load 시 None 반환."""
        with patch("rice_runtime.connector.credential.keychain.KeychainStore._keyring", return_value=self._mock_kr):
            self._keychain.save(
                "conn_del",
                access_token="tok",
                refresh_token="ref",
                expires_in=3600,
            )
            self._keychain.delete("conn_del")
            cred = self._keychain.load("conn_del")
        assert cred is None

    def test_is_expired_when_past_due(self):
        """만료 timestamp 가 과거면 is_expired() == True."""
        import time
        cred = StoredCredential(
            access_token="tok",
            refresh_token=None,
            expires_at=time.time() - 100,  # 100초 전 만료
        )
        assert cred.is_expired() is True

    def test_is_expired_false_when_not_expired(self):
        """만료 timestamp 가 충분히 미래이면 is_expired() == False."""
        import time
        cred = StoredCredential(
            access_token="tok",
            refresh_token=None,
            expires_at=time.time() + 7200,  # 2시간 후
        )
        assert cred.is_expired() is False
