"""test_learning_t4.py — T4 수용 기준 검증 (LLM 기반 review 엔진, Phase A).

Hermes learning_loop 이식 작업(2026-07-02) — RICE_LEARNING_LOOP_DESIGN.md가 "향후
고도화"로 미뤄뒀던 실제 저작 지능을 넣는 작업의 핵심 테스트 파일.

수용 기준:
  1. [완료] ReviewGate.should_review: 임계값 미만/이상에서 각각 False/True,
     "체크"만으론 카운터가 리셋되지 않고 mark_reviewed()를 호출해야만 리셋된다.
  2. [완료] apply_patch(from_version=0, 스킬 없음) → NotFoundError 없이 v1 신규 생성,
     archive 단계 스킵 (Phase A.1).
  3. [완료] ReviewEngine.review(...)를 스텁 ILLMProvider로 실행 — propose_skill_create/
     propose_skill_patch/propose_memory_write 각각이 올바른 LearningEffect로 매핑됨.
  4. [완료] agent.effect_surfacing.surface_learning_effect의 SSE+HITL 시퀀스 회귀 테스트
     (MemoryWritten 즉시 통지, PatchProposal 승인/거부 양쪽 경로).
  5. [완료] LearningService.review()/learn() 파사드: 게이트 통합, resolve_provider=None
     시 조용히 스킵.
  6. [완료] Phase D 가드: 크기 상한/시크릿 스크러빙이 apply_patch·create_skill·
     update_skill·MemoryStore.add 전부에서 실제로 동작(기존엔 무검증이었다).

격리: 실제 ~/.rice 건드리지 않음. tempfile.mkdtemp() / in-memory sqlite.
"""

from __future__ import annotations

import asyncio
import tempfile
from typing import AsyncIterator, Sequence

import pytest

from rice_runtime.agent.effect_surfacing import surface_learning_effect
from rice_runtime.agent.providers.interfaces import ProviderCapabilities, ProviderChunk
from rice_runtime.core.config import LearningConfig
from rice_runtime.core.errors import NotFoundError, ValidationError
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.core.types import ApprovalResponse, EventType, Message, ToolCall, ToolSpec
from rice_runtime.learning.interfaces import MemoryWritten, PatchProposal
from rice_runtime.learning.review.engine import ReviewEngine
from rice_runtime.learning.review.gate import ReviewGate
from rice_runtime.learning.service import LearningService
from rice_runtime.learning.skill import guard as skill_guard
from rice_runtime.learning.skill.store import SkillData, SkillStore
from rice_runtime.learning.skill.usage import UsageStore
from rice_runtime.learning.memory.store import MemoryStore
from rice_runtime.server.approval import ApprovalRegistry


# ── shared helpers ───────────────────────────────────────────────────────────

def _fresh_paths() -> RicePaths:
    tmp = tempfile.mkdtemp(prefix="rice-t4-test-")
    return RicePaths.from_env(tmp).ensure()


def _fresh_db() -> Database:
    return Database(":memory:").connect()


def _make_skill(name: str, body: str = "## Steps\n1. Old step\n", version: int = 1) -> SkillData:
    return SkillData(
        name=name, version=version, description="A test skill", state="active",
        body=body, tags=["test"], source="session:sess_abc",
        created_at="2024-01-01T00:00:00Z", updated_at="2024-01-01T00:00:00Z",
    )


class _FakeProvider:
    """A minimal stub `ILLMProvider` — yields pre-scripted tool_call chunks, no
    network. `name` deliberately != "echo" so ReviewEngine's echo-guard doesn't
    skip it."""

    name = "fake"

    def __init__(self, tool_calls: Sequence[ToolCall] = ()) -> None:
        self._tool_calls = list(tool_calls)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    async def stream(
        self, *, system: str, messages, tools: Sequence[ToolSpec] = (), temperature: float = 0.7
    ) -> AsyncIterator[ProviderChunk]:
        for call in self._tool_calls:
            yield ProviderChunk(kind="tool_call", tool_call=call)
        yield ProviderChunk(kind="done", stop_reason="tool_use" if self._tool_calls else "end_turn")


