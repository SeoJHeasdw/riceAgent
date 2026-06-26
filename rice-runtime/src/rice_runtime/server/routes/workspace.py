"""Workspace file read (CONTRACTS — local file viewer).

Lets the app open a builtin file tool's path directly. Read-only and confined to
the workspace sandbox (`RicePaths.workspace`) — the same root the file tools write
to. Any path escaping the root is reported as not-found (no info leak).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ...core.errors import NotFoundError
from ..composition_root import Container
from .deps import get_container

router = APIRouter(tags=["workspace"])

_MAX_READ_BYTES = 256 * 1024


class OpenIn(BaseModel):
    path: str


def _resolve_in_workspace(root: Path, rel: str) -> Path:
    """Resolve `rel` under the workspace root, 404-ing on escape or miss (no leak)."""
    candidate = (root / rel).resolve()
    if (candidate != root and root not in candidate.parents) or not candidate.is_file():
        raise NotFoundError(f"file not found: {rel}")
    return candidate


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
    target = str(candidate)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", target])
        elif os.name == "nt":
            os.startfile(target)  # type: ignore[attr-defined]  # Windows-only
        else:
            subprocess.Popen(["xdg-open", target])
    except Exception as exc:  # opener missing / no association
        raise NotFoundError(f"열 수 없어요: {exc}") from exc
    return {"success": True, "data": {"path": body.path}}
