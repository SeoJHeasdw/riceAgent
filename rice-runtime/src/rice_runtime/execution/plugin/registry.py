"""plugin/registry.py — enabled/disabled state management for installed plugins.

WHY: The `installed_artifacts` table records *what* is installed; this module
tracks *whether* it is active (enabled). Separating state from the loader keeps
install/remove logic clean and lets routes toggle a plugin without re-loading it.

enabled state is stored in the DB column `enabled` (added in migration 0002).
`PluginRegistry` is a thin wrapper so service.py doesn't embed raw SQL.
"""

from __future__ import annotations

from ...core.storage.sqlite import Database


class PluginRegistry:
    """Manages the enabled/disabled state of installed plugins.

    All mutations go through the DB so state survives restarts. The in-memory
    caching of the entire plugin list is intentionally avoided — the plugin
    count is tiny (demo scale) and DB reads are fast.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def is_enabled(self, plugin_id: str) -> bool:
        """Return True if the plugin exists and is enabled."""
        row = self._db.query_one(
            "SELECT enabled FROM installed_artifacts WHERE id = ? AND type = 'plugin'",
            (plugin_id,),
        )
        if row is None:
            return False
        return bool(row["enabled"])

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        """Flip the enabled flag. Silently does nothing if plugin_id unknown."""
        self._db.execute(
            "UPDATE installed_artifacts SET enabled = ? WHERE id = ? AND type = 'plugin'",
            (1 if enabled else 0, plugin_id),
        )

    def list_enabled_ids(self) -> list[str]:
        """Return ids of all enabled plugins (used by loader to surface tools/skills)."""
        rows = self._db.query_all(
            "SELECT id FROM installed_artifacts WHERE type = 'plugin' AND enabled = 1"
        )
        return [r["id"] for r in rows]
