"""Workspace information and file opening (CONTRACTS — local file viewer).

Lets the app open a builtin file tool's path directly. Read-only and confined to
the workspace sandbox (`RicePaths.workspace`) — the same root the file tools write
to. Any path escaping the root is reported as not-found (no info leak).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ...core.config import Config, UIConfig, save_config
from ...core.errors import NotFoundError
from ..composition_root import Container
from .deps import get_container

router = APIRouter(tags=["workspace"])

_MAX_READ_BYTES = 256 * 1024


class OpenIn(BaseModel):
    path: str


class WorkspaceLocationIn(BaseModel):
    # None resets to the default ~/.rice/workspace on the next runtime start.
    path: str | None = None


def _resolve_in_workspace(root: Path, rel: str) -> Path:
    """Resolve `rel` under the workspace root, 404-ing on escape or miss (no leak)."""
    candidate = (root / rel).resolve()
    if (candidate != root and root not in candidate.parents) or not candidate.is_file():
        raise NotFoundError(f"file not found: {rel}")
    return candidate


def _open_in_default_app(target: Path) -> None:
    """Hand a local workspace path to the operating system's default handler."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        elif os.name == "nt":
            os.startfile(target)  # type: ignore[attr-defined]  # Windows-only
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except Exception as exc:  # opener missing / no association
        raise NotFoundError(f"열 수 없어요: {exc}") from exc


@router.get("/workspace")
async def get_workspace(c: Container = Depends(get_container)):
    """Return the effective workspace root for display in the local app.

    The value may differ from ``~/.rice/workspace`` when the runtime is started
    with ``RICE_HOME``. It is intentionally exposed only by the localhost,
    bearer-token-protected runtime.
    """
    root = c.paths.workspace.resolve()
    return {"success": True, "data": {"path": str(root)}}


@router.put("/workspace/location")
async def update_workspace_location(body: WorkspaceLocationIn, c: Container = Depends(get_container)):
    """Persist a new output folder for the next runtime start.

    Builtin tool handlers capture their sandbox root during composition. Updating
    that root mid-turn would make a single plan read from one folder and write to
    another, so the change is deliberately applied after an app restart.
    """
    requested = (body.path or "").strip()
    if requested:
        target = Path(requested).expanduser()
        if not target.is_absolute():
            raise HTTPException(status_code=422, detail="저장 위치는 절대 경로여야 합니다.")
        try:
            target.mkdir(parents=True, exist_ok=True)
            target = target.resolve()
        except OSError as exc:
            raise HTTPException(status_code=422, detail=f"저장 폴더를 만들 수 없어요: {exc}") from exc
        workspace_path: str | None = str(target)
    else:
        workspace_path = None

    cfg = c.config
    new_ui = UIConfig(**{**cfg.ui.__dict__, "workspace_path": workspace_path})
    new_cfg = Config(
        provider=cfg.provider,
        learning=cfg.learning,
        context=cfg.context,
        agent=cfg.agent,
        ui=new_ui,
    )
    save_config(c.paths, new_cfg)
    c.config_store.update(new_cfg)
    next_path = workspace_path or str((c.paths.root / "workspace").resolve())
    return {"success": True, "data": {"path": next_path, "restart_required": True}}


@router.get("/workspace/file")
async def read_workspace_file(path: str = Query(...), c: Container = Depends(get_container)):
    candidate = _resolve_in_workspace(c.paths.workspace.resolve(), path)
    data = candidate.read_bytes()
    text = data[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
    return {
        "success": True,
        "data": {"path": path, "content": text, "truncated": len(data) > _MAX_READ_BYTES},
    }


@router.post("/workspace/open")
async def open_workspace_file(body: OpenIn, c: Container = Depends(get_container)):
    """Open a workspace file in the user's OS default app (Excel→office, .txt→editor…).

    The runtime runs locally on the user's machine, so it can hand the file to the
    desktop's default handler — same behavior in browser-dev and the packaged app.
    """
    candidate = _resolve_in_workspace(c.paths.workspace.resolve(), body.path)
    _open_in_default_app(candidate)
    return {"success": True, "data": {"path": body.path}}


@router.post("/workspace/open-folder")
async def open_workspace_folder(c: Container = Depends(get_container)):
    """Open the workspace folder in Finder / Explorer / the file manager."""
    root = c.paths.workspace.resolve()
    _open_in_default_app(root)
    return {"success": True, "data": {"path": str(root)}}
