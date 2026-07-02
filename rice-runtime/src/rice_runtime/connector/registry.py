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
    client_secret_filename: str    # ~/.rice/connectors/ 내 client JSON 파일명 (1순위)
    # 같은 Google Cloud 프로젝트의 OAuth client 는 Gmail/Calendar/Drive 등 여러 제품에
    # 재사용된다(Google API 활성화는 프로젝트 단위, OAuth client 는 그 위에 하나). 그래서
    # 타입 전용 파일이 없으면 이 목록을 순서대로 시도한다 — 파일 복제 없이 기존 설치를 공유.
    client_secret_fallback_filenames: tuple[str, ...] = ()
    # 사용자가 런타임에 "커스텀 추가"로 등록한 타입(custom_registry.py 산출) 여부.
    # 자격증명 오류 안내가 갈린다: 정적 타입은 env/JSON 설치를, 커스텀은 모달의
    # client ID 직접 입력을 안내해야 한다(OAuthFlow._require_client_id).
    is_custom: bool = False

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

CALENDAR_DESCRIPTOR = ConnectorDescriptor(
    connector_type="calendar",
    display_name="Google Calendar",
    auth_url=_GOOGLE_AUTH_URL,
    token_url=_GOOGLE_TOKEN_URL,
    # readonly: list_events/get_event/list_calendars/suggest_time. events: create/update/delete/respond_to_event.
    scopes=(
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    ),
    # NOTE(agent): 실재 확인된 Google 원격 Calendar MCP 엔드포인트(2026-07-01, tools/list 로 8개 도구 확인).
    mcp_endpoint="https://calendarmcp.googleapis.com/mcp/v1",
    env_prefix="RICE_CALENDAR",
    client_secret_filename="calendar_client_secret.json",
    # 전용 파일이 없으면 이미 설치된 Gmail 용 client(같은 GCP 프로젝트)를 그대로 재사용.
    client_secret_fallback_filenames=("gmail_client_secret.json",),
)

DRIVE_DESCRIPTOR = ConnectorDescriptor(
    connector_type="drive",
    display_name="Google Drive",
    auth_url=_GOOGLE_AUTH_URL,
    token_url=_GOOGLE_TOKEN_URL,
    # copy_file/create_file 은 검색으로 찾은 임의 기존 파일을 대상으로 하므로 drive.file(앱이
    # 만든/연 파일 한정)로는 부족 — 전체 파일 read/write 가 필요한 full drive scope 사용.
    scopes=("https://www.googleapis.com/auth/drive",),
    # NOTE(agent): 실재 확인된 Google 원격 Drive MCP 엔드포인트(2026-07-01, tools/list 로 8개 도구 확인).
    mcp_endpoint="https://drivemcp.googleapis.com/mcp/v1",
    env_prefix="RICE_DRIVE",
    client_secret_filename="drive_client_secret.json",
    client_secret_fallback_filenames=("gmail_client_secret.json",),
)


_REGISTRY: dict[str, ConnectorDescriptor] = {
    d.connector_type: d for d in (GMAIL_DESCRIPTOR, CALENDAR_DESCRIPTOR, DRIVE_DESCRIPTOR)
}


def supported_types() -> frozenset[str]:
    """등록된 connector 타입 집합 (구 _SUPPORTED_TYPES 대체)."""
    return frozenset(_REGISTRY)


def get_descriptor(connector_type: str) -> ConnectorDescriptor | None:
    """타입에 해당하는 디스크립터. 미등록이면 None."""
    return _REGISTRY.get(connector_type)


def descriptors() -> tuple[ConnectorDescriptor, ...]:
    return tuple(_REGISTRY.values())
