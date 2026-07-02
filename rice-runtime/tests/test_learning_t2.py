"""test_learning_t2.py — T2 수용 기준 검증 (skill/store.py, usage.py, lifecycle.py, curator/curator.py).

수용 기준 (learning.md T2, T3 일부):
  1. skill store: write→read 라운드트립 (frontmatter 보존) + usage 증감 + history.
  2. patch: v1 → apply_patch(approve) → v2 + .history/v1.md + usage.patch==1.
  3. curator: 오래된 스킬 stale/archived 전이.
  4. prefetch: active 스킬 전부 주입(예산 내) + 가이드 문구 포함.

격리: 실제 ~/.rice 는 건드리지 않는다. tempfile.mkdtemp()로 임시 루트를 사용한다
(CONVENTIONS §6).

외부 의존성 없음 — 파일시스템 + 순수 로직 테스트.
"""

from __future__ import annotations

import datetime
import tempfile
from pathlib import Path

import pytest

from rice_runtime.core.config import LearningConfig
from rice_runtime.core.errors import NotFoundError, ValidationError
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.core.types import TurnOutcome
from rice_runtime.learning.curator.curator import Curator
from rice_runtime.learning.skill.lifecycle import SkillLifecycle, compute_transition
from rice_runtime.learning.skill.store import SkillData, SkillStore, parse_skill_md, serialize_skill_md
from rice_runtime.learning.skill.usage import UsageStore
from rice_runtime.learning.service import LearningService


# ── helpers ───────────────────────────────────────────────────────────────────

def _fresh_paths() -> RicePaths:
    """각 테스트마다 격리된 임시 디렉토리를 사용한다."""
    tmp = tempfile.mkdtemp(prefix="rice-t2-test-")
    return RicePaths.from_env(tmp).ensure()


def _fresh_db() -> Database:
    return Database(":memory:").connect()


def _service(paths: RicePaths) -> LearningService:
    return LearningService(_fresh_db(), LearningConfig(), paths)


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


# ── frontmatter 파싱 테스트 (순수 로직) ──────────────────────────────────────

class TestSkillMdParsing:
    def test_parse_minimal_frontmatter(self):
        md = "---\nname: my-skill\nversion: 1\ndescription: Hello\nstate: active\n---\nBody text\n"
        skill = parse_skill_md(md)
        assert skill.name == "my-skill"
        assert skill.version == 1
        assert skill.description == "Hello"
        assert skill.state == "active"
        assert "Body text" in skill.body

    def test_parse_with_tags(self):
        md = "---\nname: tagged\nversion: 2\ndescription: X\nstate: stale\ntags: [a, b, c]\n---\n"
        skill = parse_skill_md(md)
        assert skill.tags == ["a", "b", "c"]
        assert skill.state == "stale"

    def test_parse_missing_frontmatter_raises(self):
        with pytest.raises(ValidationError):
            parse_skill_md("No frontmatter here")

    def test_parse_missing_name_raises(self):
        md = "---\nversion: 1\ndescription: X\nstate: active\n---\n"
        with pytest.raises(ValidationError):
            parse_skill_md(md)

    def test_parse_invalid_state_raises(self):
        md = "---\nname: x\nversion: 1\ndescription: X\nstate: invalid\n---\n"
        with pytest.raises(ValidationError):
            parse_skill_md(md)

    def test_roundtrip_preserves_fields(self):
        original = _make_skill()
        text = serialize_skill_md(original)
        restored = parse_skill_md(text)
        assert restored.name == original.name
        assert restored.version == original.version
        assert restored.description == original.description
        assert restored.state == original.state
        assert restored.tags == original.tags
        assert restored.source == original.source
        assert restored.body == original.body


# ── SkillStore 테스트 ─────────────────────────────────────────────────────────

