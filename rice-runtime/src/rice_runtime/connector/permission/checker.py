"""Plugin → Connector 권한 검증 (RUNTIME_DESIGN §9.4, CONTRACTS §4.4).

핵심 개념 (RUNTIME_DESIGN §9.4):
  - connector_type ("gmail") 과 connector_id (인스턴스 식별자) 는 **다르다**.
    check_permission 은 connector_type 으로 비교한다.
  - plugin manifest 의 requires.connectors 필드(connector_type 목록)를 읽어
    해당 type 이 포함되면 True, 아니면 False.
  - execution 모듈 구체에 의존하지 않는다 — plugin manifest 는 직접 파일에서 읽는다.
    manifest 를 찾지 못하면 fail-closed (False) (CONVENTIONS §7).

manifest 위치: ~/.rice/plugins/<plugin_id>/rice-artifact.yaml (CONTRACTS §2.2)
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("rice.connector.permission")


def _load_plugin_manifest(plugins_root: Path, plugin_id: str) -> dict:
    """plugin manifest (rice-artifact.yaml) 를 읽어 dict 반환한다.

    파일이 없거나 파싱 실패 시 빈 dict 반환 (fail-closed 처리는 호출자 책임).
    """
    manifest_path = plugins_root / plugin_id / "rice-artifact.yaml"
    if not manifest_path.exists():
        log.debug("plugin manifest not found: %s (fail-closed)", manifest_path)
        return {}
    try:
        import yaml  # PyYAML — 기본 의존성 (pyproject.toml dependencies)
        return yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("failed to parse plugin manifest %s: %s", manifest_path, exc)
        return {}


class PermissionChecker:
    """Plugin manifest 기반 connector_type 접근 권한 검증기."""

    def __init__(self, plugins_root: Path | None) -> None:
        # plugins_root = RicePaths.plugins (RUNTIME_DESIGN §7 디렉토리 구조)
        # None 이면 fail-closed (CONVENTIONS §7)
        self._plugins_root = plugins_root

    def check(self, plugin_id: str, connector_type: str) -> bool:
        """plugin_id 가 connector_type 에 접근 권한이 있는지 확인한다.

        manifest 의 requires.connectors(connector_type 목록)에 포함되면 True.
        manifest 가 없거나 파싱 실패 시 fail-closed(False) (CONVENTIONS §7).
        """
        if self._plugins_root is None:
            log.debug("plugins_root not set — fail-closed for plugin=%s", plugin_id)
            return False
        manifest = _load_plugin_manifest(self._plugins_root, plugin_id)
        required: list[str] = (
            manifest.get("requires", {}).get("connectors", []) or []
        )
        return connector_type in required
