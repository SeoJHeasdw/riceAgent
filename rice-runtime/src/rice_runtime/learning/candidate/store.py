"""learning/candidate/store.py — skill_candidates 테이블 접근자 (LEARNING_LOOP §10.6).

책임(single): 승인됐지만 아직 증명되지 않은 스킬 제안(candidate)의 영속 상태
머신. 스킬 파일에는 절대 닿지 않는다 — promoted 전이를 결정하는 것은 서비스
(LearningService._promote_candidate)이고, 여기는 행의 CRUD 와 원자 전이만 있다.

상태 전이:
  pending ──claim──▶ running ──finish──▶ promoted | discarded | expired | failed
  running ──retry(상한 미만)──▶ pending
  부팅 시 running 고아는 pending 으로 복구된다(learning_jobs 와 동일 근거 —
  단일 프로세스라 부팅 시점 running 은 전부 고아).

ledger(learning_events)가 아니라 별도 테이블인 이유: ledger 는 append-only 감사
로그라 상태 전이(원자 claim, 재시도 카운터)를 표현할 수 없다. learning_jobs 를
재사용하지 않는 이유: 리뷰 잡은 세션 수명(미리뷰 구간 워터마크)이고 candidate 는
스킬 수명(baseline 충돌 검증)이라 병합 규칙이 전혀 다르다.
"""

from __future__ import annotations

import json
import logging

from ...core.storage.sqlite import Database
from ...core.types import new_id, now_iso

log = logging.getLogger("rice.learning.candidate.store")

# 한 스킬의 pending/running candidate 는 1개로 유지한다 — 같은 스킬에 후보가
# 줄서기 시작하면 어느 baseline 과 비교했는지가 흐려진다(제안함의 skill_id+kind
# 멱등과 같은 목적).
_TERMINAL = frozenset({"promoted", "discarded", "expired", "failed"})


