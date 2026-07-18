"""test_learning_t5.py — T5 수용 기준 검증 (curator LLM 통합, Phase B).

Hermes learning_loop 이식 작업(2026-07-02) — RICE_LEARNING_LOOP_DESIGN.md §5.1이
"LLM 리뷰(선택)... 향후 고도화"로 미뤄뒀던 부분. 규칙 기반 curator(T2에서 이미
검증됨)에 LLM 통합 분석을 얹는다.

수용 기준:
  1. consolidate()는 provider 배선 여부와 무관하게 규칙 기반 전이를 항상 먼저
     실행한다(스텁 provider가 tool call 0개를 반환해도 규칙 전이는 일어남).
  2. 스텁 provider의 propose_consolidate(신규/기존 양쪽)·propose_prune 호출이
     올바른 PatchProposal/ArchiveProposal DTO로 매핑된다(trigger="T3").
  3. decide_archive() 흡수는 active/non-self 대상의 version+본문 hash가
     제안 시점과 일치할 때만 archived 전이를 허용한다.
  4. plugin: source 스킬은 후보에서 제외되고 모든 최종 쓰기 경계에서 불변이다.
  5. 가드(크기/시크릿)가 통합 제안에도 적용된다.

격리: 실제 ~/.rice 건드리지 않음. tempfile.mkdtemp() / in-memory sqlite.
"""

from __future__ import annotations

import tempfile
from typing import AsyncIterator, Sequence

import pytest

from rice_runtime.agent.providers.interfaces import ProviderCapabilities, ProviderChunk
from rice_runtime.core.config import LearningConfig
from rice_runtime.core.errors import ConflictError, ValidationError
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.core.types import ToolCall, ToolSpec
from rice_runtime.learning.curator.consolidate import ConsolidationEngine
from rice_runtime.learning.interfaces import ArchiveProposal, PatchProposal
from rice_runtime.learning.proposals import body_fingerprint
from rice_runtime.learning.service import LearningService
from rice_runtime.learning.skill.store import SkillData, SkillStore
from rice_runtime.learning.skill.usage import UsageStore


# ── shared helpers ───────────────────────────────────────────────────────────

def _fresh_paths() -> RicePaths:
    tmp = tempfile.mkdtemp(prefix="rice-t5-test-")
    return RicePaths.from_env(tmp).ensure()


def _fresh_db() -> Database:
    return Database(":memory:").connect()


def _make_skill(
    name: str, body: str = "## Steps\n1. Do thing\n", source: str | None = "user", state: str = "active"
) -> SkillData:
    return SkillData(
        name=name, version=1, description=f"skill {name}", state=state,
        body=body, tags=[], source=source,
        created_at="2024-01-01T00:00:00Z", updated_at="2024-01-01T00:00:00Z",
    )


class _FakeProvider:
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


# ── ConsolidationEngine 테스트 ────────────────────────────────────────────────

