"""OAuth 2.0 Authorization Code Flow (제네릭 — RUNTIME_DESIGN §9.2).

타입별 차이(auth/token URL, scope, env prefix, client JSON 파일명)는 전부
`ConnectorDescriptor`(registry.py)에 담기고, `OAuthFlow` 는 그 위에서 제네릭하게
동작한다. gmail 은 `GmailOAuthFlow`(= GMAIL_DESCRIPTOR 프리셋)로 노출한다.

설계 원칙:
- client_id / client_secret / redirect_uri 는 **세 경로** 중 하나에서 읽는다(우선순위):
    1) env {PREFIX}_CLIENT_ID / {PREFIX}_CLIENT_SECRET
    2) env {PREFIX}_CLIENT_SECRET_FILE 가 가리키는 Google OAuth client JSON
    3) `~/.rice/connectors/{client_secret_filename}` (앱이 설치 시 떨궈 두는 위치)
  Google "installed"(desktop)/"web" 형식 JSON 을 그대로 읽는다.
  세 경로 모두 비어 있으면 build/exchange 시점에 OAuthConfigError 로 fail-closed.
- state(CSRF 토큰)은 secrets.token_urlsafe 로 생성한다.
- httpx 는 지연 import 로 감싼다.

redirect_uri (CONTRACTS §4.2):
- 런타임 콜백 경로 `GET /connectors/oauth/callback` 로 돌아온다.
- 기본값은 런타임이 바인딩한 포트로 구성한다(`http://localhost:{port}/connectors/oauth/callback`).
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from ..registry import GMAIL_DESCRIPTOR, ConnectorDescriptor

log = logging.getLogger("rice.connector.oauth")

# NOTE(agent): 런타임 콜백 경로(라우트 connectors.py 와 일치). redirect_uri 의 path 부분.
OAUTH_CALLBACK_PATH = "/connectors/oauth/callback"

# 포트를 모를 때의 기본값(dev.sh 가 :8080 으로 띄운다).
_DEFAULT_PORT = 8080

# ── 하위호환 심볼(gmail 프리셋) — scripts/·tests 에서 참조 ──────────────────────
GMAIL_SCOPES = list(GMAIL_DESCRIPTOR.scopes)
CLIENT_SECRET_FILENAME = GMAIL_DESCRIPTOR.client_secret_filename


def default_redirect_uri(port: int | None = None) -> str:
    """런타임 포트 기준 기본 redirect_uri 를 구성한다 (CONTRACTS §4.2)."""
    p = port or _DEFAULT_PORT
    return f"http://localhost:{p}{OAUTH_CALLBACK_PATH}"


@dataclass(frozen=True)
class ClientConfig:
    """Google OAuth client 자격증명 (client_id/secret)."""

    client_id: str
    client_secret: str


# 하위호환 별칭 (구 이름).
GmailClientConfig = ClientConfig


def _load_client_config_from_json(path: Path) -> ClientConfig | None:
    """Google "installed"/"web" client JSON 에서 client_id/secret 을 읽는다.

    형식: {"installed": {"client_id":..., "client_secret":...}} 또는 {"web": {...}}.
    실패(없음/파싱오류/필드없음) 시 None — fail-closed 는 호출자 책임.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.debug("client json unreadable %s: %s", path, exc)
        return None
    block = raw.get("installed") or raw.get("web") or raw
    cid = block.get("client_id")
    csecret = block.get("client_secret")
    if not cid or not csecret:
        log.debug("client json missing client_id/secret: %s", path)
        return None
    return ClientConfig(client_id=str(cid), client_secret=str(csecret))


def load_client_config(
    descriptor: ConnectorDescriptor, connectors_dir: Path | None = None
) -> ClientConfig | None:
    """디스크립터 기준으로 client 자격증명을 우선순위대로 해석한다.

    1) env {PREFIX}_CLIENT_ID + {PREFIX}_CLIENT_SECRET
    2) env {PREFIX}_CLIENT_SECRET_FILE (JSON 경로)
    3) {connectors_dir}/{client_secret_filename}
    4) {connectors_dir}/{client_secret_fallback_filenames[i]} (같은 GCP 프로젝트의 다른
       connector 용으로 이미 설치된 client — 예: calendar/drive 가 gmail 설치를 재사용)
    하나도 없으면 None.
    """
    env_id = os.environ.get(descriptor.env_client_id, "")
    env_secret = os.environ.get(descriptor.env_client_secret, "")
    if env_id and env_secret:
        return ClientConfig(client_id=env_id, client_secret=env_secret)

    env_file = os.environ.get(descriptor.env_client_secret_file, "")
    if env_file:
        cfg = _load_client_config_from_json(Path(env_file).expanduser())
        if cfg is not None:
            return cfg

    if connectors_dir is not None:
        for filename in (descriptor.client_secret_filename, *descriptor.client_secret_fallback_filenames):
            cfg = _load_client_config_from_json(connectors_dir / filename)
            if cfg is not None:
                return cfg

    return None


