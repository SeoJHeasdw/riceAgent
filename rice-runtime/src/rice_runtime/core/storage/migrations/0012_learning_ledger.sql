-- Self-Improvement v3 (2026-07-03): learning ledger + suggestion inbox.
--
-- learning_events  — append-only audit of the learning loop's own lifecycle:
--                    무엇이 제안됐고(proposed) 사용자가 어떻게 결정했는지
--                    (approved/rejected/timeout), 메모리에 뭐가 쓰였는지
--                    (memory_written/memory_deleted), 발굴 패스가 언제 돌았는지
--                    (discovery_run). 이 기록이 있어야 루프가 "자기 결정 이력"
--                    에서 배울 수 있다(반복 거부 쿨다운, 프롬프트 회피 목록).
-- skill_suggestions — 파킹(스트림) 밖에서 살아남는 영속 제안함. 타임아웃된
--                    HITL 제안, 백그라운드 발굴(discovery) 산출물이 들어오고
--                    사용자가 자기 페이스로 승인/거부한다.
--
-- `trigger`는 SQLite 예약어라 컬럼명은 trigger_code 로 둔다.

CREATE TABLE IF NOT EXISTS learning_events (
  id           TEXT PRIMARY KEY,
  ts           TEXT NOT NULL,
  kind         TEXT NOT NULL,   -- proposed|approved|rejected|timeout|applied|memory_written|memory_deleted|discovery_run|suggestion_added|suggestion_decided
  skill_id     TEXT,
  session_id   TEXT,
  origin       TEXT,            -- review|learn|agent|discovery|consolidation|user
  trigger_code TEXT,            -- T1a|T1b|T1c|T3|T4|T5 (patch trigger, when applicable)
  detail       TEXT             -- JSON blob (changelog, counts, …)
);
CREATE INDEX IF NOT EXISTS idx_learning_events_skill ON learning_events(skill_id, ts);
CREATE INDEX IF NOT EXISTS idx_learning_events_ts ON learning_events(ts);

CREATE TABLE IF NOT EXISTS skill_suggestions (
  id         TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  origin     TEXT NOT NULL,     -- review|learn|agent|discovery|consolidation|timeout
  kind       TEXT NOT NULL,     -- create|patch|archive
  skill_id   TEXT NOT NULL,
  payload    TEXT NOT NULL,     -- JSON: PatchProposal(직렬화) 또는 ArchiveProposal
  status     TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|expired
  decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_skill_suggestions_status ON skill_suggestions(status, created_at);
