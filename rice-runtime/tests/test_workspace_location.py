"""Workspace output-location persistence and sandbox bootstrap."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from rice_runtime.core.config import RuntimeArgs
from rice_runtime.core.paths import RicePaths
from rice_runtime.server.composition_root import build_container
from rice_runtime.server.routes.workspace import WorkspaceLocationIn, update_workspace_location


def test_workspace_location_is_queued_and_promoted_at_turn_boundary(tmp_path):
    """A folder change takes effect live (next turn), not after a restart.

    The route persists the choice and *queues* it on the live holder; the current
    root stays put until the orchestrator promotes it at a turn boundary. The
    choice is still written to config, so it also survives a restart.
    """
    paths = RicePaths.from_env(tmp_path / "rice").ensure()
    selected = tmp_path / "created-files"
    container = build_container(RuntimeArgs(), paths=paths)
    try:
        result = asyncio.run(update_workspace_location(WorkspaceLocationIn(path=str(selected)), container))
        assert result["data"] == {"path": str(selected.resolve())}

        # Queued but not yet promoted: tools still write to the old root, while the
        # UI-facing display already reflects the user's pick.
        assert container.paths.workspace == (tmp_path / "rice" / "workspace").resolve()
        assert container.paths.location is not None
        assert container.paths.location.display == selected.resolve()

        # Turn boundary → promote → live relocation with no restart.
        container.paths.location.promote()
        assert container.paths.workspace == selected.resolve()
        assert container.paths.workspace.is_dir()
    finally:
        container.shutdown()

    # Persisted to config, so a fresh runtime starts already pointed at the folder.
    restarted = build_container(RuntimeArgs(), paths=paths)
    try:
        assert restarted.paths.workspace == selected.resolve()
        assert restarted.paths.workspace.is_dir()
    finally:
        restarted.shutdown()


def test_workspace_location_rejects_relative_paths(tmp_path):
    container = build_container(RuntimeArgs(), paths=RicePaths.from_env(tmp_path / "rice").ensure())
    try:
        with pytest.raises(HTTPException, match="절대 경로"):
            asyncio.run(update_workspace_location(WorkspaceLocationIn(path="relative/output"), container))
    finally:
        container.shutdown()