class _RaisingProvider:
    name = "fake-broken"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    async def stream(self, **kwargs) -> AsyncIterator[ProviderChunk]:
        raise RuntimeError("boom")
        yield  # pragma: no cover - unreachable, makes this an async generator


# ── ReviewGate 테스트 ───────────────────────────────────────────────────────

class TestReviewGate:
    def _gate(self, min_tool_calls: int = 3, nudge_turns: int = 8) -> ReviewGate:
        return ReviewGate(min_tool_calls=min_tool_calls, nudge_turns=nudge_turns)

    def test_below_threshold_no_signal_returns_false(self):
        gate = self._gate()
        assert gate.should_review("s1", tool_calls_made=1, final_message="네, 완료했습니다.") is False

    def test_tool_calls_at_threshold_returns_true(self):
        gate = self._gate(min_tool_calls=3)
        assert gate.should_review("s1", tool_calls_made=3, final_message="done") is True

    def test_correction_signal_returns_true_even_with_few_tool_calls(self):
        gate = self._gate(min_tool_calls=10)
        assert gate.should_review("s1", tool_calls_made=0, final_message="그건 틀렸어요") is True

    def test_failure_signal_in_user_text_returns_true(self):
        gate = self._gate(min_tool_calls=10)
        assert (
            gate.should_review("s1", tool_calls_made=0, final_message="알겠습니다", user_text="안 돼요, 오류가 났어요")
            is True
        )

    def test_skill_creation_intent_returns_true(self):
        gate = self._gate(min_tool_calls=10)
        assert (
            gate.should_review("s1", tool_calls_made=0, final_message="이건 재사용 가능한 절차예요")
            is True
        )

    def test_nudge_turns_fallback_fires_after_enough_checks(self):
        """신호도 없고 tool_calls_made도 낮아도, 세션 턴이 충분히 쌓이면 결국 True."""
        gate = self._gate(min_tool_calls=100, nudge_turns=3)
        assert gate.should_review("s1", tool_calls_made=0, final_message="ok") is False  # turn 1
        assert gate.should_review("s1", tool_calls_made=0, final_message="ok") is False  # turn 2
        assert gate.should_review("s1", tool_calls_made=0, final_message="ok") is True  # turn 3 → nudge

    def test_checking_alone_does_not_reset_counter(self):
        """단순 체크(should_review 호출, 결과 무관)만으론 카운터가 리셋되지 않는다 —
        mark_reviewed()를 명시적으로 호출해야만 리셋된다."""
        gate = self._gate(min_tool_calls=100, nudge_turns=3)
        gate.should_review("s1", tool_calls_made=0, final_message="ok")  # turn 1
        gate.should_review("s1", tool_calls_made=0, final_message="ok")  # turn 2
        # 여기서 카운터를 리셋하지 않았으므로 다음 호출(turn 3)에서 nudge가 발동해야 한다.
        assert gate.should_review("s1", tool_calls_made=0, final_message="ok") is True

    def test_mark_reviewed_resets_counter(self):
        gate = self._gate(min_tool_calls=100, nudge_turns=3)
        gate.should_review("s1", tool_calls_made=0, final_message="ok")  # turn 1
        gate.should_review("s1", tool_calls_made=0, final_message="ok")  # turn 2
        gate.mark_reviewed("s1")  # 실제로 리뷰를 실행했다고 가정 → 카운터 리셋
        # 리셋됐으므로 turn 1로 다시 시작 — nudge_turns=3에 도달하려면 2번 더 필요.
        assert gate.should_review("s1", tool_calls_made=0, final_message="ok") is False  # turn 1
        assert gate.should_review("s1", tool_calls_made=0, final_message="ok") is False  # turn 2
        assert gate.should_review("s1", tool_calls_made=0, final_message="ok") is True  # turn 3

    def test_counters_are_independent_per_session(self):
        gate = self._gate(min_tool_calls=100, nudge_turns=2)
        gate.should_review("session-a", tool_calls_made=0, final_message="ok")  # a: turn 1
        assert gate.should_review("session-b", tool_calls_made=0, final_message="ok") is False  # b: turn 1
        assert gate.should_review("session-a", tool_calls_made=0, final_message="ok") is True  # a: turn 2


# ── apply_patch(from_version=0) 신규 생성 테스트 (Phase A.1) ─────────────────

