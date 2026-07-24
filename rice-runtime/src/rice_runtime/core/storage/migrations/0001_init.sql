-- RICE state.db baseline.
--
-- This is the first public-release schema. Future migrations start at 0002,
-- and this file must remain immutable once external users can own a state.db.

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
  ts          INTEGER NOT NULL,              -- epoch ms
  metadata    TEXT                           -- JSON object
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, ts);

-- External-content FTS table kept in sync with messages(content).
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  content,
  content='messages',
  content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content)
  VALUES ('delete', old.rowid, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content)
  VALUES ('delete', old.rowid, old.content);
  INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TABLE IF NOT EXISTS attachments (
  id          TEXT PRIMARY KEY,
  message_id  TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,
  path        TEXT NOT NULL,
  created_at  TEXT DEFAULT (datetime('now'))
);

-- ── memories ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS memories (
  id                    TEXT PRIMARY KEY,
  kind                  TEXT NOT NULL,       -- fact | preference
  content               TEXT NOT NULL,
  created_at            TEXT DEFAULT (datetime('now')),
  source_session_id     TEXT,
  status                TEXT NOT NULL DEFAULT 'active',
  derived_from_external INTEGER NOT NULL DEFAULT 0,
  source_message_ids    TEXT                 -- JSON array
);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status, created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
  content,
  content='memories',
  content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, content)
  VALUES ('delete', old.rowid, old.content);
END;

-- ── installed artifacts / providers ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS installed_artifacts (
  id            TEXT PRIMARY KEY,
  type          TEXT NOT NULL,               -- skill | plugin
  name          TEXT NOT NULL,
  version       TEXT NOT NULL,
  source        TEXT,                        -- session:<id> | plugin:<name> | market
  installed_at  TEXT DEFAULT (datetime('now')),
  enabled       INTEGER NOT NULL DEFAULT 1,
  risk_flags    TEXT NOT NULL DEFAULT '[]'   -- JSON array
);

CREATE TABLE IF NOT EXISTS provider_models (
  model_id       TEXT PRIMARY KEY,
  provider       TEXT,                       -- anthropic | openai | google | custom
  provider_label TEXT,
  api_key        TEXT NOT NULL DEFAULT '',
  created_at     TEXT DEFAULT (datetime('now')),
  extra          TEXT                        -- provider-specific JSON
);

-- ── connectors ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS connectors (
  id               TEXT PRIMARY KEY,
  connector_type   TEXT NOT NULL,
  display_name     TEXT,
  status           TEXT NOT NULL DEFAULT 'disconnected',
  connected_at     TEXT,
  last_verified_at TEXT,
  error_code       TEXT,
  error_detail     TEXT
);

-- Access/refresh tokens remain in the OS keychain, never in this table.
CREATE TABLE IF NOT EXISTS connector_credentials (
  connector_id       TEXT PRIMARY KEY REFERENCES connectors(id) ON DELETE CASCADE,
  token_expires_at   TEXT,
  scopes             TEXT,                   -- JSON array
  tools_json         TEXT,                   -- active ToolSpec JSON array
  pending_tools_json TEXT                    -- pending ToolSpec JSON array
);

-- No FK: local connectors use connector_id="local:<name>" without a connectors row.
CREATE TABLE IF NOT EXISTS connector_tool_permissions (
  connector_id TEXT NOT NULL,
  tool_name    TEXT NOT NULL,
  permission   TEXT NOT NULL,                -- allow | approval | block
  PRIMARY KEY (connector_id, tool_name)
);

