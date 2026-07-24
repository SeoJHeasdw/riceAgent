"""Background shell tasks (RICE_AGENT_EXECUTION.md §11) — 턴을 넘겨 사는 실행.

terminal 이 `background:true` 로 띄우거나 auto-background 승격으로 넘어간 명령은
턴이 끝나도 계속 돈다. 이 모듈이 그 작업들을 소유한다:

  * **adopt** — 이미 spawn 된 프로세스(+ 부분 출력)를 받아 감시를 시작한다.
    terminal 이 프로세스를 띄우고(그쪽이 env·cwd·프로세스 그룹의 주인), 레지스트리는
    그걸 넘겨받아 stdout 을 스풀 파일로 pump 하고 완료를 기다린다.
  * **완료 큐(§11.2)** — 완료는 DB 에 `acknowledged=0` 로 기록된다. 이 "미확인 완료
    큐"를 두 지점에서 드레인한다: 창 A(턴 내 반복 사이, loop.py)·창 B(다음 턴
    ASSEMBLE, orchestrator.py). DB 백업이라 런타임이 재시작해도 미전달 완료가
    살아남아 다음 턴에 관찰로 전달된다.
  * **task_output / kill_task / kill_session / reconcile_on_boot** — 조회·종료·수명주기.

완료 감지는 작업마다 asyncio 태스크 하나(`_pump`): stdout→스풀 pump + `proc.wait()`.
`server/scheduler.py` 의 상주 감시 태스크 패턴과 동형이다.

**소유·주입(§11.3/§11.8):** 레지스트리는 단일 인스턴스로 composition_root 가
생성해 (a) terminal 도구 클로저와 (b) 오케스트레이터에 주입한다. 내부적으로
session_id 로 키잉하므로 `_render_states`·`_aborts` 와 같은 "세션 스코프 소유"다.
오케스트레이터는 이걸 매 턴 ToolDispatcher 에 넘겨 창 A 드레인을 가능케 한다.

완료 관찰은 `role="user"` synthetic 메시지로 주입한다(§11.2 는 role="tool" 을
기술하나, context_manager 의 wire repair 가 직전 assistant 의 매칭 tool_call 이
없는 tool 메시지를 orphan 으로 드롭하고 Claude 가 tool_result 인접 tool_use 를
요구하므로 "별개의 새 tool 관찰"은 wire 에서 유효하지 않다 — 기존 4개 synthetic
주입 패턴[plan_status·nudge]과 같은 wire-safe 경로를 쓴다).
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import signal
from dataclasses import dataclass
from pathlib import Path

from ..core.config import ConfigStore
from ..core.storage.sqlite import Database
from ..core.types import Message, new_id, now_iso
from .builtins.terminal_tool import _truncate_output

log = logging.getLogger("rice.agent.background_tasks")

_IS_WINDOWS = platform.system() == "Windows"

# 완료된(=running 이 아닌) 상태들 — 미확인 완료 큐 드레인의 판별.
_TERMINAL_STATUSES = ("exited", "killed", "interrupted")

# 스풀 파일 디스크 상한(바이트). 폭주 출력(`yes` 등)이 디스크를 채우지 않게, 이만큼
# 쓴 뒤엔 마커 한 줄만 남기고 이후 출력을 버린다(작업은 계속 돈다 — 필요하면
# kill_task). 표시는 어차피 40KB(_truncate_output)로 잘리므로 앞부분 보존으로 충분.
_SPOOL_MAX_BYTES = 1024 * 1024

# 세션당 보존하는 완료 작업 수 상한 — 새 작업 adopt 시 그 세션의 오래된 acked
# 완료 행/스풀을 이만큼만 남기고 정리한다. 프론트 카드가 최근 작업을 조회하되
# 무한 누적하지 않게(learning_episode_cap 과 같은 철학).
_RETAINED_COMPLETED_PER_SESSION = 20


@dataclass
class _LiveTask:
    """이 프로세스가 살아 추적 중인 작업의 인메모리 핸들. DB 행이 durable 정본이고,
    이건 프로세스 핸들·스풀 경로 등 런타임 전용 상태를 든다."""

    id: str
    session_id: str
    command: str
    cwd: str
    output_path: str
    proc: asyncio.subprocess.Process | None
    started_at: str
    status: str = "running"
    exit_code: int | None = None
    killed: bool = False
    watcher: "asyncio.Task | None" = None
    spool: object = None  # 실행 중 열린 파일 핸들(완료 시 close)
    spooled: int = 0  # 스풀에 쓴 바이트(상한 초과 판정)
    capped: bool = False  # 상한 초과로 이후 출력을 버렸는가
    purged: bool = False  # 세션 삭제로 DB 행이 제거됨(감시 finally가 되살리면 안 됨)


@dataclass(frozen=True)
class CompletedTask:
    """드레인이 돌려주는 완료 스냅샷 — 호출자(창 A/B)가 관찰 메시지 + SSE 를 만든다."""

    id: str
    command: str
    status: str
    exit_code: int | None
    output: str


def build_completion_observation(task: CompletedTask) -> Message:
    """완료를 대화에 주입할 synthetic user 관찰로 만든다 — 창 A/B 가 공유하는 단일
    생성 지점(두 드레인 지점이 다른 메시지를 만들어 리로드가 라이브와 어긋나는 것을
    막는다). content 는 모델용 렌더 {task_id, command, status, exit_code, output}.
    `synthetic` 마커로 루프의 사용자-질문 추출이 이 메시지를 이 턴의 질문으로 오인하지
    않는다(nudge/plan_status 와 같은 관례)."""
    import json  # noqa: PLC0415

    payload = {
        "task_id": task.id,
        "command": task.command,
        "status": task.status,
        "exit_code": task.exit_code,
        "output": task.output,
    }
    content = "[background task finished]\n" + json.dumps(payload, ensure_ascii=False)
    return Message(
        role="user",
        content=content,
        metadata={
            "synthetic": "task_completed",
            "task_id": task.id,
            # 성패 정본: 정상 종료(exit 0)만 성공. killed/interrupted/비영 exit 은 실패.
            "ok": task.status == "exited" and task.exit_code == 0,
            "status": task.status,
            "exit_code": task.exit_code,
        },
    )


def _kill_pgroup(proc: asyncio.subprocess.Process) -> None:
    """프로세스 그룹 전체를 SIGKILL(start_new_session 으로 셸이 그룹 리더).
    terminal_tool._kill_process_group 과 같은 정책 — 여기 둔 이유는 레지스트리가
    완료·kill 경로에서 terminal 도구 없이도 자기 프로세스를 내릴 수 있어야 하기
    때문이다(순환 import 회피)."""
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


def _kill_and_schedule_reap(proc: asyncio.subprocess.Process) -> None:
    """Adoption failed before a registry watcher existed: kill now and arrange
    an asynchronous wait so the child is not left running/zombied."""
    _kill_pgroup(proc)

    async def _reap() -> None:
        try:
            await proc.wait()
        except Exception:  # noqa: BLE001 — failure cleanup is best-effort
            pass

    reap_coro = _reap()
    try:
        asyncio.ensure_future(reap_coro)
    except RuntimeError:
        reap_coro.close()
        # adopt is normally called on the server event loop. If teardown removed
        # it between spawn and registration, the signal above is still the hard
        # safety boundary and the OS child watcher will reap on process exit.
        pass


class BackgroundTaskRegistry:
    """세션 스코프로 키잉된 백그라운드 작업 소유자(§11.3). 단일 인스턴스."""

    def __init__(self, db: Database, config_store: ConfigStore, spool_dir: Path) -> None:
        self._db = db
        self._config_store = config_store
        self._spool_dir = spool_dir
        self._spool_dir.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, _LiveTask] = {}
        self._closing = False

    # ── 자원 상한(§11.6) ──────────────────────────────────────────────────────
    def running_count(self, session_id: str) -> int:
        return sum(
            1 for t in self._tasks.values() if t.session_id == session_id and t.status == "running"
        )

    def running_count_global(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == "running")

    def capacity_error(self, session_id: str) -> str | None:
        """동시 상한 초과면 거절 사유(문자열), 여유 있으면 None. terminal 이 spawn
        **전에** 확인한다 — 넘으면 프로세스를 띄우지 않고 모델에게 에러를 돌려준다."""
        cfg = self._config_store.config.terminal
        if self.running_count(session_id) >= cfg.max_concurrent_per_session:
            return (
                f"background task limit reached for this session "
                f"({cfg.max_concurrent_per_session} running). Wait for one to finish "
                f"(you'll get its result), or kill_task one."
            )
        if self.running_count_global() >= cfg.max_concurrent_global:
            return (
                f"global background task limit reached ({cfg.max_concurrent_global} running). "
                f"Wait for one to finish before starting another."
            )
        return None

    # ── adopt: terminal 이 띄운 프로세스를 넘겨받아 감시 시작 ───────────────────
    def adopt(
        self,
        session_id: str,
        command: str,
        cwd: str,
        proc: asyncio.subprocess.Process,
        *,
        initial_output: bytes = b"",
        initial_output_truncated: bool = False,
    ) -> dict:
        """이미 spawn 된 `proc` 를 백그라운드 작업으로 등록한다. `background:true`
        (initial_output 비어 있음)와 auto-background 승격(부분 출력 있음) 양쪽이 쓴다.
        stdout 을 스풀 파일로 pump 하는 감시 태스크를 띄우고, running 행을 DB 에
        영속한 뒤 {task_id, status:"running", ...} 을 돌려준다."""
        self._prune_retained(session_id)
        task_id = new_id("task")
        output_path = str(self._spool_dir / f"{task_id}.out")
        try:
            spool = open(output_path, "wb")  # noqa: SIM115 — watcher 수명 동안 연다
        except Exception:
            _kill_and_schedule_reap(proc)
            raise
        task = _LiveTask(
            id=task_id,
            session_id=session_id,
            command=command,
            cwd=cwd,
            output_path=output_path,
            proc=proc,
            started_at=now_iso(),
            spool=spool,
        )
        if initial_output:
            self._spool_write(task, initial_output)  # 상한 카운트 포함
        if initial_output_truncated and not task.capped:
            self._mark_spool_capped(task)
        self._tasks[task_id] = task
        try:
            self._db.execute(
                "INSERT INTO background_tasks "
                "(id, session_id, command, cwd, status, started_at, output_path, acknowledged) "
                "VALUES (?, ?, ?, ?, 'running', ?, ?, 0)",
                (task_id, session_id, command, cwd, task.started_at, output_path),
            )
        except Exception:
            self._tasks.pop(task_id, None)
            try:
                spool.close()
            except Exception:  # noqa: BLE001
                pass
            self._cleanup_spool(output_path)
            _kill_and_schedule_reap(proc)
            raise
        task.watcher = asyncio.ensure_future(self._pump(task))
        log.info("background task %s started (session %s): %s", task_id, session_id, command[:120])
        return {
            "task_id": task_id,
            "status": "running",
            "background": True,
            "command": command,
        }

    async def _pump(self, task: _LiveTask) -> None:
        """stdout→스풀 pump + 종료 대기 → 완료 확정. 작업마다 하나(§11.3).

        감시 태스크 자체가 취소되면 DB를 running으로 두고 빠져 부팅 화해가
        interrupted로 정정한다. 정상 shutdown은 prepare_shutdown이 먼저 killed로
        동기 확정한다. kill_task/kill_session은 `proc.wait()` 반환 뒤 finalize한다."""
        proc = task.proc
        assert proc is not None
        rc: int | None = None
        cancelled = False
        try:
            if proc.stdout is not None:
                while True:
                    chunk = await proc.stdout.read(65536)
                    if not chunk:
                        break
                    self._spool_write(task, chunk)
            rc = await proc.wait()
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception:  # noqa: BLE001 — pump 실패가 레지스트리를 죽여선 안 된다
            log.warning("background task %s pump failed", task.id, exc_info=True)
            # stdout 감시가 깨졌는데 프로세스를 추적 밖에 둔 채 exited(None)로
            # 확정하면 고아가 된다. 그룹을 내리고 reap을 시도한 뒤, 그래도 return
            # code가 없으면 _finalize가 interrupted로 분류한다.
            if proc.returncode is None:
                _kill_pgroup(proc)
            try:
                rc = await proc.wait()
            except Exception:  # noqa: BLE001
                rc = proc.returncode
        finally:
            try:
                task.spool.flush()  # type: ignore[union-attr]
                task.spool.close()  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass
            if not cancelled:
                self._finalize(task, rc)

    def _finalize(self, task: _LiveTask, rc: int | None) -> None:
        """완료 상태를 확정하고 DB 에 영속한다. acknowledged 는 건드리지 않는다 —
        미확인 완료로 남아 창 A/B 드레인이 대화에 전달한다. 스풀 파일은 드레인(ack)
        때까지 보존한다(완료 관찰이 여기서 출력을 읽는다)."""
        status = "killed" if task.killed else ("interrupted" if rc is None else "exited")
        task.status = status
        task.exit_code = rc
        task.proc = None
        if task.purged:
            # purge_session이 행을 지운 뒤 감시 태스크가 늦게 끝난 경로. Windows는
            # 열린 파일 삭제가 실패하므로 close 이후 여기서 한 번 더 걷는다.
            self._cleanup_spool(task.output_path)
            return
        if self._closing:
            # prepare_shutdown이 DB 상태를 동기 확정한 뒤 connection을 닫는다.
            # 늦게 끝난 watcher가 닫힌 DB를 건드려 unhandled task exception을
            # 만들지 않게 인메모리 마무리만 한다.
            return
        try:
            self._db.execute(
                "UPDATE background_tasks SET status = ?, exit_code = ?, ended_at = ? WHERE id = ?",
                (status, rc, now_iso(), task.id),
            )
        except Exception:  # noqa: BLE001 — watcher 실패를 unhandled task로 만들지 않음
            # DB 행은 running으로 남을 수 있지만 다음 부팅 reconcile이 interrupted로
            # 회수한다. 프로세스·스풀 종료는 이미 끝났으므로 여기서 예외를 재전파해
            # "Task exception was never retrieved"를 만드는 것보다 복구 가능성이 높다.
            log.warning("background task %s final DB update failed", task.id, exc_info=True)
            return
        log.info("background task %s %s (exit=%s)", task.id, status, rc)

    def _spool_write(self, task: _LiveTask, chunk: bytes) -> None:
        """스풀에 chunk 를 쓰되 파일 상한(_SPOOL_MAX_BYTES)을 넘으면 마커 한 줄만
        남기고 이후는 버린다 — 폭주 출력(`yes` 등)이 디스크를 채우지 못하게. 작업
        자체는 계속 돈다(kill_task 로 멈출 수 있다)."""
        if task.spool is None or task.capped:
            return
        remaining = _SPOOL_MAX_BYTES - task.spooled
        take = chunk if len(chunk) <= remaining else chunk[: max(0, remaining)]
        try:
            if take:
                task.spool.write(take)  # type: ignore[union-attr]
                task.spooled += len(take)
            if len(chunk) > remaining:
                self._mark_spool_capped(task)
            task.spool.flush()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 — 스풀 쓰기 실패가 감시 태스크를 죽여선 안 된다
            pass

    @staticmethod
    def _mark_spool_capped(task: _LiveTask) -> None:
        if task.spool is None or task.capped:
            return
        task.capped = True
        try:
            task.spool.write(  # type: ignore[union-attr]
                b"\n\n[... output capped; task still running, kill_task to stop ...]\n"
            )
            task.spool.flush()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 — cap 마커 실패도 pump를 죽이면 안 됨
            pass

    def _prune_retained(self, session_id: str) -> None:
        """이 세션의 acked 완료 작업을 최근 _RETAINED_COMPLETED_PER_SESSION 개만
        남기고 오래된 것의 행·스풀을 정리한다(adopt 전 + 완료 drain 후 호출).
        프론트 카드가 최근 작업을 조회하되 무한 누적하지 않게 한다."""
        rows = self._db.query_all(
            "SELECT id, output_path FROM background_tasks "
            "WHERE session_id = ? AND acknowledged = 1 AND status IN (?, ?, ?) "
            "ORDER BY ended_at DESC",
            (session_id, *_TERMINAL_STATUSES),
        )
        for row in rows[_RETAINED_COMPLETED_PER_SESSION:]:
            self._cleanup_spool(row["output_path"])
            self._db.execute("DELETE FROM background_tasks WHERE id = ?", (row["id"],))
            self._tasks.pop(row["id"], None)

    # ── 완료 큐 드레인(§11.2) — 창 A(loop)·창 B(orchestrator) 공유 ──────────────
    def drain_completed(self, session_id: str) -> list[CompletedTask]:
        """이 세션의 미확인 완료(acknowledged=0 AND status!='running')를 돌려주고
        acknowledged=1 로 마킹한다. 출력은 스풀 파일에서 40KB 상한으로 읽는다.

        **스풀 파일·DB 행은 보존한다**(ack 는 "모델에게 전달됨"일 뿐, UI 표시는 별개) —
        프론트 작업 카드가 GET /sessions/{id}/tasks 로 완료 후에도 출력을 조회할 수
        있게. 인메모리 핸들만 걷어낸다(감시 태스크는 이미 끝나 proc=None). 보존량은
        _prune_retained(adopt 시)가 상한으로 정리한다.

        단일 활성 턴 불변식(세션당 턴 1개)이라 창 A/B 가 같은 세션에서 겹치지
        않는다 — 별도 락 불필요."""
        rows = self._db.query_all(
            "SELECT id, command, status, exit_code, output_path FROM background_tasks "
            "WHERE session_id = ? AND acknowledged = 0 AND status IN (?, ?, ?) "
            "ORDER BY ended_at ASC",
            (session_id, *_TERMINAL_STATUSES),
        )
        completed: list[CompletedTask] = []
        for row in rows:
            completed.append(
                CompletedTask(
                    id=row["id"],
                    command=row["command"],
                    status=row["status"],
                    exit_code=row["exit_code"],
                    output=self._read_output(row["output_path"]),
                )
            )
            self._db.execute(
                "UPDATE background_tasks SET acknowledged = 1 WHERE id = ?", (row["id"],)
            )
            self._tasks.pop(row["id"], None)  # 스풀·행은 보존(카드 조회용), 인메모리만 정리
        # 이번 ack로 보존 대상이 된 행까지 포함해 즉시 최근 20개로 맞춘다. adopt
        # 직전에만 prune하면 "기존 20 + 방금 완료 1"이 다음 adopt까지 21개 남는다.
        self._prune_retained(session_id)
        return completed

    # ── 스냅샷(§11.7 Phase 2, GET /sessions/{id}/tasks) ────────────────────────
    def snapshot(self, session_id: str) -> list[dict]:
        """이 세션의 실행 중 + 최근 완료 작업 — 프론트 작업 카드용. DB 행이 정본이라
        인메모리·재시작과 무관하게 일관된다. 출력은 스풀에서 40KB 상한으로 읽는다
        (running 이면 현재까지, 완료면 최종)."""
        rows = self._db.query_all(
            "SELECT id, command, status, exit_code, started_at, ended_at, output_path "
            "FROM background_tasks WHERE session_id = ? "
            "ORDER BY (status = 'running') DESC, started_at DESC",
            (session_id,),
        )
        return [
            {
                "task_id": row["id"],
                "command": row["command"],
                "status": row["status"],
                "exit_code": row["exit_code"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "output": self._read_output(row["output_path"]),
            }
            for row in rows
        ]

    @staticmethod
    def _read_output(output_path: str | None) -> str:
        if not output_path or not os.path.isfile(output_path):
            return ""
        try:
            with open(output_path, "rb") as fh:
                data = fh.read()
        except OSError:
            return ""
        text, _ = _truncate_output(data)
        return text

    @staticmethod
    def _cleanup_spool(output_path: str | None) -> None:
        if output_path and os.path.isfile(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass

    # ── 조회(§11.4 task_output) ────────────────────────────────────────────────
    def task_output(self, task_id: str, *, session_id: str | None = None) -> dict:
        """현재 스풀 출력(40KB 상한) + status + exit_code. running/완료 모두 조회
        가능. ack된 완료도 최근 보존 범위 안에서는 스풀 출력을 돌려주고, prune으로
        스풀이 사라진 경우 DB 상태만 남을 수 있다. `session_id`를 주면 그 세션 소유
        작업만 조회한다 — 다른 세션 task_id는 unknown으로 가린다(교차 세션 누수 차단)."""
        if session_id is not None and not self._owns(task_id, session_id):
            return {"error": f"unknown task: {task_id}"}
        live = self._tasks.get(task_id)
        if live is not None:
            if live.spool is not None:
                try:
                    live.spool.flush()  # type: ignore[union-attr]
                except Exception:  # noqa: BLE001
                    pass
            return {
                "task_id": task_id,
                "status": live.status,
                "exit_code": live.exit_code,
                "output": self._read_output(live.output_path),
            }
        row = self._db.query_one(
            "SELECT status, exit_code, output_path, acknowledged FROM background_tasks WHERE id = ?",
            (task_id,),
        )
        if row is None:
            return {"error": f"unknown task: {task_id}"}
        output = self._read_output(row["output_path"])
        note = None
        if row["acknowledged"] and not output:
            note = "output already delivered to the conversation as an observation"
        result = {
            "task_id": task_id,
            "status": row["status"],
            "exit_code": row["exit_code"],
            "output": output,
        }
        if note:
            result["note"] = note
        return result

    def _owns(self, task_id: str, session_id: str) -> bool:
        """task_id 가 session_id 소유인가 — task_output/kill_task 의 교차 세션 가드.
        인메모리 핸들 우선, 없으면 DB 행으로 확인한다."""
        live = self._tasks.get(task_id)
        if live is not None:
            return live.session_id == session_id
        row = self._db.query_one("SELECT session_id FROM background_tasks WHERE id = ?", (task_id,))
        return row is not None and row["session_id"] == session_id

    # ── 종료(§11.4 kill_task, §11.6 수명주기) ──────────────────────────────────
    def kill_task(self, task_id: str, *, session_id: str | None = None) -> dict:
        """프로세스 그룹을 SIGKILL. 감시 태스크의 proc.wait() 가 반환돼 status=killed
        로 확정된다(killed 플래그로 _finalize 가 exited 대신 killed 를 쓴다). `session_id`
        를 주면 그 세션 소유 작업만 종료한다(교차 세션 종료 차단)."""
        if session_id is not None and not self._owns(task_id, session_id):
            return {"error": f"unknown task: {task_id}"}
        live = self._tasks.get(task_id)
        if live is None or live.proc is None:
            row = self._db.query_one("SELECT status FROM background_tasks WHERE id = ?", (task_id,))
            if row is None:
                return {"error": f"unknown task: {task_id}"}
            return {"task_id": task_id, "status": row["status"], "note": "already finished"}
        live.killed = True
        _kill_pgroup(live.proc)
        return {"task_id": task_id, "status": "killed"}

    def kill_session(self, session_id: str) -> None:
        """세션의 살아있는 백그라운드 작업을 전부 내린다(세션 삭제/release/shutdown).
        sync — 프로세스 그룹 시그널 + 플래그만 세운다. 감시 태스크가 finalize 를
        마무리한다(shutdown 처럼 루프가 곧 죽으면 부팅 화해가 백스톱)."""
        for live in list(self._tasks.values()):
            if live.session_id == session_id and live.proc is not None:
                live.killed = True
                _kill_pgroup(live.proc)

    def kill_all(self) -> None:
        """모든 세션의 살아있는 작업 종료(shutdown abort_all 용, best-effort sync)."""
        for live in list(self._tasks.values()):
            if live.proc is not None:
                live.killed = True
                _kill_pgroup(live.proc)

    def prepare_shutdown(self) -> None:
        """Synchronously quiesce registry state before Container closes SQLite.

        Container.shutdown() may be called from inside an already-running event
        loop, so it cannot await every watcher. Mark all live rows killed first,
        then make late watcher finalizers DB-silent. The spool remains available
        for the next boot/turn's unacknowledged completion observation.
        """
        if self._closing:
            return
        self._closing = True
        self.kill_all()
        try:
            self._db.execute(
                "UPDATE background_tasks SET status = 'killed', ended_at = ? "
                "WHERE status = 'running'",
                (now_iso(),),
            )
        except Exception:  # noqa: BLE001 — teardown은 DB 오류에도 계속돼야 함
            log.warning("background task shutdown reconciliation failed", exc_info=True)

    def purge_session(self, session_id: str) -> None:
        """세션 삭제 시(§11.6 정리): 살아있는 작업 kill + 그 세션의 DB 행·스풀 파일
        제거. release_session 이 호출한다 — 세션이 사라졌으니 기록도 남기지 않는다."""
        for live in self._tasks.values():
            if live.session_id == session_id:
                live.purged = True
        self.kill_session(session_id)
        for row in self._db.query_all(
            "SELECT output_path FROM background_tasks WHERE session_id = ?", (session_id,)
        ):
            self._cleanup_spool(row["output_path"])
        self._db.execute("DELETE FROM background_tasks WHERE session_id = ?", (session_id,))
        for tid in [t.id for t in self._tasks.values() if t.session_id == session_id]:
            self._tasks.pop(tid, None)

    # ── 부팅 화해(§11.6) ──────────────────────────────────────────────────────
    def reconcile_on_boot(self) -> int:
        """이전 프로세스가 running 으로 남긴 행을 interrupted 로 정정한다 — OS
        프로세스는 런타임과 함께 죽었으므로 그 running 은 전부 고아다(단일 프로세스라
        부팅 시점 running=고아 판별이 정확하다; 적대 리뷰 #7 리뷰 잡과 같은 처리).
        이 행들은 acknowledged=0 이라 다음 턴 창 B 가 interrupted 관찰로 전달한다."""
        cur = self._db.execute(
            "UPDATE background_tasks SET status = 'interrupted', ended_at = ? "
            "WHERE status = 'running'",
            (now_iso(),),
        )
        n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        if n:
            log.info("boot reconcile: %d running background task(s) -> interrupted", n)
        return n
