-- 0001_init — state.db core schema (RUNTIME_DESIGN §7).
-- Forward-only. Tables align with the artifact manifest + installed_artifacts
-- (CONTRACTS §2.1). FTS5 backs session/memory retrieval (RUNTIME_DESIGN §3) —
-- the deliberately-soft retrieval center diagnosed via skill_hit (LEARNING_EVAL).

-- ── sessions / messages ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
  id          TEXT PRIMARY KEY,
  title       TEXT,
  model       TEXT,
  started_at  TEXT DEFAULT (datetime('now')),
  ended_at    TEXT,
  archived    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  role        TEXT NOT NULL,                 -- user | assistant | system | tool
  content     TEXT NOT NULL DEFAULT '',
  tool_calls  TEXT,                          -- JSON array of normalized ToolCall
  ts          INTEGER NOT NULL               -- epoch ms
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, ts);

-- FTS5 over message content for cross-session search (session_search).
-- External-content table mirrors messages(content); kept in sync via triggers.
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  content,
  content='messages',
  content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
  INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;

-- ── memories (facts / preferences) ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS memories (
  id                TEXT PRIMARY KEY,
  kind              TEXT NOT NULL,           -- fact | preference
  content           TEXT NOT NULL,
  created_at        TEXT DEFAULT (datetime('now')),
  source_session_id TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
  content,
  content='memories',
  content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
END;

-- ── installed artifacts (MySkills / MyPlugins) — CONTRACTS §2.1 alignment ────
CREATE TABLE IF NOT EXISTS installed_artifacts (
  id            TEXT PRIMARY KEY,
  type          TEXT NOT NULL,               -- skill | plugin
  name          TEXT NOT NULL,
  version       TEXT NOT NULL,
  source        TEXT,                        -- session:<id> | plugin:<name> | market
  installed_at  TEXT DEFAULT (datetime('now'))
);

-- ── attachments (future: multimodal input, CONTRACTS 1-C) ────────────────────
CREATE TABLE IF NOT EXISTS attachments (
  id          TEXT PRIMARY KEY,
  message_id  TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,
  path        TEXT NOT NULL,
  created_at  TEXT DEFAULT (datetime('now'))
);

-- ── connectors (RUNTIME_DESIGN §7, §9) — credentials live in OS keychain ─────
CREATE TABLE IF NOT EXISTS connectors (
  id               TEXT PRIMARY KEY,
  connector_type   TEXT NOT NULL,            -- 'gmail' (prototype)
  display_name     TEXT,
  status           TEXT NOT NULL DEFAULT 'disconnected',  -- connected|disconnected|error
  connected_at     TEXT,
  last_verified_at TEXT
);

CREATE TABLE IF NOT EXISTS connector_credentials (
  connector_id     TEXT PRIMARY KEY REFERENCES connectors(id) ON DELETE CASCADE,
  token_expires_at TEXT,
  scopes           TEXT                      -- JSON array; tokens themselves NOT here
);
