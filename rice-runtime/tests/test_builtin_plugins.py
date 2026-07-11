"""Builtin plugin assets, enable state, and mutation protection."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from rice_runtime.core.config import RuntimeArgs
from rice_runtime.core.errors import ValidationError
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.execution.plugin.builtin import (
    BUILTIN_PLUGIN_SOURCE,
    BuiltinPluginCatalog,
)
from rice_runtime.execution.plugin.loader import PluginLoader
from rice_runtime.execution.service import NoopSandbox
from rice_runtime.server.app import create_app
from rice_runtime.server.composition_root import build_container

BUILTIN_ID = "plugin:plugin-dev"
BUILTIN_SKILLS = {
    "agent-development",
    "command-development",
    "hook-development",
    "mcp-integration",
    "plugin-settings",
    "plugin-structure",
    "skill-development",
}


def _fresh_paths() -> RicePaths:
    return RicePaths.from_env(tempfile.mkdtemp(prefix="rice-builtin-plugin-test-")).ensure()


def _loader(paths: RicePaths) -> PluginLoader:
    db = Database(":memory:").connect()
    return PluginLoader(
        db=db,
        sandbox=NoopSandbox(),
        paths=paths,
        builtin_plugins=BuiltinPluginCatalog(paths),
    )


def test_catalog_loads_plugin_dev_from_package_assets():
    paths = _fresh_paths()
    catalog = BuiltinPluginCatalog(paths)
    info = catalog.get(BUILTIN_ID)

    assert info is not None
    assert info.name == "plugin-dev"
    assert info.source == BUILTIN_PLUGIN_SOURCE
    assert info.enabled is True
    assert info.skill_count == 7
    assert set(info.skill_names) == BUILTIN_SKILLS
    assert catalog.asset_path(BUILTIN_ID).is_relative_to(Path(__file__).parents[1] / "src")
    assert not (paths.plugins / BUILTIN_ID).exists()


def test_enabled_state_persists_but_asset_remains_immutable():
    paths = _fresh_paths()
    catalog = BuiltinPluginCatalog(paths)
    catalog.set_enabled(BUILTIN_ID, False)

    assert BuiltinPluginCatalog(paths).get(BUILTIN_ID).enabled is False
    assert (paths.plugins / ".builtin-states.json").is_file()
    assert not (paths.plugins / BUILTIN_ID).exists()


def test_loader_injects_builtin_skills_and_disable_hides_them():
    paths = _fresh_paths()
    loader = _loader(paths)

    assert BUILTIN_SKILLS == {s.name for s in loader.injected_skills()}
    loader.set_enabled(BUILTIN_ID, False)
    assert loader.injected_skills() == []


@pytest.mark.asyncio
async def test_builtin_plugin_cannot_be_reinstalled_or_removed(tmp_path: Path):
    paths = _fresh_paths()
    loader = _loader(paths)
    manifest = {
        "schema_version": 1,
        "type": "plugin",
        "name": "plugin-dev",
        "version": "9.9.9",
    }

    with pytest.raises(ValidationError, match="빌트인 플러그인"):
        await loader.install(path=str(tmp_path), manifest=manifest)
    with pytest.raises(ValidationError, match="빌트인 플러그인"):
        await loader.remove(BUILTIN_ID)


def test_stale_installed_copy_is_shadowed_by_builtin():
    paths = _fresh_paths()
    loader = _loader(paths)
    loader._db.execute(
        "INSERT INTO installed_artifacts (id, type, name, version, source, enabled) "
        "VALUES (?, 'plugin', 'plugin-dev', '9.9.9', 'market', 1)",
        (BUILTIN_ID,),
    )

    matches = [p for p in loader.list_plugins() if p.id == BUILTIN_ID]
    assert len(matches) == 1
    assert matches[0].source == BUILTIN_PLUGIN_SOURCE
    assert matches[0].version == "1.0.0"


def test_routes_expose_toggle_and_reject_delete():
    paths = _fresh_paths()
    container = build_container(RuntimeArgs(token=""), paths=paths)
    from fastapi.testclient import TestClient

    client = TestClient(create_app(container))
    host = {"host": "localhost"}
    try:
        listed = client.get("/plugins", headers=host).json()["data"]
        plugin = next(p for p in listed if p["id"] == BUILTIN_ID)
        assert plugin["source"] == BUILTIN_PLUGIN_SOURCE

        toggled = client.patch(
            f"/plugins/{BUILTIN_ID}/enabled", json={"enabled": False}, headers=host
        )
        assert toggled.status_code == 200
        assert container.plugins.get_plugin(BUILTIN_ID).enabled is False

        deleted = client.delete(f"/plugins/{BUILTIN_ID}", headers=host)
        assert deleted.status_code == 422
        assert container.plugins.get_plugin(BUILTIN_ID) is not None
    finally:
        container.shutdown()
