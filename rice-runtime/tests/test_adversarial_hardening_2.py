"""적대 리뷰(2026-07-11) P0/P1/P2 수정의 회귀 가드.

각 테스트는 리뷰 항목 번호를 달고 있다:
  P0#1 비루프백 바인딩 × 빈 토큰이 기동 단계에서 거부되지 않던 문제
  P0#2 artifact name 으로 설치 디렉터리를 탈출할 수 있던 문제
  P1#1 dispatcher 가 넘긴 owner_id 를 PluginLoaderService.invoke 가 못 받아
       실제 플러그인 도구 호출 전부가 TypeError 로 죽던 문제
  P1#2 task cancellation/abort 가 sandbox·terminal 자식 프로세스(그룹)를
       살려 두던 문제
  P1#3 ts 단독 페이지네이션 커서의 메시지 유실 + regenerate 의 ts 절단 잔존
  P1#4 /shutdown 이 uvicorn 종료 신호를 세우지 않아 좀비가 되던 문제
  P2#2 KEEPALIVE_COMMENT 가 선언만 되고 배선되지 않던 문제
"""

from __future__ import annotations

import asyncio
import os
import platform
import random
import subprocess
import tempfile
import time

import pytest

from rice_runtime.core.config import RuntimeArgs
from rice_runtime.core.errors import ValidationError
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.core.types import Message, ToolCall, ToolSpec, ev_done
from rice_runtime.execution.plugin.manifest import parse_manifest
from rice_runtime.execution.service import ChildProcessSandbox, NoopSandbox, PluginLoaderService
from rice_runtime.server.session.session_manager import SessionManager

_IS_WINDOWS = platform.system() == "Windows"


def _fresh_paths() -> RicePaths:
    return RicePaths.from_env(tempfile.mkdtemp(prefix="rice-adv2-"))


def _pids_matching(marker: str, *, child_of_self: bool = False) -> list[int]:
    """`ps` 출력에서 command 에 marker 를 포함하는 pid 목록.

    zombie 는 command 인자가 사라지므로(`(sleep)`) 자연히 매칭에서 빠진다 —
    "그룹 전체가 실제로 내려갔는가"의 관측 지표로 쓴다.
    """
    out = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True).stdout
    pids: list[int] = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, ppid, command = int(parts[0]), int(parts[1]), parts[2]
        if marker not in command:
            continue
        if child_of_self and ppid != os.getpid():
            continue
        pids.append(pid)
    return pids


async def _wait_until(predicate, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


# ── P0#1 비루프백 바인딩 기동 거부 ────────────────────────────────────────────


def test_serve_refuses_nonloopback_bind_without_flag_and_token():
    from rice_runtime.server.app import serve

    # 플래그 없음 → 거부 (토큰이 있어도)
    with pytest.raises(SystemExit):
        serve(host="0.0.0.0", port=59999, token="")
    with pytest.raises(SystemExit):
        serve(host="0.0.0.0", port=59999, token="secret")
    # 플래그가 있어도 토큰이 비면 거부 — 빈 토큰은 인증 전체 비활성이다
    with pytest.raises(SystemExit):
        serve(host="0.0.0.0", port=59999, token="", allow_external=True)


# ── P0#2 artifact name 경로 탈출 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    ["../evil", "a/b", "a\\b", "..", ".hidden", "-dash", "a" * 65, "name with space", "한글이름"],
)
def test_manifest_rejects_non_slug_names(bad):
    raw = {"schema_version": 1, "type": "skill", "name": bad, "version": "1.0"}
    with pytest.raises(ValidationError):
        parse_manifest(raw)


def test_manifest_accepts_slug_names():
    for good in ("my-plugin", "Plugin_2", "a.b-c", "x"):
        assert parse_manifest(
            {"schema_version": 1, "type": "plugin", "name": good, "version": "1.0"}
        ).name == good


def test_resolved_within_blocks_escape(tmp_path):
    from rice_runtime.execution.plugin.loader import _resolved_within

    base = tmp_path / "skills"
    base.mkdir()
    assert _resolved_within(base, base / "ok.md")
    with pytest.raises(ValidationError):
        _resolved_within(base, base / ".." / "escape.md")


@pytest.mark.asyncio
async def test_skill_install_cannot_escape_skills_root(tmp_path):
    """리뷰가 재현한 시나리오: 탈출형 이름의 skill artifact 설치 → skills 루트
    밖에 파일이 생기면 안 된다(지금은 manifest 검증에서 즉시 거부)."""
    paths = RicePaths.from_env(str(tmp_path / "rice"))
    paths.ensure()
    db = Database(":memory:").connect()
    svc = PluginLoaderService(db, NoopSandbox(), paths)

    src = tmp_path / "artifact"
    src.mkdir()
    (src / "SKILL.md").write_text("---\nname: x\n---\nbody", encoding="utf-8")

    manifest = {"schema_version": 1, "type": "skill", "name": "../escape", "version": "1"}
    with pytest.raises(ValidationError):
        await svc.install(path=str(src), manifest=manifest)
    # skills 루트 밖(RICE_HOME 루트)에 아무것도 만들어지지 않았다.
    assert not (tmp_path / "rice" / "escape.md").exists()
    assert not (tmp_path / "escape.md").exists()
    db.close()


