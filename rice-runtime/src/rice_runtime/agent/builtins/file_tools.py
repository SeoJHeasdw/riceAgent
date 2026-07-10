"""Builtin file tools: read_file, write_file, edit_file, list_dir, search_files.

All paths are confined to the workspace sandbox (`RicePaths.workspace`). A tool
call naming a path that escapes the root (via `..`, absolute paths, or symlinks)
is rejected — the agent operates under least authority (RUNTIME_DESIGN §10).

Handlers are `async (dict) -> object`; the returned object becomes the
`ToolResult.result` the loop feeds back to the model. `write_file` is marked
`requires_approval=True` so it pauses on the shared HITL gate before touching disk.

read_file auto-detects Office/notebook documents (.xlsx, .docx, .ipynb) and
extracts them as text via `read_extract` (stdlib only — no extra dependencies).
"""

from __future__ import annotations

import os
from pathlib import Path

from ...core.paths import RicePaths
from ...core.types import ToolSpec
from ..tool_dispatch import BuiltinTool
from .read_extract import ExtractionError, extract_document_text, is_extractable_document
from .workspace import resolve_workspace_path as _resolve

# Caps so a tool result can't blow the context window or the disk.
_MAX_READ_BYTES = 256 * 1024
_MAX_WRITE_BYTES = 1024 * 1024
_MAX_MATCHES = 200
_MAX_LIST_ENTRIES = 500
_MAX_LIST_DEPTH = 6

# Sub-agent 아티팩트 격리 영역 — dot-디렉토리라 list_dir 목록에는 안 잡히지만
# read_file은 정확한 경로로 읽을 수 있다(러너가 결과에 경로를 실어 준다).
_SCRATCH_DIR = ".subagent"
_MAX_SCRATCH_BYTES = 512 * 1024


def _make_read(root: Path) -> BuiltinTool:
    async def read_file(args: dict) -> object:
        rel = args.get("path", "")
        path = _resolve(root, rel)
        if not path.is_file():
            raise FileNotFoundError(f"not a file: {rel}")

        # Auto-extract Office / notebook documents as readable text.
        if is_extractable_document(str(path)):
            try:
                text = extract_document_text(str(path))
                return {"path": rel, "content": text, "truncated": False, "extracted": True}
            except ExtractionError:
                # Fall through to raw bytes read on extraction failure.
                pass

        data = path.read_bytes()[:_MAX_READ_BYTES]
        text = data.decode("utf-8", errors="replace")
        truncated = path.stat().st_size > _MAX_READ_BYTES
        return {"path": rel, "content": text, "truncated": truncated}

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


def _make_edit(root: Path) -> BuiltinTool:
    async def edit_file(args: dict) -> object:
        rel = args.get("path", "")
        old = args.get("old_text", "")
        new = args.get("new_text", "")
        if not isinstance(old, str) or not old:
            raise ValueError("old_text is required")
        if not isinstance(new, str):
            raise ValueError("new_text must be a string")
        path = _resolve(root, rel)
        if not path.is_file():
            raise FileNotFoundError(f"not a file: {rel}")
        text = path.read_text(encoding="utf-8")
        count = text.count(old)
        if count == 0:
            raise ValueError("old_text not found in file — read the file and copy the exact text")
        if count > 1 and not args.get("replace_all"):
            raise ValueError(f"old_text occurs {count} times — add surrounding lines to make it unique, or set replace_all")
        replaced = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
        if len(replaced.encode("utf-8")) > _MAX_WRITE_BYTES:
            raise ValueError("edited content exceeds size limit")
        path.write_text(replaced, encoding="utf-8")
        return {"path": rel, "replacements": count if args.get("replace_all") else 1}

    return edit_file


def _make_list(root: Path) -> BuiltinTool:
    async def list_dir(args: dict) -> object:
        rel = args.get("path") or "."
        depth = min(int(args.get("depth") or 2), _MAX_LIST_DEPTH)
        base = _resolve(root, rel) if rel != "." else root
        if not base.is_dir():
            raise NotADirectoryError(f"not a directory: {rel}")
        entries: list[dict] = []
        truncated = False

        def walk(d: Path, level: int) -> None:
            nonlocal truncated
            if truncated:
                return
            try:
                children = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name))
            except OSError:
                return
            for child in children:
                if child.name.startswith(".") or child.name in ("node_modules", "__pycache__"):
                    continue
                if len(entries) >= _MAX_LIST_ENTRIES:
                    truncated = True
                    return
                if child.is_symlink():
                    # symlink 는 워크스페이스 밖(예: ~/.ssh)을 가리킬 수 있다 —
                    # read_file 의 resolve 검증과 달리 여기선 따라가지 않고 스킵한다.
                    continue
                entry = {"path": child.relative_to(root).as_posix(), "type": "dir" if child.is_dir() else "file"}
                if child.is_file():
                    try:
                        entry["size"] = child.stat().st_size
                    except OSError:
                        pass
                entries.append(entry)
                if child.is_dir() and level + 1 < depth:
                    walk(child, level + 1)

        walk(base, 0)
        return {"path": rel, "entries": entries, "truncated": truncated}

    return list_dir


