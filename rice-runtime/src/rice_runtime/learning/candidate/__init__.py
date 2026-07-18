"""candidate 승격 게이트 (LEARNING_LOOP §10.6) — 승인된 스킬 제안을 즉시
active 로 쓰지 않고 baseline 과 비교 평가한 뒤 승격/폐기하는 서브시스템.

  store.py   — skill_candidates 상태 머신 (pending→running→promoted/…)
  rollout.py — 부작용 없는 시뮬레이션 롤아웃 (두 arm 동일 환경)
  harness.py — 과제 생성 + 롤아웃 + 심판 + 회귀 판정
"""

from .harness import CandidateEvalError, CandidateEvalHarness, CandidateVerdict
from .store import CandidateStore

__all__ = [
    "CandidateEvalError",
    "CandidateEvalHarness",
    "CandidateStore",
    "CandidateVerdict",
]