# ── P1#1 plugin invoke 의 owner_id 전달 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_plugin_loader_service_accepts_owner_id():
    """facade 가 owner_id 를 받는다 — 이전엔 TypeError 로 모든 호출이 죽었다."""
    db = Database(":memory:").connect()
    svc = PluginLoaderService(db, NoopSandbox(), _fresh_paths())
    call = ToolCall(tool_name="nope", arguments={}, id="c1")
    res = await svc.invoke(call, owner_id="plugin:ghost")  # TypeError 가 아니어야 한다
    assert res.ok is False and "no entrypoint" in (res.error or "")
    db.close()


@pytest.mark.asyncio
@pytest.mark.skipif(_IS_WINDOWS, reason="POSIX sandbox integration")
async def test_dispatcher_to_plugin_sandbox_end_to_end():
    """dispatcher → PluginLoaderService → PluginLoader → ChildProcessSandbox
    실제 조립 경로의 통합 테스트 — 592개 단위 테스트가 놓친 공백."""
    from rice_runtime.agent.tool_dispatch import ToolDispatcher
    from rice_runtime.server.approval import ApprovalRegistry

    paths = _fresh_paths()
    db = Database(":memory:").connect()
    svc = PluginLoaderService(db, ChildProcessSandbox(), paths)

    from pathlib import Path

    src = tempfile.mkdtemp(prefix="rice-plug-src-")
    (Path(src) / "placeholder.txt").write_text("x", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "type": "plugin",
        "name": "pgtest",
        "version": "1.0",
        "entrypoint": {
            "tools": [{"name": "get_pid", "entry": "os.getpid", "requires_approval": False}]
        },
    }
    await svc.install(path=src, manifest=manifest)

    class _NoConnectors:
        async def invoke(self, call, owner_id=None):  # pragma: no cover
            raise AssertionError("unexpected connector dispatch")

    dispatcher = ToolDispatcher(
        connectors=_NoConnectors(),  # type: ignore[arg-type]
        plugins=svc,
        approvals=ApprovalRegistry(),
        approval_timeout_s=1,
    )
    spec = ToolSpec(
        name="get_pid", description="", parameters={}, source="plugin",
        owner_id="plugin:pgtest", requires_approval=False,
    )
    call = ToolCall(tool_name="get_pid", arguments={}, id="c1")
    async for _ in dispatcher.dispatch("s1", call, spec):
        pass
    res = await dispatcher.result_for(call)
    assert res.ok, res.error  # 이전 구현: "unexpected keyword argument 'owner_id'"
    assert isinstance(res.result, int) and res.result != os.getpid()  # 자식에서 실행됐다
    db.close()


