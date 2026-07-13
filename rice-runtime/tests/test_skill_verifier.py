"""test_skill_verifier.py — 스킬 승격 검증 harness (2026-07-07 적대적 리뷰 P1).

기존 승격 경로는 guard(이름/크기/시크릿/위험 패턴)만 있는 proposal gate였다 —
HITL 승인은 '그럴듯하지만 틀린 스킬'(잘린 본문, 존재하지 않는 도구 호출 지시,
기존 스킬 재발명)을 막지 못한다. 이 파일이 고정하는 계약:

  결정론 lint 사다리  — 객관적 무결성 위반(fail)만 차단, 판단 항목(warn)은
                        승인 카드 warnings로 표시 (LLM 재채점 없음 — 20B 바닥
                        사양에서 배보다 배꼽, 시멘틱 기각과 동일 원칙)
  3중 적용 지점       — ① 미드턴 stage_proposal: fail 사유가 도구 결과로
                        모델에 돌아가 같은 턴에 고쳐 재제안(피드백 루프)
                        ② _screen_proposals: review/learn/sweep/discovery 의
                        LLM 제안 전부 스크리닝
                        ③ apply_patch/create_skill/update_skill: 최종 관문 —
                        어떤 경로로 왔든 fail 본문은 디스크에 닿지 못한다
  quarantine          — fail 은 증발하지 않고 ledger `verifier_rejected` 로
                        findings 와 함께 영속(감사 가능)
"""

from __future__ import annotations

import tempfile

from rice_runtime.core.config import LearningConfig
from rice_runtime.core.errors import ValidationError
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.learning.proposals import make_create_proposal
from rice_runtime.learning.service import LearningService
from rice_runtime.learning.skill.verifier import verify_candidate

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _fresh_paths() -> RicePaths:
    return RicePaths.from_env(tempfile.mkdtemp(prefix="rice-verifier-test-")).ensure()


def _service(**kwargs) -> LearningService:
    return LearningService(Database(":memory:").connect(), LearningConfig(), _fresh_paths(), **kwargs)


_GOOD_BODY = "## Steps\n1. gather the inputs\n2. produce the file\n3. verify with read_file\n"


def _checks(report) -> set[str]:
    return {f.check for f in report.findings}


# ── unit: verify_candidate 사다리 ──────────────────────────────────────────────

class TestVerifyCandidate:
    def test_clean_procedural_body_passes(self):
        report = verify_candidate(
            name="deploy-checklist", body=_GOOD_BODY, description="Deployment checklist steps"
        )
        assert report.verdict == "pass" and not report.findings

    def test_unbalanced_code_fence_fails(self):
        """절단된 본문(코드 펜스 미폐합) = 객관적 무결성 위반 — 차단."""
        report = verify_candidate(name="sk", body="1. run this:\n```bash\necho hi\n", description="d" * 10)
        assert report.failed and "unbalanced-fence" in _checks(report)

    def test_frontmatter_in_body_fails(self):
        """모델이 body 인자에 SKILL.md 전체를 붙여넣은 경우(이중 frontmatter)."""
        report = verify_candidate(
            name="sk", body="---\nname: sk\nversion: 1\n---\n1. step", description="d" * 10
        )
        assert report.failed and "frontmatter-in-body" in _checks(report)

    def test_effectively_empty_body_fails(self):
        report = verify_candidate(name="sk", body="  x \n", description="d" * 10)
        assert report.failed and "empty-body" in _checks(report)

    def test_quality_signals_warn_not_fail(self):
        """판단 항목은 warn — 카드에서 사용자가 보고 결정한다."""
        report = verify_candidate(
            name="sk",
            body="TODO: 이 절차는 항상 통하는 방식이며 단계 구분 없이 서술한다고 가정하고,",
            description="",  # 생성인데 설명 없음
        )
        assert not report.failed
        assert {"no-steps", "placeholder", "dangling-ending", "no-description"} <= _checks(report)
        assert all(line.startswith("검증 경고:") for line in report.warning_lines())

    def test_unknown_tool_reference_warns_with_catalog(self):
        """'호출하라'고 지시된 도구가 카탈로그에 없으면 fabrication 신호 — warn
        (카탈로그는 동적이므로 차단하지 않는다)."""
        body = "1. `create_presentaton` 도구를 호출한다\n2. verify"
        report = verify_candidate(
            name="sk", body=body, description="d" * 10, known_tool_names={"create_presentation"}
        )
        assert "unknown-tool" in _checks(report) and not report.failed
        assert any("create_presentaton" in line for line in report.warning_lines())

    def test_known_tool_reference_is_clean(self):
        body = "1. call `create_presentation` with the outline\n2. verify"
        report = verify_candidate(
            name="sk", body=body, description="d" * 10, known_tool_names={"create_presentation"}
        )
        assert "unknown-tool" not in _checks(report)

    def test_tool_check_skipped_without_catalog(self):
        """known_tool_names=None(배선 없음) → 검사 자체를 스킵(오탐 금지)."""
        body = "1. `ghost_tool` 도구를 호출한다\n2. done"
        report = verify_candidate(name="sk", body=body, description="d" * 10, known_tool_names=None)
        assert "unknown-tool" not in _checks(report)

    def test_parameter_backticks_are_not_flagged(self):
        """호출 문맥이 아닌 백틱(파라미터/변수 언급)은 건드리지 않는다."""
        body = "1. call `create_presentation`, passing a valid `output_path` param\n2. verify"
        report = verify_candidate(
            name="sk", body=body, description="d" * 10, known_tool_names={"create_presentation"}
        )
        assert "unknown-tool" not in _checks(report)

    def test_near_duplicate_name_warns_on_creation(self):
        report = verify_candidate(
            name="deploy-web-checklist", body=_GOOD_BODY, description="d" * 10,
            existing_names=["deploy-checklist-web"],
        )
        assert "near-duplicate" in _checks(report)

    def test_patch_shrink_warns(self):
        report = verify_candidate(
            name="sk", body="1. one step left\n", description="d" * 10,
            current_body="x" * 900,
        )
        assert "patch-shrink" in _checks(report) and not report.failed

    def test_patch_of_same_scale_no_shrink_warning(self):
        report = verify_candidate(
            name="sk", body=_GOOD_BODY, description="d" * 10, current_body=_GOOD_BODY + "4. extra\n"
        )
        assert "patch-shrink" not in _checks(report)


