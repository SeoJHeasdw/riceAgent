-- 0010_custom_connectors — 사용자가 런타임에 "커스텀 추가"로 등록하는 원격 MCP 커넥터.
--
-- registry.py 의 정적 ConnectorDescriptor(gmail/calendar/drive) 는 코드에 박혀 있지만,
-- 커스텀 커넥터는 실행 중에 생기므로 DB 에 저장한다. client_secret 은 여기 두지 않는다
-- (키체인 rice.secret.custom_client:<connector_type> — RUNTIME_DESIGN §10.4, DB/로그 금지).
CREATE TABLE IF NOT EXISTS custom_connector_descriptors (
  connector_type          TEXT PRIMARY KEY,
  display_name            TEXT NOT NULL,
  auth_url                TEXT NOT NULL,
  token_url                TEXT NOT NULL,
  mcp_endpoint             TEXT NOT NULL,
  scopes_json              TEXT NOT NULL DEFAULT '[]',
  client_id                TEXT NOT NULL,
  registration_endpoint    TEXT,
  created_at               TEXT NOT NULL DEFAULT (datetime('now'))
);
