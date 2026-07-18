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
from rice_runtime.core.types import Message, ToolCall, TurnOutcome
from rice_runtime.learning.interfaces import CandidateStaged, PatchProposal
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
            yield ProviderChunk(kind="text", text=json.dumps(self._judge(a_part, b_part)))
            yield ProviderChunk(kind="done", stop_reason="end_turn")
            return
        # 롤아웃
        if self._fail_rollouts:
            raise RuntimeError("provider down")
        self.rollout_systems.append(system)
        for chunk in self._rollout(system, list(messages)):
            yield chunk

    # 서브클래스 오버라이드 지점 — 심판/롤아웃 행동만 바꿔서 재사용한다.
    def _judge(self, a_part: str, b_part: str) -> dict:
        a_met = 2 if _MARKER in a_part else 1
        b_met = 2 if _MARKER in b_part else 1
        winner = "A" if a_met > b_met else "B" if b_met > a_met else "tie"
        return {"a_criteria_met": a_met, "b_criteria_met": b_met, "winner": winner}

    def _rollout(self, system: str, messages: list) -> list[ProviderChunk]:
        is_candidate_arm = self._marker in system
        good = is_candidate_arm if self._better else not is_candidate_arm
        text = f"Deployed with checks. {_MARKER}" if good else "Deployed."
        return [
            ProviderChunk(kind="text", text=text),
            ProviderChunk(kind="done", stop_reason="end_turn"),
        ]


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


# ═══════════════════════════════════════════════════════════════════════════
# 커버리지·증거력 보강 (EVAL §10.2-10.3): trace-grounded replay + 심판 일관성
# ═══════════════════════════════════════════════════════════════════════════

_FAKE_SECRET = "sk-" + "a1b2c3d4e5" * 3


def _turn_history_with_tool(result_text: str) -> tuple[Message, ...]:
    call = ToolCall(tool_name="check_pods", arguments={"ns": "prod", "token": _FAKE_SECRET})
    return (
        Message(role="user", content="배포가 왜 실패하는지 봐줘"),
        Message(role="assistant", content="확인합니다.", tool_calls=(call,)),
        Message(
            role="tool",
            content=result_text,
            metadata={"tool_call_id": call.id, "tool_name": "check_pods", "ok": False},
        ),
        Message(role="assistant", content="원인을 찾았습니다."),
    )


def test_transcript_is_scrubbed_on_proposal():
    """전사의 인자/결과에 섞인 시크릿은 proposal에 실리기 전에 마스킹된다."""
    proposal = make_patch_proposal(
        current=SkillData(
            name="deploy-svc", version=1, description="d", state="active",
            body="## Steps\n1. Deploy\n", tags=[], source=None,
            created_at=None, updated_at=None,
        ),
        new_body="## Steps\n1. Deploy\n2. Verify\n",
        changelog="add step",
        trigger="T1a",
        tool_transcript=(
            {
                "name": "check_pods",
                "arguments": {"ns": "prod", "token": _FAKE_SECRET},
                "ok": False,
                "result": f"auth with {_FAKE_SECRET} failed",
            },
        ),
    )
    assert proposal is not None and len(proposal.tool_transcript) == 1
    record = proposal.tool_transcript[0]
    assert _FAKE_SECRET not in record["arguments"]
    assert _FAKE_SECRET not in record["result"]
    assert record["name"] == "check_pods" and record["ok"] is False