# ── P1#2 cancel/abort 시 자식 프로세스(그룹) 종료 ────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skipif(_IS_WINDOWS, reason="POSIX process-group semantics")
async def test_sandbox_task_cancellation_kills_child():
    """timeout 뿐 아니라 **task cancellation** 도 자식을 내린다 — 리뷰 검증에서
    부모 task 취소 후 자식이 살아 있었다."""
    sandbox = ChildProcessSandbox()
    task = asyncio.create_task(
        sandbox.run(entrypoint="__sleep__", payload={"seconds": 30}, timeout_s=60)
    )
    assert await _wait_until(lambda: _pids_matching("__sleep__", child_of_self=True)), (
        "sandbox child never appeared"
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _wait_until(lambda: not _pids_matching("__sleep__", child_of_self=True)), (
        "sandbox child survived task cancellation"
    )


@pytest.mark.asyncio
@pytest.mark.skipif(_IS_WINDOWS, reason="POSIX process-group semantics")
async def test_terminal_cancel_kills_whole_process_group():
    """셸 하나가 아니라 백그라운드 손자까지 — `cmd &` 분기가 abort 이후에도
    부작용을 내지 않는다."""
    from rice_runtime.agent.builtins.terminal_tool import _run_command

    marker = str(random.randint(10_000_000, 99_999_999))  # ps 에서 식별할 sleep 인자
    cmd = f"sleep {marker} & sleep {marker}"
    task = asyncio.create_task(_run_command(cmd, None, 60, {"PATH": "/usr/bin:/bin"}))
    assert await _wait_until(lambda: len(_pids_matching(f"sleep {marker}")) >= 2), (
        "foreground+background sleeps never appeared"
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _wait_until(lambda: not _pids_matching(f"sleep {marker}")), (
        "process group survived cancellation (background child leaked)"
    )


# ── P1#3 페이지네이션 유실 + regenerate 절단 ─────────────────────────────────


def test_page_does_not_lose_same_timestamp_messages():
    """동일 timestamp 60개: 첫 페이지 50개 뒤 두 번째 페이지가 10개여야 한다 —
    ts 단독 커서는 두 번째 페이지가 0개(나머지 10개 영구 유실)였다."""
    db = Database(":memory:").connect()
    mgr = SessionManager(db)
    s = mgr.create()
    for i in range(60):
        mgr.append(s.id, Message(role="user", content=f"m{i}", ts=1_000))

    first = mgr.page(s.id, None, limit=50)
    assert len(first.messages) == 50 and first.next_cursor is not None
    second = mgr.page(s.id, first.next_cursor, limit=50)
    assert len(second.messages) == 10
    seen = {m.id for m in first.messages} | {m.id for m in second.messages}
    assert len(seen) == 60  # 유실도 중복도 없다
    db.close()


def test_page_cursor_walks_mixed_timestamps_in_insert_order():
    db = Database(":memory:").connect()
    mgr = SessionManager(db)
    s = mgr.create()
    for i, ts in enumerate([100, 200, 200, 200, 300]):
        mgr.append(s.id, Message(role="user", content=f"m{i}", ts=ts))
    page1 = mgr.page(s.id, None, limit=2)
    page2 = mgr.page(s.id, page1.next_cursor, limit=2)
    page3 = mgr.page(s.id, page2.next_cursor, limit=2)
    contents = [m.content for m in (*page3.messages, *page2.messages, *page1.messages)]
    assert contents == ["m0", "m1", "m2", "m3", "m4"]
    db.close()


def test_regenerate_cut_removes_same_timestamp_assistant():
    """절단 기준이 마지막 사용자 메시지의 고유 id — 같은 밀리초의 stale
    assistant 응답이 살아남아 답이 두 개가 되지 않는다."""
    db = Database(":memory:").connect()
    mgr = SessionManager(db)
    s = mgr.create()
    user = Message(role="user", content="질문", ts=500)
    stale = Message(role="assistant", content="낡은 답", ts=500)  # 같은 ms
    mgr.append(s.id, user)
    mgr.append(s.id, stale)

    mgr.delete_after_message(s.id, user.id)
    assert [m.role for m in mgr.history(s.id)] == ["user"]

    mgr.delete_after_message(s.id, "msg_unknown")  # 미지 id 는 no-op
    assert len(mgr.history(s.id)) == 1
    db.close()


# ── P1#4 /shutdown → uvicorn 종료 신호 ──────────────────────────────────────


def test_shutdown_endpoint_signals_uvicorn_server():
    from fastapi.testclient import TestClient

    from rice_runtime.server.app import create_app
    from rice_runtime.server.composition_root import build_container

    c = build_container(RuntimeArgs(), paths=_fresh_paths())
    app = create_app(c)

    class _FakeServer:
        should_exit = False

    fake = _FakeServer()
    app.state.uvicorn_server = fake
    try:
        client = TestClient(app)
        r = client.post("/shutdown", headers={"host": "localhost"})
        assert r.json()["success"] is True
        assert fake.should_exit is True  # 프로세스 종료 신호가 실제로 섰다
        # 자원 정리는 lifespan 몫 — endpoint 가 먼저 DB 를 닫아 좀비를 만들지 않는다
        assert c._db_closed is False
    finally:
        c.shutdown()


def test_shutdown_endpoint_without_server_still_tears_down():
    """uvicorn 밖에서 띄운 앱(TestClient 등)은 종전대로 직접 정리한다."""
    from fastapi.testclient import TestClient

    from rice_runtime.server.app import create_app
    from rice_runtime.server.composition_root import build_container

    c = build_container(RuntimeArgs(), paths=_fresh_paths())
    app = create_app(c)
    client = TestClient(app)
    r = client.post("/shutdown", headers={"host": "localhost"})
    assert r.json()["success"] is True
    assert c._db_closed is True


# ── P2#2 SSE keepalive 배선 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sse_keepalive_emitted_during_quiet_period():
    """이벤트가 없는 구간(승인 대기 등)에 주석 프레임이 흐른다 — 이전엔
    KEEPALIVE_COMMENT 가 선언만 되고 최대 300초간 무프레임이었다."""
    from rice_runtime.server.sse.emitter import KEEPALIVE_COMMENT, to_sse_stream

    async def quiet_then_done():
        await asyncio.sleep(0.12)
        yield ev_done()

    frames = [f async for f in to_sse_stream(quiet_then_done(), keepalive_s=0.03)]
    assert KEEPALIVE_COMMENT in frames[:-1]
    assert frames[-1] == ev_done().to_frame()


@pytest.mark.asyncio
async def test_sse_stream_passes_events_without_delay():
    from rice_runtime.server.sse.emitter import to_sse_stream

    async def three():
        for _ in range(3):
            yield ev_done()

    frames = [f async for f in to_sse_stream(three(), keepalive_s=5.0)]
    assert frames == [ev_done().to_frame()] * 3