class TestConsolidationEngine:
    def setup_method(self):
        self._paths = _fresh_paths()
        self._skill_store = SkillStore(self._paths)
        self._usage_store = UsageStore(self._paths)

    def _engine(self, provider) -> ConsolidationEngine:
        return ConsolidationEngine(
            resolve_provider=lambda: provider,
            skill_store=self._skill_store,
            usage_store=self._usage_store,
            candidate_budget_chars=300_000,
            max_body_chars=20_000,
        )

    @pytest.mark.asyncio
    async def test_fewer_than_two_candidates_skips_llm_call_entirely(self):
        self._skill_store.write(_make_skill("only-one"))
        # provider가 실제로 호출되면 예외를 던지게 해서 "호출 안 함"을 검증한다.
        class _Boom:
            name = "boom"
            def capabilities(self):
                return ProviderCapabilities()
            async def stream(self, **kwargs):
                raise AssertionError("should not be called with <2 candidates")
                yield  # pragma: no cover
        engine = self._engine(_Boom())
        proposals = await engine.analyze()
        assert proposals == []

    @pytest.mark.asyncio
    async def test_echo_provider_short_circuits(self):
        self._skill_store.write(_make_skill("sk-a"))
        self._skill_store.write(_make_skill("sk-b"))
        class _Echo:
            name = "echo"
        engine = self._engine(_Echo())
        assert await engine.analyze() == []

    @pytest.mark.asyncio
    async def test_propose_consolidate_new_umbrella_maps_to_creation_proposal(self):
        self._skill_store.write(_make_skill("deploy-web", body="## Steps\n1. build web\n"))
        self._skill_store.write(_make_skill("deploy-api", body="## Steps\n1. build api\n"))
        call = ToolCall(
            tool_name="propose_consolidate",
            arguments={
                "umbrella_id": "deploy-service",
                "is_new": True,
                "umbrella_description": "Deploy any service",
                "umbrella_body": "## Steps\n### Web\n1. build web\n### API\n1. build api\n",
                "changelog": "웹/API 배포 통합",
                "absorbed_skill_ids": ["deploy-web", "deploy-api"],
            },
        )
        engine = self._engine(_FakeProvider([call]))
        proposals = await engine.analyze()
        # 버그#B1 회귀 방지: umbrella PatchProposal 1개 + 흡수된 형제마다 ArchiveProposal.
        assert len(proposals) == 3
        umbrella = next(p for p in proposals if isinstance(p, PatchProposal))
        archives = [p for p in proposals if isinstance(p, ArchiveProposal)]
        assert umbrella.skill_id == "deploy-service"
        assert umbrella.from_version == 0
        assert umbrella.trigger == "T3"
        assert "build web" in umbrella.proposed_body and "build api" in umbrella.proposed_body
        assert {a.skill_id for a in archives} == {"deploy-web", "deploy-api"}
        assert all(a.absorbed_into == "deploy-service" for a in archives)
        assert all(a.absorbed_into_version == umbrella.to_version for a in archives)
        assert all(
            a.absorbed_into_body_hash == body_fingerprint(umbrella.proposed_body)
            for a in archives
        )

    @pytest.mark.asyncio
    async def test_propose_consolidate_existing_umbrella_maps_to_patch_proposal(self):
        self._skill_store.write(_make_skill("umbrella-sk", body="## Old umbrella\n"))
        self._skill_store.write(_make_skill("sibling-sk", body="## Sibling\n"))
        call = ToolCall(
            tool_name="propose_consolidate",
            arguments={
                "umbrella_id": "umbrella-sk",
                "is_new": False,
                "umbrella_body": "## New umbrella\n### Sibling section\ncontent\n",
                "changelog": "형제 흡수",
                "absorbed_skill_ids": ["sibling-sk"],
            },
        )
        engine = self._engine(_FakeProvider([call]))
        proposals = await engine.analyze()
        assert len(proposals) == 2  # umbrella patch + 흡수된 형제 1개 archive
        umbrella = next(p for p in proposals if isinstance(p, PatchProposal))
        archive = next(p for p in proposals if isinstance(p, ArchiveProposal))
        assert umbrella.skill_id == "umbrella-sk"
        assert umbrella.from_version == 1
        assert umbrella.to_version == 2
        assert archive.skill_id == "sibling-sk"
        assert archive.absorbed_into == "umbrella-sk"
        assert archive.absorbed_into_version == umbrella.to_version
        assert archive.absorbed_into_body_hash == body_fingerprint(umbrella.proposed_body)

    @pytest.mark.asyncio
    async def test_existing_unchanged_umbrella_uses_current_body_precondition(self):
        current_body = "## Already merged\n"
        self._skill_store.write(_make_skill("umbrella-sk", body=current_body))
        self._skill_store.write(_make_skill("sibling-sk", body="## Sibling\n"))
        call = ToolCall(
            tool_name="propose_consolidate",
            arguments={
                "umbrella_id": "umbrella-sk",
                "is_new": False,
                "umbrella_body": current_body,
                "changelog": "archive sibling only",
                "absorbed_skill_ids": ["sibling-sk"],
            },
        )

        proposals = await self._engine(_FakeProvider([call])).analyze()

        assert len(proposals) == 1
        archive = proposals[0]
        assert isinstance(archive, ArchiveProposal)
        assert archive.absorbed_into_version == 1
        assert archive.absorbed_into_body_hash == body_fingerprint(current_body)

    @pytest.mark.asyncio
    async def test_propose_prune_maps_to_archive_proposal_no_absorption(self):
        self._skill_store.write(_make_skill("obsolete-sk"))
        self._skill_store.write(_make_skill("other-sk"))
        call = ToolCall(
            tool_name="propose_prune",
            arguments={"skill_id": "obsolete-sk", "reason": "완전히 낡음"},
        )
        engine = self._engine(_FakeProvider([call]))
        proposals = await engine.analyze()
        assert len(proposals) == 1
        p = proposals[0]
        assert isinstance(p, ArchiveProposal)
        assert p.skill_id == "obsolete-sk"
        assert p.absorbed_into is None
        assert p.reason == "완전히 낡음"

    @pytest.mark.asyncio
    async def test_propose_prune_for_non_candidate_is_rejected(self):
        """스키마 enum이 이미 막지만, 방어적으로 후보 목록 밖 id는 버린다."""
        self._skill_store.write(_make_skill("sk-a"))
        self._skill_store.write(_make_skill("sk-b"))
        call = ToolCall(tool_name="propose_prune", arguments={"skill_id": "not-a-candidate", "reason": "x"})
        engine = self._engine(_FakeProvider([call]))
        assert await engine.analyze() == []

    @pytest.mark.asyncio
    async def test_plugin_provided_skills_excluded_from_candidates(self):
        self._skill_store.write(_make_skill("user-sk-1", source="user"))
        self._skill_store.write(_make_skill("plugin-sk", source="plugin:wxo-expert"))
        engine = self._engine(_FakeProvider([]))
        candidates = engine._build_candidates()  # noqa: SLF001 (내부 검증)
        ids = [c[0] for c in candidates]
        assert "plugin-sk" not in ids

    @pytest.mark.asyncio
    async def test_archived_skills_excluded_from_candidates(self):
        self._skill_store.write(_make_skill("active-sk", state="active"))
        self._skill_store.write(_make_skill("archived-sk", state="archived"))
        engine = self._engine(_FakeProvider([]))
        ids = [c[0] for c in engine._build_candidates()]  # noqa: SLF001
        assert "archived-sk" not in ids
        assert "active-sk" in ids

    @pytest.mark.asyncio
    async def test_oversize_umbrella_body_is_dropped(self):
        self._skill_store.write(_make_skill("sk-a"))
        self._skill_store.write(_make_skill("sk-b"))
        call = ToolCall(
            tool_name="propose_consolidate",
            arguments={
                "umbrella_id": "too-big",
                "is_new": True,
                "umbrella_body": "x" * 30_000,
                "changelog": "x",
                "absorbed_skill_ids": ["sk-a"],
            },
        )
        engine = ConsolidationEngine(
            resolve_provider=lambda: _FakeProvider([call]),
            skill_store=self._skill_store,
            usage_store=self._usage_store,
            candidate_budget_chars=300_000,
            max_body_chars=100,
        )
        assert await engine.analyze() == []

    @pytest.mark.asyncio
    async def test_secret_in_umbrella_body_is_scrubbed_and_flagged(self):
        self._skill_store.write(_make_skill("sk-a"))
        self._skill_store.write(_make_skill("sk-b"))
        call = ToolCall(
            tool_name="propose_consolidate",
            arguments={
                "umbrella_id": "leaky-umbrella",
                "is_new": True,
                "umbrella_body": "token: AKIAABCDEFGHIJKLMNOP",
                "changelog": "x",
                "absorbed_skill_ids": ["sk-a"],
            },
        )
        engine = self._engine(_FakeProvider([call]))
        proposals = await engine.analyze()
        assert len(proposals) == 2  # umbrella(scrub 대상) + 흡수된 형제 1개 archive
        umbrella = next(p for p in proposals if isinstance(p, PatchProposal))
        assert "AKIAABCDEFGHIJKLMNOP" not in umbrella.proposed_body
        assert len(umbrella.warnings) == 1