async def test_observe_persists_tool_transcript_in_review_job_evidence():
    """턴 관찰이 도구 전사를 리뷰 잡 evidence에 남기고, 병합 시 이어붙인다
    (LearningEpisode 발판 — §10.1 watermark의 순수 추가 필드)."""
    paths, db = _fresh_paths(), _fresh_db()
    svc = _service(paths, db, None, config=LearningConfig())

    async def _observe(result_text: str):
        history = _turn_history_with_tool(result_text)
        await svc.observe(
            TurnOutcome(
                session_id="sess-t", final_message="원인을 찾았습니다.",
                history=history, tool_calls_made=1, injected_skill_ids=(),
            )
        )

    await _observe("pod status: ImagePullBackOff")
    await _observe("secret created; pod Running")

    import json as _json
    row = db.query_one(
        "SELECT evidence FROM learning_jobs WHERE session_id = 'sess-t' AND status = 'pending'"
    )
    evidence = _json.loads(row["evidence"])
    transcript = evidence["tool_transcript"]
    assert len(transcript) == 2  # 두 턴 병합
    assert transcript[0]["result"].startswith("pod status")
    assert transcript[1]["result"].startswith("secret created")
    assert all(_FAKE_SECRET not in r["arguments"] for r in transcript)


async def test_t5_staged_proposal_gets_turn_transcript():
    """미드턴 스테이징(T5) 제안도 턴 종료 드레인에서 그 턴의 전사를 얻는다."""
    paths, db = _fresh_paths(), _fresh_db()
    svc = _service(paths, db, None, config=LearningConfig())
    out = svc.stage_proposal(
        "sess-t5", name="debug-pods", body="## Steps\n1. Run `check_pods` first\n",
        description="pod debugging",
    )
    assert out["status"] == "staged"
    effects = await svc.observe(
        TurnOutcome(
            session_id="sess-t5", final_message="done",
            history=_turn_history_with_tool("pod status: ImagePullBackOff"),
            tool_calls_made=1, injected_skill_ids=(),
        )
    )
    staged = [e for e in effects if isinstance(e, PatchProposal)]
    assert staged and staged[0].tool_transcript
    assert staged[0].tool_transcript[0]["name"] == "check_pods"


class _BranchingFakeProvider(_EvalFakeProvider):
    """재생된 도구 결과 '내용'에 따라 답이 갈리는 provider — 이전 중립 시뮬레이션
    에서는 관찰 불가능했던 진단→대응 축을 검증한다."""

    def _rollout(self, system: str, messages: list) -> list[ProviderChunk]:
        tool_msgs = [m for m in messages if m.role == "tool"]
        if self._marker in system and not tool_msgs:
            # 후보 스킬 절차: 먼저 check_pods 로 진단하라.
            return [
                ProviderChunk(
                    kind="tool_call",
                    tool_call=ToolCall(tool_name="check_pods", arguments={"ns": "prod"}),
                ),
                ProviderChunk(kind="done", stop_reason="tool_use"),
            ]
        if tool_msgs and "ImagePullBackOff" in tool_msgs[-1].content:
            # 재생된 실제 결과를 읽고 올바른 대응을 했다 — 이것이 grounded 분기.
            return [
                ProviderChunk(kind="text", text=f"Image pull secret을 재생성하세요. {_MARKER}"),
                ProviderChunk(kind="done", stop_reason="end_turn"),
            ]
        return [
            ProviderChunk(kind="text", text="Deployed."),
            ProviderChunk(kind="done", stop_reason="end_turn"),
        ]


