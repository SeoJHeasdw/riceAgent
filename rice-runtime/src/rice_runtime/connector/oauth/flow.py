"""Gmail OAuth 2.0 flow: authorization URL 구성 + code 교환 (RUNTIME_DESIGN §9.2).

설계 원칙:
- client_id / client_secret / redirect_uri 는 **세 경로** 중 하나에서 읽는다(우선순위):
    1) 환경변수 RICE_GMAIL_CLIENT_ID / RICE_GMAIL_CLIENT_SECRET (RUNTIME_DESIGN §10)
    2) 환경변수 RICE_GMAIL_CLIENT_SECRET_FILE 가 가리키는 Google OAuth client JSON
    3) `~/.rice/connectors/gmail_client_secret.json` (앱이 설치 시 떨궈 두는 위치)
  Google Cloud Console 에서 받은 "installed"(desktop) 형식 JSON 을 그대로 읽는다.
  세 경로 모두 비어 있으면 build/exchange 시점에 OAuthConfigError 로 fail-closed.
- state(CSRF 토큰)은 secrets.token_urlsafe 로 생성한다.
- httpx 는 지연 import 로 감싸 골격 import 에 영향을 주지 않는다.

redirect_uri (CONTRACTS §4.2):
- 런타임 콜백 경로 `GET /connectors/oauth/callback` 로 돌아온다.
- 기본값은 런타임이 바인딩한 포트로 구성한다(`http://localhost:{port}/connectors/oauth/callback`).
  사용자 자격증명이 desktop("installed") 타입이면 Google loopback 규칙상
  localhost 의 임의 포트·경로가 허용되므로 이 URL 이 동작한다.
- CLI 테스트 하니스(scripts/)는 임시 loopback 서버를 띄워 redirect_uri 를 직접 주입한다.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

log = logging.getLogger("rice.connector.oauth")

# Gmail OAuth endpoints (Google Identity Platform)
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# 필요 scope — Gmail read + send (RUNTIME_DESIGN §9.1 prototype)
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

_ENV_CLIENT_ID = "RICE_GMAIL_CLIENT_ID"
_ENV_CLIENT_SECRET = "RICE_GMAIL_CLIENT_SECRET"
_ENV_REDIRECT_URI = "RICE_GMAIL_REDIRECT_URI"
_ENV_CLIENT_SECRET_FILE = "RICE_GMAIL_CLIENT_SECRET_FILE"

# 앱이 설치 시 사용자 client_secret JSON 을 떨궈 두는 표준 위치(파일명 고정).
CLIENT_SECRET_FILENAME = "gmail_client_secret.json"

# NOTE(agent): 런타임 콜백 경로(라우트 connectors.py 와 일치). redirect_uri 의 path 부분.
OAUTH_CALLBACK_PATH = "/connectors/oauth/callback"

# 포트를 모를 때의 기본값(dev.sh 가 :8080 으로 띄운다).
_DEFAULT_PORT = 8080


def default_redirect_uri(port: int | None = None) -> str:
    """런타임 포트 기준 기본 redirect_uri 를 구성한다 (CONTRACTS §4.2)."""
    p = port or _DEFAULT_PORT
    return f"http://localhost:{p}{OAUTH_CALLBACK_PATH}"


@dataclass(frozen=True)
class GmailClientConfig:
    """Google OAuth client 자격증명 (client_id/secret)."""

    client_id: str
    client_secret: str


def _load_client_config_from_json(path: Path) -> GmailClientConfig | None:
    """Google "installed"/"web" client JSON 에서 client_id/secret 을 읽는다.

    형식: {"installed": {"client_id":..., "client_secret":...}} 또는 {"web": {...}}.
    실패(없음/파싱오류/필드없음) 시 None — fail-closed 는 호출자 책임.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.debug("gmail client json unreadable %s: %s", path, exc)
        return None
    block = raw.get("installed") or raw.get("web") or raw
    cid = block.get("client_id")
    csecret = block.get("client_secret")
    if not cid or not csecret:
        log.debug("gmail client json missing client_id/secret: %s", path)
        return None
    return GmailClientConfig(client_id=str(cid), client_secret=str(csecret))


def load_gmail_client_config(connectors_dir: Path | None = None) -> GmailClientConfig | None:
    """우선순위에 따라 Gmail client 자격증명을 해석한다.

    1) env RICE_GMAIL_CLIENT_ID + RICE_GMAIL_CLIENT_SECRET
    2) env RICE_GMAIL_CLIENT_SECRET_FILE (JSON 경로)
    3) {connectors_dir}/gmail_client_secret.json
    하나도 없으면 None.
    """
    env_id = os.environ.get(_ENV_CLIENT_ID, "")
    env_secret = os.environ.get(_ENV_CLIENT_SECRET, "")
    if env_id and env_secret:
        return GmailClientConfig(client_id=env_id, client_secret=env_secret)

    env_file = os.environ.get(_ENV_CLIENT_SECRET_FILE, "")
    if env_file:
        cfg = _load_client_config_from_json(Path(env_file).expanduser())
        if cfg is not None:
            return cfg

    if connectors_dir is not None:
        cfg = _load_client_config_from_json(connectors_dir / CLIENT_SECRET_FILENAME)
        if cfg is not None:
            return cfg

    return None


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


