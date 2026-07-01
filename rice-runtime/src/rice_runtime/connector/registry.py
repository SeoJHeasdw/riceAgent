"""Connector 타입 레지스트리 (de-Gmail 일반화 — CONVENTIONS §0, 모듈화).

각 connector 타입의 "무엇이 다른가"를 **데이터(ConnectorDescriptor)** 로 모은다:
  - OAuth 엔드포인트 / scope
  - 원격 MCP 서버 endpoint
  - 자격증명 해석용 env prefix / client_secret 파일명

서비스·매니저·OAuth flow 는 이 디스크립터 위에서 제네릭하게 동작한다. 새 커넥터
(Slack MCP, Notion MCP …)를 붙이려면 여기 디스크립터 하나만 추가하면 되고, 코어
로직은 건드리지 않는다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConnectorDescriptor:
    """한 connector 타입의 정적 명세. 타입별 차이는 전부 여기 담긴다."""

    connector_type: str            # "gmail"
    display_name: str              # "Gmail"
    auth_url: str                  # OAuth authorization endpoint
    token_url: str                 # OAuth token endpoint
    scopes: tuple[str, ...]        # 요청 scope
    mcp_endpoint: str              # 원격 MCP 서버 (기본값 — env 로 오버라이드 가능)
    env_prefix: str                # 자격증명 env prefix, 예: "RICE_GMAIL"
    client_secret_filename: str    # ~/.rice/connectors/ 내 client JSON 파일명

    # ── env 오버라이드 헬퍼 ────────────────────────────────────────────────────
    def resolved_mcp_endpoint(self) -> str:
        """env {PREFIX}_MCP_ENDPOINT 가 있으면 그것을, 없으면 기본값을 쓴다."""
        return os.environ.get(f"{self.env_prefix}_MCP_ENDPOINT") or self.mcp_endpoint

    @property
    def env_client_id(self) -> str:
        return f"{self.env_prefix}_CLIENT_ID"

    @property
    def env_client_secret(self) -> str:
        return f"{self.env_prefix}_CLIENT_SECRET"

    @property
    def env_client_secret_file(self) -> str:
        return f"{self.env_prefix}_CLIENT_SECRET_FILE"

    @property
    def env_redirect_uri(self) -> str:
        return f"{self.env_prefix}_REDIRECT_URI"


# ── Google Identity Platform 공통 엔드포인트 ──────────────────────────────────
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

GMAIL_DESCRIPTOR = ConnectorDescriptor(
    connector_type="gmail",
    display_name="Gmail",
    auth_url=_GOOGLE_AUTH_URL,
    token_url=_GOOGLE_TOKEN_URL,
    # 원격 MCP 서버가 위임 실행할 도구 집합에 맞춘 scope (draft compose + label modify).
    scopes=(
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/gmail.modify",
    ),
    # NOTE(agent): 실재 확인된 Google 원격 Gmail MCP 엔드포인트. env RICE_GMAIL_MCP_ENDPOINT 로 교체 가능.
    mcp_endpoint="https://gmailmcp.googleapis.com/mcp/v1",
    env_prefix="RICE_GMAIL",
    client_secret_filename="gmail_client_secret.json",
)


_REGISTRY: dict[str, ConnectorDescriptor] = {
    GMAIL_DESCRIPTOR.connector_type: GMAIL_DESCRIPTOR,
}


def supported_types() -> frozenset[str]:
    """등록된 connector 타입 집합 (구 _SUPPORTED_TYPES 대체)."""
    return frozenset(_REGISTRY)


def get_descriptor(connector_type: str) -> ConnectorDescriptor | None:
    """타입에 해당하는 디스크립터. 미등록이면 None."""
    return _REGISTRY.get(connector_type)


def descriptors() -> tuple[ConnectorDescriptor, ...]:
    return tuple(_REGISTRY.values())