# ── LearningService.consolidate()/decide_archive() 파사드 테스트 ──────────────

class TestConsolidateFacade:
    def setup_method(self):
        self._paths = _fresh_paths()

    @pytest.mark.asyncio
    async def test_consolidate_without_provider_still_runs_rules(self):
        """resolve_provider=None이어도 규칙 기반 전이는 정상 동작(하위호환) —
        proposals만 빈 튜플."""
        svc = LearningService(_fresh_db(), LearningConfig(stale_after_days=0), self._paths)
        SkillStore(self._paths).write(_make_skill("old-sk"))
        result = await svc.consolidate()
        assert result.proposals == ()
        # stale_after_days=0이므로 즉시 stale 전이돼야 한다(T2에서 이미 검증된 규칙).
        assert any(name == "old-sk" for name, _ in result.transitioned)

    @pytest.mark.asyncio
    async def test_consolidate_with_provider_runs_rules_then_llm(self):
        skill_store = SkillStore(self._paths)
        skill_store.write(_make_skill("sk-a"))
        skill_store.write(_make_skill("sk-b"))
        call = ToolCall(tool_name="propose_prune", arguments={"skill_id": "sk-a", "reason": "낡음"})
        svc = LearningService(
            _fresh_db(), LearningConfig(), self._paths, resolve_provider=lambda: _FakeProvider([call])
        )
        result = await svc.consolidate()
        assert len(result.proposals) == 1
        assert isinstance(result.proposals[0], ArchiveProposal)

    def test_decide_archive_approved_sets_archived_and_absorbed_into(self):
        svc = LearningService(_fresh_db(), LearningConfig(), self._paths)
        store = SkillStore(self._paths)
        store.write(_make_skill("to-archive"))
        # 순서 보장(2026-07-18): absorb 대상 umbrella가 실존해야 archive가 적용된다.
        store.write(_make_skill("umbrella-x"))
        target = store.read("umbrella-x")
        applied = svc.decide_archive(
            "to-archive",
            approved=True,
            absorbed_into="umbrella-x",
            absorbed_into_version=target.version,
            absorbed_into_body_hash=body_fingerprint(target.body),
        )
        assert applied is True
        detail = svc.get_skill("to-archive")
        assert detail is not None
        assert detail.state == "archived"
        # frontmatter 왕복 확인 — SkillStore를 통해 absorbed_into가 실제로 저장됐는지.
        raw = SkillStore(self._paths).read("to-archive")
        assert raw.absorbed_into == "umbrella-x"

    def test_decide_archive_missing_absorb_target_conflicts(self):
        """umbrella가 아직 없는데 흡수 archive를 먼저 승인하면 409 — "기존 스킬은
        사라졌는데 대체 스킬은 없는" 통합 순서 위반을 막는다."""
        svc = LearningService(_fresh_db(), LearningConfig(), self._paths)
        SkillStore(self._paths).write(_make_skill("to-archive"))
        with pytest.raises(ConflictError) as exc_info:
            svc.decide_archive("to-archive", approved=True, absorbed_into="umbrella-x")
        assert exc_info.value.code == "skill.absorb_target_missing"
        detail = svc.get_skill("to-archive")
        assert detail is not None
        assert detail.state == "active"  # 변경 없음 — 스킬은 그대로 남는다

    def test_decide_archive_requires_target_version_and_body_hash(self):
        svc = LearningService(_fresh_db(), LearningConfig(), self._paths)
        store = SkillStore(self._paths)
        store.write(_make_skill("to-archive"))
        store.write(_make_skill("umbrella-x"))

        with pytest.raises(ConflictError) as exc_info:
            svc.decide_archive("to-archive", approved=True, absorbed_into="umbrella-x")

        assert exc_info.value.code == "skill.absorb_precondition_missing"
        assert store.read("to-archive").state == "active"

    def test_decide_archive_rejects_self_absorption(self):
        svc = LearningService(_fresh_db(), LearningConfig(), self._paths)
        store = SkillStore(self._paths)
        store.write(_make_skill("self-archive"))

        with pytest.raises(ConflictError) as exc_info:
            svc.decide_archive(
                "self-archive",
                approved=True,
                absorbed_into="self-archive",
                absorbed_into_version=1,
                absorbed_into_body_hash=body_fingerprint(store.read("self-archive").body),
            )

        assert exc_info.value.code == "skill.absorb_target_self"
        assert store.read("self-archive").state == "active"

    @pytest.mark.parametrize(
        ("target_state", "target_enabled"),
        (("stale", True), ("archived", True), ("active", False)),
    )
    def test_decide_archive_requires_active_enabled_target(
        self, target_state: str, target_enabled: bool
    ):
        svc = LearningService(_fresh_db(), LearningConfig(), self._paths)
        store = SkillStore(self._paths)
        store.write(_make_skill("to-archive"))
        target = _make_skill("umbrella-x", state=target_state)
        target.enabled = target_enabled
        store.write(target)

        with pytest.raises(ConflictError) as exc_info:
            svc.decide_archive(
                "to-archive",
                approved=True,
                absorbed_into="umbrella-x",
                absorbed_into_version=target.version,
                absorbed_into_body_hash=body_fingerprint(target.body),
            )

        assert exc_info.value.code == "skill.absorb_target_inactive"
        assert store.read("to-archive").state == "active"

    @pytest.mark.parametrize("mismatch", ("version", "body_hash"))
    def test_decide_archive_rejects_changed_target(self, mismatch: str):
        svc = LearningService(_fresh_db(), LearningConfig(), self._paths)
        store = SkillStore(self._paths)
        store.write(_make_skill("to-archive"))
        target = _make_skill("umbrella-x")
        store.write(target)
        expected_version = target.version + (1 if mismatch == "version" else 0)
        expected_hash = (
            "0" * 64 if mismatch == "body_hash" else body_fingerprint(target.body)
        )

        with pytest.raises(ConflictError) as exc_info:
            svc.decide_archive(
                "to-archive",
                approved=True,
                absorbed_into="umbrella-x",
                absorbed_into_version=expected_version,
                absorbed_into_body_hash=expected_hash,
            )

        assert exc_info.value.code == "skill.absorb_target_mismatch"
        assert store.read("to-archive").state == "active"

    def test_applied_umbrella_roundtrip_matches_canonical_body_hash(self):
        svc = LearningService(_fresh_db(), LearningConfig(), self._paths)
        store = SkillStore(self._paths)
        store.write(_make_skill("to-archive"))
        store.write(_make_skill("umbrella-x"))
        proposed_body = "\r\n\r\n## Merged\r\n1. Keep both procedures\r\n"

        version = svc.apply_patch(
            "umbrella-x",
            approved=True,
            proposed_body=proposed_body,
            expected_version=1,
        )
        applied = svc.decide_archive(
            "to-archive",
            approved=True,
            absorbed_into="umbrella-x",
            absorbed_into_version=version,
            absorbed_into_body_hash=body_fingerprint(proposed_body),
        )

        assert applied is True

    def test_decide_archive_rejects_plugin_owned_absorb_target(self):
        svc = LearningService(_fresh_db(), LearningConfig(), self._paths)
        store = SkillStore(self._paths)
        store.write(_make_skill("to-archive"))
        target = _make_skill("plugin-target", source="plugin:owner")
        store.write(target)

        with pytest.raises(ConflictError) as exc_info:
            svc.decide_archive(
                "to-archive",
                approved=True,
                absorbed_into="plugin-target",
                absorbed_into_version=target.version,
                absorbed_into_body_hash=body_fingerprint(target.body),
            )

        assert exc_info.value.code == "skill.absorb_target_plugin"
        assert store.read("to-archive").state == "active"

    def test_decide_archive_already_archived_source_is_noop(self):
        svc = LearningService(_fresh_db(), LearningConfig(), self._paths)
        store = SkillStore(self._paths)
        store.write(_make_skill("already-archived", state="archived"))

        assert svc.decide_archive("already-archived", approved=True) is False

    def test_decide_archive_rejected_leaves_unchanged(self):
        svc = LearningService(_fresh_db(), LearningConfig(), self._paths)
        SkillStore(self._paths).write(_make_skill("stay-active"))
        applied = svc.decide_archive("stay-active", approved=False)
        assert applied is False
        detail = svc.get_skill("stay-active")
        assert detail is not None
        assert detail.state == "active"

    def test_decide_archive_unknown_skill_returns_false(self):
        svc = LearningService(_fresh_db(), LearningConfig(), self._paths)
        assert svc.decide_archive("does-not-exist", approved=True) is False


