"""learning loop 고도화(2026-07-18) 회귀 테스트 — 적대 리뷰가 지적한
"테스트에 표현되지 않은 시스템 불변조건"들을 명시적으로 고정한다.

  1. memory secret 경계: scrub된 content만 ledger/SSE(MemoryWritten)에 실린다.
  2. quarantine 격리: 격리 메모리는 리뷰 프롬프트의 known-facts로 재공급되지 않는다.
  3. metadata 경계: description/tags/changelog도 scrub + risk scan 대상이다.
  4. evidence watermark: 리뷰 잡이 관찰 구간·관여 스킬 버전을 영속화하고 병합한다.
  5. retryable 리뷰 실패: provider 장애는 done이 아니라 pending 재시도로 남는다.
  6. 리뷰 컨텍스트 예산: CJK 텍스트도 estimator 기준으로 실제 상한이 지켜진다.
  7. signal 오탐: 부정문("오류 없이", "no tests failed")과 첨가("뿐만 아니라")는
     신호가 아니다.
  8. friction 버전 스코핑: patch 승인 시 friction이 리셋된다.
"""

from __future__ import annotations

import json
import tempfile
from typing import AsyncIterator, Sequence

import pytest

from rice_runtime.agent.providers.interfaces import ProviderCapabilities, ProviderChunk
from rice_runtime.core.config import LearningConfig
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.core.tokens import estimate_tokens
from rice_runtime.core.types import Message, ToolCall, ToolSpec, TurnOutcome
from rice_runtime.learning.interfaces import MemoryWritten
from rice_runtime.learning.ledger import LearningLedger
from rice_runtime.learning.memory.store import MemoryStore
from rice_runtime.learning.proposals import make_create_proposal, make_patch_proposal
from rice_runtime.learning.review.context import LearningContextManager, ReviewContextConfig
from rice_runtime.learning.review.engine import ReviewEngine, ReviewLLMError
from rice_runtime.learning.service import LearningService
from rice_runtime.learning.signals import has_correction_signal, has_failure_signal
from rice_runtime.learning.skill.store import SkillData, SkillStore
from rice_runtime.learning.skill.usage import UsageStore

_FAKE_SECRET = "sk-" + "a1b2c3d4e5" * 3  # guard의 "API key (sk-*)" 패턴에 걸리는 형태


def _fresh_paths() -> RicePaths:
    return RicePaths.from_env(tempfile.mkdtemp(prefix="rice-hard-v4-")).ensure()


def _fresh_db() -> Database:
    return Database(":memory:").connect()


def _make_skill(name: str, body: str = "## Steps\n1. Step\n", version: int = 1) -> SkillData:
    return SkillData(
        name=name, version=version, description="A test skill", state="active",
        body=body, tags=["test"], source="session:sess_abc",
        created_at="2024-01-01T00:00:00Z", updated_at="2024-01-01T00:00:00Z",
    )


