"""eval/metrics.py — turns / tool_calls / success / skill_hit 집계.

WHY THIS FILE EXISTS
--------------------
eval harness는 각 task를 N회(runs_per_task) 반복해 지표를 산출한다. 이 모듈은
하나의 run (task × cold/warm 한 번 실행)에서 수집된 원시 이벤트를 집계해
TaskConditionMetrics를 만든다. 집계 로직을 분리해 runner.py·report.py가
측정과 보고 책임을 각자 갖도록 한다 (LEARNING_EVAL §2, §5).

지표 정의 (EVAL §2):
  turns         — 과제 완료까지 사용자↔에이전트 왕복 수 (여기서는 1 turn이 1회 run)
  tool_calls    — tool_call SSE 이벤트 누적 수
  success       — success_check 함수의 결과 (bool)
  skill_hit     — prefetch에 관련 스킬(related_skill)이 실제로 끌려왔는지
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunRecord:
    """단일 run (한 task 한 번 실행)에서 수집된 원시 데이터."""

    turns: int = 0
    tool_calls: int = 0
    success: bool = False
    skill_hit: bool = False
    skills_used: list[str] = field(default_factory=list)


@dataclass
class TaskConditionMetrics:
    """한 task × 한 condition (cold/warm) × N runs의 집계 결과.

    EVAL §5: 변동성 완화를 위해 N회 반복의 평균을 사용한다.
    skill_hit는 warm 조건에서의 기댓값이므로 bool이 아닌 비율이다.
    """

    turns: float = 0.0         # 평균 turn 수
    tool_calls: float = 0.0    # 평균 tool call 수
    success_rate: float = 0.0  # 성공률 (0.0~1.0)
    skill_hit: bool = False    # runs 중 하나라도 skill_hit=True면 True (대표값)
    skills_used: list[str] = field(default_factory=list)  # warm에서 사용된 스킬 이름


def aggregate(runs: list[RunRecord]) -> TaskConditionMetrics:
    """여러 run 결과를 집계해 TaskConditionMetrics를 반환한다 (EVAL §5).

    WHY: N회 반복 평균으로 LLM 비결정성 완화. skill_hit은 runs 중 하나라도
    true면 True(warm에서 skill_hit=false면 검색 실패 진단, EVAL §2).
    """
    if not runs:
        return TaskConditionMetrics()

    n = len(runs)
    avg_turns = sum(r.turns for r in runs) / n
    avg_tool_calls = sum(r.tool_calls for r in runs) / n
    success_rate = sum(1 for r in runs if r.success) / n

    # skill_hit: warm 조건에서 최소 한 번 이상 관련 스킬이 끌려왔으면 True
    skill_hit = any(r.skill_hit for r in runs)

    # skills_used: 전체 runs에서 사용된 스킬 유니온 (중복 제거, 버전 최신 우선)
    seen: dict[str, str] = {}
    for r in runs:
        for s in r.skills_used:
            base = s.split("@")[0]
            # 버전이 있으면 최신 버전 유지 (단순: 마지막 것으로 덮어씀)
            seen[base] = s
    skills_used = list(seen.values())

    return TaskConditionMetrics(
        turns=avg_turns,
        tool_calls=avg_tool_calls,
        success_rate=success_rate,
        skill_hit=skill_hit,
        skills_used=skills_used,
    )


def compute_delta(cold: TaskConditionMetrics, warm: TaskConditionMetrics) -> dict:
    """cold vs warm 델타를 산출한다 (EVAL §6.3, ImpactReport 스키마).

    *_pct: cold 대비 warm 개선율 (음수 = 감소 = 개선)
    success_pp: 퍼센트포인트 차이 (양수 = 개선)

    WHY: 개선율을 퍼센트포인트와 분리한 이유는 각각이 다른 의미를 전달하기
    때문이다. success는 비율이므로 pp가 적합하고, turns/tool_calls는 절댓값
    비율(pct)이 직관적이다 (EVAL §6.3 설계 근거).
    """
    def pct_change(cold_val: float, warm_val: float) -> float:
        if cold_val == 0:
            return 0.0
        return round((warm_val - cold_val) / cold_val, 4)

    return {
        "turns_pct": pct_change(cold.turns, warm.turns),
        "tool_calls_pct": pct_change(cold.tool_calls, warm.tool_calls),
        "success_pp": round(warm.success_rate - cold.success_rate, 4),
    }


def compute_aggregate(task_deltas: list[dict], warm_tasks: list[TaskConditionMetrics]) -> dict:
    """전체 task suite의 집계 델타와 skill_hit_rate를 산출한다 (EVAL §6.3)."""
    if not task_deltas:
        return {"turns_pct": 0.0, "tool_calls_pct": 0.0, "success_pp": 0.0, "skill_hit_rate": 0.0}

    n = len(task_deltas)
    avg_turns_pct = sum(d["turns_pct"] for d in task_deltas) / n
    avg_tool_calls_pct = sum(d["tool_calls_pct"] for d in task_deltas) / n
    avg_success_pp = sum(d["success_pp"] for d in task_deltas) / n

    # skill_hit_rate: warm에서 관련 스킬이 끌려온 task 비율
    skill_hit_rate = sum(1 for t in warm_tasks if t.skill_hit) / len(warm_tasks) if warm_tasks else 0.0

    return {
        "turns_pct": round(avg_turns_pct, 4),
        "tool_calls_pct": round(avg_tool_calls_pct, 4),
        "success_pp": round(avg_success_pp, 4),
        "skill_hit_rate": round(skill_hit_rate, 4),
    }
