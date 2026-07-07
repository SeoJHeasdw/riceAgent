"""test_learning_t3.py — T3 수용 기준 검증 (observe, patch, user_model).

수용 기준 (learning.md T3, 2026-07-02 갱신 — 스킬 생성/patch 판단은 observe()에서
review()로 옮겨졌다. 그 수용 기준은 test_learning_t4.py가 검증한다):
  1. patch: PatchProposal→apply_patch(approve)→v2 + .history/v1.md + patch 카운터.
  2. curator: 오래된 스킬 stale/archived 전이 (T2에서 이미 검증되나 T3 연계 재확인).
  3. observe가 주입 스킬 use 카운터를 올린다.
  4. observe가 fact를 감지해 MemoryWritten 효과를 반환하고 list_memories에 반영한다.
  5. prefetch가 active 스킬 전부 주입 + 가이드 문구 포함.
  6. UserProfile: preference가 system prompt에 반영된다.
  7. build_diff: unified diff → SkillDiff 형태.
  8. apply_patch: proposed_body가 실제로 반영된다(버그#0 회귀 방지), from_version=0
     (스킬이 아직 없음)이면 신규 생성으로 처리된다(Phase A.1).

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
from rice_runtime.learning.interfaces import MemoryWritten, PatchProposal
from rice_runtime.learning.memory.store import MemoryStore
from rice_runtime.learning.service import LearningService
from rice_runtime.learning.skill.patch import build_diff
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
    async def test_observe_never_writes_memory_from_regex(self):
        """v3(2026-07-03): 정규식 fact/preference 추출은 제거됐다 — 명시적 fact
        도입구가 있어도 observe()는 메모리를 쓰지 않는다. 메모리 기록의 판단
        주체는 리뷰 LLM(propose_memory_write) 하나다(test_learning_t4.py)."""
        outcome = TurnOutcome(
            session_id="sess1",
            final_message="The project uses pytest for all testing. I prefer concise answers always.",
            history=(),
            tool_calls_made=0,
            injected_skill_ids=(),
        )
        effects = await self._svc.observe(outcome)
        assert [e for e in effects if isinstance(e, MemoryWritten)] == []
        assert self._svc.list_memories() == []

    @pytest.mark.asyncio
    async def test_observe_drains_staged_proposals(self):
        """미드턴 propose_skill(T5) 스테이징은 그 세션의 observe()에서 정확히 한 번
        드레인된다 — 다른 세션의 스테이징은 건드리지 않는다."""
        res = self._svc.stage_proposal(
            "sessA", name="staged-sk", body="# staged\n1. step", description="d", changelog="cl"
        )
        assert res["status"] == "staged"
        other = TurnOutcome(
            session_id="sessB", final_message="x", history=(), tool_calls_made=0, injected_skill_ids=()
        )
        assert await self._svc.observe(other) == []
        mine = TurnOutcome(
            session_id="sessA", final_message="x", history=(), tool_calls_made=0, injected_skill_ids=()
        )
        effects = await self._svc.observe(mine)
        proposals = [e for e in effects if isinstance(e, PatchProposal)]
        assert len(proposals) == 1
        assert proposals[0].skill_id == "staged-sk"
        assert proposals[0].trigger == "T5"
        # 드레인은 1회성 — 같은 세션을 다시 관찰해도 비어 있다.
        assert await self._svc.observe(mine) == []

    @pytest.mark.asyncio
    async def test_observe_discards_staged_on_aborted_turn(self):
        """중단된 턴의 스테이징은 폐기된다 (fail-closed)."""
        self._svc.stage_proposal("sessC", name="aborted-sk", body="# b\n1. s")
        aborted = TurnOutcome(
            session_id="sessC", final_message="", history=(), tool_calls_made=0,
            injected_skill_ids=(), aborted=True,
        )
        assert await self._svc.observe(aborted) == []
        # abort로 버려졌으므로 다음 정상 턴에도 나오지 않는다.
        normal = TurnOutcome(
            session_id="sessC", final_message="x", history=(), tool_calls_made=0, injected_skill_ids=()
        )
        assert await self._svc.observe(normal) == []

    @pytest.mark.asyncio
    async def test_observe_never_produces_patch_proposal(self):
        """observe()는 규칙 기반 patch 제안을 더 이상 만들지 않는다(2026-07-02) — 강한
        교정 신호가 있어도 memory 효과만 반환한다. 저작 판단은 review()의 몫이다
        (test_learning_t4.py)."""
        self._store.write(_make_skill("target-sk"))
        outcome = TurnOutcome(
            session_id="sess3",
            final_message="Actually, the correct way is to run pytest with -v flag.",
            history=(),
            tool_calls_made=0,
            injected_skill_ids=("target-sk",),
        )
        effects = await self._svc.observe(outcome)
        assert not any(isinstance(e, PatchProposal) for e in effects)
        # 스킬 버전도 그대로다 (부수효과 없음)
        assert self._store.read("target-sk").version == 1


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

    def test_apply_patch_without_edited_body_or_proposed_body_keeps_current_body(self):
        """edited_body·proposed_body 둘 다 없으면(퇴화 케이스) 기존 본문을 그대로 유지."""
        self._store.write(_make_skill("no-body-sk"))
        new_v = self._svc.apply_patch("no-body-sk", approved=True)
        assert new_v == 2
        detail = self._svc.get_skill("no-body-sk")
        assert detail is not None
        assert "Do thing A" in detail.body  # 원본 유지

    def test_apply_patch_uses_proposed_body_when_not_edited(self):
        """버그#0 회귀 방지: 사용자가 diff 카드에서 편집 없이 그냥 "승인"만 눌러도
        (edited_body=None) 제안 자체의 proposed_body가 실제로 반영돼야 한다 — 이전에는
        이 경로에서 조용히 current.body로 되돌아가 버전만 올라가고 본문은 안 바뀌었다."""
        self._store.write(_make_skill("proposed-only-sk"))
        new_v = self._svc.apply_patch(
            "proposed-only-sk",
            approved=True,
            proposed_body="## Proposed Steps\n1. Do the new thing\n",
        )
        assert new_v == 2
        detail = self._svc.get_skill("proposed-only-sk")
        assert detail is not None
        assert "Do the new thing" in detail.body
        assert "Do thing A" not in detail.body

    def test_apply_patch_edited_body_overrides_proposed_body(self):
        """edited_body가 있으면 proposed_body보다 우선한다(사용자가 승인 카드에서 직접 고친 경우)."""
        self._store.write(_make_skill("override-sk"))
        new_v = self._svc.apply_patch(
            "override-sk",
            approved=True,
            proposed_body="## Proposed\n",
            edited_body="## User Edited\n",
        )
        assert new_v == 2
        detail = self._svc.get_skill("override-sk")
        assert detail is not None
        assert "User Edited" in detail.body
        assert "Proposed" not in detail.body

    def test_history_after_multiple_patches(self):
        """2번 패치 후 history에 v1, v2 각각 존재."""
        self._store.write(_make_skill("multi-patch"))
        self._svc.apply_patch("multi-patch", approved=True, edited_body="## v2\n1. second step\n")
        self._svc.apply_patch("multi-patch", approved=True, edited_body="## v3\n1. third step\n")
        hist = self._svc.history("multi-patch")
        assert len(hist) == 2

    def test_rollback_after_patch(self):
        """v1→patch→v2→rollback(v1)→v3, 본문 복원."""
        self._store.write(_make_skill("rb-after-patch"))
        self._svc.apply_patch("rb-after-patch", approved=True, edited_body="## v2\n1. second step\n")
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

    def test_acceptance_patch_full(self):
        """수용 기준 1: v1 → PatchProposal(review()가 만든다고 가정 — 여기선 직접 구성해
        apply_patch 메커니즘 자체를 검증) → apply_patch(approve) → v2 + history + counter."""
        # 1. v1 스킬 생성
        self._store.write(_make_skill("ac-patch"))

        # 2. PatchProposal 구성(review()가 만들 모양을 그대로 흉내 — observe()는 더 이상
        #    이걸 만들지 않는다, test_learning_t4.py가 review() 자체를 검증한다)
        old_body = self._store.read("ac-patch").body
        proposed_body = "## Improved Steps\n"
        proposal = PatchProposal(
            skill_id="ac-patch",
            changelog="사용자 교정 반영",
            diff=build_diff(old_body, proposed_body),
            trigger="T1a",
            proposed_body=proposed_body,
            from_version=1,
            to_version=2,
        )
        assert not (self._paths.skill_history("ac-patch") / "v1.md").exists(), \
            "v1.md should NOT exist before apply_patch (proposal is not yet applied)"

        # 3. apply_patch 승인 (edited_body 없이 — proposed_body가 반영돼야 한다, 버그#0)
        new_v = self._svc.apply_patch(
            "ac-patch", approved=True, proposed_body=proposal.proposed_body
        )
        assert new_v == 2

        # 4. .history/v1.md 존재
        assert (self._paths.skill_history("ac-patch") / "v1.md").exists()

        # 5. usage.patch == 1, 본문이 실제로 교체됨
        usage = UsageStore(self._paths).read("ac-patch")
        assert usage.patch == 1
        detail = self._svc.get_skill("ac-patch")
        assert detail is not None
        assert "Improved Steps" in detail.body

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
    async def test_acceptance_memory_regex_path_removed(self):
        """수용 기준(v3 개정): observe()는 어떤 문구에도 메모리를 쓰지 않는다 —
        메모리 기록은 리뷰 LLM 경로(propose_memory_write)만 남았다."""
        outcome = TurnOutcome(
            session_id="s2",
            final_message="The project uses pytest for all unit tests here.",
            history=(),
            tool_calls_made=0,
            injected_skill_ids=(),
        )
        effects = await self._svc.observe(outcome)
        assert [e for e in effects if isinstance(e, MemoryWritten)] == []
        assert self._svc.list_memories() == []
