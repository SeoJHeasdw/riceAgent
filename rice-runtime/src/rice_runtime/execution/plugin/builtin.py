"""Read-only plugins shipped as rice-runtime package assets.

Builtin plugins mirror the builtin-skill contract: their contents live in the
application package (never under ``~/.rice/plugins``), while the user may only
persist an enabled/disabled preference.  The plugin id/name namespace is
reserved so a marketplace install cannot shadow or replace a builtin.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path

from ...core.errors import ValidationError
from ...core.paths import RicePaths
from ..interfaces import PluginInfo

log = logging.getLogger("rice.execution.builtin_plugins")

__all__ = ["BUILTIN_PLUGIN_SOURCE", "BuiltinPluginCatalog"]

BUILTIN_PLUGIN_SOURCE = "builtin"
_ASSET_DIR = Path(__file__).parent / "builtin_plugins"


class BuiltinPluginCatalog:
    """Immutable plugin assets plus a user-local enable/disable overlay."""

    def __init__(self, paths: RicePaths, asset_dir: Path | None = None) -> None:
        self._paths = paths
        self._plugins: dict[str, tuple[PluginInfo, Path]] = {}
        asset_dir = asset_dir or _ASSET_DIR
        if not asset_dir.is_dir():
            return

        for plugin_dir in sorted(p for p in asset_dir.iterdir() if p.is_dir()):
            manifest_path = plugin_dir / ".claude-plugin" / "plugin.json"
            metadata_path = plugin_dir / "rice-builtin.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                metadata = (
                    json.loads(metadata_path.read_text(encoding="utf-8"))
                    if metadata_path.is_file()
                    else {}
                )
                name = str(manifest["name"])
                plugin_id = f"plugin:{name}"
                skills_dir = plugin_dir / "skills"
                flat_skills = sorted(skills_dir.glob("*.md"))
                nested_skills = sorted(skills_dir.glob("*/SKILL.md"))
                skill_names = tuple(
                    [path.stem for path in flat_skills]
                    + [path.parent.name for path in nested_skills]
                )
                author = manifest.get("author", {}) or {}
                author_name = author.get("name", "") if isinstance(author, dict) else author
                info = PluginInfo(
                    id=plugin_id,
                    name=name,
                    version=str(metadata.get("version", manifest.get("version", "1.0.0"))),
                    description=str(metadata.get("description", manifest.get("description", ""))),
                    enabled=True,
                    skill_count=len(skill_names),
                    skill_names=skill_names,
                    source=BUILTIN_PLUGIN_SOURCE,
                    author=str(author_name or ""),
                )
            except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                log.exception("builtin plugin asset unreadable: %s", plugin_dir)
                continue
            self._plugins[plugin_id] = (info, plugin_dir)

    def is_builtin(self, plugin_id_or_name: str) -> bool:
        plugin_id = (
            plugin_id_or_name
            if plugin_id_or_name.startswith("plugin:")
            else f"plugin:{plugin_id_or_name}"
        )
        return plugin_id in self._plugins

    def ids(self) -> frozenset[str]:
        return frozenset(self._plugins)

    def list(self) -> list[PluginInfo]:
        return [self._materialize(plugin_id) for plugin_id in sorted(self._plugins)]

    def get(self, plugin_id: str) -> PluginInfo | None:
        return self._materialize(plugin_id) if plugin_id in self._plugins else None

    def asset_path(self, plugin_id: str) -> Path | None:
        item = self._plugins.get(plugin_id)
        return item[1] if item else None

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        if plugin_id not in self._plugins:
            raise ValidationError(f"unknown builtin plugin: {plugin_id!r}")
        states = self._states()
        states[plugin_id] = {"enabled": bool(enabled)}
        self._write_states(states)

    def ensure_mutable(self, plugin_id_or_name: str, action: str) -> None:
        if self.is_builtin(plugin_id_or_name):
            name = plugin_id_or_name.removeprefix("plugin:")
            raise ValidationError(
                f"빌트인 플러그인 '{name}'은(는) {action}할 수 없습니다. 활성/비활성만 전환할 수 있습니다."
            )

    def _materialize(self, plugin_id: str) -> PluginInfo:
        info = self._plugins[plugin_id][0]
        override = self._states().get(plugin_id)
        enabled = info.enabled
        if isinstance(override, dict) and "enabled" in override:
            enabled = bool(override["enabled"])
        return dataclasses.replace(info, enabled=enabled)

    def _state_file(self) -> Path:
        return self._paths.plugins / ".builtin-states.json"

    def _states(self) -> dict:
        path = self._state_file()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_states(self, states: dict) -> None:
        self._paths.plugins.mkdir(parents=True, exist_ok=True)
        self._state_file().write_text(
            json.dumps(states, ensure_ascii=False, indent=2), encoding="utf-8"
        )
