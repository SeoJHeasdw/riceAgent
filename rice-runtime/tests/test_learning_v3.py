"""test_learning_v3.py — Self-Improvement v3 수용 기준 검증 (2026-07-03).

리모델의 중심 개념 두 가지를 검증한다:
  A. **Learning Ledger** — 제안→결정의 영속 기록. 반복 거부 쿨다운(억제),
     리뷰 프롬프트의 회피 목록, 감사 이벤트.
  B. **Suggestion Inbox** — 스트림 파킹 밖에서 살아남는 제안함. 타임아웃된
     HITL 제안의 이동(폐기 아님), 발굴(discovery) 산출물의 수신, 개별 결정.

부속 경로:
  C. 미드턴 propose_skill(T5) — stage_proposal/observe 드레인/빌트인 등록.
  D. resolve_proposal — approve/reject/timeout 세 갈래.
  E. DiscoveryEngine — 크로스세션 발굴, 기존 스킬 중복/억제 스킵.
  F. 메모리 위생 — delete_memory + observe()의 정규식 추출 제거는
     test_learning_t3.py에서 검증.

격리: 실제 ~/.rice 건드리지 않음. tempfile.mkdtemp() / in-memory sqlite.
"""

from __future__ import annotations

import tempfile
from typing import AsyncIterator, Sequence

import pytest

from rice_runtime.agent.builtins import build_builtin_tools
from rice_runtime.agent.effect_surfacing import surface_learning_effect
from rice_runtime.agent.providers.interfaces import ProviderCapabilities, ProviderChunk
from rice_runtime.core.config import LearningConfig
from rice_runtime.core.errors import NotFoundError
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.core.types import Message, ToolCall, ToolSpec, TurnOutcome
from rice_runtime.learning.discovery import DiscoveryEngine
from rice_runtime.learning.interfaces import PatchProposal
from rice_runtime.learning.ledger import LearningLedger
from rice_runtime.learning.review.engine import ReviewEngine
from rice_runtime.learning.service import LearningService
from rice_runtime.learning.skill.store import SkillData, SkillStore
from rice_runtime.learning.skill.usage import UsageStore
from rice_runtime.learning.memory.store import MemoryStore
from rice_runtime.prompts.discovery import SessionDigest, build_session_digest
from rice_runtime.server.approval import ApprovalRegistry


# ── shared helpers (test_learning_t4.py와 동일 관례) ─────────────────────────

def _fresh_paths() -> RicePaths:
    tmp = tempfile.mkdtemp(prefix="rice-v3-test-")
    return RicePaths.from_env(tmp).ensure()


def _fresh_db() -> Database:
    return Database(":memory:").connect()


def _make_skill(name: str, body: str = "## Steps\n1. Old step\n", version: int = 1, source: str = "session:s0") -> SkillData:
    return SkillData(
        name=name, version=version, description="A test skill", state="active",
        body=body, tags=["test"], source=source,
        created_at="2024-01-01T00:00:00Z", updated_at="2024-01-01T00:00:00Z",
    )


