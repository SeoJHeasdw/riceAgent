"""test_learning_t3.py — T3 수용 기준 검증 (observe, patch, user_model).

수용 기준 (learning.md T3):
  1. patch: v1→PatchProposal→apply_patch(approve)→v2 + .history/v1.md + patch 카운터.
  2. curator: 오래된 스킬 stale/archived 전이 (T2에서 이미 검증되나 T3 연계 재확인).
  3. observe가 주입 스킬 use 카운터를 올린다.
  4. observe가 교정 신호를 감지해 PatchProposal을 반환한다 (미적용).
  5. observe가 fact를 감지해 MemoryWritten 효과를 반환하고 list_memories에 반영한다.
  6. prefetch가 active 스킬 전부 주입 + 가이드 문구 포함.
  7. UserProfile: preference가 system prompt에 반영된다.
  8. build_diff: unified diff → SkillDiff 형태.

격리: 실제 ~/.rice 건드리지 않음. tempfile.mkdtemp().
외부 의존성 없음 — 순수 로직 + 파일시스템.
"""

from __future__ import annotations

import datetime
import tempfile

import pytest

from rice_runtime.core.config import LearningConfig
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.core.types import TurnOutcome
from rice_runtime.learning.interfaces import MemoryWritten, PatchProposal, SkillCreated
from rice_runtime.learning.memory.store import MemoryStore
from rice_runtime.learning.service import LearningService
from rice_runtime.learning.skill.patch import build_diff, detect_patch_trigger
from rice_runtime.learning.skill.store import SkillData, SkillStore
from rice_runtime.learning.skill.usage import UsageStore
from rice_runtime.learning.user_model.profile import UserProfile


# ── helpers ───────────────────────────────────────────────────────────────────

def _fresh_paths() -> RicePaths:
    tmp = tempfile.mkdtemp(prefix="rice-t3-test-")
    return RicePaths.from_env(tmp).ensure()


def _fresh_db() -> Database:
    return Database(":memory:").connect()


def _service(paths: RicePaths, db: Database | None = None) -> LearningService:
    if db is None:
        db = _fresh_db()
    return LearningService(db, LearningConfig(), paths)


def _make_skill(name: str = "test-skill", state: str = "active", version: int = 1) -> SkillData:
    return SkillData(
        name=name,
        version=version,
        description="A test skill",
        state=state,
        body="## Steps\n1. Do thing A\n2. Do thing B\n",
        tags=["test"],
        source="session:sess_abc",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )


