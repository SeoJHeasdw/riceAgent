-- 0002_plugin_enabled — add enabled column to installed_artifacts.
-- Needed by plugin/registry.py (execution module, RUNTIME_DESIGN §5).
-- Default 1 (enabled) so existing rows (skill artifacts from learning module)
-- are not inadvertently disabled by this migration.

ALTER TABLE installed_artifacts ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1;