class TestApplyPatchCreation:
    def setup_method(self):
        self._paths = _fresh_paths()
        self._svc = LearningService(_fresh_db(), LearningConfig(), self._paths)
        self._store = SkillStore(self._paths)

    def test_creates_fresh_v1_when_skill_missing(self):
        new_v = self._svc.apply_patch(
            "brand-new-skill",
            approved=True,
            proposed_body="## Steps\n1. Do the new thing\n",
            description="A brand new skill",
            tags=("test", "new"),
            source="session:sess_xyz",
        )
        assert new_v == 1
        detail = self._svc.get_skill("brand-new-skill")
        assert detail is not None
        assert detail.version == 1
        assert detail.description == "A brand new skill"
        assert tuple(detail.tags) == ("test", "new")
        assert detail.source == "session:sess_xyz"
        assert "Do the new thing" in detail.body

    def test_no_history_dir_created_for_fresh_skill(self):
        """생성 경로는 archive_to_history를 타지 않는다 — 보관할 이전 버전이 없다."""
        self._svc.apply_patch("no-history-sk", approved=True, proposed_body="body")
        hist_dir = self._paths.skill_history("no-history-sk")
        assert not hist_dir.exists() or list(hist_dir.glob("*.md")) == []

    def test_usage_starts_at_zero_not_one(self):
        """생성은 "patch"가 아니다 — usage.patch는 0으로 시작한다(create_skill과 동일)."""
        self._svc.apply_patch("fresh-usage-sk", approved=True, proposed_body="body")
        usage = UsageStore(self._paths).read("fresh-usage-sk")
        assert usage.patch == 0

    def test_reject_creates_nothing(self):
        result = self._svc.apply_patch("rejected-new-sk", approved=False, proposed_body="body")
        assert result is None
        assert self._svc.get_skill("rejected-new-sk") is None

    def test_no_body_at_all_fails_closed(self):
        """proposed_body도 edited_body도 없으면 만들 수 없다 — None 반환, 파일 없음."""
        result = self._svc.apply_patch("empty-new-sk", approved=True)
        assert result is None
        assert self._svc.get_skill("empty-new-sk") is None


# ── ReviewEngine 테스트 (Phase A.3) ───────────────────────────────────────────