def _make_search(root: Path) -> BuiltinTool:
    async def search_files(args: dict) -> object:
        query = args.get("query", "")
        if not query:
            raise ValueError("query is required")
        matches: list[dict] = []
        # os.walk(followlinks=False): 디렉토리 symlink 를 따라가지 않는다 —
        # rglob 은 파이썬 버전에 따라 symlink 디렉토리로 재귀해 워크스페이스 밖
        # (또는 무한 루프)으로 샐 수 있다. 파일 symlink 도 아래에서 스킵 +
        # resolve 재검증으로 이중 방어한다(모듈 docstring 의 sandbox 보장 준수).
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames.sort()
            for name in sorted(filenames):
                f = Path(dirpath) / name
                if f.is_symlink():
                    continue
                try:
                    if not f.resolve().is_relative_to(root):
                        continue  # 방어적 재검증 — 어떤 경로로든 root 밖이면 제외
                    if not f.is_file():
                        continue
                    # read_file 과 같은 상한으로 읽는다 — 거대 파일 하나가 메모리를
                    # 삼키지 않게(라인 검색이므로 잘린 꼬리는 감수).
                    data = f.read_bytes()[:_MAX_READ_BYTES]
                except OSError:
                    continue
                rel = f.relative_to(root).as_posix()
                for i, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                    if query in line:
                        matches.append({"path": rel, "line": i, "text": line.strip()[:200]})
                        if len(matches) >= _MAX_MATCHES:
                            return {"query": query, "matches": matches, "truncated": True}
        return {"query": query, "matches": matches, "truncated": False}

    return search_files


def scratch_tool_factory(paths: RicePaths):
    """`write_scratch` — sub-agent 전용, thread 격리 쓰기 도구의 팩토리.

    워커는 승인 게이트 밖에서 돌므로 일반 쓰기 도구를 줄 수 없다. 대신 산출물을
    `.subagent/<thread_id>/` 아래에만 남기게 한다: (1) 워크스페이스 샌드박스 안,
    (2) thread 하위로 재격리(이중 containment 검사), (3) 사용자 가시 영역으로의
    승격은 메인 에이전트의 승인 게이트 도구가 담당한다. 반환 경로는 워크스페이스
    상대라 메인이 그대로 read_file 할 수 있다."""
    root = paths.workspace
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()

    def factory(thread_id: str) -> tuple[ToolSpec, BuiltinTool]:
        base_rel = f"{_SCRATCH_DIR}/{thread_id}"

        async def write_scratch(args: dict) -> object:
            rel = str(args.get("path") or "").strip().lstrip("/")
            content = args.get("content", "")
            if not rel:
                raise ValueError("path is required (relative, e.g. 'draft.md')")
            if not isinstance(content, str):
                raise ValueError("content must be a string")
            if len(content.encode("utf-8")) > _MAX_SCRATCH_BYTES:
                raise ValueError("content exceeds scratch size limit")
            full_rel = f"{base_rel}/{rel}"
            target = _resolve(root, full_rel)
            scratch_base = _resolve(root, base_rel)
            if target != scratch_base and scratch_base not in target.parents:
                raise ValueError(f"path escapes the scratch area: {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"path": full_rel, "bytes_written": len(content.encode("utf-8"))}

        spec = ToolSpec(
            name="write_scratch",
            description=(
                "Save a long draft, table, or extraction as a UTF-8 text file in your "
                "isolated scratch area instead of pasting it into your report. Returns "
                "the workspace-relative path — cite it in your findings so the main "
                "agent can read it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file name, e.g. 'draft.md'."},
                    "content": {"type": "string", "description": "Full file contents."},
                },
                "required": ["path", "content"],
            },
            source="builtin",
        )
        return spec, write_scratch

    return factory


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
                name="edit_file",
                description=(
                    "Replace an exact text snippet inside an existing workspace file. Safer and cheaper "
                    "than write_file for small changes — the rest of the file is untouched. old_text must "
                    "match the file exactly (copy it from read_file output) and be unique unless replace_all."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Workspace-relative file path."},
                        "old_text": {"type": "string", "description": "Exact existing text to replace."},
                        "new_text": {"type": "string", "description": "Replacement text."},
                        "replace_all": {"type": "boolean", "description": "Replace every occurrence (default: false, old_text must be unique)."},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
                source="builtin",
                requires_approval=True,  # external effect → HITL gate (write_file과 동일 기준)
            ),
            _make_edit(root),
        ),
        (
            ToolSpec(
                name="list_dir",
                description=(
                    "List files and folders under a workspace directory (recursive up to `depth`, "
                    "default 2). Use this to get oriented before reading files."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Workspace-relative directory; omit for the workspace root."},
                        "depth": {"type": "integer", "description": "Recursion depth, 1-6 (default 2)."},
                    },
                },
                source="builtin",
            ),
            _make_list(root),
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