async def test_replay_grounds_tool_results_and_enables_branching_axis():
    """전사 재생: 후보 arm이 기록된 실제 도구 결과를 받아 분기 대응 → 승격.
    리포트 evidence에 grounded 비율이 남는다."""
    paths, db = _fresh_paths(), _fresh_db()
    _seed_skill(paths)
    provider = _BranchingFakeProvider(candidate_marker="CANDIDATE-STEP")
    svc = _service(paths, db, provider)
    current = SkillStore(paths).read("deploy-svc")
    proposal = make_patch_proposal(
        current=current,
        new_body=current.body + "2. CANDIDATE-STEP: run `check_pods` and act on its output\n",
        changelog="add diagnosis step",
        trigger="T1a",
        seed_prompts=("배포가 왜 실패하는지 봐줘",),
        tool_transcript=(
            {
                "name": "check_pods",
                "arguments": {"ns": "prod"},
                "ok": False,
                "result": "pod status: ImagePullBackOff — secret missing",
            },
        ),
    )
    assert proposal is not None
    staged = svc.resolve_proposal(proposal, session_id="s1", decision="approve")
    cand = svc.list_candidates()[0]
    assert cand["payload"]["tool_transcript"]  # 격리 payload에 전사가 실렸다

    result = await svc.evaluate_candidate(staged.candidate_id)

    assert result["status"] == "promoted"
    evidence = result["report"]["evidence"]
    assert evidence["grounded_tool_result_rate"] == 1.0  # 모든 호출이 재생으로 답함
    assert evidence["tool_calls_observed"] > 0
    assert evidence["judge_agreement_rate"] == 1.0  # 대칭 심판이라 전 방향 합의


class _PositionBiasedJudgeProvider(_EvalFakeProvider):
    """항상 A가 이긴다고 답하는 심판(위치 편향의 극단) — 양방향 일관성 검사가
    이 노이즈를 tie로 접어 가짜 회귀/가짜 승리를 모두 막는지 검증한다."""

    def _judge(self, a_part: str, b_part: str) -> dict:
        return {"a_criteria_met": 2, "b_criteria_met": 1, "winner": "A"}

    def _rollout(self, system: str, messages: list) -> list[ProviderChunk]:
        return [
            ProviderChunk(kind="text", text="Deployed."),
            ProviderChunk(kind="done", stop_reason="end_turn"),
        ]


async def test_position_biased_judge_collapses_to_tie_and_passes():
    """편향 심판의 판정은 방향에 따라 뒤집힌다 → 전 pairing 불일치 → tie.
    tie는 회귀가 아니므로 통과(회귀 방지 게이트), 합의율 0.0이 리포트에 남는다."""
    paths, db = _fresh_paths(), _fresh_db()
    _seed_skill(paths)
    provider = _PositionBiasedJudgeProvider(candidate_marker="CANDIDATE-STEP")
    svc = _service(paths, db, provider)
    staged = svc.resolve_proposal(_patch_proposal(paths), session_id="s1", decision="approve")

    result = await svc.evaluate_candidate(staged.candidate_id)

    assert result["status"] == "promoted"  # 전부 tie → 회귀 없음
    report = result["report"]
    assert report["reasons"] == []
    assert report["evidence"]["judge_agreement_rate"] == 0.0
    assert all(t["winner"] == "tie" for t in report["per_task"])


# ═══════════════════════════════════════════════════════════════════════════
# LearningEpisode 독립 엔티티 (§10.5 완결): 턴 판정 + 전사 + 인과 링크
# ═══════════════════════════════════════════════════════════════════════════