class TestPluginImmutability:
    def setup_method(self):
        self._paths = _fresh_paths()
        self._store = SkillStore(self._paths)
        self._svc = LearningService(_fresh_db(), LearningConfig(), self._paths)

    @pytest.mark.parametrize(
        "operation",
        (
            "apply_patch",
            "update_skill",
            "update_skill_file",
            "delete_skill",
            "decide_archive",
            "rollback",
            "set_state",
        ),
    )
    def test_store_backed_plugin_is_immutable_at_every_service_write_sink(self, operation: str):
        plugin = _make_skill("plugin-owned", source="plugin:wxo-expert")
        self._store.write(plugin)

        def mutate() -> None:
            if operation == "apply_patch":
                self._svc.apply_patch(
                    "plugin-owned", approved=True, proposed_body="## Changed\n1. no\n"
                )
            elif operation == "update_skill":
                self._svc.update_skill("plugin-owned", description="changed")
            elif operation == "update_skill_file":
                self._svc.update_skill_file("plugin-owned", "references/note.md", "changed")
            elif operation == "delete_skill":
                self._svc.delete_skill("plugin-owned")
            elif operation == "decide_archive":
                self._svc.decide_archive("plugin-owned", approved=True)
            elif operation == "rollback":
                self._svc.rollback("plugin-owned", 1)
            else:
                self._svc.set_state("plugin-owned", "archived")

        with pytest.raises(ValidationError) as exc_info:
            mutate()

        assert exc_info.value.code == "skill.plugin_immutable"
        unchanged = self._store.read("plugin-owned")
        assert (unchanged.version, unchanged.body, unchanged.state) == (
            plugin.version,
            plugin.body,
            plugin.state,
        )

    def test_rule_curator_does_not_transition_store_backed_plugin(self):
        plugin = _make_skill("plugin-owned", source="plugin:wxo-expert")
        self._store.write(plugin)
        svc = LearningService(
            _fresh_db(),
            LearningConfig(stale_after_days=0, archive_after_days=0),
            self._paths,
        )

        result = svc.curate()

        assert all(skill_id != "plugin-owned" for skill_id, _ in result.transitioned)
        assert self._store.read("plugin-owned").state == "active"
