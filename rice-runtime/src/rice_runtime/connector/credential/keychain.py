"""OS 키체인 기반 Credential 저장 (RUNTIME_DESIGN §9.3, CONTRACTS §4.3).

보안 원칙:
  - access_token, refresh_token, expires_at 만 키체인에 저장한다.
  - DB·로그·반환 JSON 에 토큰 본문을 절대 기록하지 않는다 (RUNTIME_DESIGN §10.4).
  - keyring 은 optional extra `connectors` 에 선언, 지연 import 로 감싼다.

키 형식: rice.connector.{connector_id}.{credential_type}
  credential_type: access_token | refresh_token | expires_at
"""
from __future__ import annotations

import time
from dataclasses import dataclass

# keyring 서비스명 고정 (macOS Keychain 의 "Keychain 항목 이름")
_SERVICE = "rice"


def _keychain_key(connector_id: str, credential_type: str) -> str:
    """RUNTIME_DESIGN §9.3 키 형식: rice.connector.{id}.{type}."""
    return f"rice.connector.{connector_id}.{credential_type}"


@dataclass(frozen=True)
class StoredCredential:
    """읽어 온 자격증명 (메모리에서 즉시 사용 후 해제 권장).

    expires_at 은 UNIX timestamp(초). None 이면 만료 정보 없음.
    """

    access_token: str
    refresh_token: str | None
    expires_at: float | None  # UNIX epoch seconds

    def is_expired(self, buffer_seconds: int = 60) -> bool:
        """만료 임박(기본 60초 전) 여부 — invoke/status 시 자동 갱신 판단용."""
        if self.expires_at is None:
            return False
        return time.time() + buffer_seconds >= self.expires_at


class KeychainStore:
    """OS 키체인 CRUD — keyring 라이브러리 래퍼 (RUNTIME_DESIGN §9.3)."""

    @staticmethod
    def _keyring():
        """keyring 을 지연 import 한다. 미설치 시 RuntimeError."""
        try:
            import keyring  # optional extra `connectors`
            return keyring
        except ImportError as exc:
            raise RuntimeError(
                "keyring is required for credential storage. "
                "Install with: pip install 'rice-runtime[connectors]'"
            ) from exc

    def save(
        self,
        connector_id: str,
        *,
        access_token: str,
        refresh_token: str | None,
        expires_in: int | None,
    ) -> None:
        """토큰 3종을 키체인에 저장한다.

        expires_in(초 단위 TTL)을 UNIX timestamp 로 변환해 저장한다.
        DB 나 로그에는 아무것도 기록하지 않는다.
        """
        kr = self._keyring()
        kr.set_password(_SERVICE, _keychain_key(connector_id, "access_token"), access_token)
        if refresh_token is not None:
            kr.set_password(_SERVICE, _keychain_key(connector_id, "refresh_token"), refresh_token)
        if expires_in is not None:
            expires_at = str(time.time() + expires_in)
            kr.set_password(_SERVICE, _keychain_key(connector_id, "expires_at"), expires_at)

    def load(self, connector_id: str) -> StoredCredential | None:
        """키체인에서 자격증명을 읽는다. 없으면 None."""
        kr = self._keyring()
        access_token = kr.get_password(_SERVICE, _keychain_key(connector_id, "access_token"))
        if not access_token:
            return None
        refresh_token = kr.get_password(_SERVICE, _keychain_key(connector_id, "refresh_token"))
        expires_at_str = kr.get_password(_SERVICE, _keychain_key(connector_id, "expires_at"))
        expires_at: float | None = float(expires_at_str) if expires_at_str else None
        return StoredCredential(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )

    def delete(self, connector_id: str) -> None:
        """해당 connector 의 키체인 항목 전부 삭제."""
        kr = self._keyring()
        for cred_type in ("access_token", "refresh_token", "expires_at"):
            key = _keychain_key(connector_id, cred_type)
            try:
                kr.delete_password(_SERVICE, key)
            except Exception:  # noqa: BLE001
                # 항목이 없어도 삭제 요청은 성공으로 처리 (idempotent)
                pass

    def update_access_token(
        self,
        connector_id: str,
        *,
        access_token: str,
        expires_in: int,
        refresh_token: str | None = None,
    ) -> None:
        """refresh 후 새 access_token 을 교체한다.

        refresh_token 이 주어지면(rotation 서버가 새 것을 내려준 경우 — RFC 6749 §6)
        그것도 교체한다. 이전 refresh_token 은 서버가 이미 무효화했을 수 있으므로
        버리는 게 맞다. None 이면(비-rotation, Google 등) 기존 것을 유지한다.
        """
        kr = self._keyring()
        kr.set_password(_SERVICE, _keychain_key(connector_id, "access_token"), access_token)
        expires_at = str(time.time() + expires_in)
        kr.set_password(_SERVICE, _keychain_key(connector_id, "expires_at"), expires_at)
        if refresh_token is not None:
            kr.set_password(_SERVICE, _keychain_key(connector_id, "refresh_token"), refresh_token)

    # ── generic secrets (connector 자격증명 3종 스키마 밖) ──────────────────────
    # 커스텀 원격 커넥터의 동적 등록(RFC 7591) client_secret 처럼, connector_id 인스턴스가
    # 아니라 임의 키로 비밀을 저장해야 하는 경우용. DB 에는 절대 두지 않는다(§10.4).
    def set_secret(self, key: str, value: str) -> None:
        self._keyring().set_password(_SERVICE, f"rice.secret.{key}", value)

    def get_secret(self, key: str) -> str | None:
        return self._keyring().get_password(_SERVICE, f"rice.secret.{key}")

    def delete_secret(self, key: str) -> None:
        try:
            self._keyring().delete_password(_SERVICE, f"rice.secret.{key}")
        except Exception:  # noqa: BLE001 — 없어도 삭제는 성공 취급(idempotent)
            pass
