"""skill/builtin.py — 앱과 함께 배포되는 읽기 전용 빌트인 스킬 카탈로그.

책임(single): `builtin_skills/*.md`(패키지 자산)를 로드해 SkillData로 노출하고,
사용자별 활성/비활성 상태를 `~/.rice/skills/.builtin-states.json`에 영속화한다.

빌트인 스킬의 계약 (SkillStore의 사용자 스킬과 다른 점):
  - **본문은 불변** — 패키지 자산이 원본이고 사용자 디스크에 복사하지 않는다.
    복사하면 앱 업데이트로 갱신되지 않고, 삭제/편집이 가능해 보이는 잘못된
    어포던스가 생긴다. patch/rollback/history가 없으므로 버전은 자산 frontmatter의
    version 그대로다(스킬 업데이트 = 앱 릴리스).
  - **상태는 active|archived 두 값뿐** — UI의 활성/비활성 토글에 대응한다.
    stale은 큐레이터 소관인데 큐레이터는 SkillStore만 보므로 빌트인에 닿지 않는다.
  - **이름은 예약 네임스페이스** — 같은 이름의 사용자 스킬 생성(create/제안 승인)은
    LearningService가 거부한다. 아니면 list/get에서 어느 쪽이 이기는지 모호해진다.

usage 카운터(use/view)는 UsageStore 사이드카를 그대로 재사용한다 — 이름 충돌이
예약으로 막혀 있어 사이드카 파일이 모호해질 일이 없다.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path

from ...core.errors import ValidationError
from ...core.paths import RicePaths
from ...core.types import now_iso
from .store import SkillData, parse_skill_md

log = logging.getLogger("rice.learning.builtin_skills")

__all__ = ["BUILTIN_SOURCE", "BuiltinSkillCatalog"]

BUILTIN_SOURCE = "builtin"
_ASSET_DIR = Path(__file__).parent / "builtin_skills"
_ALLOWED_STATES = frozenset({"active", "archived"})  # archived = 비활성(주입 중단)
# 빌트인도 UI inactive를 받아 enabled=False로 저장하기 위해 inactive를 추가 허용한다.
# 저장 값은 archived(동일한 "비활성" 의미)로 정규화한다.
_ALLOWED_SETTABLE = frozenset({"active", "archived", "inactive"})


class BuiltinSkillCatalog:
    """읽기 전용 스킬 자산 + 사용자별 enable/disable 상태."""

    def __init__(self, paths: RicePaths, asset_dir: Path | None = None) -> None:
        self._paths = paths
        self._skills: dict[str, SkillData] = {}
        asset_dir = asset_dir or _ASSET_DIR
        if asset_dir.is_dir():
            for path in sorted(asset_dir.glob("*.md")):
                try:
                    data = parse_skill_md(path.read_text(encoding="utf-8"))
                except Exception:
                    # 자산 손상은 배포 버그지만 한 파일이 나머지 빌트인·부팅을
                    # 막아선 안 된다 (fail-safe, SkillStore.list와 동일 원칙).
                    log.exception("builtin skill asset unreadable: %s", path)
                    continue
                data.source = BUILTIN_SOURCE  # frontmatter와 무관하게 강제
                self._skills[data.name] = data

    # ── 조회 ─────────────────────────────────────────────────────────────────
    def is_builtin(self, name: str) -> bool:
        return name in self._skills

    def names(self) -> frozenset[str]:
        return frozenset(self._skills)

    def list(self) -> list[SkillData]:
        """상태 파일을 반영한 사본 목록 (이름 정렬)."""
        return [self._materialize(name) for name in sorted(self._skills)]

    def get(self, name: str) -> SkillData | None:
        return self._materialize(name) if name in self._skills else None

    def _materialize(self, name: str) -> SkillData:
        """자산 SkillData + 사용자 상태 오버레이. 호출자가 변조 못 하게 사본 반환."""
        data = dataclasses.replace(self._skills[name])
        override = self._states().get(name)
        if isinstance(override, dict):
            state = override.get("state")
            if state in _ALLOWED_STATES:
                data.state = state
            if "enabled" in override:
                data.enabled = bool(override["enabled"])
            if override.get("updated_at"):
                data.updated_at = override["updated_at"]
        return data

    # ── 상태 (enable/disable) ────────────────────────────────────────────────
    def set_state(self, name: str, state: str) -> None:
        """active(활성) | archived/inactive(비활성) 허용 — 그 외는 ValidationError.

        inactive는 archived로 정규화해 저장한다(빌트인의 비활성 = archived).
        """
        if name not in self._skills:
            raise ValidationError(f"unknown builtin skill: {name!r}")
        if state not in _ALLOWED_SETTABLE:
            raise ValidationError(
                f"빌트인 스킬 '{name}'은(는) 활성(active)/비활성(archived/inactive)만 전환할 수 있습니다 (got {state!r})."
            )
        normalized = "archived" if state == "inactive" else state
        states = self._states()
        states[name] = {"state": normalized, "enabled": normalized == "active", "updated_at": now_iso()}
        self._write_states(states)

    def _state_file(self) -> Path:
        return self._paths.skills / ".builtin-states.json"

    def _states(self) -> dict:
        path = self._state_file()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            # 손상된 상태 파일은 기본값(전부 active)으로 복구 — 카운터 손실보다
            # 런타임 오류 방지가 우선 (usage.py와 동일한 관용 정책).
            return {}

    def _write_states(self, states: dict) -> None:
        self._paths.skills.mkdir(parents=True, exist_ok=True)
        self._state_file().write_text(
            json.dumps(states, ensure_ascii=False, indent=2), encoding="utf-8"
        )
