-- SaaS provider models registered by the user (model + API key).
-- Local single-user app: the key is stored here in state.db (see GET/PUT /settings).
-- NOTE: plaintext at rest — same tier as any local file on this machine. The
-- more-secure option (OS keychain) is tracked separately; this is the chosen
-- store for the local prototype.
CREATE TABLE IF NOT EXISTS provider_models (
  model_id       TEXT PRIMARY KEY,
  provider       TEXT,                     -- 'anthropic' | 'openai' | 'google' | 'custom'
  provider_label TEXT,                     -- display label, e.g. 'Anthropic'
  api_key        TEXT NOT NULL DEFAULT '',
  created_at     TEXT DEFAULT (datetime('now'))
);