CREATE TABLE IF NOT EXISTS custom_connector_descriptors (
  connector_type       TEXT PRIMARY KEY,
  display_name         TEXT NOT NULL,
  auth_url             TEXT NOT NULL,
  token_url            TEXT NOT NULL,
  mcp_endpoint          TEXT NOT NULL,
  scopes_json           TEXT NOT NULL DEFAULT '[]',
  client_id             TEXT NOT NULL,
  registration_endpoint TEXT,
  created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS oauth_pending_states (
  state         TEXT PRIMARY KEY,
  connector_id  TEXT NOT NULL,
  code_verifier TEXT NOT NULL,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── learning ledger / jobs / evaluation ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS learning_events (
  id           TEXT PRIMARY KEY,
  ts           TEXT NOT NULL,
  kind         TEXT NOT NULL,
  skill_id     TEXT,
  session_id   TEXT,
  origin       TEXT,
  trigger_code TEXT,
  detail       TEXT                         -- JSON
);
CREATE INDEX IF NOT EXISTS idx_learning_events_skill
  ON learning_events(skill_id, ts);
CREATE INDEX IF NOT EXISTS idx_learning_events_ts
  ON learning_events(ts);

CREATE TABLE IF NOT EXISTS skill_suggestions (
  id         TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  origin     TEXT NOT NULL,
  kind       TEXT NOT NULL,                 -- create | patch | archive
  skill_id   TEXT NOT NULL,
  payload    TEXT NOT NULL,                 -- JSON
  status     TEXT NOT NULL DEFAULT 'pending',
  decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_skill_suggestions_status
  ON skill_suggestions(status, created_at);

CREATE TABLE IF NOT EXISTS learning_jobs (
  id              TEXT PRIMARY KEY,
  session_id      TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  kind            TEXT NOT NULL DEFAULT 'review',
  status          TEXT NOT NULL DEFAULT 'pending',
  tool_calls_made INTEGER NOT NULL DEFAULT 0,
  claimed_at      TEXT,
  finished_at     TEXT,
  detail          TEXT,                     -- JSON
  evidence        TEXT,                     -- JSON
  attempts        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_learning_jobs_session
  ON learning_jobs(session_id, status);
CREATE INDEX IF NOT EXISTS idx_learning_jobs_status
  ON learning_jobs(status, created_at);

CREATE TABLE IF NOT EXISTS skill_candidates (
  id                 TEXT PRIMARY KEY,
  created_at         TEXT NOT NULL,
  skill_id           TEXT NOT NULL,
  kind               TEXT NOT NULL,         -- create | patch
  status             TEXT NOT NULL DEFAULT 'pending',
  baseline_version   INTEGER NOT NULL DEFAULT 0,
  baseline_body_hash TEXT,
  payload            TEXT NOT NULL,         -- JSON
  attempts           INTEGER NOT NULL DEFAULT 0,
  claimed_at         TEXT,
  decided_at         TEXT,
  report             TEXT                   -- JSON
);
CREATE INDEX IF NOT EXISTS idx_skill_candidates_status
  ON skill_candidates(status, created_at);
CREATE INDEX IF NOT EXISTS idx_skill_candidates_skill
  ON skill_candidates(skill_id, status);

CREATE TABLE IF NOT EXISTS learning_episodes (
  id                 TEXT PRIMARY KEY,
  session_id         TEXT NOT NULL,
  created_at         TEXT NOT NULL,
  from_message_id    TEXT,
  through_message_id TEXT,
  outcome            TEXT NOT NULL,
  judgment           TEXT,                  -- JSON
  episode            TEXT NOT NULL          -- JSON
);
CREATE INDEX IF NOT EXISTS idx_learning_episodes_session
  ON learning_episodes(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_learning_episodes_outcome
  ON learning_episodes(outcome, created_at);

-- ── background terminal tasks ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS background_tasks (
  id           TEXT PRIMARY KEY,
  session_id   TEXT NOT NULL,
  command      TEXT NOT NULL,
  cwd          TEXT,
  status       TEXT NOT NULL,                -- running | exited | killed | interrupted
  exit_code    INTEGER,
  started_at   TEXT NOT NULL,
  ended_at     TEXT,
  output_path  TEXT,
  acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_background_tasks_session
  ON background_tasks(session_id, acknowledged);
CREATE INDEX IF NOT EXISTS idx_background_tasks_status
  ON background_tasks(status);
