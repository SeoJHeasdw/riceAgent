"""tests/test_hardening_p0.py — 하드닝 회귀.

  - 토큰 추정: CJK 가중 + 세션 캘리브레이션 + 컨텍스트 길이 재시도.
  - 메모리 격리: 외부 콘텐츠 유래 메모리는 quarantined → 주입 제외 → 승인/거부.
  - 세션 락: 같은 세션 동시 턴 방어.
  - 제안 provenance: 외부 콘텐츠 유래 스킬 제안에 경고 라벨.
"""

from __future__ import annotations

import tempfile

import pytest

from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.core.tokens import TokenCalibrator, estimate_tokens, is_context_length_error
from rice_runtime.core.types import Message, TurnOutcome
from rice_runtime.core.config import LearningConfig
from rice_runtime.learning.memory.store import MemoryStore
from rice_runtime.learning.memory.search import MemorySearch
from rice_runtime.learning.service import LearningService


def _fresh_db() -> Database:
    return Database(":memory:").connect()


def _fresh_paths() -> RicePaths:
    return RicePaths.from_env(tempfile.mkdtemp(prefix="rice-p0-")).ensure()


# ── 토큰 추정 ────────────────────────────────────────────────────────────────

class TestTokenEstimation:
    def test_cjk_weighted_much_heavier_than_len_over_4(self):
        ko = "쿠버네티스에 서비스를 배포하는 표준 절차입니다"  # 한글 위주
        assert estimate_tokens(ko) > len(ko) // 4 * 2  # 옛 추정의 2배 이상

    def test_ascii_close_to_len_over_4(self):
        en = "deploy the service to the production cluster now please"
        old = len(en) // 4
        new = estimate_tokens(en)
        assert abs(new - old) <= old * 0.3  # 라틴은 거의 안 변한다

    def test_empty_is_zero(self):
        assert estimate_tokens("") == 0

    def test_calibrator_learns_ratio_from_actual(self):
        c = TokenCalibrator()
        ko = "한국어 문장 " * 20
        base = estimate_tokens(ko)
        # 실측이 추정의 1.5배라고 가정 → 비율이 그쪽으로 학습돼야 한다
        for _ in range(5):
            c.observe(base, int(base * 1.5))
        assert 1.3 < c.ratio < 1.7
        assert c.estimate(ko) > base  # 보정 후 추정이 커졌다

    def test_calibrator_noop_without_samples(self):
        c = TokenCalibrator()
        assert c.estimate("hello world") == estimate_tokens("hello world")

    def test_calibrator_ignores_cache_hit_artifacts(self):
        # KV 캐시 히트 시 Ollama prompt_eval_count는 새로 프리필한 suffix만 센다
        # — 전체 대비 비율이 하한(0.5) 미만인 관측은 실측이 아니라 캐시
        # 아티팩트이므로 통째로 무시해야 한다(EMA 오염 방지).
        c = TokenCalibrator()
        c.observe(10_000, 120)  # 캐시 히트: 새 tail 120토큰만 평가됨
        assert c.samples == 0
        assert c.ratio == 1.0
        # 정상 범위 관측은 여전히 학습된다
        c.observe(10_000, 8_000)
        assert c.samples == 1
        assert 0.7 < c.ratio < 0.9

    def test_context_length_error_detection(self):
        assert is_context_length_error(Exception("prompt is too long: 300000 tokens > 200000"))
        assert is_context_length_error(Exception("This model's maximum context length is 200000"))
        assert not is_context_length_error(Exception("connection reset by peer"))


# ── 메모리 격리 ──────────────────────────────────────────────────────────────