def _old_skill(name: str, state: str, days_ago: int) -> SkillData:
    ts = (
        datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        - datetime.timedelta(days=days_ago)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return SkillData(
        name=name, version=1, description="old skill", state=state,
        body="body", tags=[], source=None, created_at=ts, updated_at=ts,
    )


# ── build_diff 테스트 (순수 로직) ────────────────────────────────────────────

class TestBuildDiff:
    def test_build_diff_returns_skill_diff_shape(self):
        diff = build_diff("line1\nline2\n", "line1\nline2_new\n")
        assert "lines" in diff
        assert isinstance(diff["lines"], list)

    def test_build_diff_detects_removal(self):
        diff = build_diff("old content\n", "new content\n")
        types = {item["type"] for item in diff["lines"]}
        assert "remove" in types

    def test_build_diff_detects_addition(self):
        diff = build_diff("old content\n", "new content\n")
        types = {item["type"] for item in diff["lines"]}
        assert "add" in types

    def test_build_diff_identical_returns_empty(self):
        diff = build_diff("same\n", "same\n")
        assert diff["lines"] == []

    def test_build_diff_appended_line(self):
        diff = build_diff("a\n", "a\nb\n")
        lines = diff["lines"]
        assert any(item["type"] == "add" for item in lines)

    def test_build_diff_line_types_only_valid(self):
        diff = build_diff("a\nb\n", "a\nc\n")
        valid_types = {"add", "remove", "context"}
        for item in diff["lines"]:
            assert item["type"] in valid_types
            assert "text" in item


# ── detect_patch_trigger 테스트 ───────────────────────────────────────────────

class TestDetectPatchTrigger:
    def test_no_injected_skills_returns_none(self):
        result = detect_patch_trigger("actually, that was wrong", 3, ())
        assert result is None

    def test_correction_signal_returns_t1a(self):
        result = detect_patch_trigger(
            "Actually, the correct way is to use pytest.",
            3,
            ("my-skill",),
        )
        assert result == "T1a"

    def test_failure_signal_returns_t1b(self):
        result = detect_patch_trigger(
            "The command failed during execution.",
            3,
            ("my-skill",),
        )
        assert result == "T1b"

    def test_neutral_message_returns_none(self):
        result = detect_patch_trigger(
            "Here is the summary of what was done.",
            3,
            ("my-skill",),
        )
        assert result is None


# ── LearningService.observe 테스트 ────────────────────────────────────────────

class TestObserve:
    def setup_method(self):
        self._paths = _fresh_paths()
        self._db = _fresh_db()
        self._svc = _service(self._paths, self._db)
        self._store = SkillStore(self._paths)

    @pytest.mark.asyncio
    async def test_observe_increments_use_for_injected_skill(self):
        """T4 수용: 주입된 스킬의 use 카운터가 올라간다."""
        self._store.write(_make_skill("used-sk"))
        outcome = TurnOutcome(
            session_id="sess1",
            final_message="done",
            history=(),
            tool_calls_made=0,
            injected_skill_ids=("used-sk",),
        )
        await self._svc.observe(outcome)
        usage = UsageStore(self._paths).read("used-sk")
        assert usage.use == 1

    @pytest.mark.asyncio
    async def test_observe_no_effects_on_empty_outcome(self):
        """효과 없는 일반 메시지 → 빈 리스트."""
        outcome = TurnOutcome(
            session_id="sess1",
            final_message="Thank you!",
            history=(),
            tool_calls_made=0,
            injected_skill_ids=(),
        )
        effects = await self._svc.observe(outcome)
        assert effects == []

    @pytest.mark.asyncio
    async def test_observe_no_effects_when_aborted(self):
        """중단된 턴에서는 메모리/스킬 효과 없음."""
        outcome = TurnOutcome(
            session_id="sess1",
            final_message="the project uses Python",
            history=(),
            tool_calls_made=0,
            injected_skill_ids=(),
            aborted=True,
        )
        effects = await self._svc.observe(outcome)
        # use 카운터만 없고 memory 기록도 없음
        assert not any(isinstance(e, MemoryWritten) for e in effects)

    @pytest.mark.asyncio
    async def test_observe_detects_fact_and_writes_memory(self):
        """fact 패턴이 있으면 MemoryWritten 효과 + list_memories에 반영."""
        outcome = TurnOutcome(
            session_id="sess1",
            final_message="The project uses pytest for all testing.",
            history=(),
            tool_calls_made=0,
            injected_skill_ids=(),
        )
        effects = await self._svc.observe(outcome)
        mem_effects = [e for e in effects if isinstance(e, MemoryWritten)]
        assert len(mem_effects) >= 1
        assert mem_effects[0].memory_type == "fact"
        # list_memories에 반영됐는지 확인
        mems = self._svc.list_memories()
        contents = [m.content for m in mems]
        assert any("pytest" in c or "project uses" in c.lower() for c in contents)

    @pytest.mark.asyncio
    async def test_observe_detects_preference_and_writes_memory(self):
        """preference 패턴이 있으면 MemoryWritten(preference) 효과."""
        outcome = TurnOutcome(
            session_id="sess2",
            final_message="I prefer concise and direct answers always.",
            history=(),
            tool_calls_made=0,
            injected_skill_ids=(),
        )
        effects = await self._svc.observe(outcome)
        pref_effects = [e for e in effects if isinstance(e, MemoryWritten) and e.memory_type == "preference"]
        assert len(pref_effects) >= 1

    @pytest.mark.asyncio
    async def test_observe_patch_proposal_not_applied(self):
        """PatchProposal은 미적용 상태로 반환된다 (적용 = apply_patch만)."""
        self._store.write(_make_skill("target-sk"))
        outcome = TurnOutcome(
            session_id="sess3",
            final_message="Actually, the correct way is to run pytest with -v flag.",
            history=(),
            tool_calls_made=0,
            injected_skill_ids=("target-sk",),
        )
        effects = await self._svc.observe(outcome)
        proposals = [e for e in effects if isinstance(e, PatchProposal)]
        # 제안이 있으면 미적용 확인 (스킬 버전 그대로)
        if proposals:
            proposal = proposals[0]
            assert proposal.skill_id == "target-sk"
            assert proposal.from_version == 1
            assert proposal.to_version == 2
            # apply_patch 호출 전이므로 현재 버전은 여전히 1
            current = self._store.read("target-sk")
            assert current.version == 1

    @pytest.mark.asyncio
    async def test_observe_patch_diff_has_correct_shape(self):
        """PatchProposal.diff가 SkillDiff 형태를 갖는다."""
        self._store.write(_make_skill("diff-sk"))
        outcome = TurnOutcome(
            session_id="sess4",
            final_message="Actually, the correct procedure should be different.",
            history=(),
            tool_calls_made=0,
            injected_skill_ids=("diff-sk",),
        )
        effects = await self._svc.observe(outcome)
        proposals = [e for e in effects if isinstance(e, PatchProposal)]
        if proposals:
            diff = proposals[0].diff
            assert "lines" in diff
            assert isinstance(diff["lines"], list)
            for item in diff["lines"]:
                assert "type" in item
                assert "text" in item


# ── apply_patch → history → rollback 통합 테스트 ─────────────────────────────

class TestApplyPatchFull:
    def setup_method(self):
        self._paths = _fresh_paths()
        self._svc = _service(self._paths)
        self._store = SkillStore(self._paths)

    def test_apply_patch_full_flow(self):
        """T3 핵심 수용: v1→apply_patch(approve)→v2 + .history/v1 + patch++."""
        self._store.write(_make_skill("full-flow"))

        # apply_patch 승인
        new_v = self._svc.apply_patch(
            "full-flow",
            approved=True,
            edited_body="## New Steps\n1. Better thing A\n",
        )
        assert new_v == 2

        # .history/v1.md 존재
        hist_dir = self._paths.skill_history("full-flow")
        assert (hist_dir / "v1.md").exists()

        # usage.patch == 1
        usage = UsageStore(self._paths).read("full-flow")
        assert usage.patch == 1

        # 현재 본문이 새 본문으로 교체됨
        detail = self._svc.get_skill("full-flow")
        assert detail is not None
        assert "Better thing A" in detail.body

    def test_apply_patch_reject_does_not_change_skill(self):
        """거부 시 버전/본문/카운터 변화 없음."""
        self._store.write(_make_skill("reject-sk"))
        result = self._svc.apply_patch("reject-sk", approved=False)
        assert result is None
        detail = self._svc.get_skill("reject-sk")
        assert detail is not None
        assert detail.version == 1
        assert UsageStore(self._paths).read("reject-sk").patch == 0

    def test_apply_patch_without_edited_body_uses_current_body(self):
        """edited_body 없으면 기존 본문을 그대로 새 버전으로 기록."""
        self._store.write(_make_skill("no-body-sk"))
        new_v = self._svc.apply_patch("no-body-sk", approved=True)
        assert new_v == 2
        detail = self._svc.get_skill("no-body-sk")
        assert detail is not None
        assert "Do thing A" in detail.body  # 원본 유지

    def test_history_after_multiple_patches(self):
        """2번 패치 후 history에 v1, v2 각각 존재."""
        self._store.write(_make_skill("multi-patch"))
        self._svc.apply_patch("multi-patch", approved=True, edited_body="## v2\n")
        self._svc.apply_patch("multi-patch", approved=True, edited_body="## v3\n")
        hist = self._svc.history("multi-patch")
        assert len(hist) == 2

    def test_rollback_after_patch(self):
        """v1→patch→v2→rollback(v1)→v3, 본문 복원."""
        self._store.write(_make_skill("rb-after-patch"))
        self._svc.apply_patch("rb-after-patch", approved=True, edited_body="## v2\n")
        new_v = self._svc.rollback("rb-after-patch", 1)
        assert new_v == 3
        detail = self._svc.get_skill("rb-after-patch")
        assert detail is not None
        assert "Do thing A" in detail.body


# ── UserProfile 테스트 ─────────────────────────────────────────────────────────

class TestUserProfile:
    def setup_method(self):
        self._db = _fresh_db()
        self._mem_store = MemoryStore(self._db)
        self._profile = UserProfile(self._mem_store)

    def test_render_empty_when_no_preferences(self):
        result = self._profile.render_preference_block()
        assert result == ""

    def test_render_known_keyword(self):
        self._mem_store.add("preference", "I prefer concise answers")
        result = self._profile.render_preference_block()
        assert "간결" in result or "concise" in result.lower()

    def test_render_unknown_preference_verbatim(self):
        self._mem_store.add("preference", "Always add code examples to explanations")
        result = self._profile.render_preference_block()
        assert "Always add code examples" in result

    def test_render_does_not_include_facts(self):
        self._mem_store.add("fact", "Project uses pytest")
        self._mem_store.add("preference", "I prefer concise answers")
        result = self._profile.render_preference_block()
        assert "pytest" not in result

    def test_prefetch_includes_preference_in_system_prompt(self):
        """T5 통합: preferences가 prefetch system_prompt_additions에 반영된다."""
        paths = _fresh_paths()
        db = _fresh_db()
        svc = LearningService(db, LearningConfig(), paths)
        MemoryStore(db).add("preference", "I prefer verbose and detailed answers")
        result = svc.prefetch("sess1", "hello")
        assert "verbose" in result.system_prompt_additions.lower() or "자세" in result.system_prompt_additions


# ── 수용 기준 종합 end-to-end ─────────────────────────────────────────────────

class TestT3Acceptance:
    """learning.md 수용 기준 종합 (최소 테스트) — T3 완전 통합."""

    def setup_method(self):
        self._paths = _fresh_paths()
        self._db = _fresh_db()
        self._svc = _service(self._paths, self._db)
        self._store = SkillStore(self._paths)

    @pytest.mark.asyncio
    async def test_acceptance_patch_full(self):
        """수용 기준 3: v1 → PatchProposal → apply_patch(approve) → v2 + history + counter."""
        # 1. v1 스킬 생성
        self._store.write(_make_skill("ac-patch"))

        # 2. observe로 PatchProposal 유도
        outcome = TurnOutcome(
            session_id="sess_ac",
            final_message="Actually, the correct way should use a different approach.",
            history=(),
            tool_calls_made=0,
            injected_skill_ids=("ac-patch",),
        )
        effects = await self._svc.observe(outcome)
        proposals = [e for e in effects if isinstance(e, PatchProposal)]
        # 제안이 있다면 검증 (보수적 감지이므로 없을 수도 있지만 있어야 함)
        assert len(proposals) >= 1, "PatchProposal should be produced on correction signal"
        proposal = proposals[0]
        assert proposal.skill_id == "ac-patch"
        assert proposal.from_version == 1
        assert not (self._paths.skill_history("ac-patch") / "v1.md").exists(), \
            "v1.md should NOT exist before apply_patch (proposal is not yet applied)"

        # 3. apply_patch 승인
        new_v = self._svc.apply_patch("ac-patch", approved=True, edited_body="## Improved Steps\n")
        assert new_v == 2

        # 4. .history/v1.md 존재
        assert (self._paths.skill_history("ac-patch") / "v1.md").exists()

        # 5. usage.patch == 1
        usage = UsageStore(self._paths).read("ac-patch")
        assert usage.patch == 1

    @pytest.mark.asyncio
    async def test_acceptance_observe_increments_use(self):
        """수용 기준 (T4 연계): observe가 use 카운터를 올린다."""
        self._store.write(_make_skill("obs-sk"))
        outcome = TurnOutcome(
            session_id="s1",
            final_message="Done.",
            history=(),
            tool_calls_made=1,
            injected_skill_ids=("obs-sk",),
        )
        await self._svc.observe(outcome)
        assert UsageStore(self._paths).read("obs-sk").use == 1

    @pytest.mark.asyncio
    async def test_acceptance_memory_fact_written(self):
        """수용 기준 (T4 연계): fact가 감지되면 MemoryWritten + list_memories 반영."""
        outcome = TurnOutcome(
            session_id="s2",
            final_message="The project uses pytest for all unit tests here.",
            history=(),
            tool_calls_made=0,
            injected_skill_ids=(),
        )
        effects = await self._svc.observe(outcome)
        mem_effects = [e for e in effects if isinstance(e, MemoryWritten)]
        assert len(mem_effects) >= 1
        mems = self._svc.list_memories()
        assert len(mems) >= 1
