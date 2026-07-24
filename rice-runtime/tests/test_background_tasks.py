"""백그라운드 셸 작업(RICE_AGENT_EXECUTION.md §11) — 레지스트리·terminal 배선·창 B.

커버 대상:
  - BackgroundTaskRegistry: adopt→pump→완료 확정, drain_completed(ack), task_output,
    kill_task, kill_session/purge_session, reconcile_on_boot, 동시 상한, 교차세션 가드.
  - terminal 도구: 포그라운드 완료 / auto-background 승격(부분 출력 + task_id) /
    background:true 즉시 반환 / registry 없으면 포그라운드 전용(background 무시).
  - 창 B: 세션 유휴 중 끝난 완료가 다음 턴 ASSEMBLE 에서 히스토리에 관찰로 주입 +
    transcript 영속 + ev_task_completed 방출(오케스트레이터 end-to-end, echo provider).

창 A(턴 내 주입)의 종료 보장 상호작용은 test_loop_termination.py 시나리오 9·10.
"""

from __future__ import annotations

import asyncio
import os
import platform

import pytest

from rice_runtime.agent.background_tasks import BackgroundTaskRegistry, CompletedTask
from rice_runtime.agent.builtins import terminal_tool
from rice_runtime.core.config import Config, ConfigStore, TerminalConfig
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database

_IS_WINDOWS = platform.system() == "Windows"
pytestmark = pytest.mark.skipif(_IS_WINDOWS, reason="POSIX process-group semantics")


def _registry(tmp_path, term_cfg: TerminalConfig | None = None):
    db = Database(":memory:").connect()
    store = ConfigStore(Config(terminal=term_cfg or TerminalConfig()))
    return BackgroundTaskRegistry(db, store, tmp_path / "tasks"), db, store


async def _spawn(cmd: str):
    return await asyncio.create_subprocess_exec(
        "/bin/sh", "-c", cmd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL, start_new_session=True,
    )


async def _wait_terminal(reg, session_id, tries=60):
    """드레인 가능한 완료가 뜰 때까지 폴링(테스트 결정성 — 프로세스 종료는 비동기)."""
    for _ in range(tries):
        if reg.running_count(session_id) == 0:
            return
        await asyncio.sleep(0.05)


# ── 레지스트리 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_adopt_pump_drain_exited(tmp_path):
    reg, db, _ = _registry(tmp_path)
    proc = await _spawn("echo hello; sleep 0.2; echo world")
    info = reg.adopt("s1", "cmd", "/tmp", proc)
    assert info["status"] == "running" and info["background"] is True
    assert reg.running_count("s1") == 1

    await _wait_terminal(reg, "s1")
    drained = reg.drain_completed("s1")
    assert len(drained) == 1
    d = drained[0]
    assert d.status == "exited" and d.exit_code == 0
    assert "hello" in d.output and "world" in d.output
    # 두 번째 드레인은 비어 있다(ack 됨) + DB 도 acknowledged=1
    assert reg.drain_completed("s1") == []
    row = db.query_one("SELECT acknowledged FROM background_tasks WHERE id = ?", (info["task_id"],))
    assert row["acknowledged"] == 1


@pytest.mark.asyncio
async def test_adopt_db_failure_kills_process_and_cleans_spool(tmp_path, monkeypatch):
    reg, db, _ = _registry(tmp_path)
    proc = await _spawn("sleep 30")
    original_execute = db.execute

    def fail_insert(sql, params=()):
        if sql.startswith("INSERT INTO background_tasks"):
            raise RuntimeError("disk full")
        return original_execute(sql, params)

    monkeypatch.setattr(db, "execute", fail_insert)
    with pytest.raises(RuntimeError, match="disk full"):
        reg.adopt("s1", "sleep 30", "/tmp", proc)

    await asyncio.wait_for(proc.wait(), timeout=2)
    assert reg.running_count("s1") == 0
    assert list((tmp_path / "tasks").glob("*.out")) == []


@pytest.mark.asyncio
async def test_kill_task_finalizes_as_killed(tmp_path):
    reg, _, _ = _registry(tmp_path)
    proc = await _spawn("sleep 30")
    info = reg.adopt("s1", "sleep 30", "/tmp", proc)
    await asyncio.sleep(0.05)
    assert reg.kill_task(info["task_id"])["status"] == "killed"
    await _wait_terminal(reg, "s1")
    drained = reg.drain_completed("s1")
    assert len(drained) == 1 and drained[0].status == "killed"


