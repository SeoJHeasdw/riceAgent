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
from ._search_engine import grep_search
from .read_extract import ExtractionError, extract_document_text, is_extractable_document
from .workspace import resolve_workspace_path as _resolve

# Caps so a tool result can't blow the context window or the disk.
_MAX_READ_BYTES = 256 * 1024
_MAX_WRITE_BYTES = 1024 * 1024
_MAX_MATCHES = 200
_MAX_LIST_ENTRIES = 500
_MAX_LIST_DEPTH = 6

# read_file line-window defaults: line-numbered output + offset/limit let the
# model cite lines and read a large file in slices instead of all at once.
_DEFAULT_READ_LINES = 2_000  # lines returned when `limit` is omitted
_MAX_READ_LINES = 5_000      # hard cap on `limit`

# list_dir: a single directory with more immediate children than this is
# summarized (count + extension breakdown) instead of enumerated, so a
# node_modules-sized folder can't flood the listing.
_DIR_SUMMARY_THRESHOLD = 100

# Sub-agent 아티팩트 격리 영역 — dot-디렉토리라 list_dir 목록에는 안 잡히지만
# read_file은 정확한 경로로 읽을 수 있다(러너가 결과에 경로를 실어 준다).
_SCRATCH_DIR = ".subagent"
_MAX_SCRATCH_BYTES = 512 * 1024


def _coerce_int(raw: object, default: int) -> int:
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _make_read(paths: RicePaths) -> BuiltinTool:
    async def read_file(args: dict) -> object:
        root = paths.workspace  # live: reflects a workspace relocation applied this turn
        rel = args.get("path", "")
        path = _resolve(root, rel)
        if not path.is_file():
            raise FileNotFoundError(f"not a file: {rel}")

        # Auto-extract Office / notebook documents as readable text (no line
        # windowing — these are already bounded, structured extractions).
        if is_extractable_document(str(path)):
            try:
                text = extract_document_text(str(path))
                return {"path": rel, "content": text, "truncated": False, "extracted": True}
            except ExtractionError:
                # Fall through to raw bytes read on extraction failure.
                pass

        # Line-numbered window: output is "N→line" so the model can cite line
        # numbers and read big files in slices via offset/limit. The "N→" prefix
        # is NOT part of the file — edit_file's description tells the model to
        # strip it before matching old_string.
        byte_truncated = path.stat().st_size > _MAX_READ_BYTES
        data = path.read_bytes()[:_MAX_READ_BYTES]
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        total = len(lines)

        offset = max(1, _coerce_int(args.get("offset"), 1))  # 1-indexed start line
        limit = _coerce_int(args.get("limit"), _DEFAULT_READ_LINES)
        limit = min(max(1, limit), _MAX_READ_LINES)
        start = offset - 1
        window = lines[start : start + limit]
        numbered = "".join(
            f"{start + i + 1}→{line}" for i, line in enumerate(window)
        )
        window_truncated = (start + limit) < total
        return {
            "path": rel,
            "content": numbered,
            "start_line": offset if window else 0,
            "end_line": start + len(window),
            "total_lines": total,
            "truncated": byte_truncated or window_truncated,
        }

    return read_file


def _make_write(paths: RicePaths) -> BuiltinTool:
    async def write_file(args: dict) -> object:
        root = paths.workspace
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


def _make_edit(paths: RicePaths) -> BuiltinTool:
    async def edit_file(args: dict) -> object:
        root = paths.workspace
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
            raise ValueError(
                "old_text not found in file. Re-read the file with read_file and copy the exact "
                "current text — the file may have changed, and the 'N→' line-number prefix from "
                "read_file is NOT part of the file (copy only what follows the →, with its indentation)."
            )
        if count > 1 and not args.get("replace_all"):
            raise ValueError(f"old_text occurs {count} times — add surrounding lines to make it unique, or set replace_all")
        replaced = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
        if len(replaced.encode("utf-8")) > _MAX_WRITE_BYTES:
            raise ValueError("edited content exceeds size limit")
        path.write_text(replaced, encoding="utf-8")
        return {"path": rel, "replacements": count if args.get("replace_all") else 1}

    return edit_file


def _visible_children(d: Path) -> list[Path]:
    """Sorted (dirs-first-then-files) children, skipping dotfiles, the noise dirs,
    and symlinks (which could point outside the workspace — we never follow them)."""
    try:
        children = d.iterdir()
    except OSError:
        return []
    kept = [
        c
        for c in children
        if not c.name.startswith(".")
        and c.name not in ("node_modules", "__pycache__")
        and not c.is_symlink()
    ]
    return sorted(kept, key=lambda p: (p.is_file(), p.name))


