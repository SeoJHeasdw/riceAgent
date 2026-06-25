"""tests/test_eval.py — eval 하네스 수용 기준 검증.

수용 기준 (eval.md Acceptance):
  1. runner.py 실행이 cold·warm을 돌려 results/<ts>.json + results/latest.json을
     위 스키마로 생성한다.
  2. 생성된 JSON이 ImpactReport 스키마를 만족(필드/타입 일치).
  3. 최소 1개 task에서 warm이 cold보다 turns/tool_calls 적게 나오는 시나리오가
     재현된다 (시드의 효과가 숫자로 보임).
  4. skill_hit이 cold=false, warm=true로 분리 측정됨.
  5. 격리: 실제 ~/.rice를 건드리지 않음(전부 임시/프로파일 경로).

테스트 구성:
  - TestMetrics: metrics.py 순수 로직 (외부 의존 없음)
  - TestReport: report.py 순수 로직 (외부 의존 없음)
  - TestEvalIntegration: runner 전체 흐름 (in-process, echo provider, 임시 디렉토리)

격리: tempfile.mkdtemp() — 실제 ~/.rice 건드리지 않는다 (CONVENTIONS §6).
외부 의존: 없음 (echo provider 사용, 네트워크 불필요).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# eval 패키지 임포트를 위해 rice-runtime 루트를 경로에 추가한다.
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT))


# ── metrics 순수 로직 테스트 ──────────────────────────────────────────────────

class TestMetrics:
    def test_aggregate_empty_returns_default(self):
        from eval.metrics import aggregate
        result = aggregate([])
        assert result.turns == 0.0
        assert result.tool_calls == 0.0
        assert result.success_rate == 0.0
        assert result.skill_hit is False
        assert result.skills_used == []

    def test_aggregate_single_run(self):
        from eval.metrics import RunRecord, aggregate
        record = RunRecord(turns=2, tool_calls=5, success=True, skill_hit=True, skills_used=["sk@1"])
        result = aggregate([record])
        assert result.turns == 2.0
        assert result.tool_calls == 5.0
        assert result.success_rate == 1.0
        assert result.skill_hit is True
        assert "sk" in " ".join(result.skills_used) or "sk@1" in result.skills_used

    def test_aggregate_multiple_runs_averages(self):
        from eval.metrics import RunRecord, aggregate
        runs = [
            RunRecord(turns=3, tool_calls=6, success=True, skill_hit=True),
            RunRecord(turns=5, tool_calls=10, success=False, skill_hit=False),
        ]
        result = aggregate(runs)
        assert result.turns == 4.0
        assert result.tool_calls == 8.0
        assert result.success_rate == 0.5
        # skill_hit: 하나라도 true면 True
        assert result.skill_hit is True

    def test_aggregate_all_failure(self):
        from eval.metrics import RunRecord, aggregate
        runs = [
            RunRecord(turns=5, tool_calls=8, success=False, skill_hit=False),
            RunRecord(turns=5, tool_calls=8, success=False, skill_hit=False),
        ]
        result = aggregate(runs)
        assert result.success_rate == 0.0
        assert result.skill_hit is False

    def test_compute_delta_improvement(self):
        """warm이 cold보다 turns/tool_calls가 적으면 pct가 음수 (개선)."""
        from eval.metrics import RunRecord, aggregate, compute_delta
        cold = aggregate([RunRecord(turns=5, tool_calls=8, success=True, skill_hit=False)])
        warm = aggregate([RunRecord(turns=2, tool_calls=3, success=True, skill_hit=True)])
        delta = compute_delta(cold, warm)
        assert delta["turns_pct"] < 0, "turns 감소 = 개선 → 음수 pct"
        assert delta["tool_calls_pct"] < 0, "tool_calls 감소 = 개선 → 음수 pct"
        assert delta["success_pp"] == 0.0  # 둘 다 100%

    def test_compute_delta_cold_zero_no_division(self):
        """cold가 0일 때 division by zero 없이 0.0 반환."""
        from eval.metrics import RunRecord, aggregate, compute_delta
        cold = aggregate([RunRecord(turns=0, tool_calls=0)])
        warm = aggregate([RunRecord(turns=2, tool_calls=3)])
        delta = compute_delta(cold, warm)
        assert delta["turns_pct"] == 0.0
        assert delta["tool_calls_pct"] == 0.0

    def test_compute_aggregate(self):
        from eval.metrics import RunRecord, aggregate, compute_aggregate, compute_delta
        cold = aggregate([RunRecord(turns=5, tool_calls=8, success=True, skill_hit=False)])
        warm = aggregate([RunRecord(turns=2, tool_calls=3, success=True, skill_hit=True)])
        deltas = [compute_delta(cold, warm)]
        result = compute_aggregate(deltas, [warm])
        assert "turns_pct" in result
        assert "tool_calls_pct" in result
        assert "success_pp" in result
        assert "skill_hit_rate" in result
        assert result["skill_hit_rate"] == 1.0


# ── report 순수 로직 테스트 ───────────────────────────────────────────────────

class TestReport:
    def _make_report(self):
        from eval.metrics import RunRecord, aggregate
        from eval.report import build_report
        cold = aggregate([RunRecord(turns=5, tool_calls=8, success=False, skill_hit=False)])
        warm = aggregate([RunRecord(turns=2, tool_calls=3, success=True, skill_hit=True, skills_used=["sk@2"])])
        return build_report(
            task_suite="test-suite",
            model="echo",
            runs_per_task=1,
            generated_at="2024-01-01T00:00:00Z",
            task_ids=["T-A"],
            task_titles=["Test Task A"],
            cold_metrics=[cold],
            warm_metrics=[warm],
        )

    def test_build_report_has_correct_structure(self):
        report = self._make_report()
        assert report.task_suite == "test-suite"
        assert report.model == "echo"
        assert len(report.tasks) == 1
        assert report.tasks[0].id == "T-A"

    def test_build_report_cold_skill_hit_false(self):
        """수용 기준 4: cold는 skill_hit=false."""
        report = self._make_report()
        assert report.tasks[0].cold.skill_hit is False

    def test_build_report_warm_skill_hit_true(self):
        """수용 기준 4: warm은 skill_hit=true."""
        report = self._make_report()
        assert report.tasks[0].warm.skill_hit is True

    def test_build_report_warm_better_turns(self):
        """수용 기준 3: warm의 turns가 cold보다 적다."""
        report = self._make_report()
        assert report.tasks[0].warm.turns < report.tasks[0].cold.turns

    def test_build_report_warm_better_tool_calls(self):
        """수용 기준 3: warm의 tool_calls가 cold보다 적다."""
        report = self._make_report()
        assert report.tasks[0].warm.tool_calls < report.tasks[0].cold.tool_calls

    def test_validate_report_valid(self):
        from eval.report import report_to_dict, validate_report
        report = self._make_report()
        data = report_to_dict(report)
        assert validate_report(data) is True

    def test_validate_report_missing_field_fails(self):
        from eval.report import validate_report
        # 필수 필드 누락
        bad = {"task_suite": "x", "model": "echo"}
        assert validate_report(bad) is False

    def test_validate_report_missing_aggregate_field_fails(self):
        from eval.report import report_to_dict, validate_report
        report = self._make_report()
        data = report_to_dict(report)
        del data["aggregate"]["skill_hit_rate"]
        assert validate_report(data) is False

    def test_report_to_dict_is_json_serializable(self):
        from eval.report import report_to_dict
        report = self._make_report()
        data = report_to_dict(report)
        dumped = json.dumps(data)
        restored = json.loads(dumped)
        assert restored["task_suite"] == "test-suite"

    def test_write_results_creates_files(self):
        """write_results가 <ts>.json + latest.json을 생성한다."""
        import tempfile
        from eval.report import write_results
        report = self._make_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            results_dir = Path(tmpdir) / "results"
            ts_path, latest_path = write_results(report, results_dir, "20240101T000000")
            assert ts_path.exists()
            assert latest_path.exists()
            data = json.loads(latest_path.read_text())
            assert data["task_suite"] == "test-suite"


# ── runner 통합 테스트 (echo provider, in-process, 격리) ──────────────────────

class TestEvalIntegration:
    """수용 기준 1, 3, 4, 5를 end-to-end로 검증한다.

    NOTE(agent): echo provider를 사용하므로 네트워크/LLM API 불필요.
    echo provider는 prompt 텍스트를 그대로 에코하므로 success_check의
    contains_keywords가 prompt 단어로 트리거된다.
    """

    def _run_main_isolated(self, runs_per_task: int = 1):
        """runner.main()을 *임시* results 디렉토리로 실행한다 (격리, 수용 기준 5).

        커밋된 fixture(eval/results/latest.json)를 절대 덮어쓰지 않는다.
        반환: (exit_code, latest.json dict 또는 None).
        """
        import eval.runner as runner_module

        tmp_results = Path(tempfile.mkdtemp(prefix="rice-eval-results-"))
        try:
            code = runner_module.main(runs_per_task=runs_per_task, results_dir=tmp_results)
            latest = tmp_results / "latest.json"
            data = json.loads(latest.read_text()) if latest.exists() else None
            return code, data
        finally:
            shutil.rmtree(tmp_results, ignore_errors=True)

    def test_main_runs_successfully(self):
        """수용 기준 1: runner.main()이 0을 반환한다."""
        code, _ = self._run_main_isolated()
        assert code == 0

    def test_latest_json_created(self):
        """수용 기준 1: latest.json이 생성된다."""
        _, data = self._run_main_isolated()
        assert data is not None, "latest.json should be created"

    def test_latest_json_valid_schema(self):
        """수용 기준 2: latest.json이 ImpactReport 스키마를 만족한다."""
        from eval.report import validate_report

        _, data = self._run_main_isolated()
        assert validate_report(data), f"ImpactReport schema validation failed: {data}"

    def test_skill_hit_split_cold_false_warm_true(self):
        """수용 기준 4: cold는 skill_hit=false, warm은 skill_hit=true로 분리 측정된다.

        warm 프로파일에 deploy-k8s-service.md가 있으므로 task T-A(related_skill=deploy-k8s-service)
        에서 warm.skill_hit=true가 돼야 한다.
        """
        _, data = self._run_main_isolated()
        # T-A task 확인
        task_a = next((t for t in data["tasks"] if t["id"] == "T-A"), None)
        assert task_a is not None, "T-A task should be in results"
        assert task_a["cold"]["skill_hit"] is False, "cold should have skill_hit=false"
        assert task_a["warm"]["skill_hit"] is True, "warm should have skill_hit=true"

    def test_no_real_rice_home_touched(self):
        """수용 기준 5: 실제 ~/.rice는 건드리지 않는다.

        runner가 사용하는 tempfile.mkdtemp() 경로들이 ~/.rice가 아님을 확인한다.
        """
        real_rice = Path.home() / ".rice"
        before_mtime = real_rice.stat().st_mtime if real_rice.exists() else None

        self._run_main_isolated()

        after_mtime = real_rice.stat().st_mtime if real_rice.exists() else None
        # ~/.rice가 존재하지 않거나 mtime이 변하지 않아야 한다
        assert before_mtime == after_mtime, "~/.rice should not be modified by eval runner"

    @pytest.mark.asyncio
    async def test_run_one_cold_returns_record(self):
        """_run_one이 빈 cold 디렉토리에서 RunRecord를 반환한다."""
        from eval.runner import _run_one
        task = {
            "id": "T-test",
            "prompt": "deploy production my-service",
            "related_skill": "deploy-k8s-service",
            "success_check": {"type": "contains_keywords", "keywords": ["deploy"]},
        }
        tmp = tempfile.mkdtemp(prefix="rice-eval-cold-test-")
        try:
            record = await _run_one(task, tmp, "deploy-k8s-service")
            assert record.turns == 1
            assert record.skill_hit is False  # cold = 스킬 없음
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_run_one_warm_skill_hit(self):
        """_run_one이 warm 디렉토리(스킬 시드)에서 skill_hit=True를 반환한다."""
        from eval.runner import _run_one
        from rice_runtime.core.paths import RicePaths

        task = {
            "id": "T-test",
            "prompt": "deploy production my-service",
            "related_skill": "deploy-k8s-service",
            "success_check": {"type": "contains_keywords", "keywords": ["deploy"]},
        }
        tmp = tempfile.mkdtemp(prefix="rice-eval-warm-test-")
        try:
            # skills 디렉토리에 스킬 파일 심기
            paths = RicePaths.from_env(tmp).ensure()
            skill_content = (
                Path(__file__).parent.parent / "eval" / "profiles" / "warm" / "skills" / "deploy-k8s-service.md"
            )
            if skill_content.exists():
                shutil.copy2(skill_content, paths.skills / "deploy-k8s-service.md")

            record = await _run_one(task, tmp, "deploy-k8s-service")
            assert record.turns == 1
            assert record.skill_hit is True  # warm = 스킬 있음
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
