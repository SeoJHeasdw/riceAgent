"""skill/usage.py — .usage.json 사이드카 카운터.

책임(single): 각 스킬에 대한 use/view/patch 카운터와 last_used_at 타임스탬프를
<name>.usage.json 파일로 관리한다.

포맷 (LEARNING_LOOP §3):
  {
    "skill": "<name>",
    "use": <int>,      # 턴 종료 시 주입됐던 스킬에 +1 (observe에서)
    "view": <int>,     # GET /skills/{id} 호출 시 +1 (get_skill에서)
    "patch": <int>,    # 본문 수정 승인 시 +1 (apply_patch에서)
    "last_used_at": "<ISO8601 | null>",
    "created_at": "<ISO8601>"
  }

파일 경로: ~/.rice/skills/<name>.usage.json
파일이 없으면 0으로 초기화된 기본값을 반환하고, 증가 시 생성한다.

WHY: usage 데이터는 스킬 본문과 별도 파일로 관리한다. 스킬 본문을 편집해도
카운터가 날아가지 않도록 하기 위함이다. apply_patch 시 .history/에 본문만 보관하고
usage는 유지된다 (LEARNING_LOOP §5.2.4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ...core.paths import RicePaths
from ...core.types import now_iso


@dataclass
class UsageData:
    skill: str
    use: int = 0
    view: int = 0
    patch: int = 0
    last_used_at: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "skill": self.skill,
            "use": self.use,
            "view": self.view,
            "patch": self.patch,
            "last_used_at": self.last_used_at,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UsageData":
        return cls(
            skill=d.get("skill", ""),
            use=int(d.get("use", 0)),
            view=int(d.get("view", 0)),
            patch=int(d.get("patch", 0)),
            last_used_at=d.get("last_used_at"),
            created_at=d.get("created_at", ""),
        )


class UsageStore:
    """~/.rice/skills/<name>.usage.json 파일 CRUD (LEARNING_LOOP §3)."""

    def __init__(self, paths: RicePaths) -> None:
        self._paths = paths

    def _usage_path(self, name: str) -> Path:
        return self._paths.skills / f"{name}.usage.json"

    def read(self, name: str) -> UsageData:
        """usage 파일을 읽는다. 없으면 0으로 초기화된 기본값 반환."""
        path = self._usage_path(name)
        if not path.exists():
            return UsageData(skill=name, created_at=now_iso())
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return UsageData.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            # NOTE(agent): 손상된 usage 파일은 기본값으로 복구한다.
            # 카운터 손실은 감내하고 런타임 오류를 막는 것이 우선이다.
            return UsageData(skill=name, created_at=now_iso())

    def _write(self, usage: UsageData) -> None:
        """usage 파일을 원자적으로 쓴다."""
        self._paths.skills.mkdir(parents=True, exist_ok=True)
        path = self._usage_path(usage.skill)
        path.write_text(json.dumps(usage.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def increment_use(self, name: str) -> UsageData:
        """use += 1, last_used_at = now. 턴 관찰(observe)에서 호출."""
        usage = self.read(name)
        usage.use += 1
        usage.last_used_at = now_iso()
        self._write(usage)
        return usage

    def increment_view(self, name: str) -> UsageData:
        """view += 1. get_skill 호출 시 증가."""
        usage = self.read(name)
        usage.view += 1
        self._write(usage)
        return usage

    def increment_patch(self, name: str) -> UsageData:
        """patch += 1. apply_patch 승인 시 증가."""
        usage = self.read(name)
        usage.patch += 1
        self._write(usage)
        return usage