class TestSkillStore:
    def setup_method(self):
        self._paths = _fresh_paths()
        self._store = SkillStore(self._paths)

    def test_write_then_read_roundtrip(self):
        skill = _make_skill("roundtrip-skill")
        self._store.write(skill)
        restored = self._store.read("roundtrip-skill")
        assert restored.name == "roundtrip-skill"
        assert restored.version == 1
        assert restored.description == "A test skill"
        assert "Do thing A" in restored.body

    def test_read_missing_skill_raises_not_found(self):
        with pytest.raises(NotFoundError):
            self._store.read("no-such-skill")

    def test_list_returns_all_skills(self):
        self._store.write(_make_skill("skill-a"))
        self._store.write(_make_skill("skill-b"))
        skills = self._store.list()
        names = {s.name for s in skills}
        assert {"skill-a", "skill-b"} <= names

    def test_list_empty_when_no_skills(self):
        assert self._store.list() == []

    def test_archive_to_history_creates_file(self):
        skill = _make_skill("hist-skill")
        self._store.write(skill)
        text = (self._paths.skills / "hist-skill.md").read_text()
        self._store.archive_to_history("hist-skill", 1, text)
        hist_path = self._paths.skill_history("hist-skill") / "v1.md"
        assert hist_path.exists()

    def test_read_history_returns_versions(self):
        skill = _make_skill("hist-skill2")
        self._store.write(skill)
        text = (self._paths.skills / "hist-skill2.md").read_text()
        self._store.archive_to_history("hist-skill2", 1, text)
        history = self._store.read_history("hist-skill2")
        assert len(history) == 1
        assert history[0][0] == 1  # version

    def test_frontmatter_preserved_after_state_change(self):
        skill = _make_skill("state-skill")
        self._store.write(skill)
        data = self._store.read("state-skill")
        data.state = "stale"
        self._store.write(data)
        restored = self._store.read("state-skill")
        assert restored.state == "stale"
        assert restored.tags == ["test"]  # 태그 보존 확인


# ── UsageStore 테스트 ─────────────────────────────────────────────────────────

class TestUsageStore:
    def setup_method(self):
        self._paths = _fresh_paths()
        self._usage = UsageStore(self._paths)
        # 스킬 파일도 필요 (skills 디렉토리)
        self._paths.skills.mkdir(parents=True, exist_ok=True)

    def test_read_default_when_no_file(self):
        u = self._usage.read("no-skill")
        assert u.use == 0
        assert u.view == 0
        assert u.patch == 0
        assert u.last_used_at is None

    def test_increment_use(self):
        self._usage.increment_use("my-skill")
        u = self._usage.read("my-skill")
        assert u.use == 1
        assert u.last_used_at is not None

    def test_increment_view(self):
        self._usage.increment_view("my-skill")
        u = self._usage.read("my-skill")
        assert u.view == 1

    def test_increment_patch(self):
        self._usage.increment_patch("my-skill")
        u = self._usage.read("my-skill")
        assert u.patch == 1

    def test_multiple_increments_accumulate(self):
        for _ in range(3):
            self._usage.increment_use("x")
        assert self._usage.read("x").use == 3

    def test_usage_file_created_on_first_increment(self):
        self._usage.increment_use("new-skill")
        assert (self._paths.skills / "new-skill.usage.json").exists()


# ── SkillLifecycle 테스트 ─────────────────────────────────────────────────────

class TestComputeTransition:
    """compute_transition은 순수 함수 — 외부 의존 없음."""

    def _skill(self, state: str, days_ago: int) -> SkillData:
        updated_at = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(days=days_ago)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        return SkillData(
            name="x", version=1, description="x", state=state,
            body="", tags=[], source=None, created_at=updated_at, updated_at=updated_at,
        )

    def test_no_transition_when_recent(self):
        skill = self._skill("active", days_ago=1)
        assert compute_transition(skill, stale_after_days=3, archive_after_days=7) is None

    def test_active_to_stale_when_old_enough(self):
        skill = self._skill("active", days_ago=4)
        assert compute_transition(skill, stale_after_days=3, archive_after_days=7) == "stale"

    def test_stale_to_archived_when_old_enough(self):
        skill = self._skill("stale", days_ago=8)
        assert compute_transition(skill, stale_after_days=3, archive_after_days=7) == "archived"

    def test_archived_no_automatic_transition(self):
        skill = self._skill("archived", days_ago=100)
        assert compute_transition(skill, stale_after_days=3, archive_after_days=7) is None

    def test_no_transition_without_date(self):
        skill = SkillData(
            name="x", version=1, description="x", state="active",
            body="", tags=[], source=None, created_at=None, updated_at=None,
        )
        assert compute_transition(skill, stale_after_days=3, archive_after_days=7) is None


