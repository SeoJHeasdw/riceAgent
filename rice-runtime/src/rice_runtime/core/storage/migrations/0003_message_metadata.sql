-- 0003_message_metadata — persist per-message metadata (JSON).
-- Carries the model's reasoning trace (assistant `thinking`) and the
-- tool_call_id linkage on tool-result rows, so a reloaded conversation can
-- reconstruct the full agentic trace (thinking ↔ tool call ↔ result), not just
-- user/assistant text. Single-user app → cheap to store everything.
ALTER TABLE messages ADD COLUMN metadata TEXT;  -- JSON object; NULL = none