@pytest.mark.asyncio
async def test_task_output_running_then_gone(tmp_path):
    reg, _, _ = _registry(tmp_path)
    proc = await _spawn("echo peek; sleep 30")
    info = reg.adopt("s1", "c", "/tmp", proc)
    await asyncio.sleep(0.1)
    out = reg.task_output(info["task_id"])
    assert out["status"] == "running" and "peek" in out["output"]
    reg.kill_task(info["task_id"])
    await _wait_terminal(reg, "s1")
    reg.drain_completed("s1")  # ack + spool 정리 → 인메모리 핸들 제거
    gone = reg.task_output(info["task_id"])
    assert gone["status"] == "killed"  # DB 상태는 남는다(출력은 이미 전달됨)


@pytest.mark.asyncio
async def test_cross_session_guard(tmp_path):
    reg, _, _ = _registry(tmp_path)
    proc = await _spawn("sleep 30")
    info = reg.adopt("owner", "c", "/tmp", proc)
    # 다른 세션은 조회·종료 모두 unknown 으로 가려진다
    assert "unknown" in reg.task_output(info["task_id"], session_id="intruder")["error"]
    assert "unknown" in reg.kill_task(info["task_id"], session_id="intruder")["error"]
    # 소유 세션은 정상
    assert reg.task_output(info["task_id"], session_id="owner")["status"] == "running"
    reg.kill_session("owner")


@pytest.mark.asyncio
async def test_capacity_limit_per_session(tmp_path):
    reg, _, _ = _registry(tmp_path, TerminalConfig(max_concurrent_per_session=2))
    for _ in range(2):
        reg.adopt("s1", "sleep 30", "/tmp", await _spawn("sleep 30"))
    assert reg.capacity_error("s1") is not None and "limit reached" in reg.capacity_error("s1")
    assert reg.capacity_error("s2") is None  # 다른 세션은 여유
    reg.kill_session("s1")


@pytest.mark.asyncio
async def test_reconcile_on_boot_marks_running_interrupted(tmp_path):
    reg, db, _ = _registry(tmp_path)
    db.execute(
        "INSERT INTO background_tasks (id, session_id, command, status, started_at, acknowledged) "
        "VALUES ('orphan', 's1', 'x', 'running', 'now', 0)"
    )
    assert reg.reconcile_on_boot() == 1
    row = db.query_one("SELECT status FROM background_tasks WHERE id = 'orphan'")
    assert row["status"] == "interrupted"
    # interrupted 는 미확인 완료라 드레인된다(다음 턴 창 B 가 전달)
    assert [d.status for d in reg.drain_completed("s1")] == ["interrupted"]


@pytest.mark.asyncio
async def test_purge_session_kills_and_clears_rows(tmp_path):
    reg, db, _ = _registry(tmp_path)
    info = reg.adopt("s1", "sleep 30", "/tmp", await _spawn("sleep 30"))
    reg.purge_session("s1")
    await _wait_terminal(reg, "s1")
    # 행이 지워졌다(세션 삭제 정리) + 인메모리 핸들도 없다
    assert db.query_one("SELECT 1 FROM background_tasks WHERE id = ?", (info["task_id"],)) is None
    assert reg.running_count("s1") == 0


# ── terminal 도구 배선 ────────────────────────────────────────────────────────


def _terminal_tools(tmp_path, reg, store):
    paths = RicePaths(root=tmp_path).ensure()
    return {s.name: h for s, h in terminal_tool.tools(paths, registry=reg, config_store=store)}


@pytest.mark.asyncio
async def test_terminal_foreground_completes_within_budget(tmp_path):
    reg, _, store = _registry(tmp_path)
    term = _terminal_tools(tmp_path, reg, store)["terminal"]
    r = await term({"command": "echo fg"}, session_id="s1")
    assert r["exit_code"] == 0 and "fg" in r["output"] and not r.get("background")


@pytest.mark.asyncio
async def test_terminal_auto_background_promotion(tmp_path):
    reg, _, store = _registry(tmp_path, TerminalConfig(foreground_block_budget_s=1))
    term = _terminal_tools(tmp_path, reg, store)["terminal"]
    r = await term({"command": "echo start; sleep 2.5; echo end"}, session_id="s1")
    # 예산(1s) 초과 → kill 대신 백그라운드 승격: task_id + 부분 출력
    assert r["background"] is True and r["status"] == "running" and "start" in r["partial_output"]
    await _wait_terminal(reg, "s1")
    drained = reg.drain_completed("s1")
    assert len(drained) == 1 and drained[0].status == "exited" and "end" in drained[0].output


