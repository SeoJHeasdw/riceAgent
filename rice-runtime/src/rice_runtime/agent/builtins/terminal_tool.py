"""Builtin terminal tool: execute shell commands on the local host.

Adapted from Hermes copy/tools/terminal_tool.py + copy/tools/environments/local.py.
All Hermes-specific imports (hermes_constants, utils, agent.auxiliary_client, …)
have been replaced with RICE-native stdlib. Core execution logic is preserved.

HITL POLICY: terminal is marked `requires_approval=True`. Every shell command
passes through the shared approval gate (RUNTIME_DESIGN §5.1) before execution.
This is intentional — shell commands are arbitrary external effects.

Limitations vs. Hermes full implementation:
  - Local execution only (no Docker/SSH/Modal/Daytona backends).
  - Background execution is local-process only (no remote task backends).
  - No session snapshot (no env var or cwd persistence across calls).
  - No ANSI strip (output is returned raw).
"""

from __future__ import annotations

import asyncio
import os
import platform
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ...core.paths import RicePaths
from ...core.types import ToolSpec
from ..tool_dispatch import BuiltinTool

if TYPE_CHECKING:
    from ...core.config import ConfigStore
    from ..background_tasks import BackgroundTaskRegistry

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


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """셸이 띄운 백그라운드 자식까지 프로세스 그룹 전체를 SIGKILL 한다.

    start_new_session 으로 셸이 그룹 리더이므로 pgid == proc.pid. 셸 하나만
    죽이면 `cmd &` 로 분기한 손자가 abort 이후에도 부작용을 계속 낸다
    그룹 시그널 불가 시 proc.kill() 폴백.
    """
    try:
        if not _IS_WINDOWS:
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def _spawn_proc(
    command: str, cwd: str | None, env: dict[str, str]
) -> asyncio.subprocess.Process:
    """Spawn `command` in a subshell with a fresh process group (POSIX) so the
    whole tree can be signalled/killed as one. stdout+stderr merge into one PIPE;
    stdin is /dev/null. Foreground drain and background spool both read the PIPE."""
    shell = _find_shell()
    safe = _safe_cwd(cwd)
    return await asyncio.create_subprocess_exec(
        shell, "-c", command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
        cwd=safe,
        env=env,
        start_new_session=not _IS_WINDOWS,
    )


async def _run_command(
    command: str,
    cwd: str | None,
    timeout_s: float,
    env: dict[str, str],
    *,
    registry: "BackgroundTaskRegistry | None" = None,
    session_id: str | None = None,
    auto_background: bool = False,
) -> dict:
    """Run `command` in a subshell; return {output, exit_code}.

    `timeout_s` is the foreground budget. When `auto_background` is on and a
    `registry`/`session_id` are supplied, exceeding the budget while the process
    is still running **promotes it to a background task** instead of killing it
    (§11.5): the still-running process (with its partial output) is handed to the
    registry, which keeps draining stdout to a spool file, and this returns
    ``{task_id, status:"running", ...}``. With auto_background off (or no
    registry), the budget is a hard kill deadline — the original behavior."""
    try:
        proc = await _spawn_proc(command, cwd, env)
    except Exception as exc:  # noqa: BLE001 — spawn failure is data, not a crash
        return {"output": f"[error spawning shell: {exc}]", "exit_code": -1, "timed_out": False}

    # stdout 을 유계 capture로 드레인하되 예산을 넘기면 wait_for가 이 코루틴을
    # 취소한다. 취소 시점까지 받은 prefix(최대 1MB)+표시용 tail은 capture에 있고,
    # 아직 파이프에 남은 바이트는 registry의 _pump가 이어서 읽는다.
    capture = _BoundedOutputCapture()
    pipe_eof = proc.stdout is None

    async def _drain() -> None:
        nonlocal pipe_eof
        if proc.stdout is not None:
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    pipe_eof = True
                    break
                capture.append(chunk)
        await proc.wait()

    try:
        await asyncio.wait_for(_drain(), timeout=timeout_s)
    except asyncio.CancelledError:
        # 턴 abort 가 도구 실행을 취소하면(tool_scheduler 의 task.cancel) 자식
        # 프로세스 그룹을 함께 내리고 reap 까지 마친다 — 안 죽이면 셸/백그라운드
        # 손자가 고아로 계속 돈다. 백그라운드 승격과 무관하게 포그라운드 취소는 kill.
        _kill_process_group(proc)
        try:
            await asyncio.shield(proc.wait())
        except Exception:
            pass
        raise
    except asyncio.TimeoutError:
        # 경계에서 프로세스와 stdout이 모두 끝났다면 background 카드로 승격시키지
        # 않는다. returncode만 보면 안 된다: `sleep 30 &`는 셸 리더가 먼저 끝나도
        # 손자가 pipe/프로세스 그룹을 들고 있어, 여기서 다시 drain하면 timeout을
        # 무시하고 30초 더 블록한다.
        if proc.returncode is not None and pipe_eof:
            output, output_truncated = capture.render()
            return {
                "output": output,
                "exit_code": proc.returncode,
                "timed_out": False,
                "output_truncated": output_truncated,
            }

        # 예산 초과 + 아직 실행 중. auto_background 면 kill 대신 백그라운드로 승격.
        if auto_background and registry is not None and session_id:
            # explicit background와 같은 자원 상한을 승격에도 적용한다. 이미 spawn된
            # 프로세스라 거절 시 반드시 그룹 kill+reap까지 끝내 고아를 만들지 않는다.
            cap_error = registry.capacity_error(session_id)
            if cap_error is not None:
                _kill_process_group(proc)
                try:
                    await proc.wait()
                except Exception:
                    pass
                partial, truncated = capture.render()
                return {
                    "error": cap_error,
                    "output": partial,
                    "exit_code": -1,
                    "timed_out": True,
                    "output_truncated": truncated,
                }
            initial_output, initial_output_truncated = capture.handoff()
            info = registry.adopt(
                session_id,
                command,
                cwd or "",
                proc,
                initial_output=initial_output,
                initial_output_truncated=initial_output_truncated,
            )
            partial, truncated = capture.render()
            info["partial_output"] = partial
            info["output_truncated"] = truncated
            info["note"] = (
                f"still running after {timeout_s:.0f}s — promoted to a background task. "
                "Do NOT poll or sleep-wait; you'll be given the result when it finishes. "
                "Use task_output(task_id) only if you need a mid-run peek, kill_task(task_id) to stop it."
            )
            return info
        _kill_process_group(proc)
        try:
            await proc.wait()
        except Exception:
            pass
        return {
            "output": f"[timeout after {timeout_s:.0f}s]",
            "exit_code": -1,
            "timed_out": True,
        }

    output, output_truncated = capture.render()
    return {
        "output": output,
        "exit_code": proc.returncode,
        "timed_out": False,
        "output_truncated": output_truncated,
    }


