"""sandbox/runner.py — child-process spawn, timeout enforcement, result collection.

WHY THIS EXISTS:
  RUNTIME_DESIGN §5 mandates that plugin/connector code runs in an isolated
  child process, never in-process. This is the *only* place untrusted code is
  launched. `ChildProcessSandbox` is the concrete ISandbox implementation that
  replaces the `NoopSandbox` stub.

Design choices:
  - Uses `asyncio.create_subprocess_exec` for non-blocking I/O — the parent
    event loop stays responsive while the child runs.
  - Timeout is enforced via `asyncio.wait_for`. On expiry the child is killed
    (SIGKILL / TerminateProcess on Windows) and `SandboxResult(ok=False,
    error="timeout")` is returned.
  - The child's env is scrubbed by `policy.scrub_env()` — no parent secrets
    leak into the sandbox (RUNTIME_DESIGN §5).
  - Resource limits (RLIMIT_CPU/RLIMIT_AS) are applied *inside* the child
    (best-effort POSIX) via `policy.resource_limit_preamble()`, which
    ipc.make_child_script() prepends to the worker source.
  - Result is always `SandboxResult` — never an exception — so callers can
    pattern-match on `ok` without try/except.

Invariant: `ChildProcessSandbox.run` MUST NOT execute untrusted code in-process.
This is explicitly tested in test_execution.py (acceptance criterion #1).
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from ...execution.interfaces import ISandbox, SandboxResult
from .ipc import child_script_args, decode_response, encode_request
from .policy import make_child_kwargs


class ChildProcessSandbox(ISandbox):
    """Concrete ISandbox: child process + env scrub (RUNTIME_DESIGN §5).

    Every `run()` call:
      1. Spawns a new child Python process via asyncio.
      2. Sends one JSON-line request over stdin.
      3. Reads one JSON-line response from stdout.
      4. Kills the child and returns timeout error if `timeout_s` elapses.

    WHY new process per call: simplicity + isolation. The demo has low
    frequency; process startup (~50 ms) is acceptable. A process pool can be
    added later without changing callers.
    """

    async def run(
        self,
        *,
        entrypoint: str,
        payload: dict[str, Any],
        timeout_s: float = 30.0,
    ) -> SandboxResult:
        """Run `entrypoint` in an isolated child process.

        Never executes in-process — see invariant above.
        """
        try:
            return await self._run_child(
                entrypoint=entrypoint, payload=payload, timeout_s=timeout_s
            )
        except Exception as exc:  # noqa: BLE001
            # Unexpected errors (spawn failure, OS error, …) are also
            # fail-closed: never propagate as exceptions to callers.
            return SandboxResult(ok=False, error=f"sandbox error: {exc}")

    async def _run_child(
        self,
        *,
        entrypoint: str,
        payload: dict[str, Any],
        timeout_s: float,
    ) -> SandboxResult:
        """Spawn the child, send the request, collect the response.

        The timeout is enforced *here*, around `communicate()`, where `proc` is in
        scope — so on expiry we can actually `kill()` the child. (Wrapping the whole
        coroutine in `asyncio.wait_for` does NOT kill the subprocess: cancelling
        `communicate()` leaves the child running as an orphan.)
        """
        args = child_script_args()
        kwargs = make_child_kwargs()
        # asyncio subprocess: stdin/stdout are async streams.
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=kwargs["env"],
            close_fds=True,
        )
        request_bytes = encode_request(entrypoint, payload)
        try:
            # communicate() sends stdin, reads stdout+stderr, waits for exit.
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=request_bytes), timeout=timeout_s
            )
        except asyncio.TimeoutError:
            # Hard-kill the runaway child, then reap it (no orphan).
            await self._terminate(proc)
            return SandboxResult(ok=False, error="timeout")
        except Exception as exc:  # noqa: BLE001
            await self._terminate(proc)
            return SandboxResult(ok=False, error=f"ipc error: {exc}")

        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

        if not stdout_text:
            # Child produced no output — likely crashed before responding.
            return SandboxResult(
                ok=False,
                stdout="",
                stderr=stderr_text,
                error=f"child exited with code {proc.returncode} and no output",
            )

        try:
            ok, result, error = decode_response(stdout_text.splitlines()[-1])
            return SandboxResult(
                ok=ok,
                stdout=stdout_text,
                stderr=stderr_text,
                result=result,
                error=error,
            )
        except Exception as exc:  # noqa: BLE001
            return SandboxResult(
                ok=False,
                stdout=stdout_text,
                stderr=stderr_text,
                error=f"response parse error: {exc}",
            )

    @staticmethod
    async def _terminate(proc: asyncio.subprocess.Process) -> None:
        """Kill and reap a child process, tolerating an already-exited one."""
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except Exception:  # noqa: BLE001
            pass
