"""Code-search engine behind the `grep` builtin (ripgrep with a Python fallback).

Regex search over file contents with grouped, ripgrep-style output (':' = match
line, '-' = context line, one heading per file), `.gitignore` awareness, glob /
type / context filters, and output caps. A system `rg` is used when one is on PATH
(true `.gitignore` semantics, fast); otherwise a self-contained Python regex walker
runs with a pragmatic default-ignore set. Both engines feed ONE formatter so the
model sees an identical card regardless of which ran.

Sandbox: every search is rooted at (and confined to) `RicePaths.workspace`. The
caller resolves the optional sub-path through `resolve_workspace_path` first;
neither engine follows symlinks out of the root (rg default off; the Python walker
uses `followlinks=False` + a per-file re-resolve check), matching the least-authority
guarantee the other file tools uphold (RUNTIME_DESIGN §10).
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# ── output caps ─────────────────────────────────────────────────────────────
# Content head_limit defaults to 200 matches (hard max 2000): most useful
# exploration lands well under that, so a repo-wide walk stops early instead of
# dumping thousands of lines into the context window.
_DEFAULT_HEAD_LIMIT = 200
_MAX_HEAD_LIMIT = 2_000
_MAX_OUTPUT_BYTES = 40 * 1024
_MAX_CHARS_PER_LINE = 1_000
_MAX_FILE_BYTES = 256 * 1024  # per-file read cap in the Python fallback (== read_file)
_RG_TIMEOUT_S = 10.0
_RG_STDOUT_CAP = 5_000_000  # hard cap on bytes read from rg stdout

# Directories the Python fallback never descends into. rg gets true .gitignore
# semantics for free; this is the "good enough" stand-in when rg is absent.
_IGNORE_DIRS: frozenset[str] = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".next", "target",
    ".idea", ".vscode", ".tox", ".gradle", ".cache", "coverage", ".egg-info",
})

# rg --type equivalents for the Python fallback (common languages only).
_TYPE_EXTENSIONS: dict[str, frozenset[str]] = {
    "py": frozenset({".py", ".pyi"}),
    "js": frozenset({".js", ".jsx", ".mjs", ".cjs"}),
    "ts": frozenset({".ts", ".tsx", ".mts", ".cts"}),
    "rust": frozenset({".rs"}),
    "go": frozenset({".go"}),
    "java": frozenset({".java"}),
    "c": frozenset({".c", ".h"}),
    "cpp": frozenset({".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"}),
    "md": frozenset({".md", ".markdown"}),
    "json": frozenset({".json"}),
    "yaml": frozenset({".yaml", ".yml"}),
    "toml": frozenset({".toml"}),
    "html": frozenset({".html", ".htm"}),
    "css": frozenset({".css", ".scss", ".sass", ".less"}),
    "sh": frozenset({".sh", ".bash", ".zsh"}),
    "sql": frozenset({".sql"}),
    "txt": frozenset({".txt", ".text"}),
}


@dataclass
class _Line:
    """One rendered line inside a file group: a match (':') or context ('-')."""

    lineno: int
    text: str
    is_match: bool


def find_ripgrep() -> str | None:
    """Locate a real `rg` binary a subprocess can exec.

    `shutil.which` misses shells where `rg` is only a function, so probe the usual
    install prefixes too. Returns None → Python fallback engine.
    """
    found = shutil.which("rg")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/rg", "/usr/local/bin/rg", "/usr/bin/rg", "/bin/rg"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _clamp_head_limit(raw: object) -> int:
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _DEFAULT_HEAD_LIMIT
    if value <= 0:
        return _DEFAULT_HEAD_LIMIT
    return min(value, _MAX_HEAD_LIMIT)


def _format(files: list[tuple[str, list[_Line]]]) -> tuple[str, bool]:
    """Render grouped file hunks to ripgrep `--heading` style, capped at
    `_MAX_OUTPUT_BYTES`. Returns (text, byte_truncated)."""
    blocks: list[str] = []
    size = 0
    truncated = False
    for path, lines in files:
        rendered = [path]
        prev: int | None = None
        for ln in lines:
            if prev is not None and ln.lineno > prev + 1:
                rendered.append("--")  # discontiguous hunk within the same file
            text = ln.text.rstrip("\n")
            if len(text) > _MAX_CHARS_PER_LINE:
                text = text[:_MAX_CHARS_PER_LINE] + "…"
            rendered.append(f"{ln.lineno}{':' if ln.is_match else '-'}{text}")
            prev = ln.lineno
        block = "\n".join(rendered)
        cost = len(block.encode("utf-8")) + (2 if blocks else 0)
        if size + cost > _MAX_OUTPUT_BYTES:
            truncated = True
            break
        blocks.append(block)
        size += cost
    return "\n\n".join(blocks), truncated


# ── ripgrep engine (rg --json) ──────────────────────────────────────────────


def _run_ripgrep(
    rg: str,
    root: Path,
    search_path: Path,
    pattern: str,
    *,
    glob: str | None,
    type_: str | None,
    before: int,
    after: int,
    case_insensitive: bool,
    head_limit: int,
    multiline: bool,
    fixed_strings: bool,
) -> tuple[list[tuple[str, list[_Line]]], int, bool]:
    """Run `rg --json` and parse it into file hunks. `--json` gives exact control
    over head_limit + formatting (vs. scraping the pretty output)."""
    cmd = [rg, "--json", "--no-messages"]
    if case_insensitive:
        cmd.append("-i")
    if fixed_strings:
        cmd.append("-F")
    if multiline:
        cmd += ["--multiline", "--multiline-dotall"]
    if before:
        cmd += ["-B", str(before)]
    if after:
        cmd += ["-A", str(after)]
    if glob:
        cmd += ["--glob", glob]
    if type_:
        cmd += ["--type", type_]
    cmd += ["-e", pattern, "--", str(search_path)]

    proc = subprocess.run(  # noqa: S603 — argv list, no shell
        cmd,
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        timeout=_RG_TIMEOUT_S,
        check=False,
    )
    stdout = proc.stdout[:_RG_STDOUT_CAP]

    files: list[tuple[str, list[_Line]]] = []
    current_path: str | None = None
    current_lines: list[_Line] = []
    matches = 0
    truncated = False

    def flush() -> None:
        nonlocal current_path, current_lines
        if current_path is not None and current_lines:
            files.append((current_path, current_lines))
        current_path, current_lines = None, []

    for raw in stdout.splitlines():
        if matches >= head_limit:
            truncated = True
            break
        try:
            evt = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        kind = evt.get("type")
        data = evt.get("data", {})
        if kind == "begin":
            flush()
            current_path = _rel_display(root, data.get("path", {}).get("text", ""))
        elif kind in ("match", "context"):
            lineno = data.get("line_number")
            text = data.get("lines", {}).get("text", "")
            if lineno is None:
                continue
            is_match = kind == "match"
            current_lines.append(_Line(int(lineno), text, is_match))
            if is_match:
                matches += 1
    flush()
    return files, matches, truncated


def _rel_display(root: Path, raw_path: str) -> str:
    """Best-effort workspace-relative display path for an rg-reported path."""
    if not raw_path:
        return raw_path
    p = Path(raw_path)
    try:
        return (p if p.is_absolute() else root / p).resolve().relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


# ── Python fallback engine ──────────────────────────────────────────────────


def _compile(pattern: str, *, case_insensitive: bool, multiline: bool, fixed: bool) -> re.Pattern[str]:
    flags = 0
    if case_insensitive:
        flags |= re.IGNORECASE
    if multiline:
        flags |= re.DOTALL | re.MULTILINE
    return re.compile(re.escape(pattern) if fixed else pattern, flags)


def _type_ok(name: str, type_: str | None) -> bool:
    if not type_:
        return True
    exts = _TYPE_EXTENSIONS.get(type_.lower())
    if not exts:
        return True  # unknown type → don't over-filter; stay lenient
    return Path(name).suffix.lower() in exts


def _scan_file(
    f: Path, root: Path, rx: re.Pattern[str], *, before: int, after: int, multiline: bool
) -> list[_Line] | None:
    """Read one file (sandbox-rechecked, binary-skipped) and return its ordered
    match/context lines, or None if the file is skipped/empty of matches."""
    try:
        # defense-in-depth: never read through a path that escapes root.
        if f.is_symlink() or not f.resolve().is_relative_to(root) or not f.is_file():
            return None
        data = f.read_bytes()[:_MAX_FILE_BYTES]
    except OSError:
        return None
    if b"\x00" in data[:8192]:
        return None  # skip binaries (matches rg's default)
    hunks = _scan_text(data.decode("utf-8", errors="replace"), rx, before=before, after=after, multiline=multiline)
    return hunks or None


def _trim_to_limit(hunks: list[_Line], remaining: int) -> tuple[list[_Line], int, bool]:
    """Keep hunk lines until `remaining` matches are consumed. Returns
    (kept_lines, matches_kept, hit_limit)."""
    kept: list[_Line] = []
    used = 0
    for ln in hunks:
        if ln.is_match and used >= remaining:
            return kept, used, True
        kept.append(ln)
        if ln.is_match:
            used += 1
    return kept, used, False


def _run_python(
    root: Path,
    search_path: Path,
    pattern: str,
    *,
    glob: str | None,
    type_: str | None,
    before: int,
    after: int,
    case_insensitive: bool,
    head_limit: int,
    multiline: bool,
    fixed_strings: bool,
) -> tuple[list[tuple[str, list[_Line]]], int, bool]:
    """Self-contained regex walker used when no `rg` binary is available."""
    try:
        rx = _compile(pattern, case_insensitive=case_insensitive, multiline=multiline, fixed=fixed_strings)
    except re.error as exc:
        raise ValueError(f"invalid regex pattern: {exc}") from exc

    files: list[tuple[str, list[_Line]]] = []
    matches = 0
    truncated = False

    # Single-file target: scan it directly (don't walk its parent's whole subtree).
    if search_path.is_file():
        hunks = _scan_file(search_path, root, rx, before=before, after=after, multiline=multiline)
        if hunks:
            kept, used, hit = _trim_to_limit(hunks, head_limit)
            if kept:
                files.append((search_path.resolve().relative_to(root).as_posix(), kept))
            matches, truncated = used, hit
        return files, matches, truncated

    for dirpath, dirnames, filenames in os.walk(search_path, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in _IGNORE_DIRS and not d.endswith(".egg-info"))
        for name in sorted(filenames):
            if matches >= head_limit:
                truncated = True
                break
            if not _type_ok(name, type_):
                continue
            f = Path(dirpath) / name
            rel = f.relative_to(root).as_posix()
            if glob and not (fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(name, glob)):
                continue
            hunks = _scan_file(f, root, rx, before=before, after=after, multiline=multiline)
            if not hunks:
                continue
            kept, used, hit = _trim_to_limit(hunks, head_limit - matches)
            if kept:
                files.append((rel, kept))
                matches += used
            if hit:
                truncated = True
        if matches >= head_limit:
            truncated = True
            break
    return files, matches, truncated


def _scan_text(
    text: str, rx: re.Pattern[str], *, before: int, after: int, multiline: bool
) -> list[_Line]:
    """Return ordered match/context lines for one file (1-indexed line numbers)."""
    lines = text.split("\n")
    if multiline:
        # Multiline: report the starting line of each spanning match, no context
        # windows (keeps the fallback simple; rg gives full spans when present).
        hit_lines = sorted({text.count("\n", 0, m.start()) for m in rx.finditer(text)})
        return [_Line(n + 1, lines[n] if n < len(lines) else "", True) for n in hit_lines]

    match_idx = [i for i, line in enumerate(lines) if rx.search(line)]
    if not match_idx:
        return []
    want: dict[int, bool] = {}  # line index -> is_match (match wins over context)
    for i in match_idx:
        want[i] = True
        for j in range(max(0, i - before), i):
            want.setdefault(j, False)
        for j in range(i + 1, min(len(lines), i + after + 1)):
            want.setdefault(j, False)
    return [_Line(i + 1, lines[i], want[i]) for i in sorted(want)]


# ── public entry point ──────────────────────────────────────────────────────


def grep_search(
    root: Path,
    search_path: Path,
    pattern: str,
    *,
    glob: str | None = None,
    type_: str | None = None,
    before: int = 0,
    after: int = 0,
    context: int = 0,
    case_insensitive: bool = False,
    head_limit: object = None,
    multiline: bool = False,
    fixed_strings: bool = False,
) -> dict:
    """Search `search_path` (under sandbox `root`) for `pattern` and return a
    model-facing result: grouped ripgrep-style `output` plus counts + truncation.

    `context` sets both before/after (rg -C) when the specific ones are 0.
    Prefers a system `rg`; falls back to a pure-Python walker otherwise.
    """
    if not pattern or not isinstance(pattern, str):
        raise ValueError("pattern is required and must be a string")
    limit = _clamp_head_limit(head_limit)
    b = before or context
    a = after or context
    rg = find_ripgrep()
    engine = "ripgrep" if rg else "python"
    try:
        if rg:
            files, matches, truncated = _run_ripgrep(
                rg, root, search_path, pattern,
                glob=glob, type_=type_, before=b, after=a,
                case_insensitive=case_insensitive, head_limit=limit,
                multiline=multiline, fixed_strings=fixed_strings,
            )
        else:
            files, matches, truncated = _run_python(
                root, search_path, pattern,
                glob=glob, type_=type_, before=b, after=a,
                case_insensitive=case_insensitive, head_limit=limit,
                multiline=multiline, fixed_strings=fixed_strings,
            )
    except subprocess.TimeoutExpired:
        return {
            "pattern": pattern, "output": "", "match_count": 0, "file_count": 0,
            "truncated": True, "engine": engine, "note": "search timed out",
        }

    output, byte_truncated = _format(files)
    display = search_path.resolve().relative_to(root).as_posix() if search_path != root else "."
    return {
        "pattern": pattern,
        "path": display,
        "output": output,
        "match_count": matches,
        "file_count": len(files),
        "truncated": truncated or byte_truncated,
        "engine": engine,
    }
