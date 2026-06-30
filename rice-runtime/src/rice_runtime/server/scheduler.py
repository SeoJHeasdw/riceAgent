"""server/scheduler.py — 백그라운드 curator 스케줄러 (RUNTIME_DESIGN §3).

수동 "스킬 정리"(POST /skills/curate)와 동일한 `learning.curate()` 를 일정 주기로
자동 실행해 stale/archived 전이가 앱이 떠 있는 동안 반영되게 한다(v1: 수동 트리거만).

설계:
  - asyncio 태스크 하나. **sleep-first** — 부팅 직후가 아니라 한 주기 뒤부터 돈다
    (시작 비용 0, 테스트에서 즉시 부작용 없음).
  - curate() 는 동기 파일 I/O 라 `asyncio.to_thread` 로 돌려 이벤트 루프를 막지 않는다.
  - 개별 tick 실패는 삼키고 다음 주기로 — 스케줄러가 죽지 않게(fail-safe).
  - 주기는 매 tick `config_store` 에서 라이브로 읽는다(설정 변경 즉시 반영). interval<=0
    이면 비활성으로 보고 길게 잠들었다가 다시 확인한다.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("rice.scheduler")


class CuratorScheduler:
    """주기적으로 learning.curate() 를 호출하는 백그라운드 태스크 관리자."""

    def __init__(self, container) -> None:
        self._container = container
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._loop())
            log.info("curator scheduler started")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            interval = self._container.config_store.config.learning.curator_interval_seconds
            # 비활성(0) 이면 60초마다 설정만 재확인(폴링), 활성이면 그 주기로 잠든다.
            await asyncio.sleep(interval if interval and interval > 0 else 60)
            interval = self._container.config_store.config.learning.curator_interval_seconds
            if not interval or interval <= 0:
                continue
            try:
                result = await asyncio.to_thread(self._container.learning.curate)
                if result.transitioned:
                    log.info("curator auto-run transitioned %d skills", len(result.transitioned))
            except Exception as exc:  # noqa: BLE001
                log.warning("curator auto-run failed: %s", exc)
