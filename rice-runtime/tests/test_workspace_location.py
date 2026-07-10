"""Workspace output-location persistence and sandbox bootstrap."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from rice_runtime.core.config import RuntimeArgs
from rice_runtime.core.paths import RicePaths
from rice_runtime.server.composition_root import build_container
from rice_runtime.server.routes.workspace import WorkspaceLocationIn, update_workspace_location


def test_workspace_location_is_applied_after_runtime_restart(tmp_path):
    paths = RicePaths.from_env(tmp_path / "rice").ensure()
    selected = tmp_path / "created-files"
    container = build_container(RuntimeArgs(), paths=paths)
    try:
        result = asyncio.run(update_workspace_location(WorkspaceLocationIn(path=str(selected)), container))
        assert result["data"] == {"path": str(selected.resolve()), "restart_required": True}
        assert container.paths.workspace == (tmp_path / "rice" / "workspace").resolve()
    finally:
        container.shutdown()

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