@pytest.mark.asyncio
async def test_terminal_promotes_when_shell_exits_but_descendant_keeps_pipe_open(tmp_path):
    """`cmd &`의 셸 리더 returncode만 보고 완료로 오인하면 남은 pipe drain이
    foreground budget을 무시하고 손자 종료까지 블록한다."""
    loop = asyncio.get_running_loop()
    reg, _, store = _registry(tmp_path, TerminalConfig(foreground_block_budget_s=1))
    term = _terminal_tools(tmp_path, reg, store)["terminal"]
    started = loop.time()
    result = await term({"command": "sleep 5 &"}, session_id="s1")

    assert loop.time() - started < 2.5
    assert result["background"] is True
    reg.kill_task(result["task_id"])


@pytest.mark.asyncio
async def test_terminal_background_true_is_immediate(tmp_path):
    reg, _, store = _registry(tmp_path)
    tools = _terminal_tools(tmp_path, reg, store)
    r = await tools["terminal"]({"command": "sleep 30", "background": True}, session_id="s1")
    assert r["status"] == "running" and r["background"] is True
    assert (await tools["kill_task"]({"task_id": r["task_id"]}, session_id="s1"))["status"] == "killed"


@pytest.mark.asyncio
async def test_terminal_foreground_only_without_registry(tmp_path):
    """registry 미주입(구 경로/독립 테스트): background 인자는 무시되고 포그라운드
    실행 — task_output/kill_task 도 등록되지 않는다."""
    paths = RicePaths(root=tmp_path).ensure()
    entries = terminal_tool.tools(paths)  # registry 없음
    assert [s.name for s, _ in entries] == ["terminal"]
    term = entries[0][1]
    r = await term({"command": "echo x", "background": True}, session_id="s1")
    # background 무시 → 실제 포그라운드 결과(task_id 아님)
    assert "task_id" not in r and r["exit_code"] == 0 and "x" in r["output"]


@pytest.mark.asyncio
async def test_terminal_auto_background_disabled_kills_on_timeout(tmp_path):
    reg, _, store = _registry(tmp_path, TerminalConfig(auto_background_on_timeout=False))
    term = _terminal_tools(tmp_path, reg, store)["terminal"]
    r = await term({"command": "sleep 30", "timeout": 1}, session_id="s1")
    # auto-bg off → 기존 kill-on-timeout 동작(백그라운드 승격 없음)
    assert r.get("timed_out") is True and not r.get("background")
    assert reg.running_count("s1") == 0


@pytest.mark.asyncio
async def test_terminal_auto_background_respects_capacity_limit(tmp_path):
    reg, db, store = _registry(
        tmp_path,
        TerminalConfig(foreground_block_budget_s=1, max_concurrent_per_session=1),
    )
    first = reg.adopt("s1", "sleep 30", "/tmp", await _spawn("sleep 30"))
    term = _terminal_tools(tmp_path, reg, store)["terminal"]
    result = await term({"command": "sleep 30"}, session_id="s1")

    assert "limit reached" in result["error"]
    assert result["timed_out"] is True
    assert reg.running_count("s1") == 1
    assert db.query_one(
        "SELECT COUNT(*) AS n FROM background_tasks WHERE session_id = 's1'"
    )["n"] == 1
    reg.kill_task(first["task_id"])


def test_foreground_output_capture_is_memory_bounded_and_keeps_true_tail():
    from rice_runtime.agent.builtins.terminal_tool import (
        _FOREGROUND_CAPTURE_MAX_BYTES,
        _BoundedOutputCapture,
    )

    capture = _BoundedOutputCapture()
    capture.append(b"A" * (_FOREGROUND_CAPTURE_MAX_BYTES + 100))
    capture.append(b"TAIL-MARKER")

    handoff, was_truncated = capture.handoff()
    rendered, output_truncated = capture.render()
    assert len(handoff) == _FOREGROUND_CAPTURE_MAX_BYTES
    assert was_truncated is True
    assert output_truncated is True
    assert rendered.endswith("TAIL-MARKER")


