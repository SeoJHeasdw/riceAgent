-- 0007_connector_pending_tools — 원격 도구 카탈로그 "업데이트 대기" 슬롯.
--
-- tools_json 은 현재 활성 카탈로그. 원격 MCP 서버가 도구를 추가/삭제했는지 감지하되
-- 즉시 반영하지 않으려면(사용자 확인 후 적용), 원격에서 새로 받은 목록을 여기 담아 두고
-- diff(active vs pending)를 UI 에 "업데이트 있음"으로 노출한다. 사용자가 적용하면
-- pending → active 로 승격하고 이 칼럼을 비운다.
ALTER TABLE connector_credentials ADD COLUMN pending_tools_json TEXT;  -- JSON array of ToolSpec | NULL