class TestReviewEngine:
    def setup_method(self):
        self._paths = _fresh_paths()
        self._db = _fresh_db()
        self._skill_store = SkillStore(self._paths)
        self._usage_store = UsageStore(self._paths)
        self._mem_store = MemoryStore(self._db)

    def _engine(self, provider) -> ReviewEngine:
        return ReviewEngine(
            resolve_provider=lambda: provider,
            skill_store=self._skill_store,
            usage_store=self._usage_store,
            mem_store=self._mem_store,
            skill_catalog_token_budget=24_000,
        )

    @pytest.mark.asyncio
    async def test_echo_provider_short_circuits_to_empty(self):
        class _Echo:
            name = "echo"

        engine = self._engine(_Echo())
        effects = await engine.review("s1", [Message(role="user", content="hi")])
        assert effects == []

    @pytest.mark.asyncio
    async def test_no_tool_calls_returns_empty(self):
        engine = self._engine(_FakeProvider([]))
        effects = await engine.review("s1", [Message(role="user", content="hi")])
        assert effects == []

    @pytest.mark.asyncio
    async def test_propose_skill_create_maps_to_patch_proposal_from_v0(self):
        call = ToolCall(
            tool_name="propose_skill_create",
            arguments={
                "name": "deploy-service",
                "description": "Deploy a service",
                "tags": ["deploy", "k8s"],
                "body": "## Steps\n1. Do the deploy\n",
            },
        )
        engine = self._engine(_FakeProvider([call]))
        effects = await engine.review("s1", [Message(role="user", content="hi")])
        assert len(effects) == 1
        proposal = effects[0]
        assert isinstance(proposal, PatchProposal)
        assert proposal.skill_id == "deploy-service"
        assert proposal.from_version == 0
        assert proposal.to_version == 1
        assert proposal.trigger == "T4"
        assert proposal.description == "Deploy a service"
        assert proposal.tags == ("deploy", "k8s")
        assert "Do the deploy" in proposal.proposed_body
        # 아직 디스크에 쓰이지 않았다 — 이건 제안일 뿐이다.
        assert self._skill_store.list() == []

    @pytest.mark.asyncio
    async def test_propose_skill_create_for_existing_name_is_rejected(self):
        """이미 존재하는 이름으로 "생성"을 제안하면(모델의 실수) 버린다 — patch를
        썼어야 한다. 잘못된 제안을 그대로 흘리지 않는다(fail-closed)."""
        self._skill_store.write(_make_skill("already-exists"))
        call = ToolCall(
            tool_name="propose_skill_create",
            arguments={"name": "already-exists", "description": "x", "body": "body"},
        )
        engine = self._engine(_FakeProvider([call]))
        effects = await engine.review("s1", [Message(role="user", content="hi")])
        assert effects == []

    @pytest.mark.asyncio
    async def test_propose_skill_patch_maps_to_patch_proposal(self):
        self._skill_store.write(_make_skill("existing-sk", body="## Steps\n1. Old step\n"))
        call = ToolCall(
            tool_name="propose_skill_patch",
            arguments={
                "skill_id": "existing-sk",
                "changelog": "사용자 교정 반영",
                "new_body": "## Steps\n1. New corrected step\n",
                "trigger_reason": "T1a",
            },
        )
        engine = self._engine(_FakeProvider([call]))
        effects = await engine.review("s1", [Message(role="user", content="hi")])
        assert len(effects) == 1
        proposal = effects[0]
        assert isinstance(proposal, PatchProposal)
        assert proposal.skill_id == "existing-sk"
        assert proposal.from_version == 1
        assert proposal.to_version == 2
        assert proposal.trigger == "T1a"
        assert "New corrected step" in proposal.proposed_body
        # 아직 적용 전 — 원본 스킬은 그대로.
        assert self._skill_store.read("existing-sk").version == 1

    @pytest.mark.asyncio
    async def test_propose_skill_patch_with_invalid_trigger_falls_back_to_t1a(self):
        self._skill_store.write(_make_skill("bad-trigger-sk"))
        call = ToolCall(
            tool_name="propose_skill_patch",
            arguments={
                "skill_id": "bad-trigger-sk",
                "changelog": "x",
                "new_body": "## New\n",
                "trigger_reason": "not-a-real-trigger",
            },
        )
        engine = self._engine(_FakeProvider([call]))
        effects = await engine.review("s1", [Message(role="user", content="hi")])
        assert effects[0].trigger == "T1a"

    @pytest.mark.asyncio
    async def test_propose_skill_patch_for_unknown_skill_is_rejected(self):
        call = ToolCall(
            tool_name="propose_skill_patch",
            arguments={
                "skill_id": "does-not-exist",
                "changelog": "x",
                "new_body": "body",
                "trigger_reason": "T1a",
            },
        )
        engine = self._engine(_FakeProvider([call]))
        effects = await engine.review("s1", [Message(role="user", content="hi")])
        assert effects == []

    @pytest.mark.asyncio
    async def test_propose_skill_patch_with_no_actual_change_is_dropped(self):
        """새 본문이 기존과 동일(diff 없음)하면 제안하지 않는다."""
        self._skill_store.write(_make_skill("no-change-sk", body="## Steps\n1. Same\n"))
        call = ToolCall(
            tool_name="propose_skill_patch",
            arguments={
                "skill_id": "no-change-sk",
                "changelog": "x",
                "new_body": "## Steps\n1. Same\n",
                "trigger_reason": "T1a",
            },
        )
        engine = self._engine(_FakeProvider([call]))
        effects = await engine.review("s1", [Message(role="user", content="hi")])
        assert effects == []

    @pytest.mark.asyncio
    async def test_propose_memory_write_applies_immediately(self):
        call = ToolCall(
            tool_name="propose_memory_write",
            arguments={"kind": "fact", "content": "Project uses pytest"},
        )
        engine = self._engine(_FakeProvider([call]))
        effects = await engine.review("s1", [Message(role="user", content="hi")])
        assert len(effects) == 1
        assert isinstance(effects[0], MemoryWritten)
        assert effects[0].memory_type == "fact"
        # 메모리는 즉시 반영된다(HITL 대상 아님) — propose_skill_*와 다른 점.
        mems = self._mem_store.list()
        assert len(mems) == 1
        assert mems[0].content == "Project uses pytest"

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_all_mapped(self):
        calls = [
            ToolCall(tool_name="propose_memory_write", arguments={"kind": "fact", "content": "Fact A"}),
            ToolCall(
                tool_name="propose_skill_create",
                arguments={"name": "combo-skill", "description": "d", "body": "body"},
            ),
        ]
        engine = self._engine(_FakeProvider(calls))
        effects = await engine.review("s1", [Message(role="user", content="hi")])
        assert len(effects) == 2
        assert any(isinstance(e, MemoryWritten) for e in effects)
        assert any(isinstance(e, PatchProposal) for e in effects)

    @pytest.mark.asyncio
    async def test_provider_failure_returns_empty_not_raises(self):
        """LLM 호출이 실패해도 review()는 예외를 던지지 않는다 — best-effort."""
        engine = self._engine(_RaisingProvider())
        effects = await engine.review("s1", [Message(role="user", content="hi")])
        assert effects == []

    @pytest.mark.asyncio
    async def test_learn_uses_directive_prompt_and_always_attempts(self):
        """learn()은 review()와 같은 매핑 로직을 쓰되 항상 실행된다(게이트 없음 —
        게이트는 LearningService.review()가 담당하지 learn()엔 애초에 없다)."""
        call = ToolCall(
            tool_name="propose_skill_create",
            arguments={"name": "learned-skill", "description": "d", "body": "body"},
        )
        engine = self._engine(_FakeProvider([call]))
        effects = await engine.learn("s1", [Message(role="user", content="hi")], "이 대화를 저장해줘")
        assert len(effects) == 1
        assert isinstance(effects[0], PatchProposal)

    @pytest.mark.asyncio
    async def test_patchable_skill_ids_limited_to_full_body_catalog(self):
        """카탈로그 예산을 넘는 스킬은 compact(name+desc)로만 들어가고,
        propose_skill_patch의 skill_id enum에도 포함되지 않는다 — 모델이 본문을
        못 본 스킬을 patch 대상으로 지정할 수 없게 하는 구조적 방지책."""
        big_body = "x" * 200_000  # 대략 50K 토큰 추정치 — 기본 예산(24K)을 넘긴다
        self._skill_store.write(_make_skill("huge-skill", body=big_body))
        self._skill_store.write(_make_skill("small-skill", body="tiny"))
        full_body, compact, patchable_ids = self._engine(_FakeProvider([]))._build_catalog()
        assert "small-skill" in patchable_ids
        assert "huge-skill" not in patchable_ids
        assert any(s[0] == "huge-skill" for s in compact)


