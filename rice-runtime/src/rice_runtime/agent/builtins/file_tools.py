"""Builtin file tools: read_file, write_file, search_files.

All paths are confined to the workspace sandbox (`RicePaths.workspace`). A tool
call naming a path that escapes the root (via `..`, absolute paths, or symlinks)
is rejected — the agent operates under least authority (RUNTIME_DESIGN §10).

Handlers are `async (dict) -> object`; the returned object becomes the
`ToolResult.result` the loop feeds back to the model. `write_file` is marked
`requires_approval=True` so it pauses on the shared HITL gate before touching disk.
"""

from __future__ import annotations

from pathlib import Path

from ...core.paths import RicePaths
from ...core.types import ToolSpec
from ..tool_dispatch import BuiltinTool

# Caps so a tool result can't blow the context window or the disk.
_MAX_READ_BYTES = 256 * 1024
_MAX_WRITE_BYTES = 1024 * 1024
_MAX_MATCHES = 200


def _resolve(root: Path, rel: str) -> Path:
    """Resolve `rel` under `root`, rejecting any escape from the sandbox."""
    if not rel or not isinstance(rel, str):
        raise ValueError("path is required")
    # reject absolute paths outright; resolve the rest and re-check containment.
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path escapes workspace: {rel}")
    return candidate


def _make_read(root: Path) -> BuiltinTool:
    async def read_file(args: dict) -> object:
        path = _resolve(root, args.get("path", ""))
        if not path.is_file():
            raise FileNotFoundError(f"not a file: {args.get('path')}")
        data = path.read_bytes()[:_MAX_READ_BYTES]
        text = data.decode("utf-8", errors="replace")
        truncated = path.stat().st_size > _MAX_READ_BYTES
        return {"path": args.get("path"), "content": text, "truncated": truncated}

    return read_file


def _make_write(root: Path) -> BuiltinTool:
    async def write_file(args: dict) -> object:
        rel = args.get("path", "")
        content = args.get("content", "")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
            raise ValueError("content exceeds size limit")
        path = _resolve(root, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"path": rel, "bytes_written": len(content.encode("utf-8"))}

    return write_file


def _make_search(root: Path) -> BuiltinTool:
    async def search_files(args: dict) -> object:
        query = args.get("query", "")
        if not query:
            raise ValueError("query is required")
        matches: list[dict] = []
        for f in sorted(root.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(root).as_posix()
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if query in line:
                        matches.append({"path": rel, "line": i, "text": line.strip()[:200]})
                        if len(matches) >= _MAX_MATCHES:
                            return {"query": query, "matches": matches, "truncated": True}
            except OSError:
                continue
        return {"query": query, "matches": matches, "truncated": False}

    return search_files


def tools(paths: RicePaths) -> list[tuple[ToolSpec, BuiltinTool]]:
    root = paths.workspace
    root.mkdir(parents=True, exist_ok=True)
    # Resolve once so containment checks compare like-for-like (symlinked temp
    # dirs on macOS, e.g. /var → /private/var, would otherwise look like escapes).
    root = root.resolve()
    return [
        (
            ToolSpec(
                name="read_file",
                description="Read a UTF-8 text file from the workspace. Paths are relative to the workspace root.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Workspace-relative file path."}},
                    "required": ["path"],
                },
                source="builtin",
            ),
            _make_read(root),
        ),
        (
            ToolSpec(
                name="write_file",
                description="Create or overwrite a UTF-8 text file in the workspace. Requires user approval.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Workspace-relative file path."},
                        "content": {"type": "string", "description": "Full file contents to write."},
                    },
                    "required": ["path", "content"],
                },
                source="builtin",
                requires_approval=True,  # external effect → HITL gate
            ),
            _make_write(root),
        ),
        (
            ToolSpec(
                name="search_files",
                description="Search workspace files for a literal substring; returns matching path/line/text.",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "Literal substring to find."}},
                    "required": ["query"],
                },
                source="builtin",
            ),
            _make_search(root),
        ),
    ]
