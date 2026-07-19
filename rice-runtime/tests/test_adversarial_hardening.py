"""적대 리뷰(2026-07-05) P0/P1 수정의 회귀 가드.

각 테스트는 리뷰 항목 번호를 달고 있다:
  #1 세션 mutation 이 진행 중 턴을 우회하던 문제 (regenerate/delete)
  #2 커넥터 도구 라우팅이 ToolSpec.owner_id 를 무시하던 문제
  #3 도구 실행이 abort 를 무시하던 문제 + terminal timeout 무제한
  #4 설정/DB 저장의 부분 적용 (트랜잭션·원자 저장·fail-soft 로드)
  #5 승인된 스킬 패치가 조용히 no-op 되던 문제
  #6 MCP initialize 레이스
  #7 crash 후 running 리뷰 잡이 영구 방치되던 문제
  #8 workspace sandbox 의 symlink escape
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from rice_runtime.core.config import Config, LearningConfig, RuntimeArgs, load_config
from rice_runtime.core.errors import ConflictError, ValidationError
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.core.types import Message, ToolCall, ToolSpec, now_iso
from rice_runtime.server.composition_root import build_container


def _fresh_paths() -> RicePaths:
    return RicePaths.from_env(tempfile.mkdtemp(prefix="rice-adv-"))


# ── #1 세션 mutation × 진행 중 턴 ────────────────────────────────────────────


class _SlowProvider:
    """abort 를 확인할 시간을 주는 느린 스트림 — 턴을 '진행 중' 상태로 붙잡는다."""

    name = "slow"

    def capabilities(self):
        from rice_runtime.agent.providers.interfaces import ProviderCapabilities

        return ProviderCapabilities()

    async def stream(self, *, system, messages, tools=(), temperature=0.7,
                     response_schema=None, think=None, max_tokens=None):
        from rice_runtime.agent.providers.interfaces import ProviderChunk

        for _ in range(200):
            yield ProviderChunk(kind="text", text=".")
            await asyncio.sleep(0.01)
        yield ProviderChunk(kind="done", stop_reason="end_turn")


def _slow_container():
    paths = _fresh_paths()
    return build_container(
        RuntimeArgs(), paths=paths, resolve_provider_override=lambda: _SlowProvider()
    )


@pytest.mark.asyncio
async def test_regenerate_busy_does_not_destroy_transcript():
    """busy 확인이 delete_after **이전**이어야 한다 — 이전엔 무조건 지운 뒤
    스트림만 session_busy 로 거절돼 진행 중 턴의 히스토리를 파괴했다."""
    from rice_runtime.server.routes.messages import regenerate

    c = _slow_container()
    try:
        s = c.sessions.create(model="slow")
        # 사전 히스토리: 완결된 한 턴 흉내
        c.sessions.append(s.id, Message(role="user", content="질문"))
        c.sessions.append(s.id, Message(role="assistant", content="답변"))

        gen = c.agent.run_turn(s.id, "새 질문")
        await gen.__anext__()  # 턴 시작 → _active 락 획득
        before = len(c.sessions.history(s.id))
        with pytest.raises(ConflictError):
            await regenerate(s.id, c)
        # transcript 는 그대로다 — delete_after 가 실행되지 않았다.
        assert len(c.sessions.history(s.id)) == before
        await gen.aclose()
    finally:
        c.shutdown()


@pytest.mark.asyncio
async def test_regenerate_cut_ignores_synthetic_user_nudges():
    """런타임이 주입한 synthetic user 넛지(empty/narration/plan_reconcile)는 role만
    user 다 — 절단 기준으로 오인하면 넛지 문구가 "마지막 질문"이 되어 그 앞의
    스테일 도구 라운드까지 살아남는다(적대 리뷰 2026-07-19 H3). 절단은 실제
    사용자 메시지 기준이어야 한다."""
    from rice_runtime.server.routes.messages import regenerate

    c = build_container(RuntimeArgs(), paths=_fresh_paths())
    try:
        s = c.sessions.create()
        real = Message(role="user", content="진짜 질문")
        c.sessions.append(s.id, real)
        c.sessions.append(s.id, Message(role="assistant", content="", metadata={"thinking": ""}))
        c.sessions.append(
            s.id, Message(role="user", content="넛지", metadata={"synthetic": "empty_nudge"})
        )
        c.sessions.append(s.id, Message(role="assistant", content="낡은 답"))

        resp = await regenerate(s.id, c)
        await resp.body_iterator.aclose()  # 스트림은 소비하지 않는다 — 절단만 검증
        history = c.sessions.history(s.id)
        # 실제 사용자 메시지 이후가 전부 잘렸다 — 넛지·스테일 라운드가 남지 않는다.
        assert [m.role for m in history] == ["user"]
        assert history[-1].id == real.id
    finally:
        c.shutdown()


@pytest.mark.asyncio
async def test_delete_session_drains_active_turn():
    """DELETE /sessions 는 진행 중 턴을 abort+drain 한 뒤 지운다 — 턴 꼬리의
    append/observe 가 삭제된 세션에 쓰다 죽지 않는다."""
    from rice_runtime.server.routes.messages import delete_session

    c = _slow_container()
    try:
        s = c.sessions.create(model="slow")
        events: list = []

        async def consume():
            async for ev in c.agent.run_turn(s.id, "긴 작업"):
                events.append(ev)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.1)  # 턴이 스트리밍 중인 상태 확보
        assert c.agent.is_active(s.id)
        # prefetch가 생성한 상태에 나머지 learning 세션 상태도 추가해
        # DELETE의 orchestrator lifecycle 훅이 모두 정리하는지 검증한다.
        c.learning._staged[s.id] = []
        c.learning._viewed_skills[s.id] = {"viewed"}
        c.learning._sticky_skills[s.id] = {"sticky": 2}
        c.learning._review_gate.should_review(
            s.id, tool_calls_made=0, final_message="ordinary response"
        )

        await asyncio.wait_for(delete_session(s.id, c), timeout=5)
        # drain 이 끝났고(활성 아님), 세션·메시지가 실제로 사라졌다.
        assert not c.agent.is_active(s.id)
        assert c.sessions.get(s.id) is None
        assert len(c.sessions.history(s.id)) == 0
        # 세션 스코프 렌더 상태(동결 스냅샷)도 함께 걷혔다 — 죽은 세션의
        # 트랜스크립트 사본이 LRU 축출까지 잔류하지 않는다.
        assert s.id not in c.agent._render_states
        for state in (
            c.learning._staged,
            c.learning._viewed_skills,
            c.learning._sticky_skills,
            c.learning._last_query,
            c.learning._review_gate._turns_since_review,
        ):
            assert s.id not in state
        await asyncio.wait_for(task, timeout=5)  # 소비자도 오류 없이 종료(done 수신)
        assert events and events[-1].type.value == "done"
    finally:
        c.shutdown()


# ── #2 owner-aware 커넥터 라우팅 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connector_invoke_routes_by_owner_id():
    """같은 도구명을 노출하는 커넥터가 둘일 때 owner_id 지정이 이긴다."""
    from rice_runtime.connector.service import ConnectorService

    db = Database(":memory:").connect()
    svc = ConnectorService(db)

    def spec(owner: str) -> ToolSpec:
        return ToolSpec(name="echo", description="", parameters={}, source="connector", owner_id=owner)

    svc._local_specs = {"local:a": [spec("local:a")], "local:b": [spec("local:b")]}
    routed: list[str] = []

    async def fake_invoke_local(connector_id, call):
        from rice_runtime.core.types import ToolResult

        routed.append(connector_id)
        return ToolResult(call.id, ok=True, result=connector_id)

    svc._invoke_local = fake_invoke_local  # type: ignore[method-assign]

    call = ToolCall(tool_name="echo", arguments={}, id="c1")
    res = await svc.invoke(call, owner_id="local:b")
    assert res.ok and routed == ["local:b"]  # 이름 순회였다면 local:a 로 갔다

    res = await svc.invoke(call)  # owner 미지정 → 기존 이름 탐색 (하위호환)
    assert res.ok and routed[-1] == "local:a"
    db.close()


@pytest.mark.asyncio
async def test_dispatcher_passes_spec_owner_to_connector():
    from rice_runtime.agent.tool_dispatch import ToolDispatcher
    from rice_runtime.core.types import ToolResult
    from rice_runtime.server.approval import ApprovalRegistry

    seen: list[str | None] = []

    class _RecordingConnectors:
        async def invoke(self, call, owner_id=None):
            seen.append(owner_id)
            return ToolResult(call.id, ok=True, result="ok")

    class _NoopPlugins:
        async def invoke(self, call):  # pragma: no cover
            raise AssertionError("unexpected")

    dispatcher = ToolDispatcher(
        connectors=_RecordingConnectors(),  # type: ignore[arg-type]
        plugins=_NoopPlugins(),  # type: ignore[arg-type]
        approvals=ApprovalRegistry(),
        approval_timeout_s=1,
    )
    spec = ToolSpec(
        name="search", description="", parameters={}, source="connector",
        owner_id="conn-B", requires_approval=False,
    )
    call = ToolCall(tool_name="search", arguments={}, id="c1")
    async for _ in dispatcher.dispatch("s1", call, spec):
        pass
    assert seen == ["conn-B"]


def test_duplicate_tool_names_dropped_from_catalog():
    """중복 도구명(빌트인 새도잉 포함)은 카탈로그 조립에서 걸러진다."""
    from rice_runtime.agent.orchestrator import _dedupe_tool_names

    def spec(name: str, owner: str) -> ToolSpec:
        return ToolSpec(name=name, description="", parameters={}, source="connector", owner_id=owner)

    builtin = (ToolSpec(name="read_file", description="", parameters={}, source="builtin"),)
    foldable = (spec("search", "a"), spec("search", "b"), spec("read_file", "mcp"), spec("unique", "c"))
    kept = _dedupe_tool_names(builtin, foldable)
    assert [(t.name, t.owner_id) for t in kept] == [("search", "a"), ("unique", "c")]


# ── #3 도구 실행 취소 + terminal clamp ───────────────────────────────────────


@pytest.mark.asyncio
async def test_scheduler_abort_cancels_running_tool():
    """abort 는 배치 사이가 아니라 **실행 중인** 도구도 끊어야 한다."""
    from rice_runtime.agent.tool_scheduler import (
        ScheduledToolRequest,
        ToolBatchResult,
        ToolScheduler,
    )
    from rice_runtime.core.types import ToolResult

    started = asyncio.Event()

    class _HangingDispatcher:
        async def dispatch(self, session_id, call, spec):
            started.set()
            await asyncio.Event().wait()  # 영원히 안 끝나는 도구
            yield  # pragma: no cover

        async def result_for(self, call):  # pragma: no cover
            return ToolResult(call.id, ok=False, error="no result")

    spec = ToolSpec(name="hang", description="", parameters={}, source="builtin")
    req = ScheduledToolRequest(call=ToolCall(tool_name="hang", arguments={}, id="c1"), spec=spec)
    abort = asyncio.Event()
    result = ToolBatchResult()
    scheduler = ToolScheduler(_HangingDispatcher())  # type: ignore[arg-type]

    async def run():
        async for _ in scheduler.schedule("s1", [req], result, abort):
            pass

    task = asyncio.create_task(run())
    await asyncio.wait_for(started.wait(), timeout=2)
    abort.set()
    await asyncio.wait_for(task, timeout=2)  # 이전 구현은 여기서 무한 대기했다
    assert result.results["c1"].ok is False
    assert "cancel" in (result.results["c1"].error or "")


def test_terminal_timeout_is_clamped():
    from rice_runtime.agent.builtins.terminal_tool import _clamp_timeout

    assert _clamp_timeout(86400) == 600.0  # 모델이 하루를 요구해도 서버 상한
    assert _clamp_timeout(0) == 1.0
    assert _clamp_timeout(-5) == 1.0
    assert _clamp_timeout("garbage") == 60.0
    assert _clamp_timeout(30) == 30.0


# ── #4 설정/DB 부분 적용 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_provider_models_replace_is_transactional():
    """교체 도중 실패(중복 PK)하면 이전 모델 목록이 그대로 남고 live config 도
    갱신되지 않는다 — 반쪽 상태(설정은 새 모델, credential 없음) 방지."""
    from rice_runtime.server.routes.settings import SettingsIn, update_settings

    c = build_container(RuntimeArgs(), paths=_fresh_paths())
    try:
        await update_settings(
            SettingsIn(registered_models=[{"modelId": "m-old", "provider": "openai", "providerLabel": "O", "apiKey": "sk-old"}]),
            c,
        )
        before_model = c.config.provider.model
        bad = SettingsIn(
            provider={"model": "m-new"},
            registered_models=[
                {"modelId": "dup", "provider": "openai", "providerLabel": "O", "apiKey": "k1"},
                {"modelId": "dup", "provider": "openai", "providerLabel": "O", "apiKey": "k2"},
            ],
        )
        with pytest.raises(Exception):
            await update_settings(bad, c)
        rows = c.db.query_all("SELECT model_id, api_key FROM provider_models")
        assert [(r["model_id"], r["api_key"]) for r in rows] == [("m-old", "sk-old")]
        assert c.config.provider.model == before_model  # live config 미반영
    finally:
        c.shutdown()


def test_corrupted_config_yaml_boots_with_defaults(tmp_path):
    paths = RicePaths.from_env(str(tmp_path))
    paths.ensure()
    paths.config.write_text(":: not [ yaml ::", encoding="utf-8")
    cfg = load_config(paths)
    assert isinstance(cfg, Config)  # 예외 없이 기본값 부팅


def test_unknown_config_keys_are_ignored_not_fatal(tmp_path):
    paths = RicePaths.from_env(str(tmp_path))
    paths.ensure()
    paths.config.write_text(
        "provider:\n  model: my-model\n  removed_legacy_knob: 42\n", encoding="utf-8"
    )
    cfg = load_config(paths)
    assert cfg.provider.model == "my-model"  # 아는 키는 적용, 모르는 키는 무시


# ── #5 스킬 패치 무음 no-op ──────────────────────────────────────────────────


def test_apply_patch_surfaces_corrupted_skill_errors(tmp_path):
    """손상된 SKILL.md 는 return None 으로 삼켜지지 않고 예외로 드러난다."""
    from rice_runtime.learning.service import LearningService

    paths = RicePaths.from_env(str(tmp_path))
    paths.ensure()
    db = Database(":memory:").connect()
    svc = LearningService(db, LearningConfig(), paths)

    paths.skills.mkdir(parents=True, exist_ok=True)
    (paths.skills / "broken-skill.md").write_text("frontmatter 없는 파일", encoding="utf-8")

    with pytest.raises(ValidationError):
        svc.apply_patch("broken-skill", approved=True, proposed_body="새 본문")
    db.close()


def test_skill_store_write_is_atomic(tmp_path):
    from rice_runtime.learning.skill.store import SkillData, SkillStore

    paths = RicePaths.from_env(str(tmp_path))
    paths.ensure()
    store = SkillStore(paths)
    skill = SkillData(
        name="atomic-skill", version=1, description="d", state="active",
        body="본문", tags=[], source=None, created_at=now_iso(), updated_at=now_iso(),
    )
    store.write(skill)
    assert store.read("atomic-skill").body.strip() == "본문"
    # 임시 파일이 남지 않는다 (rename 완료)
    assert not list(paths.skills.glob("*.tmp"))


# ── #6 MCP initialize 직렬화 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_initialize_runs_once_under_parallel_calls():
    import httpx

    from rice_runtime.connector.mcp.client import RemoteMcpClient

    init_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal init_calls
        payload = json.loads(request.content or b"{}")
        method = payload.get("method")
        if method == "initialize":
            init_calls += 1
            await asyncio.sleep(0.05)  # 레이스 윈도우를 벌린다
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": "init", "result": {}},
                headers={"mcp-session-id": "s-1"},
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            # 세션형 서버: initialize 가 준 세션 id 를 요구한다
            assert request.headers.get("mcp-session-id") == "s-1"
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": "t", "result": {"tools": [{"name": "echo"}]}},
            )
        return httpx.Response(400)

    client = RemoteMcpClient("http://mcp.test/rpc", transport=httpx.MockTransport(handler))
    a, b = await asyncio.gather(client.list_tools("tok"), client.list_tools("tok"))
    assert init_calls == 1  # 병렬 첫 호출에서도 initialize 는 1회
    assert [s.name for s in a] == ["echo"] and [s.name for s in b] == ["echo"]


# ── #7 crash 후 running 리뷰 잡 재수확 ──────────────────────────────────────


def test_orphan_running_review_jobs_are_requeued_at_boot():
    from rice_runtime.learning.ledger import LearningLedger

    db = Database(":memory:").connect()
    db.execute(
        "INSERT INTO learning_jobs (id, session_id, created_at, kind, status, tool_calls_made, claimed_at) "
        "VALUES ('job_x', 'sess_x', ?, 'review', 'running', 3, ?)",
        (now_iso(), now_iso()),
    )
    ledger = LearningLedger(db)
    assert ledger.requeue_orphan_running_jobs() == 1
    row = db.query_one("SELECT status, claimed_at FROM learning_jobs WHERE id='job_x'")
    assert row["status"] == "pending" and row["claimed_at"] is None
    # 되돌아온 잡은 다시 claim 가능하다
    assert ledger.claim_review_job("sess_x") is not None
    db.close()


# ── #8 workspace sandbox symlink escape ─────────────────────────────────────


@pytest.mark.asyncio
async def test_search_and_list_do_not_follow_symlinks_out_of_workspace(tmp_path):
    from rice_runtime.agent.builtins import file_tools

    paths = RicePaths.from_env(str(tmp_path / "rice"))
    paths.ensure()
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("TOPSECRET_MARKER", encoding="utf-8")

    tools = {spec.name: fn for spec, fn in file_tools.tools(paths)}
    workspace = paths.workspace.resolve()
    (workspace / "normal.txt").write_text("plain TOPSECRET_MARKER 아님", encoding="utf-8")
    (workspace / "leak.txt").symlink_to(secret)
    (workspace / "leakdir").symlink_to(secret.parent)

    result = await tools["search_files"]({"query": "TOPSECRET_MARKER"})
    assert [m["path"] for m in result["matches"]] == ["normal.txt"]  # symlink 경유 없음

    listing = await tools["list_dir"]({"path": ".", "depth": 3})
    listed = {e["path"] for e in listing["entries"]}
    assert "leak.txt" not in listed and "leakdir" not in listed

    with pytest.raises(ValueError):
        await tools["read_file"]({"path": "leak.txt"})  # resolve 재검증 (기존 보장 유지)