# ── effect_surfacing 테스트 (Phase A.3, 추출 회귀 방지) ───────────────────────

class TestEffectSurfacing:
    def setup_method(self):
        self._paths = _fresh_paths()
        self._svc = LearningService(_fresh_db(), LearningConfig(), self._paths)
        self._store = SkillStore(self._paths)
        self._approvals = ApprovalRegistry()

    async def _collect(self, session_id, effect, timeout_s=5):
        events = []
        async for ev in surface_learning_effect(
            session_id=session_id, effect=effect, learning=self._svc,
            approvals=self._approvals, approval_timeout_s=timeout_s,
        ):
            events.append(ev)
        return events

    @pytest.mark.asyncio
    async def test_memory_written_is_immediate_notification_no_approval(self):
        events = await self._collect("s1", MemoryWritten(memory_type="fact", content="X uses Y"))
        assert len(events) == 1
        assert events[0].type == EventType.MEMORY_WRITTEN
        assert events[0].data == {"memory_type": "fact", "content": "X uses Y"}

    @pytest.mark.asyncio
    async def test_patch_proposal_approved_applies_and_emits_patched(self):
        self._store.write(_make_skill("target-sk", body="## Old\n"))
        proposal = PatchProposal(
            skill_id="target-sk", changelog="개선", diff={"lines": []}, trigger="T1a",
            proposed_body="## New Steps\n1. Better\n", from_version=1, to_version=2,
        )
        events = await self._approve_and_collect("s1", proposal)
        types = [e.type for e in events]
        assert EventType.SKILL_PATCH_PROPOSED in types
        assert EventType.SKILL_PATCHED in types
        # proposed_body가 SSE payload에도 실린다 (버그#0 견고성 개선).
        proposed_ev = next(e for e in events if e.type == EventType.SKILL_PATCH_PROPOSED)
        assert proposed_ev.data.get("proposed_body") == "## New Steps\n1. Better\n"
        # 실제로 본문이 바뀌었다 — 여기가 버그#0의 진짜 회귀 방지 지점.
        detail = self._svc.get_skill("target-sk")
        assert detail is not None
        assert "Better" in detail.body
        assert detail.version == 2

    @pytest.mark.asyncio
    async def test_patch_proposal_creation_from_v0_approved(self):
        proposal = PatchProposal(
            skill_id="brand-new", changelog="신규", diff={"lines": []}, trigger="T4",
            proposed_body="## New skill body\n", from_version=0, to_version=1,
            description="신규 스킬", tags=("a",),
        )
        events = await self._approve_and_collect("s2", proposal)
        assert EventType.SKILL_PATCHED in [e.type for e in events]
        detail = self._svc.get_skill("brand-new")
        assert detail is not None
        assert detail.version == 1
        assert detail.source == "session:s2"  # apply_patch가 session_id로 source를 구성

    @pytest.mark.asyncio
    async def test_patch_proposal_rejected_does_not_apply(self):
        self._store.write(_make_skill("reject-target", body="## Old\n"))
        proposal = PatchProposal(
            skill_id="reject-target", changelog="x", diff={"lines": []}, trigger="T1a",
            proposed_body="## New\n", from_version=1, to_version=2,
        )
        events = await self._reject_and_collect("s3", proposal)
        assert EventType.SKILL_PATCH_PROPOSED in [e.type for e in events]
        assert EventType.SKILL_PATCHED not in [e.type for e in events]
        detail = self._svc.get_skill("reject-target")
        assert detail is not None
        assert detail.version == 1
        assert "Old" in detail.body

    @pytest.mark.asyncio
    async def test_patch_proposal_timeout_defaults_to_reject(self):
        self._store.write(_make_skill("timeout-target"))
        proposal = PatchProposal(
            skill_id="timeout-target", changelog="x", diff={"lines": []}, trigger="T1a",
            proposed_body="## New\n", from_version=1, to_version=2,
        )
        events = await self._collect("s4", proposal, timeout_s=0)  # 즉시 타임아웃 → reject
        assert EventType.SKILL_PATCHED not in [e.type for e in events]
        assert self._svc.get_skill("timeout-target").version == 1

    # ── 승인/거부를 동시성으로 흉내내는 헬퍼 ──────────────────────────────────
    async def _approve_and_collect(self, session_id, proposal):
        return await self._resolve_and_collect(session_id, proposal, decision="approve")

    async def _reject_and_collect(self, session_id, proposal):
        return await self._resolve_and_collect(session_id, proposal, decision="reject")

    async def _resolve_and_collect(self, session_id, proposal, *, decision: str):
        events = []
        gen = surface_learning_effect(
            session_id=session_id, effect=proposal, learning=self._svc,
            approvals=self._approvals, approval_timeout_s=5,
        )
        first = await gen.__anext__()
        events.append(first)
        approval_id = first.data["approval_id"]

        async def _consume_rest():
            async for ev in gen:
                events.append(ev)

        task = asyncio.create_task(_consume_rest())
        for _ in range(1000):
            if self._approvals.get(approval_id) is not None:
                break
            await asyncio.sleep(0)
        self._approvals.resolve(approval_id, ApprovalResponse(approval_id=approval_id, decision=decision))
        await task
        return events