class TestMemoryQuarantine:
    def test_external_derived_memory_is_quarantined(self):
        store = MemoryStore(_fresh_db())
        item = store.add("fact", "사용자는 X를 선호한다", "s1", derived_from_external=True)
        assert item.status == "quarantined"
        assert item.derived_from_external is True

    def test_conversation_memory_is_active(self):
        store = MemoryStore(_fresh_db())
        item = store.add("fact", "프로젝트는 pytest를 쓴다", "s1")
        assert item.status == "active"

    def test_quarantined_excluded_from_recent_and_fts(self):
        db = _fresh_db()
        store = MemoryStore(db)
        search = MemorySearch(db)
        store.add("fact", "activefact about deployment", "s1")
        store.add("fact", "quarantined fact about deployment", "s1", derived_from_external=True)
        recent = search.recent(10)
        assert all("quarantined" not in m.content for m in recent)
        fts = search.fts_search("deployment", 10)
        assert all("quarantined" not in m.content for m in fts)
        # list()는 둘 다 보여준다(관리 UI용)
        assert len(store.list()) == 2

    def test_approve_promotes_quarantined_to_active(self):
        db = _fresh_db()
        store = MemoryStore(db)
        search = MemorySearch(db)
        item = store.add("fact", "quarantined deploy note", "s1", derived_from_external=True)
        assert search.recent(10) == []
        store.set_status(item.id, "active")
        assert len(search.recent(10)) == 1

    def test_source_message_ids_round_trip(self):
        store = MemoryStore(_fresh_db())
        item = store.add(
            "fact", "derived note", "s1",
            derived_from_external=True, source_message_ids=("msg_a", "msg_b"),
        )
        got = next(m for m in store.list() if m.id == item.id)
        assert got.source_message_ids == ("msg_a", "msg_b")


# ── 리뷰 엔진: 외부 콘텐츠 → 격리 (end-to-end, fake provider) ─────────────────

from rice_runtime.agent.providers.interfaces import ProviderCapabilities, ProviderChunk
from rice_runtime.core.types import ToolCall


class _MemoryProposingProvider:
    name = "scripted"

    def __init__(self, content: str) -> None:
        self._content = content

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(native_tool_calling=True)

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
        yield ProviderChunk(
            kind="tool_call",
            tool_call=ToolCall(tool_name="propose_memory_write", arguments={"kind": "fact", "content": self._content}),
        )
        yield ProviderChunk(kind="done", stop_reason="end_turn")


class TestReviewQuarantineIntegration:
    def _svc(self, provider):
        cfg = LearningConfig(review_min_tool_calls=1, review_nudge_turns=100)
        return LearningService(_fresh_db(), cfg, _fresh_paths(), resolve_provider=lambda: provider)

    @pytest.mark.asyncio
    async def test_memory_from_external_turn_is_quarantined_not_injected(self):
        svc = self._svc(_MemoryProposingProvider("Gmail에서 본 마감일은 금요일이다"))
        # 외부(connector) 도구 결과가 섞인 히스토리
        history = [
            Message(role="user", content="메일 정리해줘"),
            Message(role="assistant", content="", tool_calls=(ToolCall(tool_name="gmail_search", arguments={}, id="c1"),)),
            Message(role="tool", content="{...메일 본문...}", metadata={"tool_call_id": "c1", "tool_source": "connector"}),
            Message(role="assistant", content="정리했습니다."),
        ]
        await svc.observe(TurnOutcome(session_id="s1", final_message="정리했습니다.", history=tuple(history), tool_calls_made=3, injected_skill_ids=()))
        effects = await svc.review("s1", history)
        assert len(effects) == 1
        assert effects[0].status == "quarantined"
        assert effects[0].derived_from_external is True
        # 격리라 prefetch 주입 대상이 아니다
        pf = svc.prefetch("s2", "마감일 언제였지")
        assert all("마감일" not in m.content for m in pf.memories)

    @pytest.mark.asyncio
    async def test_memory_from_pure_conversation_is_active(self):
        svc = self._svc(_MemoryProposingProvider("사용자는 간결한 답변을 선호한다"))
        # 로컬(builtin) 도구만 쓴 턴 — 외부 콘텐츠 없음. 게이트는 통과(tool_calls>=1).
        history = [
            Message(role="user", content="앞으로 짧게 대답해"),
            Message(role="assistant", content="", tool_calls=(ToolCall(tool_name="read_file", arguments={}, id="c1"),)),
            Message(role="tool", content="{...}", metadata={"tool_call_id": "c1", "tool_source": "builtin"}),
            Message(role="assistant", content="알겠습니다."),
        ]
        await svc.observe(TurnOutcome(session_id="s1", final_message="알겠습니다.", history=tuple(history), tool_calls_made=1, injected_skill_ids=()))
        effects = await svc.review("s1", history)
        assert len(effects) == 1
        assert effects[0].status == "active"
        assert effects[0].derived_from_external is False


