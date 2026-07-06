"""routes/eval_impact.py — 학습 효과(cold→warm) 리포트 서빙.

  GET /eval/impact   최신 eval harness 결과(ImpactReport JSON)를 반환

원칙 (RICE_LEARNING_EVAL §6): **cold→warm 정량 수치는 eval harness 산출물만이
정본이다.** 일반 사용 데이터(usage/patch 카운터)는 "얼마나 쓰였는가"를 말해줄
뿐 "얼마나 잘했는가"를 단정할 근거가 아니다 — 성공률·개선폭은 통제된 평가
suite(eval/runner.py) 결과 또는 명시적 사용자 피드백으로만 표시한다. 이
엔드포인트가 그 분리를 API 표면에 강제한다: 스킬 상세(GET /skills/{id})는
usage 기반 사실만, 학습 효과 패널은 여기서 온 eval 결과만 그린다.

파일 탐색 순서 (write_results 가 latest.json 을 갱신):
  1. $RICE_EVAL_RESULTS/latest.json   — 명시 오버라이드
  2. ~/.rice/eval/results/latest.json — 설치 환경 표준 위치
  3. <repo>/eval/results/latest.json  — 개발 트리 (eval 하네스 기본 출력)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends

from ...core.errors import NotFoundError
from ..composition_root import Container
from .deps import get_container

router = APIRouter(prefix="/eval", tags=["eval"])

# src/rice_runtime/server/routes/eval_impact.py → rice-runtime/ (개발 트리 폴백)
_REPO_RESULTS = Path(__file__).resolve().parents[4] / "eval" / "results"


def _candidate_paths(c: Container) -> list[Path]:
    paths: list[Path] = []
    override = os.environ.get("RICE_EVAL_RESULTS")
    if override:
        paths.append(Path(override).expanduser() / "latest.json")
    paths.append(c.paths.root / "eval" / "results" / "latest.json")
    paths.append(_REPO_RESULTS / "latest.json")
    return paths


@router.get("/impact")
async def impact(c: Container = Depends(get_container)):
    for path in _candidate_paths(c):
        if not path.is_file():
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue  # 손상/경합 — 다음 후보로 (fail-safe)
        return {"success": True, "data": report}
    raise NotFoundError(
        "eval 결과가 없습니다 — eval harness(python -m eval.runner)를 먼저 실행하세요."
    )
