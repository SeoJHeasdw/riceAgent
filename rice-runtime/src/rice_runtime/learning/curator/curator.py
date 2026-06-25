"""curator/curator.py — 자동 시간 규칙 전이 + 수동 "스킬 정리".

책임(single): CurateResult를 반환하는 curate() 하나를 제공한다.
  - 스킬 디렉토리를 순회해 각 스킬의 경과 시간을 확인한다.
  - stale_after_days / archive_after_days 규칙에 맞는 스킬을 전이한다.
  - 전이된 (skill_id, new_state) 쌍을 CurateResult로 반환한다.

WHY: curate()는 수동 "스킬 정리" 버튼 경로(데모)와 향후 자동 전이 경로가 같은
로직을 공유한다. 로직을 여기에 격리하고 service.py 파사드가 노출하는 방식으로
분리한다 (LEARNING_LOOP §5.1, CONVENTIONS §4).

시간 단위는 반드시 day 단위로 유지한다. 데모 편의로 분 단위로 바꾸는 것은
명시적으로 금지되어 있다 (CONVENTIONS §2, learning.md "하지 말 것").
"""

from __future__ import annotations

from ...core.config import LearningConfig
from ...core.paths import RicePaths
from ..interfaces import CurateResult, SkillState
from ..skill.lifecycle import SkillLifecycle, compute_transition
from ..skill.store import SkillStore


class Curator:
    """시간 규칙 기반 상태 전이를 실행한다 (LEARNING_LOOP §5.1)."""

    def __init__(
        self,
        store: SkillStore,
        lifecycle: SkillLifecycle,
        config: LearningConfig,
    ) -> None:
        self._store = store
        self._lifecycle = lifecycle
        self._config = config

    def curate(self) -> CurateResult:
        """스킬 전체를 순회해 시간 규칙을 즉시 적용한다.

        반환: 전이된 스킬 목록 CurateResult(transitioned=((name, new_state), ...)).
        아무것도 전이되지 않으면 CurateResult(transitioned=()).

        수동 "스킬 정리" 버튼에서도 자동 스케줄러에서도 이 메서드를 호출한다.
        """
        skills = self._store.list()
        transitioned: list[tuple[str, SkillState]] = []

        for skill in skills:
            new_state = compute_transition(
                skill,
                stale_after_days=self._config.stale_after_days,
                archive_after_days=self._config.archive_after_days,
            )
            if new_state is None:
                continue
            try:
                self._lifecycle.set_state(skill.name, new_state)
                transitioned.append((skill.name, new_state))
            except Exception:
                # NOTE(agent): set_state 실패는 조용히 건너뛴다. 개별 파일 오류가
                # 전체 curate 결과를 망가뜨리지 않도록 fail-safe.
                pass

        return CurateResult(transitioned=tuple(transitioned))
