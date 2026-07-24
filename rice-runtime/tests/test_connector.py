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
from rice_runtime.core.types import ToolCall, ToolResult


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
            "rice_runtime.connector.oauth.flow.OAuthFlow.build_auth_url",
            return_value=(
                "https://accounts.google.com/o/oauth2/v2/auth?state=fake_state",
                "fake_state",
                "fake_verifier",
            ),
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
            "rice_runtime.connector.oauth.flow.OAuthFlow.build_auth_url",
            return_value=("https://accounts.google.com/o/oauth2?state=s1", "s1", "verifier1"),
        ), patch.dict("os.environ", {
            "RICE_GMAIL_CLIENT_ID": "cid",
            "RICE_GMAIL_CLIENT_SECRET": "csecret",
        }):
            oauth_start = await self._svc.begin_connect("gmail")

        assert oauth_start.state == "s1"
        assert oauth_start.connector_id

    def test_reconnect_clears_previous_terminal_error_while_oauth_is_pending(self):
        """error 행 재사용 시 이전 실패가 새 OAuth polling의 terminal 결과로 보이면 안 된다."""
        self._db.execute(
            "INSERT INTO connectors(id, connector_type, display_name, status, error_code, error_detail) "
            "VALUES ('c1', 'gmail', 'Gmail', 'error', 'old.code', 'old failure')"
        )
        with patch(
            "rice_runtime.connector.oauth.flow.OAuthFlow.build_auth_url",
            return_value=("https://auth?state=s2", "s2", "v2"),
        ):
            result = self._svc._manager.begin_connect("gmail")
        row = self._db.query_one("SELECT status, error_code, error_detail FROM connectors WHERE id='c1'")
        assert result.connector_id == "c1"
        assert row is not None and row["status"] == "disconnected"
        assert row["error_code"] is None and row["error_detail"] is None

    def test_new_attempt_invalidates_the_previous_pending_state(self):
        """같은 connector를 다시 열면 닫힌 과거 브라우저의 state는 더 이상 유효하지 않다."""
        with patch(
            "rice_runtime.connector.oauth.flow.OAuthFlow.build_auth_url",
            side_effect=[
                ("https://auth?state=old", "old", "v-old"),
                ("https://auth?state=new", "new", "v-new"),
            ],
        ):
            first = self._svc._manager.begin_connect("gmail")
            second = self._svc._manager.begin_connect("gmail")

        rows = self._db.query_all(
            "SELECT state, connector_id FROM oauth_pending_states ORDER BY state"
        )
        assert first.connector_id == second.connector_id
        assert [(row["state"], row["connector_id"]) for row in rows] == [
            ("new", second.connector_id)
        ]

    @pytest.mark.asyncio
    async def test_manual_cancel_removes_pending_state_immediately(self):
        with patch(
            "rice_runtime.connector.oauth.flow.OAuthFlow.build_auth_url",
            return_value=("https://auth?state=cancel", "cancel", "v-cancel"),
        ):
            start = await self._svc.begin_connect("gmail")

        assert await self._svc.cancel_oauth(start.connector_id) is True
        assert await self._svc.cancel_oauth(start.connector_id) is False
        assert self._db.query_one(
            "SELECT state FROM oauth_pending_states WHERE connector_id=?",
            (start.connector_id,),
        ) is None


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
                "rice_runtime.connector.oauth.flow.OAuthFlow.build_auth_url",
                return_value=(
                    "https://accounts.google.com/o/oauth2?state=valid_state",
                    "valid_state",
                    "valid_verifier",
                ),
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

            # OAuth token exchange와 원격 MCP 검증 사이에는 connected가 아니라
            # verifying이어야 한다. GET /connectors polling이 도구 없는 connected를
            # 순간적으로 성공으로 오인했던 회귀를 잡는다.
            observed_during_sync: list[str] = []
            original_sync_tools = svc.sync_tools

            async def sync_while_observing(connector_id: str) -> int:
                observed_during_sync.append(svc.status(connector_id))
                return await original_sync_tools(connector_id)

            with patch("rice_runtime.connector.credential.keychain.KeychainStore._keyring", return_value=self._mock_keyring), \
                 patch("rice_runtime.connector.oauth.flow.OAuthFlow._require_client_id"), \
                 patch.object(svc, "sync_tools", side_effect=sync_while_observing), \
                 patch("httpx.AsyncClient", return_value=mock_client):
                info = await svc.complete_oauth(code="valid_code", state="valid_state")

        # DB 상태 확인
        assert info.status == "connected"
        assert info.id == oauth_start.connector_id
        assert info.connected_at is not None
        assert observed_during_sync == ["verifying"]

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

    @pytest.mark.asyncio
    async def test_complete_oauth_survives_runtime_restart(self):
        """begin_connect 와 브라우저 콜백 사이에 런타임이 재시작해도(새 서비스 인스턴스)
        pending state 가 DB(oauth_pending_states)에 있어 로그인이 이어진다.
        같은 state 는 one-shot 으로 소비돼 두 번째 교환(replay)은 거부된다."""
        mock_token_resp = {
            "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
            "scope": "s", "token_type": "Bearer",
        }
        with patch.dict("os.environ", {
            "RICE_GMAIL_CLIENT_ID": "cid",
            "RICE_GMAIL_CLIENT_SECRET": "csecret",
        }):
            svc1 = ConnectorService(self._db, self._paths)
            with patch(
                "rice_runtime.connector.oauth.flow.OAuthFlow.build_auth_url",
                return_value=("https://auth?state=restart_state", "restart_state", "restart_verifier"),
            ):
                oauth_start = await svc1.begin_connect("gmail")

            # ── 재시작 시뮬레이션: 같은 DB 로 새 인스턴스(인메모리 상태 전부 소실) ──
            svc2 = ConnectorService(self._db, self._paths)

            mock_response = MagicMock()
            mock_response.json.return_value = mock_token_resp
            mock_response.raise_for_status.return_value = None
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            with patch("rice_runtime.connector.credential.keychain.KeychainStore._keyring", return_value=self._mock_keyring), \
                 patch("httpx.AsyncClient", return_value=mock_client):
                info = await svc2.complete_oauth(code="valid_code", state="restart_state")
                assert info.status == "connected"
                assert info.id == oauth_start.connector_id
                # one-shot: 같은 state 재사용은 CSRF/replay 로 거부.
                with pytest.raises(ValidationError, match="state"):
                    await svc2.complete_oauth(code="valid_code", state="restart_state")

    @pytest.mark.asyncio
    async def test_complete_oauth_expired_state_rejected(self):
        """15분 넘게 방치된 pending state 는 만료로 거부된다."""
        with patch.dict("os.environ", {
            "RICE_GMAIL_CLIENT_ID": "cid",
            "RICE_GMAIL_CLIENT_SECRET": "csecret",
        }):
            svc = ConnectorService(self._db, self._paths)
            self._db.execute(
                "INSERT INTO oauth_pending_states(state, connector_id, code_verifier, created_at) "
                "VALUES ('old_state', 'conn_x', 'v', datetime('now','-16 minutes'))"
            )
            with pytest.raises(ValidationError, match="state"):
                await svc.complete_oauth(code="c", state="old_state")

    @pytest.mark.asyncio
    async def test_callback_returns_http_200_when_oauth_succeeds_but_mcp_probe_fails(self):
        """후속 MCP/Cloud 검증 실패를 OAuth 프로토콜 실패(HTTP 400)로 기록하지 않는다."""
        from rice_runtime.connector.interfaces import ConnectorInfo
        from rice_runtime.server.routes.connectors import oauth_callback

        info = ConnectorInfo(
            id="gmail_1",
            connector_type="gmail",
            display_name="Gmail",
            status="error",
            error_code="connectors.mcp_service_permission_denied",
            error_detail="Enable gmailmcp.googleapis.com and verify again.",
        )
        container = MagicMock()
        container.connectors.complete_oauth = AsyncMock(return_value=info)

        response = await oauth_callback(code="valid", state="valid", c=container)

        assert response.status_code == 200
        body = response.body.decode()
        assert "Gmail 연결 확인 필요" in body
        # 브라우저 페이지는 비개발자용 — 영문 원문 진단(IAM/서비스명)을 노출하지 않는다.
        assert "gmailmcp.googleapis.com" not in body
        assert "도구 사용 준비" in body

    @pytest.mark.asyncio
    async def test_provider_access_denied_callback_ends_polling_as_cancelled(self):
        """Google 취소 버튼의 error callback을 DB terminal 상태로 남긴다."""
        from rice_runtime.server.routes.connectors import oauth_callback

        with patch(
            "rice_runtime.connector.oauth.flow.OAuthFlow.build_auth_url",
            return_value=("https://auth?state=denied", "denied", "v-denied"),
        ):
            svc = ConnectorService(self._db, self._paths)
            start = await svc.begin_connect("gmail")

        container = MagicMock()
        container.connectors = svc
        response = await oauth_callback(
            state="denied", error="access_denied", c=container
        )

        assert response.status_code == 200
        info = svc.get_connector(start.connector_id)
        assert info is not None and info.status == "error"
        assert info.error_code == "connectors.oauth_cancelled"
        assert self._db.query_one(
            "SELECT state FROM oauth_pending_states WHERE state='denied'"
        ) is None


