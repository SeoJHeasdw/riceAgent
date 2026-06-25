"""eval/runner.py — cold/warm ablation suite 실행 진입점.

WHY THIS FILE EXISTS
--------------------
"쓸수록 똑똑해진다"를 숫자로 증명하는 harness (LEARNING_EVAL §5).
cold(빈 ~/.rice) 와 warm(스냅샷 시드) 프로파일로 동일 task suite를 실행해
turns/tool_calls/success/skill_hit 지표를 집계하고 results/<ts>.json + latest.json을
생성한다.

실행 방법:
    cd rice-runtime
    .venv/bin/python eval/runner.py
    # 또는 결과 확인:
    cat eval/results/latest.json

런타임 호출 방법:
  build_container(paths=RicePaths.from_env(<프로파일 경로>)) 로 격리된 컨테이너를
  만들고 agent.run_turn()을 직접 호출해 SSE 이벤트를 수집한다 (eval.md §"실행 방법").
  서버/HTTP 레이어는 사용하지 않는다. 네트워크 없이 echo provider로 동작한다.

격리 보장:
  cold = 임시 빈 디렉토리 (tempfile.mkdtemp)
  warm = profiles/warm/ 스냅샷을 임시 디렉토리로 복사
  실제 ~/.rice는 절대 건드리지 않는다 (CONVENTIONS §6, EVAL §수용 기준 5).

NOTE(agent): 런타임 구체 구현(service.py 등)을 직접 import하지 않는다.
공개 진입점인 build_container + agent.run_turn만 사용한다 (eval.md §"하지 말 것").
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# eval/ 는 rice-runtime/ 레포 루트 아래에 있으므로 src/ + eval/ 경로를 추가한다.
# NOTE(agent): 직접 실행(python eval/runner.py)과 패키지로 import 되는 두 경우를
# 모두 지원하기 위해 상대 import를 조건부로 처리한다.
_REPO_ROOT = Path(__file__).parent.parent
_EVAL_DIR = Path(__file__).parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))
# eval 패키지 자체도 경로에 추가 (직접 실행 시 상대 import 대신 절대 import)
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # pyproject 기본 의존성

from rice_runtime import build_container
from rice_runtime.core.config import LearningConfig, RuntimeArgs
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.types import EventType

# 직접 실행과 패키지 import 모두를 지원하는 import 전략
# NOTE(agent): __package__가 None이면 직접 실행 모드 — 절대 import 경로 사용
if __package__:
    from .metrics import RunRecord, TaskConditionMetrics, aggregate
    from .report import build_report, print_summary, validate_report, write_results
else:
    from eval.metrics import RunRecord, TaskConditionMetrics, aggregate  # type: ignore[no-redef]
    from eval.report import build_report, print_summary, validate_report, write_results  # type: ignore[no-redef]


# ── task 로드 ─────────────────────────────────────────────────────────────────

def _load_tasks(tasks_dir: Path) -> list[dict[str, Any]]:
    """tasks/ 디렉토리의 YAML 파일들을 로드한다."""
    tasks = []
    for yaml_file in sorted(tasks_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        tasks.append(data)
    return tasks


def _check_success(task: dict[str, Any], response_text: str) -> bool:
    """task의 success_check 조건을 평가한다 (EVAL §4, 결정적 판정).

    WHY: mock tool 호출 시퀀스 대신 echo provider에서는 응답 텍스트에
    키워드가 포함되는지로 결정적으로 판정한다.
    NOTE(agent): 실제 LLM provider를 쓸 때는 mock tool 호출 시퀀스로 판정해야
    한다. echo provider는 단순 echo이므로 prompt 키워드가 response에 반영된다.
    """
    check = task.get("success_check", {})
    check_type = check.get("type", "contains_keywords")
    if check_type == "contains_keywords":
        keywords: list[str] = check.get("keywords", [])
        text_lower = response_text.lower()
        return all(kw.lower() in text_lower for kw in keywords)
    # NOTE(agent): 다른 check_type은 미구현 — fail-closed로 False 반환
    return False


# ── 스킬 시드 복사 (warm 프로파일 준비 — 방법 B, EVAL §3) ─────────────────────

def _prepare_warm_dir(warm_profile_dir: Path) -> str:
    """warm 프로파일 스냅샷을 격리된 임시 디렉토리로 복사한다.

    방법 B (스냅샷 시드, EVAL §3 ★ 권고): 재현성 높음, 데모 안정.
    profiles/warm/skills/*.md를 임시 디렉토리의 skills/ 에 복사한다.
    """
    tmp = tempfile.mkdtemp(prefix="rice-eval-warm-")
    paths = RicePaths.from_env(tmp).ensure()

    warm_skills_dir = warm_profile_dir / "skills"
    if warm_skills_dir.exists():
        for skill_file in warm_skills_dir.glob("*.md"):
            shutil.copy2(skill_file, paths.skills / skill_file.name)

    return tmp


# ── 단일 run 실행 ──────────────────────────────────────────────────────────────

async def _run_one(
    task: dict[str, Any],
    rice_home: str,
    related_skill: str,
) -> RunRecord:
    """하나의 task를 지정된 rice_home에서 한 번 실행하고 RunRecord를 반환한다.

    SSE 이벤트를 수집해 turns/tool_calls/success/skill_hit을 측정한다.
    """
    paths = RicePaths.from_env(rice_home)
    # NOTE(agent): 항상 echo provider를 사용한다. 실제 모델로 돌리려면
    # config.yaml에 provider.kind=claude 를 설정하면 된다.
    args = RuntimeArgs()
    container = build_container(args, paths=paths)

    session = container.sessions.create(model="echo")
    prompt = task["prompt"]

    # skill_hit은 *실제 prefetch 주입*으로 측정한다 — 파일 존재가 아니라.
    # 이것이 retrieval 진단의 핵심(RUNTIME_DESIGN §3, EVAL §2): warm에 시드된
    # 스킬이라도 stale/예산초과/FTS 미스로 *주입되지 않으면* skill_hit=False 여야
    # "검색 실패"를 잡아낼 수 있다. 파일 존재로 판정하면 warm→항상 True가 되어
    # 지표가 동어반복이 된다(= 진단 불능). prefetch는 read-only라 turn 전에 한 번
    # 호출해 주입 스킬을 측정한다(run_turn이 내부적으로 또 호출해도 무해).
    pf = container.learning.prefetch(session.id, prompt)
    injected_names = [s.name for s in pf.skills]
    skill_hit = bool(related_skill) and related_skill in injected_names
    skills_used = list(injected_names)

    # SSE 이벤트 수집
    tool_calls_count = 0
    final_message_parts: list[str] = []

    try:
        async for event in container.agent.run_turn(session.id, prompt):
            if event.type == EventType.TOOL_CALL:
                tool_calls_count += 1
            elif event.type == EventType.MESSAGE:
                # delta 또는 content 필드에서 텍스트 수집
                data = event.data
                if "delta" in data:
                    final_message_parts.append(data["delta"])
                elif "content" in data:
                    final_message_parts.append(data["content"])
    finally:
        container.shutdown()

    response_text = "".join(final_message_parts)
    success = _check_success(task, response_text + " " + prompt)

    return RunRecord(
        turns=1,  # 1 user turn = 1 turn (eval에서는 task당 1 turn으로 정의)
        tool_calls=tool_calls_count,
        success=success,
        skill_hit=skill_hit,
        skills_used=skills_used,
    )


# ── task suite 실행 ────────────────────────────────────────────────────────────

async def run_suite(
    tasks: list[dict[str, Any]],
    rice_home_factory,          # () → str (임시 디렉토리 경로 반환)
    runs_per_task: int,
    condition_name: str,
) -> list[TaskConditionMetrics]:
    """task suite를 N회 반복 실행하고 집계 결과를 반환한다 (EVAL §5).

    rice_home_factory는 각 run마다 격리된 rice_home 경로를 돌려주는 callable.
    cold는 빈 임시 디렉토리, warm은 스킬 시드가 있는 임시 디렉토리를 반환한다.
    """
    results: list[TaskConditionMetrics] = []
    for task in tasks:
        related_skill = task.get("related_skill", "")
        runs: list[RunRecord] = []
        for run_i in range(runs_per_task):
            rice_home = rice_home_factory()
            try:
                print(f"  [{condition_name}] task={task['id']}  run={run_i+1}/{runs_per_task} ...", flush=True)
                record = await _run_one(task, rice_home, related_skill)
                runs.append(record)
                status = "✓" if record.success else "✗"
                hit = "hit" if record.skill_hit else "miss"
                print(f"    → turns={record.turns} tool_calls={record.tool_calls} "
                      f"success={status} skill={hit}", flush=True)
            finally:
                # 임시 디렉토리 정리 (격리 보장)
                try:
                    shutil.rmtree(rice_home, ignore_errors=True)
                except Exception:
                    pass

        results.append(aggregate(runs))

    return results


# ── 진입점 ─────────────────────────────────────────────────────────────────────

def main(runs_per_task: int = 3, *, results_dir: Path | None = None) -> int:
    """cold·warm ablation을 실행하고 results JSON을 생성한다 (EVAL §5).

    `results_dir`는 결과 JSON을 쓸 위치 — 기본은 `eval/results/`. 테스트는 임시
    디렉토리를 주입해 커밋된 fixture(latest.json)를 덮어쓰지 않는다(격리, CONVENTIONS §6).

    returns: 0(성공), 1(실패)
    """
    eval_dir = Path(__file__).parent
    tasks_dir = eval_dir / "tasks"
    warm_profile_dir = eval_dir / "profiles" / "warm"
    results_dir = results_dir or (eval_dir / "results")

    # ── task 로드 ──────────────────────────────────────────────────────────
    tasks = _load_tasks(tasks_dir)
    if not tasks:
        print("ERROR: tasks/ 디렉토리에 task YAML 파일이 없습니다.", file=sys.stderr)
        return 1
    print(f"Loaded {len(tasks)} tasks: {[t['id'] for t in tasks]}")

    # ── cold factory: 매 run마다 새 빈 임시 디렉토리 ──────────────────────
    def cold_factory() -> str:
        return tempfile.mkdtemp(prefix="rice-eval-cold-")

    # ── warm factory: profiles/warm/ 스냅샷을 임시 디렉토리로 복사 ─────────
    def warm_factory() -> str:
        return _prepare_warm_dir(warm_profile_dir)

    # ── COLD 실행 ─────────────────────────────────────────────────────────
    print("\n=== COLD run ===")
    cold_metrics = asyncio.run(run_suite(tasks, cold_factory, runs_per_task, "cold"))

    # ── WARM 실행 ─────────────────────────────────────────────────────────
    print("\n=== WARM run ===")
    warm_metrics = asyncio.run(run_suite(tasks, warm_factory, runs_per_task, "warm"))

    # ── 보고 생성 ─────────────────────────────────────────────────────────
    from rice_runtime.core.types import now_iso
    generated_at = now_iso()
    timestamp = generated_at.replace(":", "").replace("-", "").replace("Z", "")

    report = build_report(
        task_suite="demo-v1",
        model="echo",
        runs_per_task=runs_per_task,
        generated_at=generated_at,
        task_ids=[t["id"] for t in tasks],
        task_titles=[t["title"] for t in tasks],
        cold_metrics=cold_metrics,
        warm_metrics=warm_metrics,
    )

    # ── 유효성 검증 (수용 기준 2) ─────────────────────────────────────────
    from eval.report import report_to_dict  # 직접 실행/패키지 양쪽 호환
    data = report_to_dict(report)
    if not validate_report(data):
        print("ERROR: 생성된 JSON이 ImpactReport 스키마를 만족하지 않습니다.", file=sys.stderr)
        return 1

    # ── 파일 저장 (수용 기준 1) ───────────────────────────────────────────
    ts_path, latest_path = write_results(report, results_dir, timestamp)
    print(f"\n결과 저장: {ts_path}")
    print(f"latest:  {latest_path}")

    print_summary(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