class CandidateStore:
    """skill_candidates CRUD + 원자 상태 전이."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ── 쓰기 ─────────────────────────────────────────────────────────────────
    def add(
        self,
        *,
        skill_id: str,
        kind: str,
        baseline_version: int,
        baseline_body_hash: str | None,
        payload: dict,
    ) -> str | None:
        """candidate 행을 만든다. 같은 스킬의 미결(pending/running) candidate 가
        이미 있으면 None — 호출자(서비스)가 ConflictError 로 번역한다."""
        try:
            dup = self._db.query_one(
                "SELECT id FROM skill_candidates "
                "WHERE skill_id = ? AND status IN ('pending', 'running')",
                (skill_id,),
            )
            if dup is not None:
                return None
            cid = new_id("cand")
            self._db.execute(
                "INSERT INTO skill_candidates "
                "(id, created_at, skill_id, kind, status, baseline_version, baseline_body_hash, payload) "
                "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)",
                (
                    cid,
                    now_iso(),
                    skill_id,
                    kind,
                    baseline_version,
                    baseline_body_hash,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            return cid
        except Exception:  # noqa: BLE001 — 저장 실패는 호출자가 폴백을 결정한다
            log.warning("candidate add failed for %s", skill_id, exc_info=True)
            return None

    def claim(self, candidate_id: str) -> dict | None:
        """pending → running 원자 전이 후 행 반환. 경합/비pending 이면 None."""
        try:
            row = self._get_row(candidate_id)
            if row is None or row["status"] != "pending":
                return None
            cur = self._db.execute(
                "UPDATE skill_candidates SET status = 'running', claimed_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (now_iso(), candidate_id),
            )
            if cur.rowcount == 0:
                return None
            return self.get(candidate_id)
        except Exception:  # noqa: BLE001
            log.warning("candidate claim failed for %s", candidate_id, exc_info=True)
            return None

    def claim_for_decision(self, candidate_id: str) -> bool:
        """수동 결정(decide_candidate)용 claim — pending 뿐 아니라 평가 인프라
        실패로 failed 에 놓인 후보도 사용자가 직접 결정할 수 있어야 한다(탈출구).
        running(평가 진행 중)·이미 결정된 후보는 거부한다."""
        try:
            cur = self._db.execute(
                "UPDATE skill_candidates SET status = 'running', claimed_at = ? "
                "WHERE id = ? AND status IN ('pending', 'failed')",
                (now_iso(), candidate_id),
            )
            return cur.rowcount > 0
        except Exception:  # noqa: BLE001
            log.warning("candidate decision claim failed for %s", candidate_id, exc_info=True)
            return False

    def claim_next(self) -> dict | None:
        """가장 오래된 pending candidate 를 claim 한다 (스케줄러 드레인)."""
        row = self._db.query_one(
            "SELECT id FROM skill_candidates WHERE status = 'pending' "
            "ORDER BY created_at ASC, rowid ASC LIMIT 1"
        )
        if row is None:
            return None
        return self.claim(row["id"])

    def retry(self, candidate_id: str, *, max_attempts: int) -> bool:
        """평가 인프라 실패의 처분 — 상한 미만이면 pending 복귀(True), 도달이면
        failed 마감(False). retry_review_job 과 동일 계약."""
        try:
            attempt_limit = max(1, int(max_attempts))
            with self._db.transaction() as conn:
                row = conn.execute(
                    "SELECT attempts FROM skill_candidates WHERE id = ? AND status = 'running'",
                    (candidate_id,),
                ).fetchone()
                if row is None:
                    return False
                attempts = int(row["attempts"]) + 1
                if attempts >= attempt_limit:
                    conn.execute(
                        "UPDATE skill_candidates SET status = 'failed', decided_at = ?, "
                        "attempts = ?, report = ? WHERE id = ? AND status = 'running'",
                        (
                            now_iso(),
                            attempts,
                            json.dumps(
                                {"error": "eval_infra", "attempts": attempts},
                                ensure_ascii=False,
                            ),
                            candidate_id,
                        ),
                    )
                    return False
                cur = conn.execute(
                    "UPDATE skill_candidates SET status = 'pending', claimed_at = NULL, "
                    "attempts = ? WHERE id = ? AND status = 'running'",
                    (attempts, candidate_id),
                )
                return cur.rowcount > 0
        except Exception:  # noqa: BLE001
            log.warning("candidate retry failed for %s", candidate_id, exc_info=True)
            return False

    def finish(self, candidate_id: str, status: str, report: dict | None = None) -> bool:
        """running → 최종 상태 마감. 비running 이면 False (이중 결정 방지)."""
        if status not in _TERMINAL:
            raise ValueError(f"invalid terminal candidate status: {status!r}")
        try:
            cur = self._db.execute(
                "UPDATE skill_candidates SET status = ?, decided_at = ?, report = ? "
                "WHERE id = ? AND status = 'running'",
                (
                    status,
                    now_iso(),
                    json.dumps(report, ensure_ascii=False) if report else None,
                    candidate_id,
                ),
            )
            return cur.rowcount > 0
        except Exception:  # noqa: BLE001
            log.warning("candidate finish failed for %s", candidate_id, exc_info=True)
            return False

    def requeue_orphan_running(self) -> int:
        """부팅 시 1회 — 이전 프로세스가 남긴 running 고아를 pending 으로."""
        try:
            cur = self._db.execute(
                "UPDATE skill_candidates SET status = 'pending', claimed_at = NULL "
                "WHERE status = 'running'"
            )
            if cur.rowcount:
                log.info("requeued %d orphan running candidate(s)", cur.rowcount)
            return cur.rowcount
        except Exception:  # noqa: BLE001
            log.warning("candidate orphan requeue failed", exc_info=True)
            return 0

    # ── 읽기 ─────────────────────────────────────────────────────────────────
    def _get_row(self, candidate_id: str):
        return self._db.query_one(
            "SELECT id, created_at, skill_id, kind, status, baseline_version, "
            "baseline_body_hash, payload, attempts, claimed_at, decided_at, report "
            "FROM skill_candidates WHERE id = ?",
            (candidate_id,),
        )

    def get(self, candidate_id: str) -> dict | None:
        row = self._get_row(candidate_id)
        return self._row_to_dict(row) if row else None

    def list(self, limit: int = 50) -> list[dict]:
        """미결 우선(pending/running 먼저), 그 다음 최근 결정 순."""
        rows = self._db.query_all(
            "SELECT id, created_at, skill_id, kind, status, baseline_version, "
            "baseline_body_hash, payload, attempts, claimed_at, decided_at, report "
            "FROM skill_candidates "
            "ORDER BY CASE WHEN status IN ('pending', 'running') THEN 0 ELSE 1 END, "
            "created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(r) -> dict:
        def _decode(raw):
            if not raw:
                return None
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                return {"_decode_error": True}

        return {
            "id": r["id"],
            "created_at": r["created_at"],
            "skill_id": r["skill_id"],
            "kind": r["kind"],
            "status": r["status"],
            "baseline_version": int(r["baseline_version"]),
            "baseline_body_hash": r["baseline_body_hash"],
            "payload": _decode(r["payload"]) or {},
            "attempts": int(r["attempts"]),
            "claimed_at": r["claimed_at"],
            "decided_at": r["decided_at"],
            "report": _decode(r["report"]),
        }