def _summarize_dir(children: list[Path]) -> dict:
    """Count + top-10 extension breakdown for a directory too large to enumerate."""
    files = [c for c in children if c.is_file()]
    dirs = [c for c in children if c.is_dir()]
    exts: dict[str, int] = {}
    for f in files:
        exts[f.suffix.lower() or "(none)"] = exts.get(f.suffix.lower() or "(none)", 0) + 1
    top = dict(sorted(exts.items(), key=lambda kv: (-kv[1], kv[0]))[:10])
    return {"file_count": len(files), "dir_count": len(dirs), "by_extension": top}


def _make_list(paths: RicePaths) -> BuiltinTool:
    async def list_dir(args: dict) -> object:
        root = paths.workspace
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
            for child in _visible_children(d):
                if len(entries) >= _MAX_LIST_ENTRIES:
                    truncated = True
                    return
                is_dir = child.is_dir()
                entry = {"path": child.relative_to(root).as_posix(), "type": "dir" if is_dir else "file"}
                if not is_dir:
                    try:
                        entry["size"] = child.stat().st_size
                    except OSError:
                        pass
                    entries.append(entry)
                    continue
                # Directory: summarize instead of enumerating when it's huge
                # (e.g. a vendored folder), so one big subtree can't flood the list.
                grandchildren = _visible_children(child)
                if len(grandchildren) > _DIR_SUMMARY_THRESHOLD:
                    entry["summary"] = _summarize_dir(grandchildren)
                    entries.append(entry)
                    continue
                entries.append(entry)
                if level + 1 < depth:
                    walk(child, level + 1)

        walk(base, 0)
        return {"path": rel, "entries": entries, "truncated": truncated}

    return list_dir


def _make_search(paths: RicePaths) -> BuiltinTool:
    async def search_files(args: dict) -> object:
        root = paths.workspace
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


def _make_grep(paths: RicePaths) -> BuiltinTool:
    """Regex code search (ripgrep-backed, Python fallback).

    Confined to the workspace: an optional `path` is resolved through the sandbox
    guard before the engine runs, so a search can't escape the root."""

    async def grep(args: dict) -> object:
        root = paths.workspace
        pattern = args.get("pattern", "")
        if not pattern or not isinstance(pattern, str):
            raise ValueError("pattern is required and must be a string")
        sub = args.get("path")
        search_path = _resolve(root, sub) if sub else root
        if not search_path.exists():
            raise FileNotFoundError(f"path not found: {sub}")
        return grep_search(
            root,
            search_path,
            pattern,
            glob=args.get("glob"),
            type_=args.get("type"),
            before=_coerce_int(args.get("before_context") or args.get("-B"), 0),
            after=_coerce_int(args.get("after_context") or args.get("-A"), 0),
            context=_coerce_int(args.get("context") or args.get("-C"), 0),
            case_insensitive=bool(args.get("case_insensitive") or args.get("-i")),
            head_limit=args.get("head_limit"),
            multiline=bool(args.get("multiline")),
            fixed_strings=bool(args.get("fixed_strings")),
        )

    return grep


