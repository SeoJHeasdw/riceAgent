"""sandbox/ipc.py — JSON-line stdin/stdout protocol between parent and child.

WHY: We need a simple, deterministic, text-only channel over the subprocess
stdin/stdout pipes. JSON-lines (one JSON object per line) meets those
requirements: no framing, easy to test, language-agnostic, no external deps.

Protocol (EXECUTION_MD §Sandbox):
  → parent writes one JSON line to child stdin:  {"entrypoint": ..., "payload": ...}
  ← child writes one JSON line to stdout:        {"ok": true/false, "result": ..., "error": ...}

The child process produced by `make_child_script()` executes the entrypoint
string as a Python expression evaluated in a restricted namespace containing
only the payload dict. Keeping evaluation in the child is safe — the parent
only writes a JSON payload, never raw code; the entrypoint is an opaque name
that maps to a registered function *inside* the child worker.

For the demo phase the child worker is a minimal Python script embedded here.
The entrypoint is a dotted-path string (`module.function`) that the worker
imports and calls with the payload. This is how plugin tools are dispatched.
"""

from __future__ import annotations

import json
import sys
from typing import Any


# ─── wire encoding ───────────────────────────────────────────────────────────

def encode_request(entrypoint: str, payload: dict[str, Any]) -> bytes:
    """Serialise a sandbox request as a UTF-8 JSON line (LF-terminated)."""
    line = json.dumps({"entrypoint": entrypoint, "payload": payload}, ensure_ascii=False)
    return (line + "\n").encode("utf-8")


def decode_request(line: bytes | str) -> tuple[str, dict[str, Any]]:
    """Parse a JSON-line request. Returns (entrypoint, payload)."""
    data = json.loads(line)
    return data["entrypoint"], data.get("payload", {})


def encode_response(ok: bool, result: Any = None, error: str | None = None) -> bytes:
    """Serialise a sandbox response as a UTF-8 JSON line (LF-terminated)."""
    line = json.dumps({"ok": ok, "result": result, "error": error}, ensure_ascii=False)
    return (line + "\n").encode("utf-8")


def decode_response(line: bytes | str) -> tuple[bool, Any, str | None]:
    """Parse a JSON-line response. Returns (ok, result, error)."""
    data = json.loads(line)
    return data["ok"], data.get("result"), data.get("error")


# ─── child worker script ─────────────────────────────────────────────────────

def make_child_script() -> str:
    """Return the source of the child worker Python script.

    The worker:
      1. Reads one JSON line from stdin (the request).
      2. Imports `module` from `entrypoint` ("module.func") and calls `func(payload)`.
      3. Writes one JSON line to stdout (the response), then exits.

    Errors (import, call, …) are caught; the response sets ok=False.

    WHY the script is embedded here rather than a standalone .py file: keeping
    it in the same module makes the protocol and the child symmetric — one
    import to understand the full IPC contract. It also survives PyInstaller
    bundling without needing resource files.
    """
    # WHY: Module-path entrypoints (e.g. "os.getpid") call func(**payload) so
    # that tools with named parameters work naturally. Functions that take no
    # args (e.g. os.getpid) receive an empty payload dict → no arguments passed.
    # Bare-string entrypoints (no ".") are eval'd as Python expressions for
    # simple probes (acceptance tests, debug) — builtins are available so range/
    # sorted/etc. work in the expression context.
    # Special sentinel "__sleep__" blocks for payload["seconds"] — used by tests
    # to reliably trigger the sandbox timeout without a real plugin.
    from .policy import resource_limit_preamble

    # Apply RLIMIT_CPU/RLIMIT_AS in the child *before* any untrusted code runs.
    return (
        resource_limit_preamble()
        + "import json, sys, importlib, time\n"
        "raw = sys.stdin.readline()\n"
        "try:\n"
        "    data = json.loads(raw)\n"
        "    ep   = data['entrypoint']\n"
        "    payload = data.get('payload', {})\n"
        "    if ep == '__sleep__':\n"
        "        time.sleep(float(payload.get('seconds', 10)))\n"
        "        result = 'slept'\n"
        "    elif '.' in ep:\n"
        "        mod_name, func_name = ep.rsplit('.', 1)\n"
        "        mod  = importlib.import_module(mod_name)\n"
        "        func = getattr(mod, func_name)\n"
        "        result = func(**payload) if payload else func()\n"
        "    else:\n"
        "        # bare entrypoint — eval with standard builtins for simple probes\n"
        "        result = eval(ep, {'payload': payload})\n"
        "    print(json.dumps({'ok': True, 'result': result}), flush=True)\n"
        "except Exception as e:\n"
        "    print(json.dumps({'ok': False, 'result': None, 'error': str(e)}), flush=True)\n"
    )


def child_script_args() -> list[str]:
    """CLI args to pass to `subprocess.Popen` to run the embedded child script.

    Uses the *same* Python interpreter as the parent (sys.executable) so the
    sandbox worker is always on the same Python version.
    """
    return [sys.executable, "-c", make_child_script()]
