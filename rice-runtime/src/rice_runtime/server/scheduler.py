"""server/scheduler.py — 백그라운드 curator + discovery 스케줄러 (RUNTIME_DESIGN §3).

수동 "스킬 정리"(POST /skills/curate)와 동일한 `learning.curate()` 를 일정 주기로
자동 실행해 stale/archived 전이가 앱이 떠 있는 동안 반영되게 한다(v1: 수동 트리거만).

Self-Improvement v3(2026-07-03): 크로스세션 발굴(discovery) 틱이 추가됐다 — 켜져
있으면(`discovery_interval_seconds` > 0) 주기적으로 최근 세션 다이제스트를 모아
`learning.discover()`를 돌리고, 산출된 신규 스킬 제안을 **제안함**(skill_suggestions)
에 쌓는다. 파킹할 스트림이 없는 백그라운드 경로라 사용자를 실시간으로 방해하지
않는다 — 사용자는 LearningPanel 제안함에서 자기 페이스로 결정한다. 기본은 꺼짐
(LLM 1회 호출 비용 — 수동 POST /skills/discover 만).

설계:
  - asyncio 태스크 하나. **sleep-first** — 부팅 직후가 아니라 한 주기 뒤부터 돈다
    (시작 비용 0, 테스트에서 즉시 부작용 없음).
  - curate() 는 동기 파일 I/O 라 `asyncio.to_thread` 로 돌려 이벤트 루프를 막지 않는다.
    discover() 는 애초에 async(LLM 호출)라 그대로 await 한다.
  - 개별 tick 실패는 삼키고 다음 주기로 — 스케줄러가 죽지 않게(fail-safe).
  - 주기는 매 tick `config_store` 에서 라이브로 읽는다(설정 변경 즉시 반영). interval<=0
    이면 비활성으로 보고 길게 잠들었다가 다시 확인한다.
  - 두 작업의 주기가 다르므로 60초 해상도의 단일 루프가 경과 시간을 각자 누적한다
    (태스크 두 개를 띄우는 것보다 단순하고, 초 단위 정밀도는 이 용도에 불필요).
"""

from __future__ import annotations

import asyncio
import logging

from ..prompts.discovery import build_session_digest

log = logging.getLogger("rice.scheduler")

_TICK_SECONDS = 60  # 루프 해상도 — 두 주기의 공약수 역할


def gather_recent_digests(container, *, max_sessions: int, digest_chars: int):
    """최근 세션들의 압축 다이제스트 — POST /skills/discover 라우트와 백그라운드
    틱이 공유한다. 메시지가 2개 미만인 세션(빈 껍데기)은 제외."""
    digests = []
    for info in container.sessions.list()[:max_sessions]:
        history = container.sessions.history(info.id)
        if len(history) < 2:
            continue
        digests.append(
            build_session_digest(info.id, info.title or "", history, max_chars=digest_chars)
        )
    return digests


class CuratorScheduler:
    """주기적으로 learning.curate()(+ 옵션 discover())를 호출하는 백그라운드 태스크."""

    def __init__(self, container) -> None:
        self._container = container
        self._task: asyncio.Task | None = None
        self._since_curate = 0
        self._since_discovery = 0
        self._since_review_sweep = 0
        self._since_analysis = 0

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
            await asyncio.sleep(_TICK_SECONDS)
            self._since_curate += _TICK_SECONDS
            self._since_discovery += _TICK_SECONDS
            self._since_review_sweep += _TICK_SECONDS
            self._since_analysis += _TICK_SECONDS
            cfg = self._container.config_store.config.learning

            if cfg.curator_interval_seconds and 0 < cfg.curator_interval_seconds <= self._since_curate:
                self._since_curate = 0
                try:
                    result = await asyncio.to_thread(self._container.learning.curate)
                    if result.transitioned:
                        log.info("curator auto-run transitioned %d skills", len(result.transitioned))
                except Exception as exc:  # noqa: BLE001
                    log.warning("curator auto-run failed: %s", exc)

            # 서버 소유 리뷰 잡 sweep: 탭 종료 등으로 클라이언트가 끝내
            # claim하지 않은 pending 잡을 백그라운드 실행 → 제안함 라우팅. 검사
            # 주기는 stale 임계값 자체를 쓴다(그보다 자주 볼 이유가 없다).
            if cfg.review_job_stale_seconds and 0 < cfg.review_job_stale_seconds <= self._since_review_sweep:
                self._since_review_sweep = 0
                try:
                    sessions = self._container.sessions
                    processed = await self._container.learning.sweep_review_jobs(
                        lambda sid: list(sessions.history(sid)),
                        older_than_seconds=cfg.review_job_stale_seconds,
                    )
                    if processed:
                        log.info("review sweep processed %d stale jobs", processed)
                except Exception as exc:  # noqa: BLE001
                    log.warning("review sweep failed: %s", exc)

            # 설명용 스킬 분석 드레인: 본문 변경(patch 승인/편집/rollback …)이
            # enqueue 한 재분석 잡을 실행한다. 수동 refresh 엔드포인트와 동일
            # 경로(refresh_skill_analysis)로 수렴하고, 사이드카만 쓴다.
            if cfg.analysis_interval_seconds and 0 < cfg.analysis_interval_seconds <= self._since_analysis:
                self._since_analysis = 0
                try:
                    done = await self._container.learning.run_pending_analyses(
                        limit=cfg.analysis_max_per_tick
                    )
                    if done:
                        log.info("skill analysis tick refreshed %d sidecar(s)", done)
                except Exception as exc:  # noqa: BLE001
                    log.warning("skill analysis tick failed: %s", exc)

            if cfg.discovery_interval_seconds and 0 < cfg.discovery_interval_seconds <= self._since_discovery:
                self._since_discovery = 0
                try:
                    digests = await asyncio.to_thread(
                        gather_recent_digests,
                        self._container,
                        max_sessions=cfg.discovery_max_sessions,
                        digest_chars=cfg.discovery_digest_chars,
                    )
                    added = await self._container.learning.discover(digests)
                    if added:
                        log.info("discovery auto-run added %d suggestions", len(added))
                except Exception as exc:  # noqa: BLE001
                    log.warning("discovery auto-run failed: %s", exc)
