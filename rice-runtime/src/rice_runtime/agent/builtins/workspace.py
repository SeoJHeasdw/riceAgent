"""Workspace sandbox path resolution — shared by every file-writing builtin.

One containment rule, one place: a tool path is only ever a *relative* path
resolved under `RicePaths.workspace`; anything that escapes the root (via `..`,
absolute paths, or symlinks) is rejected. `file_tools` and `office` both route
through here so the least-authority guarantee (RUNTIME_DESIGN §10) cannot drift
between modules.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["resolve_workspace_path"]


def resolve_workspace_path(root: Path, rel: str) -> Path:
    """Resolve `rel` under `root` (which must already be `.resolve()`d),
    rejecting any escape from the sandbox."""
    if not rel or not isinstance(rel, str):
        raise ValueError("path is required")
    # reject absolute paths outright; resolve the rest and re-check containment.
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path escapes workspace: {rel}")
    return candidate
