"""Plugin → Connector 권한 검증 (RUNTIME_DESIGN §9.4, CONTRACTS §4.4).

핵심 개념 (RUNTIME_DESIGN §9.4):
  - connector_type ("gmail") 과 connector_id (인스턴스 식별자) 는 **다르다**.
    check_permission 은 connector_type 으로 비교한다.
  - plugin manifest 의 requires.connectors 필드(connector_type 목록)를 읽어
    해당 type 이 포함되면 True, 아니면 False.
  - execution 모듈 구체에 의존하지 않는다 — plugin manifest 는 직접 파일에서 읽는다.
    manifest 를 찾지 못하면 fail-closed (False) (CONVENTIONS §7).

manifest 위치 (우선순위):
  1) ~/.rice/plugins/<plugin_id>/rice-artifact.json  ← loader.install() 이 저장하는 형식
  2) ~/.rice/plugins/<plugin_id>/rice-artifact.yaml  ← 손수 작성한 plugin(개발용)
(CONTRACTS §2.2 / §2.3 — 설치 흐름은 manifest 를 JSON 으로 떨군다.)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("rice.connector.permission")


def _load_plugin_manifest(plugins_root: Path, plugin_id: str) -> dict:
    """plugin manifest 를 읽어 dict 반환한다 (JSON 우선, YAML 폴백).

    파일이 없거나 파싱 실패 시 빈 dict 반환 (fail-closed 처리는 호출자 책임).

    NOTE(agent): loader.install() 은 rice-artifact.json 을 저장하므로 JSON 을 먼저
    읽는다(v1 의 .yaml-only 버그 교정 — checker 와 loader 의 파일명 불일치 해소).
    """
    plugin_dir = plugins_root / plugin_id
    json_path = plugin_dir / "rice-artifact.json"
    if json_path.exists():
        try:
            return json.loads(json_path.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("failed to parse plugin manifest %s: %s", json_path, exc)
            return {}

    yaml_path = plugin_dir / "rice-artifact.yaml"
    if yaml_path.exists():
        try:
            import yaml  # PyYAML — 기본 의존성 (pyproject.toml dependencies)
            return yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to parse plugin manifest %s: %s", yaml_path, exc)
            return {}

    log.debug("plugin manifest not found for %s (fail-closed)", plugin_id)
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
