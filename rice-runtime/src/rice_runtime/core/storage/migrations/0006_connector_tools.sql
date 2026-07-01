-- 0006_connector_tools — persist the remote MCP tool catalog per connector.
--
-- Connector tools are NOT hardcoded: they come from the connector's remote MCP
-- `tools/list`. Rather than calling the remote server on every app boot or every
-- turn, we fetch the catalog ONCE at connect-time (complete_oauth) and cache it
-- here as JSON. `list_tools()` then reads it locally (no network in the hot path).
-- The catalog is refreshed on reconnect (or an explicit resync).
ALTER TABLE connector_credentials ADD COLUMN tools_json TEXT;  -- JSON array of ToolSpec
