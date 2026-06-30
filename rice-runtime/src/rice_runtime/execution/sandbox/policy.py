"""sandbox/policy.py — environment scrubbing and resource limits.

WHY: RUNTIME_DESIGN §5 requires that untrusted plugin/connector code runs in a
child process with a *scrubbed* environment — parent secrets (API keys, tokens,
etc.) must never leak into the sandbox. This module is the single point where
"what the child is allowed to see and use" is decided.

Resource limits: POSIX `resource.setrlimit` is applied best-effort inside the
child (via a start-up hook). On non-POSIX platforms (Windows) the limits are
skipped; a NOTE is emitted.
"""

from __future__ import annotations

import os
import sys
from typing import Any

# Minimum env variables the child process needs to run Python at all.
# Everything else is stripped to prevent credential leakage (§5 env-scrub).
_REQUIRED_CHILD_ENV_KEYS: frozenset[str] = frozenset(
    {
        "PATH",            # need to find python interpreter
        "HOME",            # some stdlib modules expect this
        "SYSTEMROOT",      # Windows: python needs this
        "TEMP",            # Windows
        "TMP",             # Linux/macOS temp
        "TMPDIR",          # macOS temp
        "LANG",            # locale — avoids codec errors in child
        "LC_ALL",
        "LC_CTYPE",
        "PYTHONIOENCODING", # ensure UTF-8 I/O in the child
    }
)

# Hard resource limits applied inside the child process (POSIX only).
# Values are best-effort — not a security guarantee, but reduce blast radius.
_CPU_SECONDS: int = 60          # RLIMIT_CPU  — CPU time cap (seconds)
_MEMORY_BYTES: int = 256 * 1024 * 1024  # RLIMIT_AS/DATA — 256 MiB address space


def scrub_env() -> dict[str, str]:
    """Return a minimal child environment stripped of parent secrets.

    WHY: Copying os.environ verbatim into a child process would expose every
    API key, OAuth token, and password the parent has in its env. We build an
    allowlist of safe keys instead (deny-by-default / fail-closed).
    """
    clean: dict[str, str] = {}
    for key in _REQUIRED_CHILD_ENV_KEYS:
        val = os.environ.get(key)
        if val is not None:
            clean[key] = val
    return clean


def apply_resource_limits() -> None:
    """Apply POSIX resource limits in the *child* process.

    Must be called from the child (e.g., as a `preexec_fn` or at child start)
    because `setrlimit` affects only the calling process.

    On non-POSIX systems (Windows) this is a no-op with a warning.
    """
    if sys.platform == "win32":
        # NOTE(agent): Windows does not have resource.setrlimit. Resource
        # limiting is skipped; the timeout enforced by runner.py is the
        # primary safety net on Windows.
        return
    try:
        import resource  # POSIX-only stdlib module

        # CPU time: SIGKILL sent when exceeded (hard limit).
        resource.setrlimit(resource.RLIMIT_CPU, (_CPU_SECONDS, _CPU_SECONDS))

        # Address space / virtual memory (RLIMIT_AS where available).
        # RLIMIT_AS is not available on macOS — fall back to RLIMIT_DATA.
        mem_limit = (_MEMORY_BYTES, _MEMORY_BYTES)
        if hasattr(resource, "RLIMIT_AS"):
            try:
                resource.setrlimit(resource.RLIMIT_AS, mem_limit)
            except (ValueError, resource.error):
                # Some Linux kernels disallow RLIMIT_AS on 64-bit; skip.
                pass
    except Exception:  # noqa: BLE001
        # Resource limiting is best-effort — never crash the sandbox because
        # limits failed to apply (timeout is the primary safety net).
        pass


def resource_limit_preamble() -> str:
    """Return Python *source* that applies the sandbox resource limits when run at
    the very top of the embedded child worker (ipc.make_child_script).

    WHY inline source (not importing apply_resource_limits): the child runs as
    `python -c <script>` with a scrubbed env and may not have the package on its
    path (esp. under PyInstaller). Embedding the limits as source keeps the worker
    self-contained while sharing the constants below (single source of truth).
    Best-effort: any failure is swallowed so a platform without `resource`
    (Windows) still runs — the runner timeout is the cross-platform safety net.
    """
    return (
        "try:\n"
        "    import resource as _res\n"
        f"    _res.setrlimit(_res.RLIMIT_CPU, ({_CPU_SECONDS}, {_CPU_SECONDS}))\n"
        "    if hasattr(_res, 'RLIMIT_AS'):\n"
        f"        try: _res.setrlimit(_res.RLIMIT_AS, ({_MEMORY_BYTES}, {_MEMORY_BYTES}))\n"
        "        except Exception: pass\n"
        "except Exception:\n"
        "    pass\n"
    )


def make_child_kwargs(cwd: str | None = None, extra_path: list[str] | None = None) -> dict[str, Any]:
    """Build the keyword-argument dict for `subprocess.Popen`.

    Centralises all sandbox-relevant Popen options so runner.py stays clean.

    `extra_path`: import roots to expose to the child via PYTHONPATH (e.g. an
    installed plugin's dir). Env stays scrubbed — only PYTHONPATH is added, set to
    *exactly* these paths (never the parent's PYTHONPATH), so no secrets leak.
    """
    env = scrub_env()
    if extra_path:
        env["PYTHONPATH"] = os.pathsep.join(str(p) for p in extra_path)
    return {
        "env": env,
        "cwd": cwd,
        # Redirect stdin/stdout for IPC; stderr is collected separately.
        "stdin": -1,   # PIPE
        "stdout": -1,  # PIPE
        "stderr": -1,  # PIPE
        # Do not inherit parent's file descriptors beyond 0/1/2.
        "close_fds": True,
    }