# 모델이 제공하는 timeout 의 서버 상한(초). 클램프가 없으면 timeout=86400 같은
# 인자 하나로(사용자가 승인만 하면) 세션이 하루 종일 묶인다 — abort/drain 지연의
# 최악 케이스를 유계로 만든다.
_MIN_TIMEOUT_S = 1.0
_MAX_TIMEOUT_S = 600.0
_DEFAULT_TIMEOUT_S = 60.0

# 셸 출력 상한(바이트). 상한이 없으면 `cat huge.log`/`npm install` 한 번이 컨텍스트
# 윈도를 통째로 삼킨다. 앞부분(무엇을 실행했는지)과 뒷부분(에러·요약은 보통 끝에
# 있다)을 함께 남기고 가운데를 잘라 낸다.
_MAX_OUTPUT_BYTES = 40 * 1024
_OUTPUT_TAIL_BYTES = 10 * 1024  # 뒤에서 보존할 몫(나머지는 앞에서 보존)
# auto-bg 승격 전 stdout도 무한 bytearray로 쌓지 않는다. 스풀의 쓰기 상한과
# 같은 1MB prefix만 핸드오프용으로 보존하고, 모델 표시용 tail 10KB를 별도로
# 굴린다. `yes` 같은 폭주 명령이 60초 foreground budget 동안 메모리를 채우는
# 것을 막는 상주 메모리 상한이다.
_FOREGROUND_CAPTURE_MAX_BYTES = 1024 * 1024


def _truncate_output(data: bytes) -> tuple[str, bool]:
    """바이트 출력을 상한 안으로 자른다(head+tail 보존). (텍스트, 잘림여부) 반환."""
    if len(data) <= _MAX_OUTPUT_BYTES:
        return data.decode("utf-8", errors="replace"), False
    head_bytes = _MAX_OUTPUT_BYTES - _OUTPUT_TAIL_BYTES
    head = data[:head_bytes].decode("utf-8", errors="replace")
    tail = data[-_OUTPUT_TAIL_BYTES:].decode("utf-8", errors="replace")
    omitted = len(data) - _MAX_OUTPUT_BYTES
    marker = f"\n\n[... {omitted} bytes truncated ...]\n\n"
    return head + marker + tail, True


