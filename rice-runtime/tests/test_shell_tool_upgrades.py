"""Regression guard for the shell-tool upgrades (code-search + file I/O ergonomics).

Covers:
  - grep: regex search (ripgrep, Python fallback), context lines, head_limit cap,
    glob/type filters, and workspace-sandbox containment.
  - read_file: line-numbered output ("N→...") + offset/limit windowing.
  - edit_file: re-read nudge + "line-number prefix isn't part of the file" hint.
  - list_dir: large-subdirectory summarization (count + extension breakdown).
  - terminal: output byte cap (head+tail kept).

When no standalone `rg` binary is installed the Python fallback is the live path;
tests that must be engine-deterministic force it via monkeypatch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rice_runtime.agent.builtins import _search_engine, file_tools, terminal_tool
from rice_runtime.core.paths import RicePaths


def _tools(tmp_path: Path) -> dict:
    paths = RicePaths.from_env(str(tmp_path / "rice"))
    return {spec.name: fn for spec, fn in file_tools.tools(paths)}, paths


def _force_python_engine(monkeypatch) -> None:
    monkeypatch.setattr(_search_engine, "find_ripgrep", lambda: None)


# ── read_file: line numbers + offset/limit ───────────────────────────────────


async def test_read_file_is_line_numbered(tmp_path):
    tools, paths = _tools(tmp_path)
    (paths.workspace / "a.txt").write_text("alpha\nbeta\ngamma", encoding="utf-8")

    out = await tools["read_file"]({"path": "a.txt"})

    assert out["content"] == "1→alpha\n2→beta\n3→gamma"
    assert out["total_lines"] == 3
    assert out["start_line"] == 1 and out["end_line"] == 3
    assert out["truncated"] is False


async def test_read_file_offset_limit_window(tmp_path):
    tools, paths = _tools(tmp_path)
    (paths.workspace / "big.txt").write_text(
        "".join(f"line{i}\n" for i in range(1, 21)), encoding="utf-8"
    )

    out = await tools["read_file"]({"path": "big.txt", "offset": 5, "limit": 3})

    assert out["content"] == "5→line5\n6→line6\n7→line7\n"
    assert out["start_line"] == 5 and out["end_line"] == 7
    assert out["total_lines"] == 20
    assert out["truncated"] is True  # more lines remain after the window


# ── edit_file: re-read nudge ─────────────────────────────────────────────────


async def test_edit_file_not_found_error_nudges_reread(tmp_path):
    tools, paths = _tools(tmp_path)
    (paths.workspace / "c.py").write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(ValueError) as ei:
        # The model pasted the read_file line-number prefix — must be rejected
        # with guidance, not silently.
        await tools["edit_file"]({"path": "c.py", "old_text": "1→x = 1", "new_text": "x = 2"})

    msg = str(ei.value)
    assert "read_file" in msg and "N→" in msg


# ── grep: Python fallback engine (deterministic) ─────────────────────────────


async def test_grep_regex_match_grouped_output(tmp_path, monkeypatch):
    _force_python_engine(monkeypatch)
    tools, paths = _tools(tmp_path)
    (paths.workspace / "m.py").write_text("def foo():\n    return 42\n\ndef bar():\n    pass\n", encoding="utf-8")

    out = await tools["grep"]({"pattern": r"def \w+\("})

    assert out["engine"] == "python"
    assert out["match_count"] == 2
    assert out["file_count"] == 1
    assert "m.py" in out["output"]
    assert "1:def foo():" in out["output"]  # ':' marks a match line
    assert "4:def bar():" in out["output"]


async def test_grep_context_lines(tmp_path, monkeypatch):
    _force_python_engine(monkeypatch)
    tools, paths = _tools(tmp_path)
    (paths.workspace / "ctx.txt").write_text("a\nb\nNEEDLE\nc\nd\n", encoding="utf-8")

    out = await tools["grep"]({"pattern": "NEEDLE", "-C": 1})

    # match line uses ':', context uses '-'
    assert "3:NEEDLE" in out["output"]
    assert "2-b" in out["output"] and "4-c" in out["output"]


async def test_grep_case_insensitive(tmp_path, monkeypatch):
    _force_python_engine(monkeypatch)
    tools, paths = _tools(tmp_path)
    (paths.workspace / "case.txt").write_text("Hello World\n", encoding="utf-8")

    hit = await tools["grep"]({"pattern": "hello world", "-i": True})
    miss = await tools["grep"]({"pattern": "hello world"})

    assert hit["match_count"] == 1
    assert miss["match_count"] == 0


async def test_grep_head_limit_truncates(tmp_path, monkeypatch):
    _force_python_engine(monkeypatch)
    tools, paths = _tools(tmp_path)
    (paths.workspace / "many.txt").write_text("".join("hit\n" for _ in range(50)), encoding="utf-8")

    out = await tools["grep"]({"pattern": "hit", "head_limit": 10})

    assert out["match_count"] == 10
    assert out["truncated"] is True


async def test_grep_glob_and_type_filter(tmp_path, monkeypatch):
    _force_python_engine(monkeypatch)
    tools, paths = _tools(tmp_path)
    (paths.workspace / "keep.py").write_text("TARGET\n", encoding="utf-8")
    (paths.workspace / "skip.md").write_text("TARGET\n", encoding="utf-8")

    by_glob = await tools["grep"]({"pattern": "TARGET", "glob": "*.py"})
    by_type = await tools["grep"]({"pattern": "TARGET", "type": "py"})

    for out in (by_glob, by_type):
        assert out["match_count"] == 1
        assert "keep.py" in out["output"] and "skip.md" not in out["output"]


async def test_grep_stays_in_workspace(tmp_path, monkeypatch):
    _force_python_engine(monkeypatch)
    tools, paths = _tools(tmp_path)

    with pytest.raises(ValueError):
        await tools["grep"]({"pattern": "x", "path": "../.."})


async def test_grep_does_not_follow_symlink_escape(tmp_path, monkeypatch):
    """Mirror of the adversarial search_files guard: grep must not read through a
    symlink pointing outside the workspace."""
    _force_python_engine(monkeypatch)
    tools, paths = _tools(tmp_path)
    workspace = paths.workspace.resolve()

    secret = tmp_path / "outside-secret.txt"
    secret.write_text("TOPSECRET_MARKER", encoding="utf-8")
    (workspace / "normal.txt").write_text("plain TOPSECRET_MARKER 아님", encoding="utf-8")
    (workspace / "leak.txt").symlink_to(secret)
    (workspace / "leakdir").symlink_to(secret.parent)

    out = await tools["grep"]({"pattern": "TOPSECRET_MARKER"})

    assert "normal.txt" in out["output"]
    assert "leak.txt" not in out["output"] and "leakdir" not in out["output"]


async def test_grep_engine_agnostic_contract(tmp_path):
    """Whatever engine is present (rg or Python), the result contract holds."""
    tools, paths = _tools(tmp_path)
    (paths.workspace / "z.txt").write_text("findme\n", encoding="utf-8")

    out = await tools["grep"]({"pattern": "findme"})

    assert out["engine"] in ("ripgrep", "python")
    assert out["match_count"] == 1
    assert "z.txt" in out["output"]


# ── list_dir: large-subdir summarization ─────────────────────────────────────


async def test_list_dir_summarizes_large_subdir(tmp_path):
    tools, paths = _tools(tmp_path)
    big = paths.workspace / "vendored"
    big.mkdir(parents=True)
    for i in range(120):
        (big / f"f{i}.py").write_text("x\n", encoding="utf-8")
    (big / "readme.md").write_text("x\n", encoding="utf-8")
    (paths.workspace / "top.txt").write_text("x\n", encoding="utf-8")

    out = await tools["list_dir"]({"path": "."})

    by_path = {e["path"]: e for e in out["entries"]}
    assert "top.txt" in by_path  # normal file still enumerated
    vend = by_path["vendored"]
    assert vend["type"] == "dir"
    assert "summary" in vend  # too big → summarized, not enumerated
    assert vend["summary"]["file_count"] == 121
    assert vend["summary"]["by_extension"][".py"] == 120
    # did NOT recurse into the summarized directory
    assert not any(p.startswith("vendored/") for p in by_path)


# ── terminal: output byte cap ────────────────────────────────────────────────


def test_terminal_truncate_output_keeps_head_and_tail():
    small = b"hello"
    text, truncated = terminal_tool._truncate_output(small)
    assert text == "hello" and truncated is False

    big = (b"H" * 60_000) + (b"T" * 60_000)
    text, truncated = terminal_tool._truncate_output(big)
    assert truncated is True
    assert text.startswith("H") and text.endswith("T")
    assert "truncated" in text
    assert len(text.encode("utf-8")) < len(big)


async def test_terminal_run_truncates_large_output(tmp_path):
    paths = RicePaths.from_env(str(tmp_path / "rice"))
    handler = {spec.name: fn for spec, fn in terminal_tool.tools(paths)}["terminal"]

    out = await handler({"command": "python3 -c \"print('x'*100000)\""})

    assert out["exit_code"] == 0
    assert out["output_truncated"] is True
    assert "truncated" in out["output"]