# ── 세션 동시 턴 락 (P0.5) ────────────────────────────────────────────────────

from rice_runtime import build_container


class TestSessionLock:
    @pytest.mark.asyncio
    async def test_concurrent_turn_on_same_session_is_rejected(self):
        c = build_container(paths=_fresh_paths())
        try:
            s = c.sessions.create(model="echo")
            gen1 = c.agent.run_turn(s.id, "첫 번째 질문")
            # 첫 턴을 시작만 하고(락 획득) 완료하지 않은 채로 둔다.
            await gen1.__anext__()
            # 같은 세션 두 번째 턴 → session_busy 에러 후 done.
            events = [e async for e in c.agent.run_turn(s.id, "두 번째 질문")]
            codes = [e.data.get("error_code") for e in events if e.type.value == "error"]
            assert "session_busy" in codes
            await gen1.aclose()
        finally:
            c.shutdown()

    @pytest.mark.asyncio
    async def test_sequential_turns_ok(self):
        c = build_container(paths=_fresh_paths())
        try:
            s = c.sessions.create(model="echo")
            ev1 = [e.type.value async for e in c.agent.run_turn(s.id, "하나")]
            ev2 = [e.type.value async for e in c.agent.run_turn(s.id, "둘")]
            assert ev1[-1] == "done" and ev2[-1] == "done"
            # 두 번째 턴이 락에 걸리지 않았다 — 에러 없음.
            assert "error" not in ev2
        finally:
            c.shutdown()


# ── 로컬 API Origin/Host/토큰 방어 (P0.5) ─────────────────────────────────────

class TestLocalApiGuard:
    def _client(self, token: str = "secret-token"):
        from fastapi.testclient import TestClient  # noqa: PLC0415
        from rice_runtime.core.config import RuntimeArgs  # noqa: PLC0415
        from rice_runtime.server.app import create_app  # noqa: PLC0415

        c = build_container(RuntimeArgs(token=token), paths=_fresh_paths())
        return TestClient(create_app(c)), token

    def test_health_is_public(self):
        client, _ = self._client()
        assert client.get("/health").status_code == 200

    def test_missing_token_rejected(self):
        client, _ = self._client()
        r = client.get("/skills", headers={"host": "localhost"})
        assert r.status_code == 401

    def test_valid_token_ok(self):
        client, token = self._client()
        r = client.get("/skills", headers={"authorization": f"Bearer {token}", "host": "localhost"})
        assert r.status_code == 200

    def test_cross_origin_rejected(self):
        client, token = self._client()
        r = client.get(
            "/skills",
            headers={"authorization": f"Bearer {token}", "origin": "https://evil.com", "host": "localhost"},
        )
        assert r.status_code == 403

    def test_local_origin_allowed(self):
        client, token = self._client()
        r = client.get(
            "/skills",
            headers={"authorization": f"Bearer {token}", "origin": "http://localhost:5273", "host": "localhost"},
        )
        assert r.status_code == 200

    def test_foreign_host_rejected(self):
        client, token = self._client()
        r = client.get(
            "/skills",
            headers={"authorization": f"Bearer {token}", "host": "evil.com"},
        )
        assert r.status_code == 403