class _BoundedOutputCapture:
    """Foreground stdout collector with a fixed memory ceiling.

    `prefix` is the at-most-1MB payload handed to the background spool. `tail`
    keeps the true last 10KB so a foreground/partial result preserves final
    diagnostics even after the prefix fills. Total memory stays ~1MB regardless
    of command output volume.
    """

    def __init__(self) -> None:
        self._prefix = bytearray()
        self._tail = bytearray()
        self._total = 0

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._total += len(chunk)
        remaining = _FOREGROUND_CAPTURE_MAX_BYTES - len(self._prefix)
        if remaining > 0:
            self._prefix.extend(chunk[:remaining])
        self._tail.extend(chunk)
        if len(self._tail) > _OUTPUT_TAIL_BYTES:
            del self._tail[:-_OUTPUT_TAIL_BYTES]

    def render(self) -> tuple[str, bool]:
        if self._total <= _MAX_OUTPUT_BYTES:
            return _truncate_output(bytes(self._prefix))
        head = bytes(self._prefix[: _MAX_OUTPUT_BYTES - _OUTPUT_TAIL_BYTES]).decode(
            "utf-8", errors="replace"
        )
        tail = bytes(self._tail).decode("utf-8", errors="replace")
        omitted = self._total - _MAX_OUTPUT_BYTES
        marker = f"\n\n[... {omitted} bytes truncated ...]\n\n"
        return head + marker + tail, True

    def handoff(self) -> tuple[bytes, bool]:
        """Return the spool prefix and whether pre-promotion output exceeded it."""
        return bytes(self._prefix), self._total > len(self._prefix)


def _clamp_timeout(raw: object) -> float:
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S
    return min(max(value, _MIN_TIMEOUT_S), _MAX_TIMEOUT_S)


def _make_terminal(
    paths: RicePaths,
    registry: "BackgroundTaskRegistry | None",
    config_store: "ConfigStore | None",
) -> BuiltinTool:
    _env = _sanitize_env()

    async def terminal(args: dict, *, session_id: str = "") -> object:
        workspace = paths.workspace  # live: relocation applied at the turn boundary
        command = args.get("command", "")
        if not command or not isinstance(command, str):
            raise ValueError("command is required and must be a string")
        # cwd: 상대경로는 workspace 아래로, 절대경로는 그대로 허용한다. terminal 은
        # 파일 도구와 달리 의도적으로 "호스트 셸"이다(docstring 참고) — 명령 자체가
        # `cd /` 로 어디든 갈 수 있어 cwd 제한은 방어가 아니며, 실제 방어선은
        # requires_approval=True 의 HITL 게이트다.
        cwd_rel = args.get("cwd")
        if cwd_rel:
            cwd = str(workspace / cwd_rel) if not os.path.isabs(cwd_rel) else cwd_rel
        else:
            cwd = str(workspace)

        term_cfg = config_store.config.terminal if config_store is not None else None
        auto_bg = bool(registry is not None and term_cfg is not None and term_cfg.auto_background_on_timeout)

        # 예산: 모델이 timeout 을 명시하면 그 값, 아니면 auto-bg 켜졌을 때 config
        # 예산(foreground_block_budget_s), 둘 다 아니면 기존 기본(60). auto-bg 켜지면
        # 이 값은 "죽이기 전 한계"가 아니라 "포그라운드로 얼마나 블록할지의 예산"이다.
        raw_timeout = args.get("timeout")
        if raw_timeout is not None:
            budget = _clamp_timeout(raw_timeout)
        elif auto_bg and term_cfg is not None:
            budget = _clamp_timeout(term_cfg.foreground_block_budget_s)
        else:
            budget = _DEFAULT_TIMEOUT_S

        # 명시적 background:true — 즉시 백그라운드로 띄우고 task_id 를 돌려준다.
        # registry 가 없으면(구 경로/테스트) 포그라운드로 폴백한다(명령은 실행됨).
        if bool(args.get("background")) and registry is not None:
            cap_error = registry.capacity_error(session_id)
            if cap_error is not None:
                return {"error": cap_error}
            try:
                proc = await _spawn_proc(command, cwd, _env)
            except Exception as exc:  # noqa: BLE001
                return {"output": f"[error spawning shell: {exc}]", "exit_code": -1, "timed_out": False}
            # 다른 세션의 spawn await와 경쟁해 전역 상한이 찰 수 있다. adopt 직전
            # 무-await 재검사로 check→등록을 이벤트 루프 안에서 원자화한다.
            cap_error = registry.capacity_error(session_id)
            if cap_error is not None:
                _kill_process_group(proc)
                try:
                    await proc.wait()
                except Exception:
                    pass
                return {"error": cap_error}
            return registry.adopt(session_id, command, cwd, proc)

        return await _run_command(
            command, cwd, budget, _env,
            registry=registry,
            session_id=session_id,
            auto_background=auto_bg,
        )

    terminal.needs_session = True  # type: ignore[attr-defined]  # dispatcher가 session_id 주입
    return terminal