def scratch_tool_factory(paths: RicePaths):
    """`write_scratch` — sub-agent 전용, thread 격리 쓰기 도구의 팩토리.

    워커는 승인 게이트 밖에서 돌므로 일반 쓰기 도구를 줄 수 없다. 대신 산출물을
    `.subagent/<thread_id>/` 아래에만 남기게 한다: (1) 워크스페이스 샌드박스 안,
    (2) thread 하위로 재격리(이중 containment 검사), (3) 사용자 가시 영역으로의
    승격은 메인 에이전트의 승인 게이트 도구가 담당한다. 반환 경로는 워크스페이스
    상대라 메인이 그대로 read_file 할 수 있다."""
    paths.workspace.mkdir(parents=True, exist_ok=True)  # ensure initial root

    def factory(thread_id: str) -> tuple[ToolSpec, BuiltinTool]:
        base_rel = f"{_SCRATCH_DIR}/{thread_id}"

        async def write_scratch(args: dict) -> object:
            root = paths.workspace  # live: relocation applied at the turn boundary
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
    # Ensure the *initial* root exists; handlers read `paths.workspace` live at
    # call time (already resolved), so a relocation applied at a turn boundary is
    # picked up without rebuilding the toolset. Containment checks compare
    # like-for-like because `paths.workspace` is always a resolved path
    # (symlinked temp dirs on macOS, e.g. /var → /private/var, stay consistent).
    paths.workspace.mkdir(parents=True, exist_ok=True)
    return [
        (
            ToolSpec(
                name="read_file",
                description=(
                    "Read a file from the workspace. Paths are relative to the workspace root.\n"
                    "- Content is returned with line numbers, one per line, in the format: LINE_NUMBER→LINE_CONTENT. "
                    "The 'N→' prefix is display only — it is not part of the file.\n"
                    "- By default up to 2000 lines are returned from the top of the file. For a large file, pass "
                    "`offset` (1-indexed start line) and `limit` (line count) to read a specific slice.\n"
                    "- Office/notebook documents (.xlsx, .docx, .pptx, .ipynb) are auto-extracted to readable text."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Workspace-relative file path."},
                        "offset": {"type": "integer", "description": "The line number to start reading from (1-indexed). Only provide if the file is too large to read at once."},
                        "limit": {"type": "integer", "description": "The number of lines to read (default 2000, max 5000). Only provide if the file is too large to read at once."},
                    },
                    "required": ["path"],
                },
                source="builtin",
            ),
            _make_read(paths),
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
                parallel_safe=False,  # 파일 쓰기는 모델 호출 순서대로 직렬 실행
            ),
            _make_write(paths),
        ),
        (
            ToolSpec(
                name="edit_file",
                description=(
                    "Replace an exact text snippet inside an existing workspace file. Safer and cheaper "
                    "than write_file for small changes — the rest of the file is untouched.\n"
                    "- Read the file with read_file before editing it.\n"
                    "- read_file prefixes each line with 'N→'. That prefix is NOT part of the file: copy only "
                    "what comes after the →, with its exact indentation, into old_text.\n"
                    "- old_text must match exactly one place in the file. If it appears more than once, add "
                    "surrounding lines to make it unique, or set replace_all to change every occurrence."
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
                parallel_safe=False,  # 같은 파일 동시 편집 레이스 방지 — 직렬 실행
            ),
            _make_edit(paths),
        ),
        (
            ToolSpec(
                name="list_dir",
                description=(
                    "List files and folders under a workspace directory (recursive up to `depth`, "
                    "default 2). Use this to get oriented before reading files. Dot-files and folders "
                    "like node_modules/__pycache__ are skipped; very large subdirectories are summarized "
                    "with a file count and extension breakdown instead of being enumerated."
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
            _make_list(paths),
        ),
        (
            ToolSpec(
                name="grep",
                description=(
                    "Search file contents with regular expressions (ripgrep). Prefer this over "
                    "search_files for anything beyond a plain substring.\n"
                    "- Full regex syntax, so escape literal special characters: `functionCall\\(`.\n"
                    "- Pass `pattern` as a raw regex string — no surrounding quotes.\n"
                    "- Respects .gitignore. Filter by `glob` or `type` only when you are sure of the file type "
                    "(import paths may not match source extensions, e.g. .js vs .ts).\n"
                    "- Output is grouped by file, ripgrep-style: ':' marks match lines, '-' marks context lines. "
                    "Large results are capped (default 200 matches) and flagged as truncated."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "The regular expression pattern to search for in file contents."},
                        "path": {"type": "string", "description": "Workspace-relative file or directory to search in. Defaults to the workspace root."},
                        "glob": {"type": "string", "description": "Glob to filter files, e.g. \"*.py\" or \"*.{ts,tsx}\"."},
                        "type": {"type": "string", "description": "File type to search (rg --type): py, js, ts, rust, go, java, c, cpp, md, json, yaml, toml, html, css, sh, sql, txt."},
                        "-A": {"type": "integer", "description": "Number of lines to show after each match."},
                        "-B": {"type": "integer", "description": "Number of lines to show before each match."},
                        "-C": {"type": "integer", "description": "Number of lines to show before and after each match."},
                        "-i": {"type": "boolean", "description": "Case-insensitive search."},
                        "head_limit": {"type": "integer", "description": "Limit output to the first N matches (default 200, max 2000)."},
                        "multiline": {"type": "boolean", "description": "Enable multiline mode where . matches newlines and patterns can span lines."},
                    },
                    "required": ["pattern"],
                },
                source="builtin",
            ),
            _make_grep(paths),
        ),
        (
            ToolSpec(
                name="search_files",
                description=(
                    "Search workspace files for a literal substring; returns matching path/line/text. "
                    "For regex, file-type filters, or context lines, use grep instead."
                ),
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "Literal substring to find."}},
                    "required": ["query"],
                },
                source="builtin",
            ),
            _make_search(paths),
        ),
    ]
