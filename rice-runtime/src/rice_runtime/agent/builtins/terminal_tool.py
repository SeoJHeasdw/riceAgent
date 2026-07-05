"""Builtin terminal tool: execute shell commands on the local host.

Adapted from Hermes copy/tools/terminal_tool.py + copy/tools/environments/local.py.
All Hermes-specific imports (hermes_constants, utils, agent.auxiliary_client, …)
have been replaced with RICE-native stdlib. Core execution logic is preserved.

HITL POLICY: terminal is marked `requires_approval=True`. Every shell command
passes through the shared approval gate (RUNTIME_DESIGN §5.1) before execution.
This is intentional — shell commands are arbitrary external effects.

Limitations vs. Hermes full implementation:
  - Local execution only (no Docker/SSH/Modal/Daytona backends).
  - No background process support (foreground only).
  - No session snapshot (no env var or cwd persistence across calls).
  - No ANSI strip (output is returned raw).
"""

from __future__ import annotations

import asyncio
import os
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ...core.paths import RicePaths
from ...core.types import ToolSpec
from ..tool_dispatch import BuiltinTool

_IS_WINDOWS = platform.system() == "Windows"

# API keys and secrets that should NEVER leak into spawned subprocesses.
# A strict deny-list: any key that could let a subprocess call the LLM
# provider on the user's behalf without RICE's oversight.
_CREDENTIAL_ENV_BLOCKLIST: frozenset[str] = frozenset({
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "OPENAI_BASE_URL",
    "OPENAI_ORG_ID",
    "OPENAI_ORGANIZATION",
    "OPENROUTER_API_KEY",
    "GOOGLE_API_KEY",
    "DEEPSEEK_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "TOGETHER_API_KEY",
    "PERPLEXITY_API_KEY",
    "COHERE_API_KEY",
    "FIREWORKS_API_KEY",
    "XAI_API_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "MODAL_TOKEN_ID",
    "MODAL_TOKEN_SECRET",
    "DAYTONA_API_KEY",
})

# venv markers that must not leak into subprocesses to avoid clobbering
# another project's environment (adapted from Hermes local.py).
_VENV_MARKER_VARS: tuple[str, ...] = ("VIRTUAL_ENV", "CONDA_PREFIX")

# Standard POSIX PATH fallbacks for environments with a minimal PATH.
_SANE_PATH = (
    "/opt/homebrew/bin:/opt/homebrew/sbin:"
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)


def _sanitize_env() -> dict[str, str]:
    """Build a subprocess env: inherit os.environ, strip credentials + venv markers."""
    env = os.environ.copy()
    for key in _CREDENTIAL_ENV_BLOCKLIST:
        env.pop(key, None)
    for key in _VENV_MARKER_VARS:
        env.pop(key, None)

    # Ensure common POSIX paths are reachable even in a minimal environment.
    if not _IS_WINDOWS:
        path = env.get("PATH", "")
        existing = set(path.split(":")) if path else set()
        extras = [e for e in _SANE_PATH.split(":") if e and e not in existing]
        if extras:
            env["PATH"] = path + ":" + ":".join(extras) if path else ":".join(extras)

    return env


def _find_shell() -> str:
    """Return a POSIX-compatible shell executable path.

    Prefers the user's $SHELL when it is a well-known sh-family shell (bash,
    zsh, sh, dash, ksh). Falls back to bash/sh lookup in PATH.
    """
    if not _IS_WINDOWS:
        user_shell = os.environ.get("SHELL", "")
        sh_family = {"bash", "zsh", "sh", "dash", "ksh", "mksh"}
        if (
            user_shell
            and os.path.isfile(user_shell)
            and os.access(user_shell, os.X_OK)
            and Path(user_shell).name in sh_family
        ):
            return user_shell
        for name in ("bash", "sh"):
            found = shutil.which(name)
            if found:
                return found
        return "/bin/sh"

    # Windows: look for Git Bash
    found = shutil.which("bash")
    if found:
        return found
    for candidate in (
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "bash.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Git", "bin", "bash.exe"),
    ):
        if candidate and os.path.isfile(candidate):
            return candidate
    raise RuntimeError(
        "Shell not found. On Windows, install Git for Windows: https://git-scm.com/download/win"
    )


def _msys_to_windows_path(cwd: str) -> str:
    """Translate MSYS-style paths (/c/Users/x) to Windows form (C:\\Users\\x).
    No-op on non-Windows or non-MSYS paths.
    """
    if not _IS_WINDOWS or not cwd:
        return cwd
    m = re.match(r"^/([a-zA-Z])(/.*)?$", cwd)
    if not m:
        return cwd
    drive = m.group(1).upper()
    tail = (m.group(2) or "").replace("/", "\\")
    return f"{drive}:{tail or chr(92)}"


