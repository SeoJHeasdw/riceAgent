"""oauth/ — Gmail OAuth 2.0 Authorization Code Flow (RUNTIME_DESIGN §9.2).

이 패키지는 OAuth 흐름의 두 단계를 담당한다:
  1. begin: state(CSRF) 생성 + Google authorization URL 구성
  2. exchange: authorization_code → access_token / refresh_token 교환

httpx는 optional extra `connectors` 에 선언, 지연 import 패턴으로 감싼다
(CONVENTIONS §2 — 골격이 의존성 없이 import 유지).
"""
from __future__ import annotations

from .flow import GmailOAuthFlow, OAuthFlow

__all__ = ["GmailOAuthFlow", "OAuthFlow"]