# ── integration ① 미드턴 스테이징: 모델 피드백 루프 ────────────────────────────

class TestStageProposalHarness:
    def test_fail_rejects_staging_with_actionable_feedback(self):
        svc = _service()
        res = svc.stage_proposal("s1", name="broken-sk", body="1. run:\n```bash\necho", description="d" * 10)
        assert res["status"] == "rejected"
        assert "다시 propose_skill" in res["message"]  # 같은 턴 재제안 유도
        assert svc._staged.get("s1", []) == []  # 큐에 닿지 않았다
        # quarantine: ledger에 findings와 함께 영속
        events = svc.learning_events()
        rejected = [e for e in events if e["kind"] == "verifier_rejected"]
        assert rejected and rejected[0]["skill_id"] == "broken-sk"

    def test_warn_stages_with_card_warnings(self):
        svc = _service(resolve_tool_names=lambda: {"create_presentation"})
        res = svc.stage_proposal(
            "s1", name="typo-sk",
            body="1. `create_presentaton` 도구를 호출한다\n2. verify the deck\n",
            description="presentation build steps",
        )
        assert res["status"] == "staged"
        staged = svc._staged["s1"][0]
        assert any("create_presentaton" in w for w in staged.warnings)

    def test_resolver_failure_does_not_break_staging(self):
        def _boom() -> set[str]:
            raise RuntimeError("catalog down")

        svc = _service(resolve_tool_names=_boom)
        res = svc.stage_proposal("s1", name="ok-sk", body=_GOOD_BODY, description="d" * 10)
        assert res["status"] == "staged"


# ── integration ② LLM 제안 스크리닝 (review/learn/sweep/discovery 공용) ────────

class TestScreenProposals:
    def test_failing_proposal_is_dropped_and_quarantined(self):
        svc = _service()
        proposal = make_create_proposal(
            name="fence-sk", body="1. run\n```py\nbroken", description="d" * 10
        )
        assert proposal is not None  # guard(기존 gate)는 이걸 통과시킨다 — 그게 P1의 요점
        kept = svc._screen_proposals([proposal], origin="review", session_id="s9")
        assert kept == []
        rejected = [e for e in svc.learning_events() if e["kind"] == "verifier_rejected"]
        assert rejected and rejected[0]["origin"] == "review"

    def test_warning_proposal_passes_with_merged_warnings(self):
        svc = _service()
        proposal = make_create_proposal(name="thin-sk", body="1. do the thing quickly", description="")
        assert proposal is not None
        kept = svc._screen_proposals([proposal], origin="learn")
        assert len(kept) == 1
        assert any(w.startswith("검증 경고:") for w in kept[0].warnings)


# ── integration ③ 최종 관문: 어떤 경로든 fail 본문은 디스크에 닿지 못한다 ──────

class TestFinalGate:
    def test_apply_patch_creation_fail_closed(self):
        svc = _service()
        with pytest.raises(ValidationError) as exc_info:
            svc.apply_patch("gate-sk", approved=True, proposed_body="1. run\n```sh\ntrunc")
        assert exc_info.value.code == "skill.validation_failed"
        assert svc.get_skill("gate-sk") is None

    def test_apply_patch_user_edited_body_also_gated(self):
        """카드에서 사용자가 직접 고친 본문(edited_body)이 무결성을 깨면 역시 차단."""
        svc = _service()
        svc.create_skill(name="edit-sk", body=_GOOD_BODY)
        with pytest.raises(ValidationError) as exc_info:
            svc.apply_patch("edit-sk", approved=True, edited_body="---\nname: edit-sk\n---\n1. pasted whole file")
        assert exc_info.value.code == "skill.validation_failed"

    def test_update_skill_fail_closed(self):
        svc = _service()
        svc.create_skill(name="upd-sk", body=_GOOD_BODY)
        with pytest.raises(ValidationError) as exc_info:
            svc.update_skill("upd-sk", body="```\nbroken fence body")
        assert exc_info.value.code == "skill.validation_failed"
        assert "gather the inputs" in svc.get_skill("upd-sk").body  # 원본 무손상

    def test_create_skill_fail_closed(self):
        svc = _service()
        with pytest.raises(ValidationError) as exc_info:
            svc.create_skill(name="new-sk", body="x")
        assert exc_info.value.code == "skill.validation_failed"