# ── LearningService.review()/learn() 파사드 테스트 ────────────────────────────

class TestLearningServiceReviewFacade:
    def setup_method(self):
        self._paths = _fresh_paths()

    def _svc_with_provider(self, provider, config: LearningConfig | None = None) -> LearningService:
        return LearningService(
            _fresh_db(), config or LearningConfig(), self._paths, resolve_provider=lambda: provider
        )

    @pytest.mark.asyncio
    async def test_no_resolve_provider_means_review_is_noop(self):
        """resolve_provider=None(대부분의 단위 테스트/구성)이면 review()는 조용히
        빈 리스트를 반환한다 — 게이트조차 평가하지 않는다."""
        svc = LearningService(_fresh_db(), LearningConfig(), self._paths)
        effects = await svc.review("s1", [Message(role="assistant", content="done")], tool_calls_made=100)
        assert effects == []

    @pytest.mark.asyncio
    async def test_review_below_gate_threshold_skips_engine_call(self):
        cfg = LearningConfig(review_min_tool_calls=100, review_nudge_turns=100)
        svc = self._svc_with_provider(_FakeProvider([]), cfg)
        history = [Message(role="assistant", content="네, 완료했습니다.")]
        effects = await svc.review("s1", history, tool_calls_made=0)
        assert effects == []

    @pytest.mark.asyncio
    async def test_review_above_gate_threshold_calls_engine(self):
        cfg = LearningConfig(review_min_tool_calls=1, review_nudge_turns=100)
        call = ToolCall(
            tool_name="propose_memory_write", arguments={"kind": "fact", "content": "Learned via review()"}
        )
        svc = self._svc_with_provider(_FakeProvider([call]), cfg)
        history = [Message(role="assistant", content="완료")]
        effects = await svc.review("s1", history, tool_calls_made=5)
        assert len(effects) == 1
        assert isinstance(effects[0], MemoryWritten)

    @pytest.mark.asyncio
    async def test_learn_always_runs_regardless_of_gate_config(self):
        """learn()은 게이트를 아예 거치지 않는다 — review_min_tool_calls가 아무리
        높아도 명시적 사용자 요청이면 항상 시도한다."""
        cfg = LearningConfig(review_min_tool_calls=9999, review_nudge_turns=9999)
        call = ToolCall(
            tool_name="propose_skill_create",
            arguments={"name": "explicit-learn-sk", "description": "d", "body": "body"},
        )
        svc = self._svc_with_provider(_FakeProvider([call]), cfg)
        history = [Message(role="user", content="hi"), Message(role="assistant", content="done")]
        effects = await svc.learn("s1", history, "")
        assert len(effects) == 1
        assert isinstance(effects[0], PatchProposal)