class _FakeProvider:
    name = "fake"

    def __init__(self, tool_calls: Sequence[ToolCall] = ()) -> None:
        self._tool_calls = list(tool_calls)
        self.seen_messages: list[Message] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    async def stream(
        self,
        *,
        system: str,
        messages,
        tools: Sequence[ToolSpec] = (),
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        self.seen_messages = list(messages)
        for call in self._tool_calls:
            yield ProviderChunk(kind="tool_call", tool_call=call)
        yield ProviderChunk(kind="done", stop_reason="tool_use" if self._tool_calls else "end_turn")


class _RaisingProvider:
    name = "fake-broken"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    async def stream(self, **kwargs) -> AsyncIterator[ProviderChunk]:
        raise RuntimeError("prompt is too long")
        yield  # pragma: no cover


class _CapturingReviewEngine:
    def __init__(self) -> None:
        self.calls = 0
        self.histories: list[list[Message]] = []

    async def review(self, session_id, history, *, priority_skill_ids=()):
        self.calls += 1
        self.histories.append(list(history))
        return []


class _FailOnceReviewEngine(_CapturingReviewEngine):
    async def review(self, session_id, history, *, priority_skill_ids=()):
        self.calls += 1
        self.histories.append(list(history))
        if self.calls == 1:
            raise ReviewLLMError("transient provider failure")
        return []


class _AlwaysFailReviewEngine(_CapturingReviewEngine):
    async def review(self, session_id, history, *, priority_skill_ids=()):
        self.calls += 1
        self.histories.append(list(history))
        raise RuntimeError("deterministic review failure")


# ── 1. memory secret 경계 ────────────────────────────────────────────────────

class TestMemorySecretBoundary:
    def setup_method(self):
        self._paths = _fresh_paths()
        self._db = _fresh_db()
        self._ledger = LearningLedger(self._db)

    def _engine(self, provider) -> ReviewEngine:
        return ReviewEngine(
            resolve_provider=lambda: provider,
            skill_store=SkillStore(self._paths),
            usage_store=UsageStore(self._paths),
            mem_store=MemoryStore(self._db),
            skill_catalog_token_budget=24_000,
            ledger=self._ledger,
        )

    @pytest.mark.asyncio
    async def test_memory_written_effect_and_ledger_carry_scrubbed_content(self):
        """P0: memories 테이블에는 [REDACTED]가 저장되는데 ledger detail과
        SSE(MemoryWritten.content)로는 원문 시크릿이 새던 구멍의 회귀 방어."""
        call = ToolCall(
            tool_name="propose_memory_write",
            arguments={"kind": "fact", "content": f"프로젝트 API 키는 {_FAKE_SECRET} 이다"},
        )
        effects = await self._engine(_FakeProvider([call])).review(
            "s1", [Message(role="user", content="hi")]
        )
        assert len(effects) == 1
        effect = effects[0]
        assert isinstance(effect, MemoryWritten)
        assert _FAKE_SECRET not in effect.content
        assert "[REDACTED" in effect.content
        # ledger 감사 기록에도 원문이 없어야 한다.
        events = self._ledger.events()
        written = [e for e in events if e["kind"] == "memory_written"]
        assert written, "memory_written 이벤트가 기록돼야 한다"
        assert _FAKE_SECRET not in json.dumps(written, ensure_ascii=False)


# ── 2. quarantine 격리 ───────────────────────────────────────────────────────

class TestQuarantineIsolation:
    @pytest.mark.asyncio
    async def test_quarantined_memory_not_fed_back_as_known_fact(self):
        paths = _fresh_paths()
        db = _fresh_db()
        mem = MemoryStore(db)
        mem.add("fact", "확인된 사실입니다")  # active
        mem.add("fact", "외부 도구가 주장한 미검증 사실", derived_from_external=True)  # quarantined
        provider = _FakeProvider([])
        engine = ReviewEngine(
            resolve_provider=lambda: provider,
            skill_store=SkillStore(paths),
            usage_store=UsageStore(paths),
            mem_store=mem,
            skill_catalog_token_budget=24_000,
        )
        await engine.review("s1", [Message(role="user", content="hi")])
        context_text = provider.seen_messages[-1].content
        assert "확인된 사실입니다" in context_text
        assert "미검증 사실" not in context_text


# ── 3. metadata 경계 ─────────────────────────────────────────────────────────

class TestMetadataScrubBoundary:
    def test_create_proposal_scrubs_description_tags_changelog(self):
        p = make_create_proposal(
            name="meta-skill",
            body="## Steps\n1. clean body\n",
            description=f"토큰 {_FAKE_SECRET} 사용",
            tags=(f"key-{_FAKE_SECRET}", "ok-tag"),
            changelog=f"secret {_FAKE_SECRET} added",
        )
        assert p is not None
        assert _FAKE_SECRET not in p.description
        assert all(_FAKE_SECRET not in t for t in p.tags)
        assert _FAKE_SECRET not in p.changelog
        assert any(w.startswith("masked:") for w in p.warnings)

    def test_patch_proposal_scrubs_new_description(self):
        current = _make_skill("meta-skill-2")
        p = make_patch_proposal(
            current=current,
            new_body=current.body + "\n2. New step\n",
            changelog="normal",
            trigger="T1a",
            new_description=f"새 설명 {_FAKE_SECRET}",
        )
        assert p is not None
        assert p.new_description is not None
        assert _FAKE_SECRET not in p.new_description

    def test_risk_scan_covers_metadata(self):
        """hidden_characters(zero-width) 지시가 description으로 우회하는 경로 탐지."""
        p = make_create_proposal(
            name="meta-skill-3",
            body="## Steps\n1. clean\n",
            description="정상 설명​숨은 지시",
        )
        assert p is not None
        assert any(w == "risk_flag:hidden_characters" for w in p.warnings)


# ── 4. evidence watermark ────────────────────────────────────────────────────

class TestEvidenceWatermark:
    def test_ledger_merge_preserves_from_and_unions_versions(self):
        ledger = LearningLedger(_fresh_db())
        ledger.enqueue_review_job(
            "s1", 3,
            {
                "from_message_id": "m1", "through_message_id": "m2",
                "injected_skills": {"deploy": [3]}, "viewed_skills": {},
                "engaged_skills": ["deploy"],
                "engaged_skill_versions": {"deploy": [3]},
                "friction_by_skill_version": {"deploy": {"3": 1}},
                "friction_turns": 1, "turns": 1,
            },
        )
        ledger.enqueue_review_job(
            "s1", 1,
            {
                "from_message_id": "m3", "through_message_id": "m4",
                "injected_skills": {"deploy": [4], "lint": [1]}, "viewed_skills": {},
                "engaged_skills": ["deploy", "lint"],
                "engaged_skill_versions": {"deploy": [4], "lint": [1]},
                "friction_by_skill_version": {"deploy": {"4": 1}},
                "friction_turns": 1, "turns": 1,
            },
        )
        job = ledger.claim_review_job("s1")
        assert job is not None
        ev = job["evidence"]
        assert ev["from_message_id"] == "m1"  # 가장 오래된 미리뷰 지점 보존
        assert ev["through_message_id"] == "m4"  # 최신 관찰로 갱신
        assert ev["injected_skills"]["deploy"] == [3, 4]  # 버전 합집합 — v3·v4 모두 관여
        assert ev["engaged_skills"] == ["deploy", "lint"]
        assert ev["engaged_skill_versions"] == {"deploy": [3, 4], "lint": [1]}
        assert ev["friction_by_skill_version"] == {"deploy": {"3": 1, "4": 1}}
        assert ev["friction_turns"] == 2
        assert ev["turns"] == 2
        # tool_calls_made는 MAX 보존(기존 불변식 유지)
        assert job["tool_calls_made"] == 3

    @pytest.mark.asyncio
    async def test_observe_persists_turn_evidence(self):
        paths = _fresh_paths()
        db = _fresh_db()
        # The live file can move while a turn is running; evidence must retain
        # the exact prefetch snapshot carried by TurnOutcome.
        SkillStore(paths).write(_make_skill("evi-skill", version=4))
        svc = LearningService(db, LearningConfig(), paths)
        history = (
            Message(role="user", content="배포해줘"),
            Message(role="assistant", content="완료했습니다"),
        )
        await svc.observe(
            TurnOutcome(
                session_id="s-evi", final_message="완료했습니다", history=history,
                tool_calls_made=5, injected_skill_ids=("evi-skill",),
                injected_skill_versions=(("evi-skill", 3),),
            )
        )
        row = db.query_one("SELECT evidence FROM learning_jobs WHERE session_id = 's-evi'")
        assert row is not None and row["evidence"]
        ev = json.loads(row["evidence"])
        assert ev["injected_skills"] == {"evi-skill": [3]}  # 주입 시점의 버전이 고정된다
        assert ev["from_message_id"] == history[0].id
        assert ev["through_message_id"] == history[-1].id

    def test_engaged_versions_union_injected_and_midturn_view_snapshots(self):
        svc = LearningService(_fresh_db(), LearningConfig(), _fresh_paths())
        history = [Message(role="user", content="run it"), Message(role="assistant", content="done")]
        evidence = svc._turn_evidence(
            history,
            injected_versions={"moving-skill": 3},
            viewed_versions={"moving-skill": 4},
            engaged=("moving-skill",),
            friction=True,
        )
        assert evidence["engaged_skill_versions"] == {"moving-skill": [3, 4]}
        assert evidence["friction_by_skill_version"] == {
            "moving-skill": {"3": 1, "4": 1}
        }

    def test_evidence_priority_orders_engaged_first(self):
        priority = LearningService._evidence_priority(
            {
                "engaged_skills": ["b"],
                "viewed_skills": {"c": [1]},
                "injected_skills": {"a": [1], "b": [2]},
            }
        )
        assert priority == ("b", "c", "a")

    def test_catalog_prioritizes_engaged_over_high_use(self):
        """예산이 하나만 허용할 때, 누적 use가 높은 무관 스킬이 아니라 이번 구간에
        관여한 스킬이 전문(full-body)을 차지한다 — 인과 보존의 핵심."""
        paths = _fresh_paths()
        store = SkillStore(paths)
        usage = UsageStore(paths)
        store.write(_make_skill("popular", body="x" * 400))
        store.write(_make_skill("engaged-now", body="y" * 400))
        for _ in range(10):
            usage.increment_use("popular")
        engine = ReviewEngine(
            resolve_provider=lambda: _FakeProvider([]),
            skill_store=store,
            usage_store=usage,
            mem_store=MemoryStore(_fresh_db()),
            skill_catalog_token_budget=150,  # 400자 본문 하나(≈100토큰)만 들어가는 예산
        )
        full_body, compact, patchable = engine._build_catalog(("engaged-now",))
        assert [s[0] for s in full_body] == ["engaged-now"]
        assert any(s[0] == "popular" for s in compact)
        assert patchable == ("engaged-now",)

    def test_retry_transitions_are_running_only_and_claim_is_oldest_first(self):
        db = _fresh_db()
        ledger = LearningLedger(db)
        first_id = ledger.enqueue_review_job("s-order", 1)
        first = ledger.claim_review_job("s-order")
        assert first is not None and first["id"] == first_id

        # A successor may arrive while the first provider call is running.
        second_id = ledger.enqueue_review_job("s-order", 1)
        assert second_id is not None and second_id != first_id
        assert ledger.retry_review_job(first_id, max_attempts=3) is True

        retried = ledger.claim_review_job("s-order")
        assert retried is not None
        assert retried["id"] == first_id  # retry predecessor before successor
        assert retried["attempts"] == 1
        ledger.finish_review_job(first_id, "done")

        # A stale caller cannot revive or rewrite a terminal job.
        assert ledger.retry_review_job(first_id, max_attempts=3) is False
        terminal = db.query_one(
            "SELECT status, attempts FROM learning_jobs WHERE id = ?", (first_id,)
        )
        assert terminal["status"] == "done"
        assert terminal["attempts"] == 1

        # finish() is likewise a running-only transition.
        ledger.finish_review_job(second_id, "done")
        pending = db.query_one("SELECT status FROM learning_jobs WHERE id = ?", (second_id,))
        assert pending["status"] == "pending"

    @pytest.mark.asyncio
    async def test_review_gate_and_engine_use_the_same_evidence_slice(self):
        db = _fresh_db()
        svc = LearningService(
            db,
            LearningConfig(review_min_tool_calls=999, review_nudge_turns=999),
            _fresh_paths(),
            resolve_provider=lambda: _FakeProvider(),
        )
        capture = _CapturingReviewEngine()
        svc._review_engine = capture
        history = [
            Message(role="user", content="old request"),
            Message(role="assistant", content="old answer"),
            Message(role="user", content="배포가 실패했어요"),
            Message(role="assistant", content="오류가 발생했습니다"),
            Message(role="user", content="new unrelated request"),
            Message(role="assistant", content="all good"),
        ]
        svc._ledger.enqueue_review_job(
            "s-bound",
            0,
            {
                "from_message_id": history[2].id,
                "through_message_id": history[3].id,
                "turns": 1,
            },
        )

        assert await svc.review("s-bound", history) == []
        assert capture.calls == 1  # gate saw the bounded failure signal
        assert [m.id for m in capture.histories[0]] == [history[2].id, history[3].id]

    @pytest.mark.asyncio
    async def test_invalid_evidence_boundaries_fail_without_calling_engine(self):
        db = _fresh_db()
        svc = LearningService(
            db,
            LearningConfig(review_min_tool_calls=0),
            _fresh_paths(),
            resolve_provider=lambda: _FakeProvider(),
        )
        capture = _CapturingReviewEngine()
        svc._review_engine = capture
        history = [Message(role="user", content="hi"), Message(role="assistant", content="ok")]
        cases = {
            "s-missing": (history[0].id, "missing-message", "evidence_boundary_missing"),
            "s-reversed": (history[1].id, history[0].id, "evidence_boundary_invalid"),
        }
        for session_id, (from_id, through_id, expected_error) in cases.items():
            svc._ledger.enqueue_review_job(
                session_id,
                1,
                {"from_message_id": from_id, "through_message_id": through_id, "turns": 1},
            )
            assert await svc.review(session_id, history) == []
            row = db.query_one(
                "SELECT status, detail FROM learning_jobs WHERE session_id = ?", (session_id,)
            )
            assert row["status"] == "failed"
            assert json.loads(row["detail"])["error"] == expected_error
        assert capture.calls == 0

    @pytest.mark.asyncio
    async def test_corrupt_evidence_json_is_claimed_and_failed_not_stranded_pending(self):
        db = _fresh_db()
        svc = LearningService(
            db,
            LearningConfig(review_min_tool_calls=0),
            _fresh_paths(),
            resolve_provider=lambda: _FakeProvider(),
        )
        capture = _CapturingReviewEngine()
        svc._review_engine = capture
        job_id = svc._ledger.enqueue_review_job("s-corrupt", 1, {"turns": 1})
        db.execute("UPDATE learning_jobs SET evidence = '{' WHERE id = ?", (job_id,))

        assert await svc.review("s-corrupt", [Message(role="user", content="hi")]) == []
        row = db.query_one("SELECT status, detail FROM learning_jobs WHERE id = ?", (job_id,))
        assert row["status"] == "failed"
        assert json.loads(row["detail"])["error"] == "evidence_decode_failed"
        assert capture.calls == 0

    @pytest.mark.asyncio
    async def test_merged_turn_count_advances_nudge_gate(self):
        db = _fresh_db()
        svc = LearningService(
            db,
            LearningConfig(review_min_tool_calls=999, review_nudge_turns=2),
            _fresh_paths(),
            resolve_provider=lambda: _FakeProvider(),
        )
        capture = _CapturingReviewEngine()
        svc._review_engine = capture
        history = [Message(role="user", content="ordinary"), Message(role="assistant", content="ok")]
        svc._ledger.enqueue_review_job(
            "s-turns",
            0,
            {
                "from_message_id": history[0].id,
                "through_message_id": history[1].id,
                "turns": 2,
            },
        )

        assert await svc.review("s-turns", history) == []
        assert capture.calls == 1


# ── 5. retryable 리뷰 실패 ───────────────────────────────────────────────────

class TestReviewRetry:
    @pytest.mark.asyncio
    async def test_provider_failure_requeues_then_fails_at_cap(self):
        paths = _fresh_paths()
        db = _fresh_db()
        svc = LearningService(
            db, LearningConfig(review_job_max_attempts=2), paths,
            resolve_provider=lambda: _RaisingProvider(),
        )
        history = [Message(role="user", content="배포해줘"), Message(role="assistant", content="done")]
        await svc.observe(
            TurnOutcome(
                session_id="s-retry", final_message="done", history=tuple(history),
                tool_calls_made=5, injected_skill_ids=(),
            )
        )
        # 1차 시도: provider 실패 → 잡은 done이 아니라 pending으로 되돌아간다.
        assert await svc.review("s-retry", history) == []
        row = db.query_one("SELECT status, attempts FROM learning_jobs WHERE session_id = 's-retry'")
        assert row["status"] == "pending"
        assert row["attempts"] == 1
        # 2차 시도: 상한(2) 도달 → failed로 마감(무한 재시도 방지, 감사 가능).
        assert await svc.review("s-retry", history) == []
        row = db.query_one("SELECT status, attempts FROM learning_jobs WHERE session_id = 's-retry'")
        assert row["status"] == "failed"
        assert row["attempts"] == 2

    @pytest.mark.asyncio
    async def test_nudge_triggered_failure_retries_without_rechecking_gate(self):
        db = _fresh_db()
        svc = LearningService(
            db,
            LearningConfig(
                review_min_tool_calls=999,
                review_nudge_turns=2,
                review_job_max_attempts=3,
            ),
            _fresh_paths(),
            resolve_provider=lambda: _FakeProvider(),
        )
        engine = _FailOnceReviewEngine()
        svc._review_engine = engine
        history = [Message(role="user", content="ordinary"), Message(role="assistant", content="ok")]
        evidence = {
            "from_message_id": history[0].id,
            "through_message_id": history[1].id,
            "turns": 1,
        }

        # First ordinary job advances the nudge counter to one and is skipped.
        svc._ledger.enqueue_review_job("s-nudge-retry", 0, evidence)
        assert await svc.review("s-nudge-retry", history) == []
        assert engine.calls == 0

        # Second reaches the nudge, then the provider fails and the job requeues.
        svc._ledger.enqueue_review_job("s-nudge-retry", 0, evidence)
        assert await svc.review("s-nudge-retry", history) == []
        pending = db.query_one(
            "SELECT status, attempts FROM learning_jobs "
            "WHERE session_id = 's-nudge-retry' ORDER BY rowid DESC LIMIT 1"
        )
        assert pending["status"] == "pending"
        assert pending["attempts"] == 1

        # Retry is already admitted, so it reaches the engine even though the
        # successful-review reset has not happened yet.
        assert await svc.review("s-nudge-retry", history) == []
        terminal = db.query_one(
            "SELECT status, attempts FROM learning_jobs "
            "WHERE session_id = 's-nudge-retry' ORDER BY rowid DESC LIMIT 1"
        )
        assert terminal["status"] == "done"
        assert terminal["attempts"] == 1
        assert engine.calls == 2

    @pytest.mark.asyncio
    async def test_sweep_generic_failure_finishes_job_instead_of_stranding_running(self):
        db = _fresh_db()
        svc = LearningService(
            db,
            LearningConfig(review_min_tool_calls=0),
            _fresh_paths(),
            resolve_provider=lambda: _FakeProvider(),
        )
        engine = _AlwaysFailReviewEngine()
        svc._review_engine = engine
        history = [Message(role="user", content="ordinary"), Message(role="assistant", content="ok")]
        job_id = svc._ledger.enqueue_review_job(
            "s-sweep-fail",
            1,
            {
                "from_message_id": history[0].id,
                "through_message_id": history[1].id,
                "turns": 1,
            },
        )
        db.execute(
            "UPDATE learning_jobs SET created_at = '2000-01-01T00:00:00Z' WHERE id = ?",
            (job_id,),
        )

        assert await svc.sweep_review_jobs(
            lambda _session_id: history, older_than_seconds=1
        ) == 0
        row = db.query_one("SELECT status, detail FROM learning_jobs WHERE id = ?", (job_id,))
        assert row["status"] == "failed"
        assert json.loads(row["detail"])["error"] == "engine"
        assert engine.calls == 1


# ── 6. 리뷰 컨텍스트 예산 (CJK) ──────────────────────────────────────────────

class TestReviewContextCjkBudget:
    def test_korean_history_respects_estimator_budget(self):
        """이전 구현은 1토큰=4자 고정 환산이라 한국어에서 24K 예산이 ~96K 토큰이
        됐다(리뷰 호출이 provider 한도에서 터지는 원인). 이제 estimator 기준으로
        실제 상한이 지켜져야 한다."""
        cfg = ReviewContextConfig(
            recent_messages=4, max_total_tokens=1_000,
            tool_output_max_tokens=200, message_max_tokens=400, older_summary_tokens=200,
        )
        manager = LearningContextManager(cfg)
        korean_block = "한국어 문장입니다 예산 검증용 텍스트 " * 500  # ≈10K자
        history = [
            Message(role="user" if i % 2 == 0 else "assistant", content=korean_block)
            for i in range(10)
        ]
        rendered = manager.render(history)
        total = sum(estimate_tokens(m.content) for m in rendered)
        assert total <= cfg.max_total_tokens, f"rendered {total} tokens > budget {cfg.max_total_tokens}"


# ── 7. signal 오탐 ───────────────────────────────────────────────────────────

class TestSignalFalsePositives:
    def test_korean_negated_failure_is_not_a_signal(self):
        assert has_failure_signal("오류 없이 정상 완료됐습니다") is False
        assert has_failure_signal("실패 없이 전부 통과했습니다") is False
        assert has_failure_signal("에러가 없었어요") is False

    def test_korean_real_failure_still_detected(self):
        assert has_failure_signal("오류가 발생했습니다") is True
        assert has_failure_signal("배포가 실패했어요") is True

    def test_additive_phrase_is_not_a_correction(self):
        assert has_correction_signal("A뿐만 아니라 B도 지원해줘") is False
        assert has_correction_signal("성능뿐 아니라 안정성도 중요해") is False

    def test_real_correction_still_detected(self):
        assert has_correction_signal("그게 아니라 이렇게 해야지") is True
        assert has_correction_signal("JSON이 아니라 YAML로 해줘") is True

    def test_english_negated_failure_is_not_a_signal(self):
        assert has_failure_signal("all good — no tests failed") is False
        assert has_failure_signal("finished without errors") is False
        assert has_failure_signal("0 failed, 12 passed") is False

    def test_english_mixed_sentence_keeps_real_failure(self):
        assert has_failure_signal("no error occurred in lint, but the deploy failed") is True


# ── 8. friction 버전 스코핑 ──────────────────────────────────────────────────

class TestFrictionVersionScoping:
    def test_patch_resets_friction(self):
        usage = UsageStore(_fresh_paths())
        for _ in range(3):
            usage.increment_friction("scoped-skill")
        assert usage.read("scoped-skill").friction == 3
        usage.increment_patch("scoped-skill")
        after = usage.read("scoped-skill")
        assert after.patch == 1
        assert after.friction == 0, "패치 전 버전의 friction이 새 버전 통계에 남으면 안 된다"
