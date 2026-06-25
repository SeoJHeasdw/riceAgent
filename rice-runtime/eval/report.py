"""eval/report.py — results JSON(ImpactReport) + 사람이 읽는 요약 생성.

WHY THIS FILE EXISTS
--------------------
runner.py가 집계한 cold/warm 지표를 UI가 기대하는 ImpactReport JSON 스키마로
직렬화한다(EVAL §6.3). UI(rice-app)는 eval/results/latest.json을 정적 fixture로
읽는다 — 새 엔드포인트는 만들지 않는다(CONTRACTS §1.3 "MVP는 fixture").

출력 스키마는 EVAL §6.3 / eval.md §"출력 데이터 계약"이 정본이다.
rice-app/src/lib/types.ts의 ImpactReport와 동기화를 유지해야 한다.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .metrics import TaskConditionMetrics, compute_aggregate, compute_delta


# ── ImpactReport 스키마 클래스 (EVAL §6.3) ──────────────────────────────────

@dataclass
class ConditionResult:
    """cold 또는 warm 한 조건의 집계 결과 (ImpactReport.tasks[i].cold/warm)."""
    turns: float
    tool_calls: float
    success_rate: float
    skill_hit: bool
    skills_used: list[str] = field(default_factory=list)


@dataclass
class TaskResult:
    """task 하나의 cold/warm/delta (ImpactReport.tasks[i])."""
    id: str
    title: str
    cold: ConditionResult
    warm: ConditionResult
    delta: dict[str, float]


@dataclass
class ImpactReport:
    """UI의 Learning Impact 패널이 기대하는 최상위 JSON 구조 (EVAL §6.3).

    rice-app/src/lib/types.ts ImpactReport 와 필드/타입이 1:1이어야 한다.
    스키마 변경 금지 — UI가 묶여 있음 (eval.md "하지 말 것").
    """
    task_suite: str
    model: str
    runs_per_task: int
    generated_at: str
    tasks: list[TaskResult]
    aggregate: dict[str, float]


def build_report(
    *,
    task_suite: str,
    model: str,
    runs_per_task: int,
    generated_at: str,
    task_ids: list[str],
    task_titles: list[str],
    cold_metrics: list[TaskConditionMetrics],
    warm_metrics: list[TaskConditionMetrics],
) -> ImpactReport:
    """cold + warm 집계 지표를 받아 ImpactReport를 빌드한다.

    WHY: report.py가 직접 run을 모르고 TaskConditionMetrics만 받도록 분리해
    runner.py ↔ report.py 결합을 줄인다.
    """
    tasks: list[TaskResult] = []

    for i, (tid, title) in enumerate(zip(task_ids, task_titles)):
        cold = cold_metrics[i]
        warm = warm_metrics[i]
        delta = compute_delta(cold, warm)
        tasks.append(TaskResult(
            id=tid,
            title=title,
            cold=ConditionResult(
                turns=round(cold.turns, 2),
                tool_calls=round(cold.tool_calls, 2),
                success_rate=round(cold.success_rate, 4),
                skill_hit=cold.skill_hit,
                skills_used=cold.skills_used,
            ),
            warm=ConditionResult(
                turns=round(warm.turns, 2),
                tool_calls=round(warm.tool_calls, 2),
                success_rate=round(warm.success_rate, 4),
                skill_hit=warm.skill_hit,
                skills_used=warm.skills_used,
            ),
            delta=delta,
        ))

    deltas = [compute_delta(cold_metrics[i], warm_metrics[i]) for i in range(len(task_ids))]
    aggregate = compute_aggregate(deltas, warm_metrics)

    return ImpactReport(
        task_suite=task_suite,
        model=model,
        runs_per_task=runs_per_task,
        generated_at=generated_at,
        tasks=tasks,
        aggregate=aggregate,
    )


def report_to_dict(report: ImpactReport) -> dict[str, Any]:
    """ImpactReport를 JSON-직렬화 가능한 dict로 변환한다.

    WHY: dataclass.asdict가 중첩 dataclass를 자동으로 dict로 변환하므로
    별도 직렬화 로직 없이 사용한다. UI 스키마와 동일한 key 구조를 유지한다.
    """
    return asdict(report)


def validate_report(data: dict[str, Any]) -> bool:
    """최소 스키마 유효성 검증. UI가 기대하는 필드가 모두 있는지 확인한다.

    WHY: eval.md 수용 기준 2 "생성된 JSON이 ImpactReport 스키마를 만족"을
    자동 검증하기 위해 report.py 안에 검증 로직을 둔다.
    """
    required_top = {"task_suite", "model", "runs_per_task", "generated_at", "tasks", "aggregate"}
    if not required_top.issubset(data.keys()):
        return False

    aggregate = data.get("aggregate", {})
    required_agg = {"turns_pct", "tool_calls_pct", "success_pp", "skill_hit_rate"}
    if not required_agg.issubset(aggregate.keys()):
        return False

    for task in data.get("tasks", []):
        required_task = {"id", "title", "cold", "warm", "delta"}
        if not required_task.issubset(task.keys()):
            return False
        for cond in (task["cold"], task["warm"]):
            required_cond = {"turns", "tool_calls", "success_rate", "skill_hit", "skills_used"}
            if not required_cond.issubset(cond.keys()):
                return False
        required_delta = {"turns_pct", "tool_calls_pct", "success_pp"}
        if not required_delta.issubset(task["delta"].keys()):
            return False

    return True


def write_results(report: ImpactReport, results_dir: Path, timestamp: str) -> tuple[Path, Path]:
    """results/<ts>.json 과 results/latest.json을 기록한다 (EVAL §5).

    반환: (timestamped_path, latest_path)
    """
    results_dir.mkdir(parents=True, exist_ok=True)
    data = report_to_dict(report)

    ts_path = results_dir / f"{timestamp}.json"
    ts_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    latest_path = results_dir / "latest.json"
    latest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    return ts_path, latest_path


def print_summary(report: ImpactReport) -> None:
    """사람이 읽는 요약을 stdout에 출력한다 (EVAL §5 "사람이 읽는 요약 생성")."""
    agg = report.aggregate
    print(f"\n{'='*60}")
    print(f"  RICE Learning Impact Report")
    print(f"  suite={report.task_suite}  model={report.model}  n={report.runs_per_task}")
    print(f"  generated={report.generated_at}")
    print(f"{'='*60}")
    print(f"  Aggregate:")
    print(f"    turns        : {agg['turns_pct']:+.1%}")
    print(f"    tool_calls   : {agg['tool_calls_pct']:+.1%}")
    print(f"    success      : {agg['success_pp']:+.0%} pp")
    print(f"    skill_hit_rate: {agg['skill_hit_rate']:.0%}")
    print(f"{'='*60}")
    print(f"  Per-task:")
    for t in report.tasks:
        d = t.delta
        hit_cold = "✗" if not t.cold.skill_hit else "✓"
        hit_warm = "✓" if t.warm.skill_hit else "✗"
        print(f"  [{t.id}] {t.title}")
        print(f"    turns      cold={t.cold.turns:.1f}  warm={t.warm.turns:.1f}  Δ={d['turns_pct']:+.1%}")
        print(f"    tool_calls cold={t.cold.tool_calls:.1f}  warm={t.warm.tool_calls:.1f}  Δ={d['tool_calls_pct']:+.1%}")
        print(f"    success    cold={t.cold.success_rate:.0%}  warm={t.warm.success_rate:.0%}  Δ={d['success_pp']:+.0%} pp")
        print(f"    skill_hit  cold={hit_cold}  warm={hit_warm}")
        if t.warm.skills_used:
            print(f"    skills_used: {', '.join(t.warm.skills_used)}")
    print(f"{'='*60}\n")