class TestSkillLifecycle:
    def setup_method(self):
        self._paths = _fresh_paths()
        self._store = SkillStore(self._paths)
        self._lifecycle = SkillLifecycle(self._store, self._paths)

    def test_set_state_changes_state(self):
        self._store.write(_make_skill("ls"))
        self._lifecycle.set_state("ls", "stale")
        assert self._store.read("ls").state == "stale"

    def test_set_state_invalid_raises(self):
        self._store.write(_make_skill("ls2"))
        with pytest.raises(ValidationError):
            self._lifecycle.set_state("ls2", "bogus")

    def test_rollback_restores_body(self):
        skill = _make_skill("rb-skill")
        self._store.write(skill)
        # v1 히스토리 보관
        path = self._paths.skills / "rb-skill.md"
        self._store.archive_to_history("rb-skill", 1, path.read_text())
        # v2로 업데이트
        skill.version = 2
        skill.body = "## Updated\nNew content\n"
        self._store.write(skill)
        # rollback v1
        new_v = self._lifecycle.rollback("rb-skill", 1)
        assert new_v == 3  # version은 단조 증가
        restored = self._store.read("rb-skill")
        assert "Do thing A" in restored.body  # v1 내용 복원됨

    def test_rollback_missing_version_raises(self):
        self._store.write(_make_skill("rb2"))
        with pytest.raises(NotFoundError):
            self._lifecycle.rollback("rb2", 99)


# ── Curator 테스트 ────────────────────────────────────────────────────────────

class TestCurator:
    def setup_method(self):
        self._paths = _fresh_paths()
        self._store = SkillStore(self._paths)
        self._lifecycle = SkillLifecycle(self._store, self._paths)
        self._config = LearningConfig(stale_after_days=3, archive_after_days=7)
        self._curator = Curator(self._store, self._lifecycle, self._config)

    def _old_skill(self, name: str, state: str, days_ago: int) -> SkillData:
        ts = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(days=days_ago)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        return SkillData(
            name=name, version=1, description="old skill", state=state,
            body="body", tags=[], source=None, created_at=ts, updated_at=ts,
        )

    def test_curate_transitions_old_active_to_stale(self):
        self._store.write(self._old_skill("stale-me", "active", days_ago=5))
        result = self._curator.curate()
        transitions = dict(result.transitioned)
        assert "stale-me" in transitions
        assert transitions["stale-me"] == "stale"

    def test_curate_transitions_old_stale_to_archived(self):
        self._store.write(self._old_skill("archive-me", "stale", days_ago=10))
        result = self._curator.curate()
        transitions = dict(result.transitioned)
        assert "archive-me" in transitions
        assert transitions["archive-me"] == "archived"

    def test_curate_leaves_recent_skill_alone(self):
        self._store.write(self._old_skill("keep-me", "active", days_ago=1))
        result = self._curator.curate()
        transitions = dict(result.transitioned)
        assert "keep-me" not in transitions

    def test_curate_empty_returns_empty_result(self):
        result = self._curator.curate()
        assert result.transitioned == ()


# ── LearningService (T2 end-to-end) ──────────────────────────────────────────

