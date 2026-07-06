"""skill/lifecycle.py — 스킬 상태 전이 및 rollback 헬퍼.

책임(single): 스킬의 상태(state) 전이 규칙을 캡슐화한다. Curator와 set_state가
이 모듈을 통해 상태를 바꾼다.

상태 머신 (LEARNING_LOOP §5.1):
  active ──(stale_after_days 경과)──▶ stale ──(archive_after_days 경과)──▶ archived
  모든 상태 ──(set_state 수동)──▶ active | stale | archived

rollback (LEARNING_LOOP §5.2.4, T3 연계):
  .history/v<N>.md 본문을 현재로 복원한다. 복원 시 현재 본문은 새 버전으로
  history에 보관하고, frontmatter의 version을 갱신한다.

WHY: 전이 판단 로직을 lifecycle.py에 격리함으로써 curator.py와 service.py가 동일한
규칙을 재사용한다. 규칙 변경 시 한 곳만 수정한다.
"""

from __future__ import annotations

import datetime
from typing import Literal

from ...core.errors import NotFoundError
from ...core.paths import RicePaths
from ...core.types import now_iso
from .store import SkillData, SkillStore, parse_skill_md, serialize_skill_md

SkillState = Literal["active", "stale", "archived"]

_VALID_STATES: frozenset[str] = frozenset({"active", "stale", "archived"})
# inactive는 UI 전용 표현 — DB(파일)에는 state=active + enabled=False로 저장된다.
_ALL_SETTABLE: frozenset[str] = frozenset({"active", "stale", "archived", "inactive"})


def _parse_iso(ts: str | None) -> datetime.datetime | None:
    """ISO8601(Z 허용) → naive UTC datetime. 파싱 불가/None 이면 None."""
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts.rstrip("Z"))
    except ValueError:
        return None


def compute_transition(
    skill: SkillData,
    stale_after_days: int,
    archive_after_days: int,
    now: datetime.datetime | None = None,
    last_used_at: str | None = None,
) -> SkillState | None:
    """time-rule 기반 상태 전이 대상 state를 반환한다. 전이 불필요하면 None.

    규칙 (LEARNING_LOOP §5.1):
    - active → stale  : "마지막 활동" 시점이 stale_after_days 이전인 경우
    - stale  → archived: 같은 기준으로 archive_after_days 이전인 경우
    - archived: 자동 전이 없음(수동 activate만)

    "마지막 활동" = max(last_used_at, updated_at, created_at) 중 파싱 가능한 가장
    최신 시각. last_used_at(usage.json 사이드카)을 우선한다는 설계 의도(주석 §44)를
    실제로 반영한다 — 매일 쓰는 스킬이 본문 미수정만으로 stale 되던 v1 버그#5 교정.
    UsageStore.last_used_at 은 호출자(Curator/service)가 주입한다.
    """
    if skill.state == "archived":
        return None

    _now = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    # 사용·수정·생성 중 가장 최근 신호를 기준으로 삼는다(어떤 활동이든 신선도로 인정).
    candidates = [
        _parse_iso(last_used_at),
        _parse_iso(skill.updated_at),
        _parse_iso(skill.created_at),
    ]
    refs = [c for c in candidates if c is not None]
    if not refs:
        # NOTE(agent): 기준 날짜가 없으면 전이하지 않는다 (fail-closed).
        return None
    ref = max(refs)

    elapsed_days = (_now - ref).total_seconds() / 86400

    if skill.state == "active" and elapsed_days >= stale_after_days:
        return "stale"
    if skill.state == "stale" and elapsed_days >= archive_after_days:
        return "archived"
    return None


class SkillLifecycle:
    """상태 전이와 rollback을 수행하는 헬퍼 (LEARNING_LOOP §5.1, §5.2.4)."""

    def __init__(self, store: SkillStore, paths: RicePaths) -> None:
        self._store = store
        self._paths = paths

    def set_state(self, name: str, state: str) -> None:
        """스킬 state를 명시적으로 변경한다.

        active/stale/archived/inactive 를 허용한다.
        inactive는 파일에 state=active + enabled=False 로 저장된다.
        active로 복구하면 enabled=True 로 함께 초기화한다.
        """
        if state not in _ALL_SETTABLE:
            from ...core.errors import ValidationError
            raise ValidationError(f"Unknown skill state {state!r}")
        skill = self._store.read(name)  # NotFoundError 위임
        if state == "inactive":
            skill.state = "active"
            skill.enabled = False
        else:
            skill.state = state
            skill.enabled = True
        skill.updated_at = now_iso()
        self._store.write(skill)

    def archive_with_absorption(self, name: str, absorbed_into: str | None) -> None:
        """Curator 통합(Phase B) 승인 시 호출 — archived로 전이하며 `absorbed_into`를
        frontmatter에 기록한다(병합 흡수 대상이 있으면 그 umbrella skill 이름,
        순수 정리면 None). `set_state`와 분리한 이유: absorbed_into는 통합 컨텍스트
        에서만 의미 있는 필드라 범용 set_state에 억지로 끼워 넣지 않는다."""
        skill = self._store.read(name)  # NotFoundError 위임
        skill.state = "archived"
        skill.absorbed_into = absorbed_into
        skill.updated_at = now_iso()
        self._store.write(skill)

    def rollback(self, name: str, version: int) -> int:
        """히스토리 버전 <version>을 현재 본문으로 복원한다.

        절차 (보수적):
        1. 히스토리에서 v<version>.md를 읽는다.
        2. 현재 본문을 history에 보관(현재 버전 번호로).
        3. 복원 본문으로 덮어쓰고 version을 현재+1로 설정.
        반환: 새 version 번호.

        NOTE(agent): 롤백 시 version 번호는 단조 증가를 유지한다. 히스토리를 현재
        버전으로 되돌리는 것처럼 보이지 않게 하기 위함이다. 예: v1→v2→rollback(1) → v3.
        """
        hist_dir = self._paths.skill_history(name)
        hist_path = hist_dir / f"v{version}.md"
        if not hist_path.exists():
            raise NotFoundError(f"History v{version} not found for skill {name!r}")

        hist_text = hist_path.read_text(encoding="utf-8")
        hist_data = parse_skill_md(hist_text)

        # 현재 본문 보관
        current = self._store.read(name)
        current_text = (self._paths.skills / f"{name}.md").read_text(encoding="utf-8")
        self._store.archive_to_history(name, current.version, current_text)

        # 복원: version 은 현재+1
        new_version = current.version + 1
        restored = SkillData(
            name=name,
            version=new_version,
            description=hist_data.description,
            state=current.state,  # 현재 상태 유지
            body=hist_data.body,
            tags=hist_data.tags,
            source=hist_data.source,
            created_at=current.created_at,
            updated_at=now_iso(),
        )
        self._store.write(restored)
        return new_version