def load_gmail_client_config(connectors_dir: Path | None = None) -> ClientConfig | None:
    """하위호환: gmail 디스크립터로 load_client_config 를 호출한다."""
    return load_client_config(GMAIL_DESCRIPTOR, connectors_dir)


@dataclass(frozen=True)
class TokenResponse:
    """Google 토큰 교환 응답 (정규화 — RUNTIME_DESIGN §9.3)."""

    access_token: str
    refresh_token: str | None
    expires_in: int  # seconds
    scope: str
    token_type: str


class OAuthConfigError(RuntimeError):
    """필수 OAuth 자격증명이 없을 때 — fail-closed (CONVENTIONS §7)."""


class OAuthFlow:
    """제네릭 Authorization Code Flow 헬퍼 (디스크립터 구동).

    사용법:
        flow = OAuthFlow(descriptor, connectors_dir=paths.connectors, port=8080)
        url, state = flow.build_auth_url()
        ...browser redirect...
        token = await flow.exchange_code(code=..., state=..., expected_state=state)
    """

    def __init__(
        self,
        descriptor: ConnectorDescriptor,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
        connectors_dir: Path | None = None,
        port: int | None = None,
    ) -> None:
        self._d = descriptor
        # 자격증명: 명시 인자 > env/JSON 로더 순.
        if client_id and client_secret:
            resolved: ClientConfig | None = ClientConfig(client_id=client_id, client_secret=client_secret)
        else:
            resolved = load_client_config(descriptor, connectors_dir)

        self._client_id = (resolved.client_id if resolved else "") or client_id or ""
        self._client_secret = (resolved.client_secret if resolved else "") or client_secret or ""
        self._redirect_uri = (
            redirect_uri
            or os.environ.get(descriptor.env_redirect_uri)
            or default_redirect_uri(port)
        )

    @property
    def descriptor(self) -> ConnectorDescriptor:
        return self._d

    @property
    def redirect_uri(self) -> str:
        return self._redirect_uri

    def _require_credentials(self) -> None:
        if not self._client_id or not self._client_secret:
            raise OAuthConfigError(
                f"{self._d.display_name} OAuth requires credentials. Set "
                f"{self._d.env_client_id}/{self._d.env_client_secret}, or "
                f"{self._d.env_client_secret_file}=<client_secret.json>, or drop the client JSON at "
                f"~/.rice/connectors/{self._d.client_secret_filename} (RUNTIME_DESIGN §9.2, §10)."
            )

    def build_auth_url(self) -> tuple[str, str]:
        """Authorization URL 과 CSRF state 토큰 쌍을 반환한다."""
        self._require_credentials()
        state = secrets.token_urlsafe(32)
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": " ".join(self._d.scopes),
            "state": state,
            "access_type": "offline",  # refresh_token 요청
            "prompt": "consent",       # 매번 refresh_token 보장
        }
        url = f"{self._d.auth_url}?{urlencode(params)}"
        return url, state

    async def exchange_code(self, *, code: str, state: str, expected_state: str) -> TokenResponse:
        """Authorization code 를 토큰으로 교환한다. state 불일치 시 ValueError."""
        if state != expected_state:
            raise ValueError("OAuth state mismatch — possible CSRF attack")
        self._require_credentials()

        try:
            import httpx  # lazy import (connectors extra)
        except ImportError as exc:
            raise RuntimeError(
                "httpx is required for OAuth token exchange. "
                "Install with: pip install 'rice-runtime[connectors]'"
            ) from exc

        payload = {
            "code": code,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "redirect_uri": self._redirect_uri,
            "grant_type": "authorization_code",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(self._d.token_url, data=payload, timeout=30.0)
            resp.raise_for_status()
            data = resp.json()

        return TokenResponse(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_in=int(data.get("expires_in", 3600)),
            scope=data.get("scope", ""),
            token_type=data.get("token_type", "Bearer"),
        )

    async def refresh_access_token(self, refresh_token: str) -> TokenResponse:
        """refresh_token 으로 새 access_token 을 발급한다 (RUNTIME_DESIGN §9.3)."""
        self._require_credentials()

        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required for token refresh.") from exc

        payload = {
            "refresh_token": refresh_token,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(self._d.token_url, data=payload, timeout=30.0)
            resp.raise_for_status()
            data = resp.json()

        return TokenResponse(
            access_token=data["access_token"],
            refresh_token=refresh_token,  # refresh_token 은 재사용
            expires_in=int(data.get("expires_in", 3600)),
            scope=data.get("scope", ""),
            token_type=data.get("token_type", "Bearer"),
        )


class GmailOAuthFlow(OAuthFlow):
    """Gmail 프리셋 — GMAIL_DESCRIPTOR 로 OAuthFlow 를 구성한다(하위호환)."""

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
        connectors_dir: Path | None = None,
        port: int | None = None,
    ) -> None:
        super().__init__(
            GMAIL_DESCRIPTOR,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            connectors_dir=connectors_dir,
            port=port,
        )
