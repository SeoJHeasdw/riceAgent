"""learning/ledger.py — 학습 루프의 자기 이력 저장소 (Self-Improvement v3).

두 테이블을 소유한다(0012_learning_ledger.sql):

  1. **learning_events** — 제안/결정/적용/메모리 기록의 append-only 감사 로그.
     이 기록이 루프의 "메타학습" 원천이다: 같은 스킬에 대한 반복 거부를 세어
     제안을 쿨다운(`is_suppressed`)하고, 최근 거부 목록을 리뷰 프롬프트에 넣어
     LLM이 애초에 재제안하지 않게 한다(`recent_rejections`). 지금까지 제안→결정은
     in-memory 파킹에만 존재해 재시작·타임아웃과 함께 증발했다 — 시스템이 자기
     결정에서 아무것도 배울 수 없던 구조적 구멍을 이 모듈이 닫는다.

  2. **skill_suggestions** — 스트림 파킹 밖에서 살아남는 영속 **제안함(inbox)**.
     (a) HITL 카드가 타임아웃되면 제안을 버리는 대신 여기로 옮긴다(무응답 ≠ 거부
     의사표시 — 자리 비움일 수 있다. 적용은 여전히 안 하므로 safe-default는 유지).
     (b) 백그라운드 발굴(discovery)·통합처럼 애초에 파킹할 스트림이 없는 제안의
     수신처다. 프론트 LearningPanel "제안함"이 이 테이블을 읽는다.

모든 기록 메서드는 best-effort다 — 감사 로그 실패가 턴/리뷰를 깨서는 안 된다
(usage 카운터의 기존 fail-safe 원칙과 동일).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from ..core.storage.sqlite import Database
from ..core.types import new_id, now_iso

log = logging.getLogger("rice.learning.ledger")

# 제안함 크기 상한 — 발굴 패스가 폭주해도 인박스가 무한히 쌓이지 않게 하는
# 마지막 방어선(skill_max_body_chars와 같은 성격). 넘으면 add가 조용히 거절.
DEFAULT_PENDING_CAP = 30


def _cutoff_iso(hours: float) -> str:
    """now_iso()와 동일 포맷(UTC, 초 단위)의 과거 시각 — 문자열 비교로 시간창 필터."""
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class LearningLedger:
    """learning_events + skill_suggestions 접근자."""

    def __init__(self, db: Database, *, pending_cap: int = DEFAULT_PENDING_CAP) -> None:
        self._db = db
        self._pending_cap = pending_cap

    # ── events (append-only audit) ────────────────────────────────────────────
    def record(
        self,
        kind: str,
        *,
        skill_id: str | None = None,
        session_id: str | None = None,
        origin: str | None = None,
        trigger: str | None = None,
        detail: dict | None = None,
    ) -> None:
        try:
            self._db.execute(
                "INSERT INTO learning_events (id, ts, kind, skill_id, session_id, origin, trigger_code, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id("lev"),
                    now_iso(),
                    kind,
                    skill_id,
                    session_id,
                    origin,
                    trigger,
                    json.dumps(detail, ensure_ascii=False) if detail else None,
                ),
            )
        except Exception:  # noqa: BLE001 — 감사 실패는 삼킨다(fail-safe)
            log.warning("ledger record failed (kind=%s, skill=%s)", kind, skill_id, exc_info=True)

    def events(self, limit: int = 100) -> list[dict]:
        rows = self._db.query_all(
            "SELECT id, ts, kind, skill_id, session_id, origin, trigger_code, detail "
            "FROM learning_events ORDER BY ts DESC, rowid DESC LIMIT ?",
            (limit,),
        )
        out = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "ts": r["ts"],
                    "kind": r["kind"],
                    "skill_id": r["skill_id"],
                    "session_id": r["session_id"],
                    "origin": r["origin"],
                    "trigger": r["trigger_code"],
                    "detail": json.loads(r["detail"]) if r["detail"] else None,
                }
            )
        return out

    # ── meta-learning reads ───────────────────────────────────────────────────
    def rejection_count(self, skill_id: str, *, within_hours: float) -> int:
        row = self._db.query_one(
            "SELECT COUNT(*) AS n FROM learning_events "
            "WHERE skill_id = ? AND kind = 'rejected' AND ts >= ?",
            (skill_id, _cutoff_iso(within_hours)),
        )
        return int(row["n"]) if row else 0

    def is_suppressed(self, skill_id: str, *, within_hours: float, max_rejections: int) -> bool:
        """쿨다운 창 안에서 max_rejections번 이상 거부된 스킬이면 True — 리뷰/발굴이
        이 스킬에 대한 제안을 만들지 않는다(설계문서 §5.2.4 '(향후) 반복 거부 시
        제안 빈도 하향'의 구현)."""
        if max_rejections <= 0:
            return False
        return self.rejection_count(skill_id, within_hours=within_hours) >= max_rejections

    def recent_rejections(self, *, within_hours: float, limit: int = 10) -> list[dict]:
        """최근 거부된 제안 (skill_id + changelog) — 리뷰 프롬프트의 '재제안 금지'
        블록에 들어간다. 사용자가 이미 '아니'라고 한 것을 LLM이 다시 들이밀지
        않게 하는 것이 메타학습의 첫 단계다."""
        rows = self._db.query_all(
            "SELECT skill_id, detail FROM learning_events "
            "WHERE kind = 'rejected' AND ts >= ? ORDER BY ts DESC LIMIT ?",
            (_cutoff_iso(within_hours), limit),
        )
        out = []
        for r in rows:
            detail = json.loads(r["detail"]) if r["detail"] else {}
            out.append({"skill_id": r["skill_id"], "changelog": detail.get("changelog", "")})
        return out

    # ── review jobs (server-owned post-turn review) ───────────────
    def enqueue_review_job(self, session_id: str, tool_calls_made: int) -> str | None:
        """턴 종료 시 observe()가 호출 — 이 세션의 리뷰 잡을 영속화한다.

        같은 세션의 pending 잡이 이미 있으면(이전 턴 리뷰가 아직 안 돌았다) 새 행을
        만들지 않고 병합한다 — 리뷰는 어차피 세션 히스토리 전체를 보므로 잡 하나면
        충분하고, 이렇게 해야 빠르게 이어진 턴들이 잡을 도배하지 않는다.

        병합 시 tool_calls_made는 **MAX를 보존**한다(2026-07-03 적대 리뷰 High#2):
        복잡한 턴(5콜) 직후 짧은 턴(0콜)이 오면 최신 값으로 덮는 순간 게이트가
        0만 보고 skip해 앞 턴의 학습 기회가 지워졌다. 잡은 "아직 리뷰 안 된 구간의
        워터마크"이므로 그 구간에서 가장 리뷰 가치가 높았던 턴 기준으로 판단해야
        한다. Best-effort(fail-safe)."""
        try:
            dup = self._db.query_one(
                "SELECT id FROM learning_jobs WHERE session_id = ? AND status = 'pending' AND kind = 'review'",
                (session_id,),
            )
            if dup is not None:
                self._db.execute(
                    "UPDATE learning_jobs SET tool_calls_made = MAX(tool_calls_made, ?), created_at = ? WHERE id = ?",
                    (tool_calls_made, now_iso(), dup["id"]),
                )
                return dup["id"]
            job_id = new_id("job")
            self._db.execute(
                "INSERT INTO learning_jobs (id, session_id, created_at, kind, status, tool_calls_made) "
                "VALUES (?, ?, ?, 'review', 'pending', ?)",
                (job_id, session_id, now_iso(), tool_calls_made),
            )
            return job_id
        except Exception:  # noqa: BLE001
            log.warning("enqueue_review_job failed for %s", session_id, exc_info=True)
            return None

    def claim_review_job(self, session_id: str) -> dict | None:
        """pending 잡을 원자적으로 running으로 전이하고 반환한다. 없으면(이미
        claim됨/실행됨/애초에 없음) None — 이 None이 exactly-once의 근거다:
        중복 POST /learning-review의 두 번째 호출은 여기서 no-op이 된다."""
        try:
            row = self._db.query_one(
                "SELECT id, session_id, tool_calls_made FROM learning_jobs "
                "WHERE session_id = ? AND status = 'pending' AND kind = 'review' "
                "ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            )
            if row is None:
                return None
            cur = self._db.execute(
                "UPDATE learning_jobs SET status = 'running', claimed_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (now_iso(), row["id"]),
            )
            if cur.rowcount == 0:  # 동시 claim 경합에서 짐 — 상대가 실행한다
                return None
            return {
                "id": row["id"],
                "session_id": row["session_id"],
                "tool_calls_made": int(row["tool_calls_made"]),
            }
        except Exception:  # noqa: BLE001
            log.warning("claim_review_job failed for %s", session_id, exc_info=True)
            return None

    def finish_review_job(self, job_id: str, status: str, detail: dict | None = None) -> None:
        """running → done|failed 마감. Best-effort."""
        try:
            self._db.execute(
                "UPDATE learning_jobs SET status = ?, finished_at = ?, detail = ? WHERE id = ?",
                (status, now_iso(), json.dumps(detail, ensure_ascii=False) if detail else None, job_id),
            )
        except Exception:  # noqa: BLE001
            log.warning("finish_review_job failed for %s", job_id, exc_info=True)

    def stale_pending_review_jobs(self, *, older_than_seconds: int, limit: int = 5) -> list[dict]:
        """탭 종료 등으로 클라이언트가 끝내 claim하지 않은 pending 잡 — 스케줄러
        sweep이 백그라운드로 실행해 제안함으로 라우팅한다. (running 상태로 방치된
        잡은 재수확하지 않는다 — 로컬 단일 프로세스에서 crash-mid-review는 드물고,
        잘못 재실행하면 이중 리뷰가 된다. best-effort 손실로 감수.)"""
        cutoff = _cutoff_iso(older_than_seconds / 3600)
        rows = self._db.query_all(
            "SELECT id, session_id, tool_calls_made FROM learning_jobs "
            "WHERE status = 'pending' AND kind = 'review' AND created_at < ? "
            "ORDER BY created_at ASC LIMIT ?",
            (cutoff, limit),
        )
        return [
            {"id": r["id"], "session_id": r["session_id"], "tool_calls_made": int(r["tool_calls_made"])}
            for r in rows
        ]

    def requeue_orphan_running_jobs(self) -> int:
        """부팅 시 1회 호출 — 이전 프로세스가 crash 하며 'running' 으로 남긴 리뷰
        잡을 pending 으로 되돌린다. 단일 프로세스 런타임이므로 **부팅 시점**의
        running 은 전부 고아라 판별이 정확하다(별도 lease/heartbeat 불필요).
        재실행돼도 제안함(add_suggestion)이 skill_id+kind 로 멱등이라 이중 리뷰의
        해가 유계다. 반환: 되돌린 잡 수."""
        try:
            cur = self._db.execute(
                "UPDATE learning_jobs SET status = 'pending', claimed_at = NULL "
                "WHERE status = 'running' AND kind = 'review'"
            )
            if cur.rowcount:
                log.info("requeued %d orphan running review job(s) from previous process", cur.rowcount)
            return cur.rowcount
        except Exception:  # noqa: BLE001
            log.warning("requeue_orphan_running_jobs failed", exc_info=True)
            return 0

    def claim_job_by_id(self, job_id: str) -> bool:
        """sweep 경로의 claim — id 지정 pending → running 원자 전이."""
        try:
            cur = self._db.execute(
                "UPDATE learning_jobs SET status = 'running', claimed_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (now_iso(), job_id),
            )
            return cur.rowcount > 0
        except Exception:  # noqa: BLE001
            return False

    # ── suggestion inbox ─────────────────────────────────────────────────────
    def add_suggestion(self, *, origin: str, kind: str, skill_id: str, payload: dict) -> str | None:
        """제안함에 넣는다. 같은 skill_id+kind의 pending 제안이 이미 있으면 중복
        생성하지 않고 None(멱등 — 발굴 패스가 여러 번 돌아도 인박스가 도배되지
        않는다). pending 총량이 cap을 넘으면 조용히 거절한다."""
        try:
            dup = self._db.query_one(
                "SELECT id FROM skill_suggestions WHERE skill_id = ? AND kind = ? AND status = 'pending'",
                (skill_id, kind),
            )
            if dup is not None:
                return None
            count = self._db.query_one(
                "SELECT COUNT(*) AS n FROM skill_suggestions WHERE status = 'pending'"
            )
            if count is not None and int(count["n"]) >= self._pending_cap:
                log.info("suggestion inbox full (cap=%d) — dropped %s/%s", self._pending_cap, kind, skill_id)
                return None
            sid = new_id("sug")
            self._db.execute(
                "INSERT INTO skill_suggestions (id, created_at, origin, kind, skill_id, payload) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sid, now_iso(), origin, kind, skill_id, json.dumps(payload, ensure_ascii=False)),
            )
            self.record("suggestion_added", skill_id=skill_id, origin=origin, detail={"kind": kind})
            return sid
        except Exception:  # noqa: BLE001
            log.warning("add_suggestion failed for %s", skill_id, exc_info=True)
            return None

    def suggestions(self, status: str = "pending", limit: int = 50) -> list[dict]:
        rows = self._db.query_all(
            "SELECT id, created_at, origin, kind, skill_id, payload, status, decided_at "
            "FROM skill_suggestions WHERE status = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (status, limit),
        )
        return [self._row_to_suggestion(r) for r in rows]

    def get_suggestion(self, suggestion_id: str) -> dict | None:
        row = self._db.query_one(
            "SELECT id, created_at, origin, kind, skill_id, payload, status, decided_at "
            "FROM skill_suggestions WHERE id = ?",
            (suggestion_id,),
        )
        return self._row_to_suggestion(row) if row else None

    def decide_suggestion(self, suggestion_id: str, status: str) -> bool:
        """pending → approved/rejected/expired 전이. pending이 아니었으면 False
        (이중 결정 방지 — approvals.resolve의 '이미 해소됨 → 409' 관례와 동일)."""
        cur = self._db.execute(
            "UPDATE skill_suggestions SET status = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
            (status, now_iso(), suggestion_id),
        )
        return cur.rowcount > 0

    @staticmethod
    def _row_to_suggestion(r) -> dict:
        return {
            "id": r["id"],
            "created_at": r["created_at"],
            "origin": r["origin"],
            "kind": r["kind"],
            "skill_id": r["skill_id"],
            "payload": json.loads(r["payload"]),
            "status": r["status"],
            "decided_at": r["decided_at"],
        }