# ── guard.py 단위 테스트 (Phase D) ────────────────────────────────────────────

class TestGuard:
    def test_validate_content_size_within_limit_ok(self):
        skill_guard.validate_content_size("short body", max_chars=100)  # 예외 없음

    def test_validate_content_size_over_limit_raises(self):
        with pytest.raises(ValidationError):
            skill_guard.validate_content_size("x" * 101, max_chars=100)

    def test_scrub_secrets_no_match_returns_original_unchanged(self):
        body = "## Steps\n1. Run the deploy script\n"
        redacted, found = skill_guard.scrub_secrets(body)
        assert redacted == body
        assert found == []

    def test_scrub_secrets_redacts_aws_key(self):
        body = "export AWS_KEY=AKIAABCDEFGHIJKLMNOP"
        redacted, found = skill_guard.scrub_secrets(body)
        assert "AKIAABCDEFGHIJKLMNOP" not in redacted
        assert "AWS access key" in found

    def test_scrub_secrets_redacts_anthropic_key(self):
        body = "ANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnopqrstuvwxyz"
        redacted, found = skill_guard.scrub_secrets(body)
        assert "sk-ant-api03-abcdefghijklmnopqrstuvwxyz" not in redacted
        assert "Anthropic API key" in found

    def test_scrub_secrets_redacts_private_key_header(self):
        body = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...\n"
        redacted, found = skill_guard.scrub_secrets(body)
        assert "BEGIN RSA PRIVATE KEY" not in redacted
        assert "private key" in found

    def test_scrub_secrets_multiple_distinct_findings_deduped(self):
        body = "key1=AKIAABCDEFGHIJKLMNOP key2=AKIAZZZZZZZZZZZZZZZZ"
        _redacted, found = skill_guard.scrub_secrets(body)
        assert found == ["AWS access key"]  # 같은 라벨은 한 번만 보고된다

    def test_valid_skill_name_regex_matches_kebab_case(self):
        assert skill_guard.VALID_SKILL_NAME.match("deploy-k8s-service")
        assert not skill_guard.VALID_SKILL_NAME.match("Not_Kebab")