class TestLearningServiceT2:
    def setup_method(self):
        self._paths = _fresh_paths()
        self._svc = _service(self._paths)

    def _write_skill(self, name: str = "test-skill", state: str = "active") -> None:
        skill = _make_skill(name, state)
        SkillStore(self._paths).write(skill)

    def test_list_skills_empty_initially(self):
        assert self._svc.list_skills() == []

    def test_write_then_list_skills(self):
        self._write_skill("alpha")
        skills = self._svc.list_skills()
        assert len(skills) == 1
        assert skills[0].id == "alpha"

    def test_get_skill_returns_detail(self):
        self._write_skill("detail-skill")
        detail = self._svc.get_skill("detail-skill")
        assert detail is not None
        assert detail.id == "detail-skill"
        assert "Do thing A" in detail.body

    def test_get_skill_increments_view(self):
        self._write_skill("view-skill")
        self._svc.get_skill("view-skill")
        self._svc.get_skill("view-skill")
        detail = self._svc.get_skill("view-skill")
        assert detail is not None
        assert detail.view == 3  # 3번 호출

    def test_get_skill_not_found_returns_none(self):
        assert self._svc.get_skill("no-such") is None

    def test_set_state_changes_skill_state(self):
        self._write_skill("state-skill")
        self._svc.set_state("state-skill", "stale")
        detail = self._svc.get_skill("state-skill")
        assert detail is not None
        assert detail.state == "stale"

    def test_apply_patch_reject_returns_none(self):
        self._write_skill("patch-skill")
        result = self._svc.apply_patch("patch-skill", approved=False)
        assert result is None

    def test_apply_patch_approve_returns_new_version(self):
        self._write_skill("patch-skill2")
        new_v = self._svc.apply_patch("patch-skill2", approved=True, edited_body="## New\nContent\n")
        assert new_v == 2

    def test_apply_patch_creates_history(self):
        self._write_skill("patch-hist")
        self._svc.apply_patch("patch-hist", approved=True, edited_body="## New\n")
        hist = self._svc.history("patch-hist")
        assert len(hist) == 1
        assert hist[0].version == 1

    def test_apply_patch_increments_patch_counter(self):
        self._write_skill("patch-cnt")
        self._svc.apply_patch("patch-cnt", approved=True)
        detail = self._svc.get_skill("patch-cnt")
        assert detail is not None
        assert detail.patch == 1

    def test_history_returns_versions(self):
        self._write_skill("hist-sk")
        self._svc.apply_patch("hist-sk", approved=True, edited_body="v2 body")
        history = self._svc.history("hist-sk")
        assert len(history) == 1

    def test_rollback_restores_body(self):
        """v1 → apply_patch → v2 → rollback(v1) → v3 with v1 body."""
        self._write_skill("rb-svc")
        self._svc.apply_patch("rb-svc", approved=True, edited_body="## v2 body\n")
        # 현재 v2, history에 v1.md
        new_v = self._svc.rollback("rb-svc", 1)
        assert new_v == 3
        detail = self._svc.get_skill("rb-svc")
        assert detail is not None
        assert "Do thing A" in detail.body

    def test_curate_transitions_old_skill(self):
        import datetime
        ts = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        skill = SkillData(
            name="old-sk", version=1, description="old", state="active",
            body="body", tags=[], source=None, created_at=ts, updated_at=ts,
        )
        SkillStore(self._paths).write(skill)
        result = self._svc.curate()
        assert any(sid == "old-sk" for sid, _ in result.transitioned)

    def test_prefetch_injects_active_skills(self):
        self._write_skill("active-sk", "active")
        result = self._svc.prefetch("sess1", "hello")
        assert len(result.skills) == 1
        assert result.skills[0].skill_id == "active-sk"

    def test_prefetch_does_not_inject_archived_skills(self):
        self._write_skill("arch-sk", "archived")
        result = self._svc.prefetch("sess1", "hello")
        assert len(result.skills) == 0

    def test_prefetch_includes_skill_guidance(self):
        result = self._svc.prefetch("sess1", "hello")
        assert "propose_skill" in result.system_prompt_additions

    def test_prefetch_includes_skill_body_in_prompt(self):
        self._write_skill("body-sk", "active")
        result = self._svc.prefetch("sess1", "hello")
        assert "Do thing A" in result.system_prompt_additions

    @pytest.mark.asyncio
    async def test_observe_increments_use_for_injected_skills(self):
        self._write_skill("used-sk", "active")
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