class _FakeProvider:
    name = "fake"

    def __init__(self, tool_calls: Sequence[ToolCall] = ()) -> None:
        self._tool_calls = list(tool_calls)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    async def stream(
        self,
        *,
        system: str,
        messages,
        tools: Sequence[ToolSpec] = (),
        temperature: float = 0.7,
        response_schema: dict | None = None,
        think: bool | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        for call in self._tool_calls:
            yield ProviderChunk(kind="tool_call", tool_call=call)
        yield ProviderChunk(kind="done", stop_reason="tool_use" if self._tool_calls else "end_turn")


def _service(db=None, paths=None, provider=None, **cfg_kw) -> LearningService:
    cfg = LearningConfig(review_rejection_cooldown_hours=24.0, review_max_rejections=2, **cfg_kw)
    resolver = (lambda: provider) if provider is not None else None
    return LearningService(db or _fresh_db(), cfg, paths or _fresh_paths(), resolve_provider=resolver)


# ── A. Learning Ledger ───────────────────────────────────────────────────────

class TestLedger:
    def setup_method(self):
        self._db = _fresh_db()
        self._ledger = LearningLedger(self._db)

    def test_record_and_events_roundtrip(self):
        self._ledger.record("proposed", skill_id="sk-a", session_id="s1", origin="review",
                            trigger="T4", detail={"changelog": "x"})
        events = self._ledger.events()
        assert len(events) == 1
        ev = events[0]
        assert ev["kind"] == "proposed" and ev["skill_id"] == "sk-a"
        assert ev["trigger"] == "T4" and ev["detail"] == {"changelog": "x"}

    def test_rejection_cooldown_suppression(self):
        assert self._ledger.is_suppressed("sk-a", within_hours=24, max_rejections=2) is False
        self._ledger.record("rejected", skill_id="sk-a", detail={"changelog": "no"})
        assert self._ledger.is_suppressed("sk-a", within_hours=24, max_rejections=2) is False
        self._ledger.record("rejected", skill_id="sk-a", detail={"changelog": "still no"})
        assert self._ledger.is_suppressed("sk-a", within_hours=24, max_rejections=2) is True
        # 다른 스킬은 영향 없음
        assert self._ledger.is_suppressed("sk-b", within_hours=24, max_rejections=2) is False
        # 프롬프트 회피 목록에 changelog가 실린다
        rejections = self._ledger.recent_rejections(within_hours=24)
        assert any("sk-a" in r["skill_id"] for r in rejections)

    def test_add_suggestion_dedups_pending_same_skill_kind(self):
        first = self._ledger.add_suggestion(origin="discovery", kind="create", skill_id="sk-x", payload={"a": 1})
        assert first is not None
        dup = self._ledger.add_suggestion(origin="discovery", kind="create", skill_id="sk-x", payload={"a": 2})
        assert dup is None  # 멱등 — 발굴이 여러 번 돌아도 도배되지 않는다
        assert len(self._ledger.suggestions()) == 1

    def test_add_suggestion_respects_pending_cap(self):
        capped = LearningLedger(self._db, pending_cap=2)
        assert capped.add_suggestion(origin="review", kind="create", skill_id="a", payload={}) is not None
        assert capped.add_suggestion(origin="review", kind="create", skill_id="b", payload={}) is not None
        assert capped.add_suggestion(origin="review", kind="create", skill_id="c", payload={}) is None

    def test_decide_suggestion_is_single_shot(self):
        sid = self._ledger.add_suggestion(origin="timeout", kind="patch", skill_id="sk-y", payload={})
        assert self._ledger.decide_suggestion(sid, "approved") is True
        assert self._ledger.decide_suggestion(sid, "rejected") is False  # 이중 결정 방지
        assert self._ledger.suggestions("approved")[0]["id"] == sid


# ── C. 미드턴 스테이징 (T5) ──────────────────────────────────────────────────

class TestStageProposal:
    def setup_method(self):
        self._paths = _fresh_paths()
        self._svc = _service(paths=self._paths)
        self._store = SkillStore(self._paths)

    def test_stage_create_then_observe_drains_t5(self):
        res = self._svc.stage_proposal("s1", name="new-sk", body="# b\n1. step", description="d", changelog="cl")
        assert res["status"] == "staged"
        # ledger에 proposed(T5, agent) 기록
        events = self._svc.learning_events()
        assert any(e["kind"] == "proposed" and e["trigger"] == "T5" and e["origin"] == "agent" for e in events)

    def test_stage_patch_for_existing_skill(self):
        self._store.write(_make_skill("old-sk"))
        res = self._svc.stage_proposal("s1", name="old-sk", body="## Steps\n1. New step\n", changelog="fix")
        assert res["status"] == "staged"
        assert "v1→v2" in res["message"]

    def test_stage_refuses_plugin_owned_skill(self):
        self._store.write(_make_skill("plug-sk", source="plugin:deployment"))
        res = self._svc.stage_proposal("s1", name="plug-sk", body="x")
        assert res["status"] == "dropped"

    def test_stage_drops_invalid_name_and_identical_body(self):
        assert self._svc.stage_proposal("s1", name="Bad Name!", body="x")["status"] == "dropped"
        self._store.write(_make_skill("same-sk", body="same\n"))
        assert self._svc.stage_proposal("s1", name="same-sk", body="same\n")["status"] == "dropped"

    def test_stage_queue_cap_is_three(self):
        for i in range(3):
            assert self._svc.stage_proposal("s1", name=f"sk-{i}", body="# b\n1. s")["status"] == "staged"
        assert self._svc.stage_proposal("s1", name="sk-3", body="# b\n1. s")["status"] == "dropped"

    @pytest.mark.asyncio
    async def test_builtin_registration_and_handler(self):
        specs, handlers = build_builtin_tools(self._paths, learning=self._svc)
        assert "propose_skill" in handlers
        assert any(s.name == "propose_skill" for s in specs)
        fn = handlers["propose_skill"]
        assert getattr(fn, "needs_session", False) is True
        result = await fn({"name": "tool-made-sk", "body": "# b\n1. s", "changelog": "via tool"}, session_id="s9")
        assert result["status"] == "staged"
        # learning 미주입이면 등록되지 않는다 (역호환)
        specs2, handlers2 = build_builtin_tools(self._paths)
        assert "propose_skill" not in handlers2


# ── D. resolve_proposal — approve/reject/timeout ────────────────────────────

class TestResolveProposal:
    def setup_method(self):
        self._paths = _fresh_paths()
        self._svc = _service(paths=self._paths)
        self._store = SkillStore(self._paths)

    def _create_proposal(self, name="res-sk") -> PatchProposal:
        from rice_runtime.learning.proposals import make_create_proposal
        p = make_create_proposal(name=name, body="# b\n1. s", description="d", changelog="cl", trigger="T4")
        assert p is not None
        return p

    def test_approve_applies_and_records(self):
        version = self._svc.resolve_proposal(self._create_proposal(), session_id="s1", decision="approve")
        assert version == 1
        assert self._store.read("res-sk").version == 1
        assert any(e["kind"] == "approved" for e in self._svc.learning_events())

    def test_reject_records_and_feeds_suppression(self):
        p = self._create_proposal()
        assert self._svc.resolve_proposal(p, session_id="s1", decision="reject") is None
        assert self._svc.resolve_proposal(p, session_id="s1", decision="reject") is None
        with pytest.raises(NotFoundError):
            self._store.read("res-sk")  # 파일 불변
        # 2회 거부 → 쿨다운 억제 대상
        assert self._svc._ledger.is_suppressed("res-sk", within_hours=24, max_rejections=2) is True

    def test_timeout_moves_to_inbox_not_discarded(self):
        p = self._create_proposal()
        assert self._svc.resolve_proposal(p, session_id="s1", decision="timeout") is None
        with pytest.raises(NotFoundError):
            self._store.read("res-sk")  # 적용은 안 됐다 (safe-default 유지)
        pending = self._svc.suggestions()
        assert len(pending) == 1
        sug = pending[0]
        assert sug["origin"] == "timeout" and sug["kind"] == "create"
        assert sug["payload"]["proposed_body"] == p.proposed_body
        assert sug["payload"]["session_id"] == "s1"

    @pytest.mark.asyncio
    async def test_effect_surfacing_timeout_path_goes_through_inbox(self):
        """effect_surfacing이 resolve_proposal로 위임하는 회귀 테스트 — 파킹
        타임아웃(0초)이 폐기가 아니라 제안함 이동으로 끝난다."""
        approvals = ApprovalRegistry()
        events = []
        async for ev in surface_learning_effect(
            session_id="s1", effect=self._create_proposal("timeout-sk"),
            learning=self._svc, approvals=approvals, approval_timeout_s=0,
        ):
            events.append(ev)
        assert [e.type for e in events] == ["skill_patch_proposed"]  # skill_patched 없음
        assert self._svc.suggestions()[0]["skill_id"] == "timeout-sk"

    def test_decide_suggestion_approve_creates_skill(self):
        p = self._create_proposal("inbox-sk")
        self._svc.resolve_proposal(p, session_id="s7", decision="timeout")
        sug = self._svc.suggestions()[0]
        result = self._svc.decide_suggestion(sug["id"], approved=True)
        assert result["applied"] is True and result["version"] == 1
        created = self._store.read("inbox-sk")
        assert created.source == "session:s7"  # 출처 세션 복원
        assert self._svc.suggestions() == []  # pending에서 사라짐

    def test_decide_suggestion_reject_feeds_cooldown(self):
        p = self._create_proposal("nope-sk")
        self._svc.resolve_proposal(p, session_id="s7", decision="timeout")
        sug = self._svc.suggestions()[0]
        assert self._svc.decide_suggestion(sug["id"], approved=False)["applied"] is False
        assert any(e["kind"] == "rejected" and e["skill_id"] == "nope-sk" for e in self._svc.learning_events())
        with pytest.raises(NotFoundError):
            self._svc.decide_suggestion(sug["id"], approved=True)  # 이중 결정 → 404


# ── 억제가 리뷰 엔진 산출물에 실제로 걸리는지 ────────────────────────────────

class TestReviewSuppression:
    @pytest.mark.asyncio
    async def test_suppressed_skill_proposal_is_dropped(self):
        paths = _fresh_paths()
        db = _fresh_db()
        store = SkillStore(paths)
        store.write(_make_skill("hot-sk"))
        ledger = LearningLedger(db)
        ledger.record("rejected", skill_id="hot-sk", detail={"changelog": "no"})
        ledger.record("rejected", skill_id="hot-sk", detail={"changelog": "no again"})

        provider = _FakeProvider([
            ToolCall(tool_name="propose_skill_patch", arguments={
                "skill_id": "hot-sk", "changelog": "yet another try",
                "new_body": "## Steps\n1. Different step\n", "trigger_reason": "T1a",
            }),
        ])
        engine = ReviewEngine(
            resolve_provider=lambda: provider, skill_store=store, usage_store=UsageStore(paths),
            mem_store=MemoryStore(db), skill_catalog_token_budget=24_000,
            ledger=ledger, rejection_cooldown_hours=24.0, max_rejections=2,
        )
        effects = await engine.review("s1", [Message(role="user", content="hi")])
        assert effects == []  # 쿨다운 창 안 — 사용자를 다시 방해하지 않는다

    def test_recent_rejections_reach_prompt(self):
        from rice_runtime.prompts.review import build_review_messages
        _, messages = build_review_messages(
            [Message(role="user", content="hi")],
            full_body_skills=[], compact_skills=[], known_facts=[],
            rejected_recently=["[hot-sk] pytest 병렬도 변경"],
        )
        assert "REJECTED" in messages[-1].content
        assert "hot-sk" in messages[-1].content


# ── E. Discovery (크로스세션 발굴) ───────────────────────────────────────────

class TestDiscovery:
    def _digests(self) -> list[SessionDigest]:
        return [
            SessionDigest("s1", "주간 리포트", "user: 매출 뽑아줘\nassistant: database 조회 후 요약했습니다"),
            SessionDigest("s2", "리포트 또", "user: 이번주도 매출 리포트\nassistant: 같은 절차로 처리"),
        ]

    @pytest.mark.asyncio
    async def test_discover_adds_create_suggestion_to_inbox(self):
        paths = _fresh_paths()
        provider = _FakeProvider([
            ToolCall(tool_name="propose_skill_create", arguments={
                "name": "weekly-sales-report", "description": "주간 매출 리포트",
                "body": "1. database 조회\n2. 요약\n", "tags": ["report"],
            }),
        ])
        svc = _service(paths=paths, provider=provider)
        added = await svc.discover(self._digests())
        assert len(added) == 1
        assert added[0]["origin"] == "discovery" and added[0]["kind"] == "create"
        assert svc.suggestions()[0]["skill_id"] == "weekly-sales-report"
        assert any(e["kind"] == "discovery_run" for e in svc.learning_events())

    @pytest.mark.asyncio
    async def test_discover_skips_existing_and_needs_two_digests(self):
        paths = _fresh_paths()
        store = SkillStore(paths)
        store.write(_make_skill("weekly-sales-report"))
        provider = _FakeProvider([
            ToolCall(tool_name="propose_skill_create", arguments={
                "name": "weekly-sales-report", "description": "d", "body": "1. x\n",
            }),
        ])
        svc = _service(paths=paths, provider=provider)
        assert await svc.discover(self._digests()) == []  # 기존 스킬 재발명 스킵
        engine = DiscoveryEngine(
            resolve_provider=lambda: provider, skill_store=store, max_body_chars=20_000,
        )
        assert await engine.discover(self._digests()[:1]) == []  # 세션 1개론 반복을 못 본다

    def test_build_session_digest_compacts_and_skips_tool_bodies(self):
        messages = [
            Message(role="user", content="매출 뽑아줘"),
            Message(role="tool", content="x" * 5000, metadata={"tool_name": "database.query"}),
            Message(role="assistant", content="y" * 500),
        ]
        d = build_session_digest("s1", "리포트", messages, max_chars=1000)
        assert "database.query" in d.excerpt and "xxxx" not in d.excerpt
        assert len(d.excerpt) <= 1000

    @pytest.mark.asyncio
    async def test_discover_without_provider_is_noop(self):
        svc = _service()  # resolve_provider=None
        assert await svc.discover(self._digests()) == []


# ── F. 메모리 위생 ───────────────────────────────────────────────────────────

class TestMemoryHygiene:
    def test_delete_memory_removes_and_records(self):
        svc = _service()
        item = svc._mem_store.add("fact", "Project uses pytest for tests")
        assert len(svc.list_memories()) == 1
        svc.delete_memory(item.id)
        assert svc.list_memories() == []
        assert any(e["kind"] == "memory_deleted" for e in svc.learning_events())
        with pytest.raises(NotFoundError):
            svc.delete_memory(item.id)
