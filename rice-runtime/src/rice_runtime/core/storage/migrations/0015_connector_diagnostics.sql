-- 원격 MCP는 tools/list가 성공해도 tools/call만 거부될 수 있다.
-- 마지막 실행 검증 오류를 저장해 "connected" 오탐 대신 복구 가능한 원인을 UI에 노출한다.
-- status는 connected|disconnected|verifying|error. verifying은 OAuth 성공 후 실제
-- read-only MCP tool probe가 끝나기 전의 비-terminal 상태다.
ALTER TABLE connectors ADD COLUMN error_code TEXT;
ALTER TABLE connectors ADD COLUMN error_detail TEXT;