class GmailOAuthFlow:
    """Gmail Authorization Code Flow 헬퍼.

    사용법:
        flow = GmailOAuthFlow(connectors_dir=paths.connectors, port=8080)
        url, state = flow.build_auth_url()
        ...browser redirect...
        token = await flow.exchange_code(code=..., state=..., expected_state=state)

    자격증명/redirect 해석:
      - client_id/secret: 생성자 인자 > load_gmail_client_config(env/JSON) 순.
      - redirect_uri: 생성자 인자 > env RICE_GMAIL_REDIRECT_URI > default_redirect_uri(port).
    """

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
        connectors_dir: Path | None = None,
        port: int | None = None,
    ) -> None:
        # NOTE(agent): 자격증명은 생성자 인자 > env/JSON 로더 순. 명시 인자가 없으면
        # 즉시 로더를 호출해 캐시한다. 로더가 None 이면 _require_credentials 가 raise.
        resolved: GmailClientConfig | None = None
        if client_id and client_secret:
            resolved = GmailClientConfig(client_id=client_id, client_secret=client_secret)
        else:
            resolved = load_gmail_client_config(connectors_dir)

        self._client_id = (resolved.client_id if resolved else "") or client_id or ""
        self._client_secret = (resolved.client_secret if resolved else "") or client_secret or ""
        self._redirect_uri = (
            redirect_uri
            or os.environ.get(_ENV_REDIRECT_URI)
            or default_redirect_uri(port)
        )

    @property
    def redirect_uri(self) -> str:
        return self._redirect_uri

    def _require_credentials(self) -> None:
        if not self._client_id or not self._client_secret:
            raise OAuthConfigError(
                f"Gmail OAuth requires credentials. Set {_ENV_CLIENT_ID}/{_ENV_CLIENT_SECRET}, "
                f"or {_ENV_CLIENT_SECRET_FILE}=<client_secret.json>, or drop the client JSON at "
                f"~/.rice/connectors/{CLIENT_SECRET_FILENAME} (RUNTIME_DESIGN §9.2, §10)."
            )

    def build_auth_url(self) -> tuple[str, str]:
        """Authorization URL 과 CSRF state 토큰 쌍을 반환한다.

        Returns:
            (oauth_url, state) — OAuthStart DTO 에 그대로 담긴다.
        """
        self._require_credentials()
        state = secrets.token_urlsafe(32)
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": " ".join(GMAIL_SCOPES),
            "state": state,
            "access_type": "offline",  # refresh_token 요청
            "prompt": "consent",       # 매번 refresh_token 보장
        }
        url = f"{_GOOGLE_AUTH_URL}?{urlencode(params)}"
        return url, state

    async def exchange_code(self, *, code: str, state: str, expected_state: str) -> TokenResponse:
        """Authorization code 를 토큰으로 교환한다.

        state 불일치 시 ValueError (CSRF 방어 — RUNTIME_DESIGN §9.2).
        httpx 는 optional extra `connectors` 에 선언됨.
        """
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
            resp = await client.post(_GOOGLE_TOKEN_URL, data=payload, timeout=30.0)
            resp.raise_for_status()
            data = resp.json()

        # NOTE(agent): Google 토큰 응답 형태는
        # https://developers.google.com/identity/protocols/oauth2/web-server#exchange-authorization-code
        # 에서 확인. refresh_token 은 offline access + consent 시에만 포함.
        return TokenResponse(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_in=int(data.get("expires_in", 3600)),
            scope=data.get("scope", ""),
            token_type=data.get("token_type", "Bearer"),
        )

    async def refresh_access_token(self, refresh_token: str) -> TokenResponse:
        """refresh_token 으로 새 access_token 을 발급한다 (RUNTIME_DESIGN §9.3).

        백그라운드 갱신이 아니라 invoke/status 시 호출용 함수 (MVP 범위 — connector.md).
        """
        self._require_credentials()

        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError(
                "httpx is required for token refresh."
            ) from exc

        payload = {
            "refresh_token": refresh_token,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(_GOOGLE_TOKEN_URL, data=payload, timeout=30.0)
            resp.raise_for_status()
            data = resp.json()

        return TokenResponse(
            access_token=data["access_token"],
            refresh_token=refresh_token,  # refresh_token 은 재사용
            expires_in=int(data.get("expires_in", 3600)),
            scope=data.get("scope", ""),
            token_type=data.get("token_type", "Bearer"),
        )