# ── refresh token rotation — 갱신 응답의 새 refresh_token 채택 ─────────────────

class TestRefreshTokenRotation:
    """rotation 을 강제하는 서버(OAuth 2.0 Security BCP)는 갱신 응답에 새 refresh_token 을
    내려주고 이전 것을 무효화한다 — 키체인의 것도 함께 교체돼야 다음 갱신이 성공한다."""

    @pytest.mark.asyncio
    async def test_rotated_refresh_token_is_persisted(self):
        from rice_runtime.connector.oauth.flow import TokenResponse

        db = _fresh_db()
        paths = _fresh_paths()
        db.execute(
            "INSERT INTO connectors(id, connector_type, display_name, status) "
            "VALUES ('c1','gmail','Gmail','connected')"
        )
        backing: dict[tuple, str] = {}
        mock_kr = MagicMock()
        mock_kr.set_password.side_effect = lambda s, u, p: backing.__setitem__((s, u), p)
        mock_kr.get_password.side_effect = lambda s, u: backing.get((s, u))
        mock_kr.delete_password.side_effect = lambda s, u: backing.pop((s, u), None)

        with patch("rice_runtime.connector.credential.keychain.KeychainStore._keyring", return_value=mock_kr):
            # 만료된 access_token + 기존 refresh_token 을 심는다 → 갱신 경로 강제.
            backing[("rice", "rice.connector.c1.access_token")] = "old_at"
            backing[("rice", "rice.connector.c1.refresh_token")] = "old_rt"
            backing[("rice", "rice.connector.c1.expires_at")] = "1.0"  # 과거 → 만료

            svc = ConnectorService(db, paths)
            rotated = TokenResponse(
                access_token="new_at", refresh_token="new_rt",
                expires_in=3600, scope="", token_type="Bearer",
            )
            with patch(
                "rice_runtime.connector.oauth.flow.OAuthFlow.refresh_access_token",
                AsyncMock(return_value=rotated),
            ):
                token = await svc._access_token("c1", "gmail")

        assert token == "new_at"
        assert backing[("rice", "rice.connector.c1.access_token")] == "new_at"
        # 핵심: 이전 refresh_token 이 새것으로 교체됐다(이전 것은 서버가 무효화했을 수 있음).
        assert backing[("rice", "rice.connector.c1.refresh_token")] == "new_rt"

    @pytest.mark.asyncio
    async def test_non_rotating_server_keeps_existing_refresh_token(self):
        """Google 처럼 rotation 을 안 하는 서버(응답에 refresh_token 없음)는 flow 가 기존
        것을 그대로 돌려주고, 키체인의 refresh_token 도 유지된다."""
        from rice_runtime.connector.oauth.flow import TokenResponse

        db = _fresh_db()
        paths = _fresh_paths()
        db.execute(
            "INSERT INTO connectors(id, connector_type, display_name, status) "
            "VALUES ('c1','gmail','Gmail','connected')"
        )
        backing: dict[tuple, str] = {}
        mock_kr = MagicMock()
        mock_kr.set_password.side_effect = lambda s, u, p: backing.__setitem__((s, u), p)
        mock_kr.get_password.side_effect = lambda s, u: backing.get((s, u))
        mock_kr.delete_password.side_effect = lambda s, u: backing.pop((s, u), None)

        with patch("rice_runtime.connector.credential.keychain.KeychainStore._keyring", return_value=mock_kr):
            backing[("rice", "rice.connector.c1.access_token")] = "old_at"
            backing[("rice", "rice.connector.c1.refresh_token")] = "old_rt"
            backing[("rice", "rice.connector.c1.expires_at")] = "1.0"

            svc = ConnectorService(db, paths)
            # flow.refresh_access_token 은 응답에 refresh_token 이 없으면 기존 것을 채워 돌려준다.
            same = TokenResponse(
                access_token="new_at", refresh_token="old_rt",
                expires_in=3600, scope="", token_type="Bearer",
            )
            with patch(
                "rice_runtime.connector.oauth.flow.OAuthFlow.refresh_access_token",
                AsyncMock(return_value=same),
            ):
                token = await svc._access_token("c1", "gmail")

        assert token == "new_at"
        assert backing[("rice", "rice.connector.c1.refresh_token")] == "old_rt"

    @pytest.mark.asyncio
    async def test_flow_adopts_rotated_refresh_token_from_response(self):
        """flow.refresh_access_token 이 응답에 새 refresh_token 이 있으면 그걸 채택하고,
        없으면(비-rotation) 기존 것을 재사용한다."""
        from rice_runtime.connector.oauth.flow import GmailOAuthFlow

        flow = GmailOAuthFlow(client_id="cid", client_secret="cs", redirect_uri="http://localhost:9/cb")

        def _resp(payload: dict) -> MagicMock:
            r = MagicMock()
            r.json.return_value = payload
            r.raise_for_status.return_value = None
            return r

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        # rotation 서버: 응답에 새 refresh_token 동봉 → 채택.
        mock_client.post = AsyncMock(
            return_value=_resp({"access_token": "at2", "refresh_token": "rt2", "expires_in": 60})
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            tok = await flow.refresh_access_token("rt1")
        assert tok.refresh_token == "rt2"

        # 비-rotation 서버: 응답에 없음 → 기존 재사용.
        mock_client.post = AsyncMock(return_value=_resp({"access_token": "at3", "expires_in": 60}))
        with patch("httpx.AsyncClient", return_value=mock_client):
            tok = await flow.refresh_access_token("rt1")
        assert tok.refresh_token == "rt1"


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

    def test_list_tools_empty_before_sync_even_when_connected(self):
        """connected 여도 sync_tools() 전(카탈로그 미저장)에는 [] 를 반환한다.

        도구는 하드코딩이 아니라 원격 MCP tools/list 로 연결 시점에 DB 에 저장된다.
        """
        self._db.execute(
            "INSERT INTO connectors(id, connector_type, display_name, status) "
            "VALUES ('conn_1', 'gmail', 'Gmail', 'connected')"
        )
        assert self._svc.list_tools() == []

    @pytest.mark.asyncio
    async def test_sync_tools_persists_remote_catalog_and_list_reads_it(self):
        """sync_tools() 가 원격 MCP tools/list 결과를 DB 에 저장하고, list_tools() 는
        네트워크 없이 그 DB 값을 읽는다. 도구는 source=connector, requires_approval=True."""
        from rice_runtime.core.types import ToolSpec

        self._db.execute(
            "INSERT INTO connectors(id, connector_type, display_name, status) "
            "VALUES ('conn_1', 'gmail', 'Gmail', 'connected')"
        )
        remote_specs = [
            ToolSpec(name="search_threads", description="search", parameters={"type": "object"},
                     source="connector", owner_id="conn_1", requires_approval=True),
            ToolSpec(name="create_draft", description="draft", parameters={"type": "object"},
                     source="connector", owner_id="conn_1", requires_approval=True),
        ]
        # 원격 tools/list 결과를 seam 에서 바로 주입(토큰·MCP 클라이언트 우회).
        with patch.object(self._svc, "_fetch_remote_tools", AsyncMock(return_value=remote_specs)):
            n = await self._svc.sync_tools("conn_1")
        assert n == 2

        # list_tools() 는 이제 DB 만 읽는다 — 원격을 다시 호출하지 않아야 한다.
        with patch.object(self._svc, "_fetch_remote_tools", AsyncMock(side_effect=AssertionError)):
            tools = self._svc.list_tools()
        names = {t.name for t in tools}
        assert names == {"search_threads", "create_draft"}
        assert all(t.source == "connector" for t in tools)
        assert all(t.requires_approval is True for t in tools)

    @pytest.mark.asyncio
    async def test_check_updates_flags_pending_and_apply_promotes(self):
        """check_updates() 가 원격 변경을 pending 으로 표시하고, apply_update() 가 승격한다."""
        from rice_runtime.core.types import ToolSpec

        self._db.execute(
            "INSERT INTO connectors(id, connector_type, display_name, status) "
            "VALUES ('conn_1', 'gmail', 'Gmail', 'connected')"
        )
        v1 = [ToolSpec(name="search_threads", description="", parameters={}, source="connector",
                       owner_id="conn_1", requires_approval=True)]
        v2 = v1 + [ToolSpec(name="create_draft", description="", parameters={}, source="connector",
                            owner_id="conn_1", requires_approval=True)]
        with patch.object(self._svc, "_fetch_remote_tools", AsyncMock(return_value=v1)):
            await self._svc.sync_tools("conn_1")
        # 원격이 v2 로 늘어남 → check_updates 가 added 감지 + pending 표시(적용 X)
        with patch.object(self._svc, "_fetch_remote_tools", AsyncMock(return_value=v2)):
            result = await self._svc.check_updates("conn_1")
        assert result["changed"] is True and result["added"] == ["create_draft"]
        assert {t.name for t in self._svc.list_tools()} == {"search_threads"}  # 아직 미적용
        pu = self._svc.pending_update("conn_1")
        assert pu is not None and pu["added"] == ["create_draft"]
        # 적용 → 활성이 v2 로 승격, pending 소거
        await self._svc.apply_update("conn_1")
        assert {t.name for t in self._svc.list_tools()} == {"search_threads", "create_draft"}
        assert self._svc.pending_update("conn_1") is None

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

    @pytest.mark.asyncio
    async def test_verify_detects_google_mcp_execution_permission_failure(self):
        """tools/list 성공과 무관하게 read-only probe 거부를 error 상태+복구 안내로 남긴다."""
        self._db.execute(
            "INSERT INTO connectors(id, connector_type, display_name, status) "
            "VALUES ('calendar_1', 'calendar', 'Google Calendar', 'connected')"
        )
        client = MagicMock()
        client.call_tool = AsyncMock(return_value=ToolResult(
            "health", ok=False,
            error='[{"text":"The caller does not have permission","type":"text"}]',
        ))
        with patch.object(self._svc, "_access_token", AsyncMock(return_value="valid-token")), \
             patch.object(self._svc, "_client_for", return_value=client):
            result = await self._svc.verify_connector("calendar_1")

        assert result["ok"] is False
        assert result["code"] == "connectors.mcp_service_permission_denied"
        assert "calendarmcp.googleapis.com" in result["error"]
        info = self._svc.get_connector("calendar_1")
        assert info is not None and info.status == "error"
        assert info.error_code == "connectors.mcp_service_permission_denied"
        assert info.setup_url and "configure-mcp-server" in info.setup_url
        client.call_tool.assert_awaited_once_with(
            "list_calendars", {}, "valid-token", "health-calendar_1"
        )

    @pytest.mark.asyncio
    async def test_verify_success_clears_persisted_mcp_error(self):
        """Cloud service를 활성화한 뒤 재검증하면 OAuth 재연결 없이 connected로 복구한다."""
        self._db.execute(
            "INSERT INTO connectors(id, connector_type, display_name, status, error_code, error_detail) "
            "VALUES ('gmail_1', 'gmail', 'Gmail', 'error', "
            "'connectors.mcp_service_permission_denied', 'enable service')"
        )
        client = MagicMock()
        client.call_tool = AsyncMock(return_value=ToolResult("health", ok=True, result={"labels": []}))
        with patch.object(self._svc, "_access_token", AsyncMock(return_value="valid-token")), \
             patch.object(self._svc, "_client_for", return_value=client):
            result = await self._svc.verify_connector("gmail_1")

        assert result == {"ok": True}
        info = self._svc.get_connector("gmail_1")
        assert info is not None and info.status == "connected"
        assert info.error_code is None and info.error_detail is None

    @pytest.mark.asyncio
    async def test_verify_classifies_http_error_body_from_transport(self):
        """HTTP 403 본문(PERMISSION_DENIED)이 예외로 유실되지 않고 진단 코드로 분류된다."""
        self._db.execute(
            "INSERT INTO connectors(id, connector_type, display_name, status) "
            "VALUES ('gmail_1', 'gmail', 'Gmail', 'connected')"
        )
        client = MagicMock()
        client.call_tool = AsyncMock(side_effect=RuntimeError(
            "MCP server returned HTTP 403 for tools/call: "
            '{"error":{"code":403,"message":"The caller does not have permission",'
            '"status":"PERMISSION_DENIED"}}'
        ))
        with patch.object(self._svc, "_access_token", AsyncMock(return_value="valid-token")), \
             patch.object(self._svc, "_client_for", return_value=client):
            result = await self._svc.verify_connector("gmail_1")

        assert result["ok"] is False
        assert result["code"] == "connectors.mcp_service_permission_denied"
        info = self._svc.get_connector("gmail_1")
        assert info is not None and info.status == "error"

    def test_diagnose_classifies_service_disabled_and_scope_errors(self):
        """SERVICE_DISABLED/scope 오류는 permission_denied 와 다른 복구 안내를 받는다."""
        code, detail = self._svc._diagnose_mcp_failure(
            "gmail",
            "Gmail MCP API has not been used in project 1082091413989 before or it is disabled.",
        )
        assert code == "connectors.mcp_service_disabled"
        assert "gmailmcp.googleapis.com" in detail

        code, detail = self._svc._diagnose_mcp_failure(
            "gmail", "Request had insufficient authentication scopes."
        )
        assert code == "connectors.mcp_scope_insufficient"
        assert "Reconnect" in detail

        # 분류 불가 오류는 원문 그대로 — 억지 분류로 잘못된 복구 안내를 내지 않는다.
        code, detail = self._svc._diagnose_mcp_failure("gmail", "socket hang up")
        assert code is None and detail == "socket hang up"

    def test_client_for_google_type_sends_quota_project_header(self):
        """Google MCP tools/call 요건인 x-goog-user-project 를 client_id 에서 파생해 붙인다."""
        with patch.dict("os.environ", {
            "RICE_GMAIL_CLIENT_ID": "1082091413989-abc.apps.googleusercontent.com",
            "RICE_GMAIL_CLIENT_SECRET": "csecret",
        }):
            svc = ConnectorService(self._db, self._paths)
            client = svc._client_for("conn_1", "gmail")
        assert client._extra_headers.get("x-goog-user-project") == "1082091413989"

    def test_client_for_non_google_type_omits_quota_project_header(self):
        """Google 프로젝트 개념이 없는 client_id 면 헤더를 붙이지 않는다(오탐 방지)."""
        with patch.dict("os.environ", {
            "RICE_GMAIL_CLIENT_ID": "plain-client-id",
            "RICE_GMAIL_CLIENT_SECRET": "csecret",
        }):
            svc = ConnectorService(self._db, self._paths)
            client = svc._client_for("conn_1", "gmail")
        assert "x-goog-user-project" not in client._extra_headers


# ── 원격 MCP HTTP 오류 본문 보존 ────────────────────────────────────────────────

class TestRemoteMcpHttpErrors:
    @pytest.mark.asyncio
    async def test_http_4xx_body_is_preserved_in_raised_error(self):
        """상태줄('403 Forbidden')만 남던 기존 동작 대신 서버 본문을 예외에 싣는다."""
        import httpx

        from rice_runtime.connector.mcp.client import RemoteMcpClient

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                json={"error": {"code": 403, "status": "PERMISSION_DENIED",
                                "message": "The caller does not have permission"}},
            )

        client = RemoteMcpClient(
            "https://gmailmcp.googleapis.com/mcp/v1",
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(RuntimeError) as excinfo:
            await client.call_tool("list_labels", {}, "token", "call-1")
        message = str(excinfo.value)
        assert "HTTP 403" in message
        assert "PERMISSION_DENIED" in message


# ── OAuth client 의 quota project 해석 ─────────────────────────────────────────

class TestQuotaProjectResolution:
    def test_project_id_from_client_json_wins(self, tmp_path):
        """client_secret JSON 의 project_id 필드가 x-goog-user-project 의 정본이다."""
        import json as _json

        from rice_runtime.connector.oauth.flow import OAuthFlow
        from rice_runtime.connector.registry import GMAIL_DESCRIPTOR

        secret = tmp_path / "gmail_client_secret.json"
        secret.write_text(_json.dumps({
            "installed": {
                "client_id": "999-abc.apps.googleusercontent.com",
                "client_secret": "s",
                "project_id": "rice-501004",
            }
        }), encoding="utf-8")
        flow = OAuthFlow(GMAIL_DESCRIPTOR, connectors_dir=tmp_path)
        assert flow.quota_project == "rice-501004"

    def test_project_number_derived_from_google_client_id(self):
        from rice_runtime.connector.oauth.flow import OAuthFlow
        from rice_runtime.connector.registry import GMAIL_DESCRIPTOR

        with patch.dict("os.environ", {
            "RICE_GMAIL_CLIENT_ID": "1082091413989-xyz.apps.googleusercontent.com",
            "RICE_GMAIL_CLIENT_SECRET": "s",
        }):
            flow = OAuthFlow(GMAIL_DESCRIPTOR)
        assert flow.quota_project == "1082091413989"

    def test_non_google_client_id_yields_none(self):
        from rice_runtime.connector.oauth.flow import OAuthFlow
        from rice_runtime.connector.registry import GMAIL_DESCRIPTOR

        with patch.dict("os.environ", {
            "RICE_GMAIL_CLIENT_ID": "not-a-google-id",
            "RICE_GMAIL_CLIENT_SECRET": "s",
        }):
            flow = OAuthFlow(GMAIL_DESCRIPTOR)
        assert flow.quota_project is None


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


# ── 도구별 권한(퍼미션) 영속화 + dispatcher 강제 ─────────────────────────────

class TestToolPermissions:
    """connector 도구별 정책 저장/조회 + 기본값 휴리스틱."""

    def setup_method(self):
        self._db = _fresh_db()
        self._svc = ConnectorService(self._db, _fresh_paths())
        self._db.execute(
            "INSERT INTO connectors(id, connector_type, display_name, status) "
            "VALUES ('c1','gmail','Gmail','connected')"
        )

    def test_default_permission_read_vs_write(self):
        """저장값이 없으면 읽기류=allow, 그 외=approval."""
        assert self._svc.permission_for("c1", "search_threads") == "allow"
        assert self._svc.permission_for("c1", "list_labels") == "allow"
        assert self._svc.permission_for("c1", "create_draft") == "approval"
        assert self._svc.permission_for("c1", "label_message") == "approval"

    def test_set_and_get_permission(self):
        self._svc.set_tool_permission("c1", "create_draft", "block")
        assert self._svc.permission_for("c1", "create_draft") == "block"
        self._svc.set_tool_permission("c1", "create_draft", "allow")
        assert self._svc.permission_for("c1", "create_draft") == "allow"
        assert self._svc.tool_permissions("c1") == {"create_draft": "allow"}

    def test_invalid_permission_raises(self):
        with pytest.raises(ValidationError):
            self._svc.set_tool_permission("c1", "create_draft", "maybe")

    def test_set_permission_for_local_mcp_connector_id_does_not_raise(self):
        """connector_tool_permissions 는 connectors(id) FK 가 없다 — local:<name>
        (.mcp.json 파생, connectors 테이블에 행이 없음)도 저장 가능해야 한다."""
        self._svc.set_tool_permission("local:filesystem", "write_file", "approval")
        assert self._svc.permission_for("local:filesystem", "write_file") == "approval"
        assert self._svc.tool_permissions("local:filesystem") == {"write_file": "approval"}

    @pytest.mark.asyncio
    async def test_delete_cascades_tool_permissions(self):
        """FK CASCADE 대신 manager.delete() 가 저장된 권한 행을 명시적으로 정리한다."""
        self._svc.set_tool_permission("c1", "create_draft", "block")
        await self._svc.delete("c1")
        assert self._svc.tool_permissions("c1") == {}


class TestDispatcherPolicyEnforcement:
    """dispatcher 가 도구별 정책(allow/approval/block)을 강제하는지 검증."""

    def _spec(self):
        from rice_runtime.core.types import ToolSpec
        return ToolSpec(
            name="create_draft", description="", parameters={},
            source="connector", owner_id="c1", requires_approval=True,
        )

    def _dispatcher(self, permission: str, *, approve: bool = True):
        from unittest.mock import AsyncMock, MagicMock
        from rice_runtime.agent.tool_dispatch import ToolDispatcher
        from rice_runtime.core.types import ApprovalResponse, ToolResult

        connectors = MagicMock()
        connectors.permission_for = MagicMock(return_value=permission)
        connectors.invoke = AsyncMock(return_value=ToolResult("call_x", ok=True, result={"ok": 1}))
        approvals = MagicMock()
        approvals.request = AsyncMock(
            return_value=ApprovalResponse(approval_id="a1", decision="approve" if approve else "reject")
        )
        disp = ToolDispatcher(
            connectors=connectors, plugins=MagicMock(), approvals=approvals, approval_timeout_s=1,
        )
        return disp, connectors, approvals

    @pytest.mark.asyncio
    async def test_block_refuses_without_invoke_or_approval(self):
        from rice_runtime.core.types import ToolCall
        disp, connectors, approvals = self._dispatcher("block")
        call = ToolCall(tool_name="create_draft", arguments={}, id="call_x")
        events = [ev async for ev in disp.dispatch("s1", call, self._spec())]
        res = await disp.result_for(call)
        assert res.ok is False and "block" in (res.error or "").lower()
        connectors.invoke.assert_not_called()
        approvals.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_allow_runs_without_approval(self):
        from rice_runtime.core.types import ToolCall
        disp, connectors, approvals = self._dispatcher("allow")
        call = ToolCall(tool_name="create_draft", arguments={}, id="call_x")
        _ = [ev async for ev in disp.dispatch("s1", call, self._spec())]
        res = await disp.result_for(call)
        assert res.ok is True
        approvals.request.assert_not_called()
        connectors.invoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_approval_requests_hitl(self):
        from rice_runtime.core.types import ToolCall
        disp, connectors, approvals = self._dispatcher("approval", approve=True)
        call = ToolCall(tool_name="create_draft", arguments={}, id="call_x")
        _ = [ev async for ev in disp.dispatch("s1", call, self._spec())]
        res = await disp.result_for(call)
        approvals.request.assert_awaited_once()
        assert res.ok is True
        connectors.invoke.assert_awaited_once()
