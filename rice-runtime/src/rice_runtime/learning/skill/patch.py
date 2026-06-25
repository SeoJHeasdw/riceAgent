"""skill/patch.py — diff 산출 헬퍼.

책임(single): 스킬 본문의 두 버전을 받아 `SkillDiff` 형태의 dict를 산출한다.
`SkillDiff`는 UI(types.ts)와 wire 계약(sse-events.schema.json)이 기대하는 형태다:
  {"lines": [{"type": "add|remove|context", "text": str}, ...]}

apply_patch 적용 절차는 service.py(LearningService.apply_patch)가 담당한다.
이 파일은 순수 diff 산출만 한다. 외부 의존성 없음 — 표준 라이브러리 `difflib` 사용.

WHY: difflib를 직접 서비스 레이어에 두지 않고 여기에 격리한 이유는:
1. 테스트 용이성 (순수 함수, 의존 없음).
2. 장차 diff 형식을 바꾸거나 확장할 때 한 곳만 수정한다.
3. PatchProposal을 생성하는 observe 로직과 분리해 단일 책임을 유지한다.

(LEARNING_LOOP §5.2.3)
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


# ── 트리거 판단 헬퍼 (보수적) ────────────────────────────────────────────────

def detect_patch_trigger(
    final_message: str,
    tool_calls_made: int,
    injected_skill_ids: tuple[str, ...],
) -> str | None:
    """observe()에서 patch 트리거 여부를 판단한다 (LEARNING_LOOP §5.2.2).

    반환:
      - "T1a" : 명시적 교정 신호가 final_message에 있을 때
      - "T1b" : 주입된 스킬이 있는데 실패 신호가 final_message에 있을 때
      - None  : 확실하지 않으면 제안 없음 (보수적)

    WHY 보수적: 데모 안정성 > 적극적 제안. 애매한 경우는 제안하지 않는다.
    분류기를 만들지 않는 원칙 — 프롬프트 가이드 기반만 (LEARNING_LOOP §5.2.2).

    NOTE(agent): 실제 LLM 응답에서 교정 신호를 100% 감지하는 것은 불가능하다.
    이 구현은 패턴 매칭이며, 실제 배포 시에는 더 정교한 신호가 필요하다.
    MVP 데모 범위에서 충분히 보수적인 선택이다.
    """
    if not injected_skill_ids:
        # 주입된 스킬이 없으면 patch 대상이 없다.
        return None

    msg_lower = final_message.lower()

    # T1a: 명시적 교정 신호 — "actually", "correction", "the correct way", etc.
    correction_signals = [
        "actually,",
        "actually the",
        "correction:",
        "incorrect",
        "the correct way",
        "should be",
        "i made a mistake",
        "that was wrong",
    ]
    if any(sig in msg_lower for sig in correction_signals):
        return "T1a"

    # T1b: 실패 신호 — 스킬을 주입했는데 "failed", "error", "didn't work"
    failure_signals = [
        "failed",
        "error occurred",
        "didn't work",
        "did not work",
        "could not",
        "couldn't",
    ]
    if any(sig in msg_lower for sig in failure_signals):
        return "T1b"

    return None
