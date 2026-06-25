"""eval — RICE 학습 측정 하네스 패키지.

rice-runtime/eval/ 은 "쓸수록 똑똑해진다"를 정량적으로 증명하는 cold/warm
ablation harness다 (LEARNING_EVAL §5). tests/ 와 별개로 위치한다.

진입점: runner.py (python eval/runner.py)
출력:   results/latest.json (UI ImpactReport fixture)
"""

from __future__ import annotations
