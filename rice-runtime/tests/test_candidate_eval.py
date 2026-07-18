"""candidate 승격 게이트 (LEARNING_LOOP §10.6) — baseline-vs-candidate 평가.

고정하는 불변조건:

  1. 게이트 켜짐 + 승인 → 스킬 파일 불변, candidate pending, CandidateStaged 반환.
  2. 게이트 꺼짐(기본) → 기존 즉시 적용 동작 그대로 (순수 추가).
  3. 평가에서 candidate 우세 → promoted + apply_patch 실행(version 증가) + 리포트.
  4. 평가에서 회귀 검출 → discarded + 스킬 불변 + 사유 코드가 리포트에 남는다.
  5. 평가 인프라 실패 → pending 재시도(attempts), 상한 도달 시 failed 로 보존.
  6. 격리 이후 스킬이 움직이면(직접 편집 등) 평가/승격 대신 expired — 오래된
     전제의 후보가 최신 내용을 덮어쓰지 않는다.
  7. 신규 생성 후보(from_version=0)는 baseline arm 이 스킬 없이 돌고, 승격 시 v1.
  8. 같은 스킬의 미결 candidate 는 1개 — 중복 격리는 409.
  9. 제안함 승인 경로도 같은 게이트를 지난다.
 10. 수동 결정(decide_candidate)은 평가와 무관하게 승격/폐기할 수 있다(탈출구).
"""

from __future__ import annotations

import json
import tempfile
from typing import AsyncIterator, Sequence

import pytest

from rice_runtime.agent.providers.interfaces import ProviderCapabilities, ProviderChunk
from rice_runtime.core.config import LearningConfig
from rice_runtime.core.errors import ConflictError
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.learning.interfaces import CandidateStaged
from rice_runtime.learning.proposals import make_create_proposal, make_patch_proposal
from rice_runtime.learning.service import LearningService
from rice_runtime.learning.skill.store import SkillData, SkillStore
from rice_runtime.prompts.candidate_eval import JUDGE_SYSTEM_PROMPT, TASK_GEN_SYSTEM_PROMPT

_MARKER = "MARKER-OK"


def _fresh_paths() -> RicePaths:
    return RicePaths.from_env(tempfile.mkdtemp(prefix="rice-cand-")).ensure()


def _fresh_db() -> Database:
    return Database(":memory:").connect()


def _eval_config(**overrides) -> LearningConfig:
    defaults = dict(
        candidate_eval_enabled=True,
        candidate_eval_paraphrase_tasks=1,
        candidate_eval_max_tasks=2,
        candidate_eval_runs_per_task=1,
        candidate_eval_max_steps=2,
        candidate_eval_max_attempts=2,
    )
    defaults.update(overrides)
    return LearningConfig(**defaults)


def _seed_skill(paths: RicePaths, name: str = "deploy-svc", body: str = "## Steps\n1. Deploy\n") -> SkillData:
    store = SkillStore(paths)
    skill = SkillData(
        name=name, version=1, description="Deploy the service", state="active",
        body=body, tags=["deploy"], source="session:s1",
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
    )
    store.write(skill)
    return skill


