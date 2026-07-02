"""skill/patch.py — diff 산출 헬퍼.

책임(single): 스킬 본문의 두 버전을 받아 `SkillDiff` 형태의 dict를 산출한다.
`SkillDiff`는 UI(types.ts)와 wire 계약(sse-events.schema.json)이 기대하는 형태다:
  {"lines": [{"type": "add|remove|context", "text": str}, ...]}

apply_patch 적용 절차는 service.py(LearningService.apply_patch)가 담당한다.
이 파일은 순수 diff 산출만 한다. 외부 의존성 없음 — 표준 라이브러리 `difflib` 사용.

WHY: difflib를 직접 서비스 레이어에 두지 않고 여기에 격리한 이유는:
1. 테스트 용이성 (순수 함수, 의존 없음).
2. 장차 diff 형식을 바꾸거나 확장할 때 한 곳만 수정한다.
3. PatchProposal을 생성하는 review 로직과 분리해 단일 책임을 유지한다.

(LEARNING_LOOP §5.2.3)

NOTE(agent): 이 파일은 한때 `detect_patch_trigger`(정규식 기반 T1a/T1b 트리거 판단)도
갖고 있었다. 그 판단은 이제 `learning/review/engine.py`의 LLM이 직접 한다(모델이
`propose_skill_patch`의 `trigger_reason` 파라미터로 T1a/T1b/T1c 중 자기분류) —
규칙 기반 사전 분류가 아니라 실제로 "왜 patch가 필요한지"를 판단할 수 있는 유일한
주체이기 때문이다. `has_correction_signal`/`has_failure_signal`(signals.py)은 폐기되지
않고 `review/gate.py::should_review()`가 "LLM을 부를 가치가 있는가"를 판단하는 게이트로
재사용한다.
"""

from __future__ import annotations

import difflib


def build_diff(old_body: str, new_body: str) -> dict:
    """두 본문 문자열의 diff를 SkillDiff 형태로 반환한다.

    반환 형태:
      {"lines": [{"type": "add"|"remove"|"context", "text": str}, ...]}

    이 형태는 rice-app/src/lib/types.ts 의 SkillDiff와 정확히 대응한다
    (RICE_CONTRACTS §2.2, LEARNING_LOOP §5.2.3).

    구현 선택: unified_diff의 @@-헤더는 생략하고 +/-/space 줄만 변환한다.
    헤더는 UI가 렌더링하는 콘텐츠가 아니므로 제외한다.
    """
    old_lines = old_body.splitlines(keepends=True)
    new_lines = new_body.splitlines(keepends=True)

    lines: list[dict] = []
    for line in difflib.unified_diff(old_lines, new_lines, lineterm=""):
        if line.startswith("+++") or line.startswith("---"):
            # 파일명 헤더 — SkillDiff에 포함하지 않는다.
            continue
        if line.startswith("@@"):
            # 헝크 헤더 — 생략 (UI는 연속 줄로 렌더링)
            continue
        if line.startswith("+"):
            lines.append({"type": "add", "text": line[1:]})
        elif line.startswith("-"):
            lines.append({"type": "remove", "text": line[1:]})
        else:
            # ' ' 로 시작하는 context 줄
            lines.append({"type": "context", "text": line[1:]})

    return {"lines": lines}