def _safe_cwd(cwd: str | None) -> str:
    """Return cwd if it exists, else the nearest existing ancestor, else tempdir."""
    if not cwd:
        return os.getcwd()
    resolved = _msys_to_windows_path(cwd) if _IS_WINDOWS else cwd
    if os.path.isdir(resolved):
        return resolved
    parent = os.path.dirname(resolved)
    while parent and parent != resolved:
        if os.path.isdir(parent):
            return parent
        resolved = parent
        parent = os.path.dirname(parent)
    return tempfile.gettempdir()


async def _run_command(
    command: str,
    cwd: str | None,
    timeout_s: float,
    env: dict[str, str],
) -> dict:
    """Run `command` in a subshell; return {output, exit_code}."""
    shell = _find_shell()
    safe = _safe_cwd(cwd)

    if _IS_WINDOWS:
        args = [shell, "-c", command]
    else:
        args = [shell, "-c", command]

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=safe,
            env=env,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s
            )
        except asyncio.CancelledError:
            # 턴 abort 가 도구 실행을 취소하면(tool_scheduler 의 task.cancel)
            # 자식 프로세스를 함께 내린다 — 안 죽이면 셸이 고아로 계속 돈다.
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.wait()
            except Exception:
                pass
            return {
                "output": f"[timeout after {timeout_s:.0f}s]",
                "exit_code": -1,
                "timed_out": True,
            }

        output = stdout_bytes.decode("utf-8", errors="replace")
        return {
            "output": output,
            "exit_code": proc.returncode,
            "timed_out": False,
        }
    except Exception as exc:
        return {
            "output": f"[error spawning shell: {exc}]",
            "exit_code": -1,
            "timed_out": False,
        }


# 모델이 제공하는 timeout 의 서버 상한(초). 클램프가 없으면 timeout=86400 같은
# 인자 하나로(사용자가 승인만 하면) 세션이 하루 종일 묶인다 — abort/drain 지연의
# 최악 케이스를 유계로 만든다.
_MIN_TIMEOUT_S = 1.0
_MAX_TIMEOUT_S = 600.0
_DEFAULT_TIMEOUT_S = 60.0


def _clamp_timeout(raw: object) -> float:
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S
    return min(max(value, _MIN_TIMEOUT_S), _MAX_TIMEOUT_S)


def _make_terminal(workspace: Path) -> BuiltinTool:
    _env = _sanitize_env()

    async def terminal(args: dict) -> object:
        command = args.get("command", "")
        if not command or not isinstance(command, str):
            raise ValueError("command is required and must be a string")
        timeout_s = _clamp_timeout(args.get("timeout", _DEFAULT_TIMEOUT_S))
        # cwd: 상대경로는 workspace 아래로, 절대경로는 그대로 허용한다. terminal 은
        # 파일 도구와 달리 의도적으로 "호스트 셸"이다(docstring 참고) — 명령 자체가
        # `cd /` 로 어디든 갈 수 있어 cwd 제한은 방어가 아니며, 실제 방어선은
        # requires_approval=True 의 HITL 게이트다.
        cwd_rel = args.get("cwd")
        if cwd_rel:
            cwd = str(workspace / cwd_rel) if not os.path.isabs(cwd_rel) else cwd_rel
        else:
            cwd = str(workspace)
        return await _run_command(command, cwd, timeout_s, _env)

    return terminal


def tools(paths: RicePaths) -> list[tuple[ToolSpec, BuiltinTool]]:
    workspace = paths.workspace
    workspace.mkdir(parents=True, exist_ok=True)
    workspace = workspace.resolve()
    return [
        (
            ToolSpec(
                name="terminal",
                description=(
                    "Execute a shell command on the local host and return its output. "
                    "Commands run in a subshell (bash/sh on POSIX, Git Bash on Windows). "
                    "Use for file operations, running scripts, installing packages, etc. "
                    "Requires user approval before execution."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Shell command to execute.",
                        },
                        "cwd": {
                            "type": "string",
                            "description": (
                                "Working directory. Relative paths are resolved under the workspace root. "
                                "Omit to use the workspace root."
                            ),
                        },
                        "timeout": {
                            "type": "number",
                            "description": "Execution timeout in seconds (default: 60, max: 600).",
                        },
                    },
                    "required": ["command"],
                },
                source="builtin",
                requires_approval=True,  # shell commands are arbitrary external effects — HITL gate (§5.1)
            ),
            _make_terminal(workspace),
        ),
    ]