async def test_episode_recorded_per_turn_with_judgment_and_link():
    """턴 1개 = 에피소드 1행. 판정(success/corrected)과 전사가 남고, 리뷰 잡
    evidence의 episode_ids가 에피소드를 가리킨다(인과 링크)."""
    paths, db = _fresh_paths(), _fresh_db()
    svc = _service(paths, db, None, config=LearningConfig())

    # 정상 완주 턴 (신호 단어 없는 중립 발화 — "실패" 같은 단어가 있으면
    # 판정이 정확하게 failure 로 나온다)
    call = ToolCall(tool_name="check_pods", arguments={"ns": "prod"})
    await svc.observe(
        TurnOutcome(
            session_id="sess-ep", final_message="완료했습니다.",
            history=(
                Message(role="user", content="pod 상태 확인해줘"),
                Message(role="assistant", content="확인합니다.", tool_calls=(call,)),
                Message(
                    role="tool", content="pod status: Running",
                    metadata={"tool_call_id": call.id, "tool_name": "check_pods", "ok": True},
                ),
                Message(role="assistant", content="완료했습니다."),
            ),
            tool_calls_made=1, injected_skill_ids=(),
        )
    )
    # 교정 신호 턴 ("그게 아니라" — signals.py 교정 패턴)
    await svc.observe(
        TurnOutcome(
            session_id="sess-ep", final_message="다시 수정했습니다.",
            history=(
                Message(role="user", content="그게 아니라 프로덕션 네임스페이스로 해야지"),
                Message(role="assistant", content="다시 수정했습니다."),
            ),
            tool_calls_made=0, injected_skill_ids=(),
        )
    )

    episodes = svc.list_episodes(10)
    assert len(episodes) == 2
    outcomes = {e["outcome"] for e in episodes}
    assert outcomes == {"success", "corrected"}
    success_ep = next(e for e in episodes if e["outcome"] == "success")
    assert success_ep["episode"]["tool_transcript"][0]["name"] == "check_pods"
    assert success_ep["judgment"]["correction_signal"] is False
    corrected_ep = next(e for e in episodes if e["outcome"] == "corrected")
    assert corrected_ep["judgment"]["correction_signal"] is True

    # 리뷰 잡 evidence → episode 인과 링크 (두 턴 병합 = id 2개 합집합)
    import json as _json
    row = db.query_one(
        "SELECT evidence FROM learning_jobs WHERE session_id = 'sess-ep' AND status = 'pending'"
    )
    evidence = _json.loads(row["evidence"])
    linked = set(evidence["episode_ids"])
    assert linked == {e["id"] for e in episodes}


async def test_aborted_turn_records_episode_but_no_review_job():
    """중단 턴도 에피소드(outcome=aborted)는 남는다 — 단 리뷰 잡/제안은 기존
    fail-closed 그대로 만들지 않는다."""
    paths, db = _fresh_paths(), _fresh_db()
    svc = _service(paths, db, None, config=LearningConfig())
    await svc.observe(
        TurnOutcome(
            session_id="sess-ab", final_message="",
            history=_turn_history_with_tool("pod status: Pending"),
            tool_calls_made=1, injected_skill_ids=(), aborted=True,
        )
    )
    episodes = svc.list_episodes(10, "sess-ab")
    assert len(episodes) == 1 and episodes[0]["outcome"] == "aborted"
    # 중단 전 실행된 도구 활동은 관찰로 보존된다.
    assert episodes[0]["episode"]["tool_transcript"]
    row = db.query_one("SELECT id FROM learning_jobs WHERE session_id = 'sess-ab'")
    assert row is None


async def test_episode_cap_prunes_oldest():
    """보존 상한: cap을 넘으면 오래된 에피소드부터 잘린다."""
    paths, db = _fresh_paths(), _fresh_db()
    svc = _service(paths, db, None, config=LearningConfig(learning_episode_cap=3))
    for i in range(5):
        await svc.observe(
            TurnOutcome(
                session_id="sess-cap", final_message=f"done {i}",
                history=(
                    Message(role="user", content=f"작업 {i} 해줘"),
                    Message(role="assistant", content=f"done {i}"),
                ),
                tool_calls_made=0, injected_skill_ids=(),
            )
        )
    episodes = svc.list_episodes(50, "sess-cap")
    assert len(episodes) == 3
    kept = {e["judgment"]["user_text_excerpt"] for e in episodes}
    assert kept == {"작업 2 해줘", "작업 3 해줘", "작업 4 해줘"}


async def test_episode_cap_zero_disables_recording():
    paths, db = _fresh_paths(), _fresh_db()
    svc = _service(paths, db, None, config=LearningConfig(learning_episode_cap=0))
    await svc.observe(
        TurnOutcome(
            session_id="sess-off", final_message="done",
            history=(
                Message(role="user", content="해줘"),
                Message(role="assistant", content="done"),
            ),
            tool_calls_made=0, injected_skill_ids=(),
        )
    )
    assert svc.list_episodes(10) == []
