-- 0008_connector_tool_permissions — 커넥터 도구별 호출 정책 영속화.
--
-- UI 의 3단 토글(항상 허용/승인 필요/차단)이 여태 프론트 전용이라 실제 실행에 영향을
-- 주지 못했다. 여기 (connector, tool) 별 정책을 저장하고 dispatcher 가 이를 강제한다:
--   allow    → 승인 없이 실행
--   approval → HITL 승인 후 실행 (기존 requires_approval 기본)
--   block    → 실행 거부(호출 자체를 막음)
-- 미저장 도구는 이름 휴리스틱(읽기=allow, 그 외=approval)으로 기본값을 계산한다.
CREATE TABLE IF NOT EXISTS connector_tool_permissions (
  connector_id  TEXT NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
  tool_name     TEXT NOT NULL,
  permission    TEXT NOT NULL,   -- 'allow' | 'approval' | 'block'
  PRIMARY KEY (connector_id, tool_name)
);
