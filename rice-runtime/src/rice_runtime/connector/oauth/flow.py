"""Gmail OAuth 2.0 flow: authorization URL 구성 + code 교환 (RUNTIME_DESIGN §9.2).

설계 원칙:
- client_id / client_secret / redirect_uri 는 환경변수에서만 읽는다.
  (RUNTIME_DESIGN §10: 시크릿 하드코딩·커밋 금지)
  환경변수가 없으면 OAuthConfigError 를 올려 fail-closed.
- state(CSRF 토큰)은 secrets.token_urlsafe 로 생성한다.
- httpx 는 지연 import 로 감싸 골격 import 에 영향을 주지 않는다.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

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

# NOTE(agent): redirect_uri 기본값은 로컬 런타임 OAuth callback 엔드포인트.
# 실제 배포 시 Google Cloud Console 에 등록된 URI 와 일치해야 한다.
_DEFAULT_REDIRECT_URI = "http://localhost:0/connectors/oauth/callback"


@dataclass(frozen=True)
class TokenResponse:
    """Google 토큰 교환 응답 (정규화 — RUNTIME_DESIGN §9.3)."""

    access_token: str
    refresh_token: str | None
    expires_in: int  # seconds
    scope: str
    token_type: str


class OAuthConfigError(RuntimeError):
    """필수 OAuth 환경변수가 없을 때 — fail-closed (CONVENTIONS §7)."""


class GmailOAuthFlow:
    """Gmail Authorization Code Flow 헬퍼.

    사용법:
        flow = GmailOAuthFlow()
        url, state = flow.build_auth_url()
        ...browser redirect...
        token = await flow.exchange_code(code=..., state=..., expected_state=state)
    """

    def __init__(self) -> None:
        # NOTE(agent): 환경변수 미설정 시 즉시 fail-closed (OAuthConfigError).
        # 테스트는 monkeypatch 로 주입하거나 환경변수를 직접 세팅한다.
        self._client_id = os.environ.get(_ENV_CLIENT_ID, "")
        self._client_secret = os.environ.get(_ENV_CLIENT_SECRET, "")
        self._redirect_uri = os.environ.get(_ENV_REDIRECT_URI, _DEFAULT_REDIRECT_URI)

    def _require_credentials(self) -> None:
        if not self._client_id or not self._client_secret:
            raise OAuthConfigError(
                f"Gmail OAuth requires {_ENV_CLIENT_ID} and {_ENV_CLIENT_SECRET} "
                "environment variables (RUNTIME_DESIGN §9.2, §10)."
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