# ── 창 B: 오케스트레이터 end-to-end ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_window_b_injects_idle_completion_into_next_turn(tmp_path):
    """이전 턴 종료 후(유휴) 끝난 백그라운드 완료가, 다음 턴 ASSEMBLE 에서
    히스토리에 관찰로 주입 + transcript 영속 + ev_task_completed 방출."""
    from rice_runtime.server.composition_root import build_container

    c = build_container(paths=RicePaths(root=tmp_path))
    try:
        s = c.sessions.create()
        # 유휴 중 완료한(미확인) 작업을 직접 시드 — 스풀 파일 + exited 행.
        spool = tmp_path / "tasks" / "t_done.out"
        spool.parent.mkdir(parents=True, exist_ok=True)
        spool.write_bytes(b"BUILD SUCCESSFUL\n")
        c.db.execute(
            "INSERT INTO background_tasks "
            "(id, session_id, command, cwd, status, exit_code, started_at, ended_at, output_path, acknowledged) "
            "VALUES ('t_done', ?, 'npm run build', '/tmp', 'exited', 0, 'now', 'now', ?, 0)",
            (s.id, str(spool)),
        )
        events = [ev async for ev in c.agent.run_turn(s.id, "did the build finish?")]

        # ev_task_completed 방출
        completed = [ev for ev in events if ev.type.value == "task_completed"]
        assert len(completed) == 1 and completed[0].data["task_id"] == "t_done"
        assert completed[0].data["status"] == "exited" and completed[0].data["exit_code"] == 0

        # transcript 에 완료 관찰(role="user" synthetic)이 영속됐다
        hist = c.sessions.history(s.id)
        obs = [m for m in hist if m.metadata.get("synthetic") == "task_completed"]
        assert len(obs) == 1
        assert obs[0].role == "user" and obs[0].metadata["task_id"] == "t_done"
        assert obs[0].metadata["ok"] is True and "BUILD SUCCESSFUL" in obs[0].content

        # ack 됨 → 재드레인 없음(다음 턴에 중복 전달되지 않는다)
        row = c.db.query_one("SELECT acknowledged FROM background_tasks WHERE id = 't_done'")
        assert row["acknowledged"] == 1
    finally:
        c.shutdown()


# ── Phase 2: snapshot · spool cap · retention · 조회/kill 라우트 ───────────────


@pytest.mark.asyncio
async def test_snapshot_running_first_and_retained_after_drain(tmp_path):
    reg, _, _ = _registry(tmp_path)
    reg.adopt("s1", "sleep 30", "/tmp", await _spawn("echo run-out; sleep 30"))
    reg.adopt("s1", "echo done", "/tmp", await _spawn("echo done-out"))
    await asyncio.sleep(0.4)
    snap = reg.snapshot("s1")
    assert len(snap) == 2
    assert snap[0]["status"] == "running"  # running 먼저(ORDER BY status='running' DESC)
    done = next(s for s in snap if s["status"] == "exited")
    assert done["exit_code"] == 0 and "done-out" in done["output"]
    # 드레인해도(모델에 전달) 카드용으로 스냅샷에 남는다
    reg.drain_completed("s1")
    snap2 = reg.snapshot("s1")
    assert any(s["status"] == "exited" and "done-out" in s["output"] for s in snap2)
    reg.kill_session("s1")


@pytest.mark.asyncio
async def test_spool_cap_bounds_disk(tmp_path):
    from rice_runtime.agent.background_tasks import _SPOOL_MAX_BYTES

    reg, _, _ = _registry(tmp_path)
    info = reg.adopt("s1", "big", "/tmp", await _spawn("yes ABCDEFGH | head -c 3000000"))
    await _wait_terminal(reg, "s1")
    fsize = os.path.getsize(tmp_path / "tasks" / f"{info['task_id']}.out")
    assert fsize <= _SPOOL_MAX_BYTES + 256  # 상한 + 마커 한 줄
    assert "capped" in reg.task_output(info["task_id"])["output"]


def test_retention_prunes_old_completed_on_adopt(tmp_path):
    reg, db, _ = _registry(tmp_path)
    from rice_runtime.agent.background_tasks import _RETAINED_COMPLETED_PER_SESSION

    # 25개 acked 완료 행 + 스풀을 직접 시드(오래된→최신 ended_at)
    (tmp_path / "tasks").mkdir(parents=True, exist_ok=True)
    for i in range(25):
        sp = tmp_path / "tasks" / f"old{i}.out"
        sp.write_bytes(b"x")
        db.execute(
            "INSERT INTO background_tasks "
            "(id, session_id, command, status, started_at, ended_at, output_path, acknowledged) "
            "VALUES (?, 's1', 'c', 'exited', 'now', ?, ?, 1)",
            (f"old{i}", f"2026-07-24T00:{i:02d}:00Z", str(sp)),
        )
    reg._prune_retained("s1")  # adopt 가 부르는 정리
    remaining = db.query_all("SELECT id FROM background_tasks WHERE session_id = 's1'")
    assert len(remaining) == _RETAINED_COMPLETED_PER_SESSION  # 20개만 남는다
    # 가장 오래된 5개 스풀이 지워졌다
    assert not (tmp_path / "tasks" / "old0.out").exists()
    assert (tmp_path / "tasks" / "old24.out").exists()  # 최신은 보존


