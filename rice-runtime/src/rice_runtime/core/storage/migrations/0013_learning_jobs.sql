-- 출품 전 P0 (2026-07-03): post-turn learning-review의 서버 소유 작업 큐.
--
-- 지금까지 /learning-review는 프론트가 메인 턴 SSE에서 tool_call 이벤트를 세어
-- tool_calls_made를 다시 보내는 구조였다 — 이벤트 누락, 재시도(이중 리뷰), 탭
-- 종료(리뷰 증발)에 취약했다. 이제 턴 종료 시 서버(observe())가 TurnOutcome을
-- 여기 영속화하고, 리뷰는 pending 잡을 원자적으로 claim해서 실행한다:
--   - 중복 POST /learning-review → 두 번째 claim이 실패 → no-op (exactly-once)
--   - 탭 종료 → 스케줄러 sweep이 오래된 pending 잡을 집어 제안함으로 라우팅
--
-- status: pending(턴 종료, 리뷰 대기) → running(claim됨) → done(리뷰 완료
--         또는 게이트 스킵) | failed(엔진 오류)
CREATE TABLE IF NOT EXISTS learning_jobs (
  id              TEXT PRIMARY KEY,
  session_id      TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  kind            TEXT NOT NULL DEFAULT 'review',
  status          TEXT NOT NULL DEFAULT 'pending',
  tool_calls_made INTEGER NOT NULL DEFAULT 0,
  claimed_at      TEXT,
  finished_at     TEXT,
  detail          TEXT              -- JSON (효과 수, 게이트 결정, 오류 등)
);
CREATE INDEX IF NOT EXISTS idx_learning_jobs_session ON learning_jobs(session_id, status);
CREATE INDEX IF NOT EXISTS idx_learning_jobs_status ON learning_jobs(status, created_at);