class _EvalFakeProvider:
    """세 호출 표면(과제 생성/롤아웃/심판)을 결정적으로 연기한다.

    롤아웃: 시스템 프롬프트에 후보 본문 고유 문구(marker)가 보이면 '좋은' 응답
    (_MARKER 포함)을, 아니면 밋밋한 응답을 낸다 — candidate_better=False 면 반대.
    심판: 전사에 _MARKER 가 있는 쪽에 더 높은 rubric 충족 수를 준다.
    이 페이크는 테스트 더블일 뿐이다 — 출하 하네스는 실제 provider 행동을 관찰한다.
    """

    name = "fake-eval"

    def __init__(
        self,
        *,
        candidate_marker: str,
        candidate_better: bool = True,
        fail_rollouts: bool = False,
    ) -> None:
        self._marker = candidate_marker
        self._better = candidate_better
        self._fail_rollouts = fail_rollouts
        self.rollout_systems: list[str] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(native_tool_calling=True)

    async def stream(
        self,
        *,
        system: str,
        messages,
        tools: Sequence = (),
        temperature: float = 0.7,
        response_schema: dict | None = None,
        think: bool | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        if system == TASK_GEN_SYSTEM_PROMPT:
            yield ProviderChunk(
                kind="text",
                text=json.dumps(
                    {
                        "paraphrases": ["please deploy the service once more"],
                        "rubric": ["states the deploy steps", "confirms completion"],
                    }
                ),
            )
            yield ProviderChunk(kind="done", stop_reason="end_turn")
            return
        if system == JUDGE_SYSTEM_PROMPT:
            user = messages[-1].content
            a_section = user.split("--- Assistant B transcript ---")[0]
            a_part = a_section.split("--- Assistant A transcript ---")[-1]
            b_part = user.split("--- Assistant B transcript ---")[-1]
            a_met = 2 if _MARKER in a_part else 1
            b_met = 2 if _MARKER in b_part else 1
            winner = "A" if a_met > b_met else "B" if b_met > a_met else "tie"
            yield ProviderChunk(
                kind="text",
                text=json.dumps(
                    {"a_criteria_met": a_met, "b_criteria_met": b_met, "winner": winner}
                ),
            )
            yield ProviderChunk(kind="done", stop_reason="end_turn")
            return
        # 롤아웃
        if self._fail_rollouts:
            raise RuntimeError("provider down")
        self.rollout_systems.append(system)
        is_candidate_arm = self._marker in system
        good = is_candidate_arm if self._better else not is_candidate_arm
        text = f"Deployed with checks. {_MARKER}" if good else "Deployed."
        yield ProviderChunk(kind="text", text=text)
        yield ProviderChunk(kind="done", stop_reason="end_turn")


def _service(paths: RicePaths, db: Database, provider, config: LearningConfig | None = None) -> LearningService:
    return LearningService(
        db,
        config or _eval_config(),
        paths,
        resolve_provider=(lambda: provider) if provider is not None else None,
    )


def _patch_proposal(svc_paths: RicePaths, skill_name: str = "deploy-svc", new_step: str = "2. Verify rollout CANDIDATE-STEP\n"):
    current = SkillStore(svc_paths).read(skill_name)
    proposal = make_patch_proposal(
        current=current,
        new_body=current.body + new_step,
        changelog="add verification step",
        trigger="T1a",
        seed_prompts=("서비스 배포해줘",),
    )
    assert proposal is not None
    return proposal


# ── 1·2: 격리 vs 기본 동작 ────────────────────────────────────────────────────

def test_approve_with_gate_stages_candidate_and_keeps_skill_untouched():
    paths, db = _fresh_paths(), _fresh_db()
    _seed_skill(paths)
    provider = _EvalFakeProvider(candidate_marker="CANDIDATE-STEP")
    svc = _service(paths, db, provider)
    proposal = _patch_proposal(paths)

    result = svc.resolve_proposal(proposal, session_id="s1", decision="approve")

    assert isinstance(result, CandidateStaged)
    assert result.skill_id == "deploy-svc" and result.baseline_version == 1
    # 스킬 파일 불변 — 격리가 곧 적용이 아니다.
    on_disk = SkillStore(paths).read("deploy-svc")
    assert on_disk.version == 1 and "CANDIDATE-STEP" not in on_disk.body
    cands = svc.list_candidates()
    assert len(cands) == 1 and cands[0]["status"] == "pending"
    assert cands[0]["payload"]["seed_prompts"] == ["서비스 배포해줘"]
    kinds = [e["kind"] for e in svc.learning_events(50)]
    assert "candidate_staged" in kinds and "approved" in kinds


def test_gate_disabled_by_default_applies_immediately():
    paths, db = _fresh_paths(), _fresh_db()
    _seed_skill(paths)
    provider = _EvalFakeProvider(candidate_marker="CANDIDATE-STEP")
    svc = _service(paths, db, provider, config=LearningConfig())  # 기본: 게이트 꺼짐

    version = svc.resolve_proposal(_patch_proposal(paths), session_id="s1", decision="approve")

    assert version == 2
    assert "CANDIDATE-STEP" in SkillStore(paths).read("deploy-svc").body
    assert svc.list_candidates() == []


# ── 3·4: 평가 → 승격/폐기 ────────────────────────────────────────────────────

async def test_eval_promotes_when_candidate_wins():
    paths, db = _fresh_paths(), _fresh_db()
    _seed_skill(paths)
    provider = _EvalFakeProvider(candidate_marker="CANDIDATE-STEP", candidate_better=True)
    svc = _service(paths, db, provider)
    staged = svc.resolve_proposal(_patch_proposal(paths), session_id="s1", decision="approve")

    cand = await svc.evaluate_candidate(staged.candidate_id)

    assert cand["status"] == "promoted"
    report = cand["report"]
    assert report["verdict"] == "promote" and report["version"] == 2
    assert report["candidate"]["success_rate"] > report["baseline"]["success_rate"]
    assert report["mode"] == "simulated_rollout"
    # held-out + paraphrase 두 축이 실제로 평가에 들어갔다.
    assert {t["kind"] for t in report["tasks"]} == {"held_out", "paraphrase"}
    on_disk = SkillStore(paths).read("deploy-svc")
    assert on_disk.version == 2 and "CANDIDATE-STEP" in on_disk.body
    assert "candidate_promoted" in [e["kind"] for e in svc.learning_events(50)]


async def test_eval_discards_on_regression_and_keeps_baseline():
    paths, db = _fresh_paths(), _fresh_db()
    _seed_skill(paths)
    provider = _EvalFakeProvider(candidate_marker="CANDIDATE-STEP", candidate_better=False)
    svc = _service(paths, db, provider)
    staged = svc.resolve_proposal(_patch_proposal(paths), session_id="s1", decision="approve")

    cand = await svc.evaluate_candidate(staged.candidate_id)

    assert cand["status"] == "discarded"
    assert "success_regression" in cand["report"]["reasons"]
    on_disk = SkillStore(paths).read("deploy-svc")
    assert on_disk.version == 1 and "CANDIDATE-STEP" not in on_disk.body
    assert "candidate_discarded" in [e["kind"] for e in svc.learning_events(50)]


# ── 5: 인프라 실패 = 재시도 계약 (판정 아님) ──────────────────────────────────

async def test_eval_infra_failure_retries_then_fails_and_manual_promote_works():
    paths, db = _fresh_paths(), _fresh_db()
    _seed_skill(paths)
    provider = _EvalFakeProvider(candidate_marker="CANDIDATE-STEP", fail_rollouts=True)
    svc = _service(paths, db, provider)
    staged = svc.resolve_proposal(_patch_proposal(paths), session_id="s1", decision="approve")

    cand = await svc.evaluate_candidate(staged.candidate_id)
    assert cand["status"] == "pending" and cand["attempts"] == 1  # 재시도 보존

    cand = await svc.evaluate_candidate(staged.candidate_id)
    assert cand["status"] == "failed"  # max_attempts=2 도달 — 후보는 남는다

    # 탈출구: 평가가 못 끝나도 사용자는 직접 승격할 수 있다(스킬은 사용자 소유).
    result = svc.decide_candidate(staged.candidate_id, approved=True)
    assert result["applied"] is True and result["version"] == 2
    assert SkillStore(paths).read("deploy-svc").version == 2


# ── 6: 격리 이후 스킬 변경 → expired ─────────────────────────────────────────

async def test_baseline_moved_after_staging_expires_candidate():
    paths, db = _fresh_paths(), _fresh_db()
    _seed_skill(paths)
    provider = _EvalFakeProvider(candidate_marker="CANDIDATE-STEP")
    svc = _service(paths, db, provider)
    staged = svc.resolve_proposal(_patch_proposal(paths), session_id="s1", decision="approve")

    # 사용자가 그 사이 직접 편집 — 버전이 움직인다.
    svc.update_skill("deploy-svc", body="## Steps\n1. Deploy differently\n")

    cand = await svc.evaluate_candidate(staged.candidate_id)
    assert cand["status"] == "expired"
    assert cand["report"]["error"] == "baseline_version_moved"
    # 사용자 편집본이 보존된다 — 오래된 후보가 덮어쓰지 않았다.
    assert "differently" in SkillStore(paths).read("deploy-svc").body


# ── 7: 신규 생성 후보 — baseline arm 은 스킬 없이 ────────────────────────────

async def test_creation_candidate_promotes_to_v1():
    paths, db = _fresh_paths(), _fresh_db()
    provider = _EvalFakeProvider(candidate_marker="NEW-SKILL-STEP")
    svc = _service(paths, db, provider)
    proposal = make_create_proposal(
        name="fresh-skill",
        body="## Steps\n1. NEW-SKILL-STEP do the thing\n",
        description="A brand new skill",
        changelog="create",
        seed_prompts=("그 작업 처리해줘",),
    )
    staged = svc.resolve_proposal(proposal, session_id="s1", decision="approve")
    assert isinstance(staged, CandidateStaged) and staged.baseline_version == 0
    with pytest.raises(Exception):
        SkillStore(paths).read("fresh-skill")  # 아직 디스크에 없다

    cand = await svc.evaluate_candidate(staged.candidate_id)

    assert cand["status"] == "promoted" and cand["report"]["version"] == 1
    assert SkillStore(paths).read("fresh-skill").version == 1
    # baseline arm 시스템 프롬프트에는 스킬 블록이 없었어야 한다(cold arm).
    assert any("NEW-SKILL-STEP" not in s for s in provider.rollout_systems)


# ── 8: 같은 스킬 미결 후보는 1개 ─────────────────────────────────────────────

def test_second_candidate_for_same_skill_conflicts():
    paths, db = _fresh_paths(), _fresh_db()
    _seed_skill(paths)
    provider = _EvalFakeProvider(candidate_marker="CANDIDATE-STEP")
    svc = _service(paths, db, provider)
    svc.resolve_proposal(_patch_proposal(paths), session_id="s1", decision="approve")

    with pytest.raises(ConflictError) as exc:
        svc.resolve_proposal(
            _patch_proposal(paths, new_step="2. Another step\n"),
            session_id="s1",
            decision="approve",
        )
    assert exc.value.code == "skill.candidate_pending"


# ── 9: 제안함 승인도 같은 게이트 ─────────────────────────────────────────────

def test_suggestion_approval_stages_candidate():
    paths, db = _fresh_paths(), _fresh_db()
    _seed_skill(paths)
    provider = _EvalFakeProvider(candidate_marker="CANDIDATE-STEP")
    svc = _service(paths, db, provider)
    proposal = _patch_proposal(paths)
    # 타임아웃 → 제안함으로 이동(기존 경로).
    svc.resolve_proposal(proposal, session_id="s1", decision="timeout")
    sug = svc.suggestions()[0]

    result = svc.decide_suggestion(sug["id"], approved=True)

    assert result["applied"] is False and result.get("staged") is True
    assert result["candidate_id"]
    assert SkillStore(paths).read("deploy-svc").version == 1  # 아직 불변
    assert svc.list_candidates()[0]["status"] == "pending"


# ── 10: 수동 폐기 ────────────────────────────────────────────────────────────

def test_manual_discard_keeps_skill_and_records_forced():
    paths, db = _fresh_paths(), _fresh_db()
    _seed_skill(paths)
    provider = _EvalFakeProvider(candidate_marker="CANDIDATE-STEP")
    svc = _service(paths, db, provider)
    staged = svc.resolve_proposal(_patch_proposal(paths), session_id="s1", decision="approve")

    result = svc.decide_candidate(staged.candidate_id, approved=False)

    assert result["applied"] is False and result["status"] == "discarded"
    assert SkillStore(paths).read("deploy-svc").version == 1
    cand = svc.list_candidates()[0]
    assert cand["report"]["forced"] is True


# ── 부팅 복구: running 고아 → pending ────────────────────────────────────────

def test_boot_recovery_requeues_orphan_running_candidate():
    paths, db = _fresh_paths(), _fresh_db()
    _seed_skill(paths)
    provider = _EvalFakeProvider(candidate_marker="CANDIDATE-STEP")
    svc = _service(paths, db, provider)
    staged = svc.resolve_proposal(_patch_proposal(paths), session_id="s1", decision="approve")
    # 평가 중 crash 를 흉내: pending → running 으로만 옮기고 끝낸다.
    db.execute(
        "UPDATE skill_candidates SET status = 'running' WHERE id = ?",
        (staged.candidate_id,),
    )

    requeued = svc.recover_boot_state()

    assert requeued >= 1
    cand = svc.list_candidates()[0]
    assert cand["status"] == "pending"