_TERMINAL_DESCRIPTION = (
    "Execute a shell command on the local host and return its output. "
    "Commands run in a subshell (bash/sh on POSIX, Git Bash on Windows). "
    "Use for file operations, running scripts, installing packages, etc. "
    "Requires user approval before execution. "
    "Output larger than ~40KB is truncated (head + tail kept); redirect bulk "
    "output to a file and read the parts you need instead. "
    "Long-running commands (builds, dev servers, long scripts) are automatically "
    "moved to the background if they don't finish within the foreground budget: you "
    "get a task_id back and the command keeps running past this turn — you'll be "
    "given its result automatically when it finishes, so do NOT poll or sleep-wait. "
    "Set background=true to background it immediately (e.g. a dev server you'll leave "
    "running). Check on a background task with task_output(task_id); stop it with kill_task(task_id)."
)


def _make_task_output(registry: "BackgroundTaskRegistry") -> BuiltinTool:
    async def task_output(args: dict, *, session_id: str = "") -> object:
        task_id = str(args.get("task_id") or "").strip()
        if not task_id:
            return {"error": "task_id is required"}
        return registry.task_output(task_id, session_id=session_id or None)

    task_output.needs_session = True  # type: ignore[attr-defined]
    return task_output


def _make_kill_task(registry: "BackgroundTaskRegistry") -> BuiltinTool:
    async def kill_task(args: dict, *, session_id: str = "") -> object:
        task_id = str(args.get("task_id") or "").strip()
        if not task_id:
            return {"error": "task_id is required"}
        return registry.kill_task(task_id, session_id=session_id or None)

    kill_task.needs_session = True  # type: ignore[attr-defined]
    return kill_task


def tools(
    paths: RicePaths,
    *,
    registry: "BackgroundTaskRegistry | None" = None,
    config_store: "ConfigStore | None" = None,
) -> list[tuple[ToolSpec, BuiltinTool]]:
    """terminal (+ background 관리 도구 task_output/kill_task).

    `registry`/`config_store` 가 주어지면 background:true / auto-background 승격 +
    task_output/kill_task 가 활성화된다. 없으면(구 경로/독립 테스트) terminal 은
    포그라운드 전용으로 동작한다(background 인자 무시, 기존 동작 그대로)."""
    paths.workspace.mkdir(parents=True, exist_ok=True)  # ensure initial root; handler reads live
    entries: list[tuple[ToolSpec, BuiltinTool]] = [
        (
            ToolSpec(
                name="terminal",
                description=_TERMINAL_DESCRIPTION,
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
                            "description": (
                                "Foreground budget in seconds (default: 60, max: 600). If the command is "
                                "still running after this, it moves to the background (you get a task_id) "
                                "rather than being killed. Keep it small — you'll get the result when it finishes."
                            ),
                        },
                        "background": {
                            "type": "boolean",
                            "description": (
                                "Start the command in the background immediately and return a task_id "
                                "without waiting. Use for dev servers or long jobs you'll leave running."
                            ),
                        },
                    },
                    "required": ["command"],
                },
                source="builtin",
                requires_approval=True,  # shell commands are arbitrary external effects — HITL gate (§5.1)
                parallel_safe=False,  # 임의 부작용 — 모델 호출 순서대로 직렬 실행
            ),
            _make_terminal(paths, registry, config_store),
        ),
    ]
    if registry is not None:
        entries.append((
            ToolSpec(
                name="task_output",
                description=(
                    "Peek at a background task's current output + status (running/exited/killed/"
                    "interrupted) + exit_code by its task_id. Read-only, no approval. You normally "
                    "don't need this — a finished task's result is delivered to you automatically. "
                    "Use it only for a deliberate mid-run check (e.g. a dev server's startup log)."
                ),
                parameters={
                    "type": "object",
                    "properties": {"task_id": {"type": "string", "description": "The task_id returned by terminal."}},
                    "required": ["task_id"],
                },
                source="builtin",
                requires_approval=False,  # read-only
                parallel_safe=True,
            ),
            _make_task_output(registry),
        ))
        entries.append((
            ToolSpec(
                name="kill_task",
                description=(
                    "Stop a background task by its task_id (terminates the whole process group). "
                    "Use to stop a dev server you started, or a command that's stuck or no longer needed."
                ),
                parameters={
                    "type": "object",
                    "properties": {"task_id": {"type": "string", "description": "The task_id returned by terminal."}},
                    "required": ["task_id"],
                },
                source="builtin",
                requires_approval=False,  # 자기가 띄운 프로세스 종료는 저위험 — 무승인(§11.10 사용자 확정)
                parallel_safe=False,  # 프로세스 상태 변경 — 직렬
            ),
            _make_kill_task(registry),
        ))
    return entries