def test_drain_prunes_newly_acked_completions_to_exact_retention_limit(tmp_path):
    reg, db, _ = _registry(tmp_path)
    from rice_runtime.agent.background_tasks import _RETAINED_COMPLETED_PER_SESSION

    for i in range(_RETAINED_COMPLETED_PER_SESSION + 1):
        sp = tmp_path / "tasks" / f"pending{i}.out"
        sp.write_bytes(str(i).encode())
        db.execute(
            "INSERT INTO background_tasks "
            "(id, session_id, command, status, started_at, ended_at, output_path, acknowledged) "
            "VALUES (?, 's1', 'c', 'exited', 'now', ?, ?, 0)",
            (f"pending{i}", f"2026-07-24T00:{i:02d}:00Z", str(sp)),
        )

    assert len(reg.drain_completed("s1")) == _RETAINED_COMPLETED_PER_SESSION + 1
    remaining = db.query_one(
        "SELECT COUNT(*) AS n FROM background_tasks WHERE session_id = 's1'"
    )
    assert remaining["n"] == _RETAINED_COMPLETED_PER_SESSION
    assert not (tmp_path / "tasks" / "pending0.out").exists()


@pytest.mark.asyncio
async def test_container_shutdown_with_live_task_has_no_late_db_exception(tmp_path):
    from rice_runtime.server.composition_root import build_container

    c = build_container(paths=RicePaths(root=tmp_path))
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    late_errors = []
    loop.set_exception_handler(lambda _loop, context: late_errors.append(context))
    try:
        proc = await _spawn("sleep 30")
        info = c.background_tasks.adopt("s1", "sleep 30", "/tmp", proc)
        c.shutdown()
        await asyncio.sleep(0.2)
        assert late_errors == []

        reopened = Database(tmp_path / "state.db").connect()
        try:
            row = reopened.query_one(
                "SELECT status FROM background_tasks WHERE id = ?", (info["task_id"],)
            )
            assert row["status"] == "killed"
        finally:
            reopened.close()
    finally:
        loop.set_exception_handler(previous_handler)
        c.shutdown()


def test_tasks_routes_list_and_kill(tmp_path):
    from fastapi.testclient import TestClient

    from rice_runtime.core.config import RuntimeArgs
    from rice_runtime.server.app import create_app
    from rice_runtime.server.composition_root import build_container

    c = build_container(RuntimeArgs(), paths=RicePaths(root=tmp_path))
    try:
        client = TestClient(create_app(c))
        s = c.sessions.create()
        # 완료 작업 1개 시드(스풀 포함)
        sp = tmp_path / "tasks" / "tr.out"
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_bytes(b"route out\n")
        c.db.execute(
            "INSERT INTO background_tasks "
            "(id, session_id, command, cwd, status, exit_code, started_at, ended_at, output_path, acknowledged) "
            "VALUES ('tr', ?, 'npm run build', '/tmp', 'exited', 0, 'now', 'now', ?, 1)",
            (s.id, str(sp)),
        )
        # GET /tasks
        r = client.get(f"/sessions/{s.id}/tasks", headers={"host": "localhost"})
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data) == 1 and data[0]["task_id"] == "tr" and "route out" in data[0]["output"]
        # POST kill: 이미 끝난 작업 → 그 상태를 돌려준다(라우팅·응답 형태 검증)
        rk = client.post(f"/sessions/{s.id}/tasks/tr/kill", headers={"host": "localhost"})
        assert rk.status_code == 200 and rk.json()["data"]["status"] == "exited"
        # 알 수 없는 task_id → 404
        assert client.post(f"/sessions/{s.id}/tasks/nope/kill", headers={"host": "localhost"}).status_code == 404
        # 교차 세션 조회 가드: 다른 세션의 GET /tasks 는 tr 을 안 보여준다
        s2 = c.sessions.create()
        r2 = client.get(f"/sessions/{s2.id}/tasks", headers={"host": "localhost"})
        assert r2.json()["data"] == []
    finally:
        c.shutdown()
