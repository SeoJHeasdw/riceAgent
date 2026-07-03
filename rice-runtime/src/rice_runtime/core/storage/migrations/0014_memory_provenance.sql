-- 메모리 출처 추적 + 격리(quarantine).
--
-- 메모리는 HITL 없이 매 턴 시스템 프롬프트에 재주입되는 유일한 학습 경로다.
-- 정규식 스캐너는 "악성 문장"만 잡고 "그럴듯한 거짓 사실"은 못 잡는다 —
-- Gmail/웹/도구 결과 같은 외부 콘텐츠가 섞인 턴에서 나온 메모리는 조용히
-- 영구 주입되면 안 된다. 그래서:
--   - derived_from_external = 1 이면 status='quarantined'로 저장되고 **주입되지
--     않는다**(prefetch가 active만 읽는다). 사용자가 승인해야 active가 된다.
--   - source_message_ids: 이 메모리가 근거로 삼은 메시지 id들(감사/undo용).
--   - status: active | quarantined.
ALTER TABLE memories ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE memories ADD COLUMN derived_from_external INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memories ADD COLUMN source_message_ids TEXT;  -- JSON 배열
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status, created_at);