# ── guard가 실제 write 경로에 적용되는지 (Phase D 회귀) ───────────────────────

class TestGuardWiredIntoWritePaths:
    def setup_method(self):
        self._paths = _fresh_paths()
        self._svc = LearningService(_fresh_db(), LearningConfig(skill_max_body_chars=100), self._paths)

    def test_create_skill_rejects_oversize_body(self):
        """버그: create_skill/update_skill은 이전엔 크기/시크릿 검증이 전혀 없었다."""
        with pytest.raises(ValidationError):
            self._svc.create_skill(name="too-big-sk", body="x" * 101)

    def test_create_skill_redacts_secret_in_body(self):
        detail = self._svc.create_skill(
            name="leaky-sk", body="설정: AWS_KEY=AKIAABCDEFGHIJKLMNOP 를 넣으세요"
        )
        assert "AKIAABCDEFGHIJKLMNOP" not in detail.body
        assert "REDACTED" in detail.body

    def test_update_skill_rejects_oversize_body(self):
        self._svc.create_skill(name="update-target", body="short")
        with pytest.raises(ValidationError):
            self._svc.update_skill("update-target", body="x" * 101)

    def test_update_skill_redacts_secret_when_body_changes(self):
        self._svc.create_skill(name="update-leaky", body="short")
        detail = self._svc.update_skill("update-leaky", body="token: sk-ant-api03-abcdefghijklmnopqrstuvwxyz")
        assert "sk-ant-api03" not in detail.body
        assert "REDACTED" in detail.body

    def test_update_skill_unchanged_body_skips_guard_check(self):
        """body를 안 바꾸면(=None) 크기 검증도 안 탄다 — description/tags만 바꾸는
        흔한 경우가 오버사이즈 기존 스킬 때문에 막히지 않아야 한다."""
        self._svc.create_skill(name="meta-only", body="short")
        detail = self._svc.update_skill("meta-only", description="새 설명")
        assert detail.description == "새 설명"

    def test_apply_patch_rejects_oversize_proposed_body(self):
        self._svc.create_skill(name="patch-target", body="short")
        with pytest.raises(ValidationError):
            self._svc.apply_patch("patch-target", approved=True, proposed_body="x" * 101)

    def test_apply_patch_redacts_secret_in_new_skill_creation(self):
        new_v = self._svc.apply_patch(
            "brand-new-leaky",
            approved=True,
            proposed_body="token: sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
        )
        assert new_v == 1
        detail = self._svc.get_skill("brand-new-leaky")
        assert detail is not None
        assert "sk-ant-api03" not in detail.body

    def test_memory_store_redacts_secret_on_add(self):
        """MemoryStore.add()는 observe()/ReviewEngine 양쪽이 직접 호출하므로
        저장소 경계 자체에서 스크러빙된다(호출부가 잊을 수 없게)."""
        mem_store = MemoryStore(_fresh_db())
        item = mem_store.add("fact", "API key is AKIAABCDEFGHIJKLMNOP for this project")
        assert "AKIAABCDEFGHIJKLMNOP" not in item.content
        assert "REDACTED" in item.content

    @pytest.mark.asyncio
    async def test_review_engine_proposal_carries_warnings_when_secret_found(self):
        skill_store = SkillStore(self._paths)
        usage_store = UsageStore(self._paths)
        mem_store = MemoryStore(_fresh_db())
        call = ToolCall(
            tool_name="propose_skill_create",
            arguments={
                "name": "leaky-proposal",
                "description": "d",
                "body": "credential: AKIAABCDEFGHIJKLMNOP",
            },
        )
        engine = ReviewEngine(
            resolve_provider=lambda: _FakeProvider([call]),
            skill_store=skill_store,
            usage_store=usage_store,
            mem_store=mem_store,
            skill_catalog_token_budget=24_000,
        )
        effects = await engine.review("s1", [Message(role="user", content="hi")])
        assert len(effects) == 1
        proposal = effects[0]
        assert isinstance(proposal, PatchProposal)
        assert "AKIAABCDEFGHIJKLMNOP" not in proposal.proposed_body
        assert len(proposal.warnings) == 1
        assert "AWS access key" in proposal.warnings[0]
