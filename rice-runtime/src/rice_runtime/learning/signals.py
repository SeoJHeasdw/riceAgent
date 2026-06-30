"""learning/signals.py — 학습 루프의 자연어 신호셋 (다국어 중앙화).

RICE 는 MVP 에서 LLM 분류기 없이 **규칙 기반(키워드)** 으로 보수적으로 신호를 잡는다
(LEARNING_LOOP §4·§5.2.2). v1 의 문제는 신호가 전부 영어라 한국어 대화에서 학습 루프가
사실상 동작하지 않은 것(버그#8). 이 모듈은 모든 신호를 **한 곳**에 모아 한국어 우선 +
영어를 함께 지원한다. 앱은 현재 ko, 이후 en 까지 지원 예정이므로 두 언어를 같이 둔다.

매칭은 모두 소문자 비교(영어) + 원문 부분일치(한국어). 한국어는 교착어라 접사 변형이
많으므로 **어간/핵심구**를 키워드로 둔다(예: "틀렸", "안 돼"). 보수적이되 한국어에서
최소한의 학습 신호는 잡히게 하는 것이 목표.
"""

from __future__ import annotations

import re

# ── 선언적 사실(fact) 도입구 (LEARNING_LOOP §6) ──────────────────────────────
FACT_PREFIXES: tuple[str, ...] = (
    # English
    "the project uses ",
    "project uses ",
    "the codebase uses ",
    "we use ",
    "i use ",
    "the project is ",
    "the language is ",
    "the stack is ",
    # 한국어
    "프로젝트는 ",
    "이 프로젝트는 ",
    "코드베이스는 ",
    "이 코드베이스는 ",
    "사용하는 언어는 ",
    "기술 스택은 ",
    "스택은 ",
)

# ── 사용자 선호(preference) 도입구 ────────────────────────────────────────────
PREF_PREFIXES: tuple[str, ...] = (
    # English
    "i prefer ",
    "the user prefers ",
    "please use ",
    "please respond in ",
    "please answer in ",
    # 한국어
    "사용자는 ",
    "저는 ",
    "선호하는 ",
    "앞으로는 ",
    "앞으로 ",
    "항상 ",
)

# ── 스킬화 의도 (LEARNING_LOOP §4) — 어시스턴트 최종 메시지에서 탐지 ──────────
SKILL_INTENT_KEYWORDS: tuple[str, ...] = (
    # English
    "here's a reusable procedure",
    "reusable steps",
    "skill.md",
    "i'll save this as a skill",
    "save this as a skill",
    "these steps can be reused",
    "you can reuse these steps",
    # 한국어
    "스킬로 저장",
    "스킬로 만들",
    "재사용 가능한 절차",
    "재사용 가능한 단계",
    "다음에도 쓸 수 있",
    "절차로 저장",
    "이 절차를 저장",
    "반복 작업으로",
)

# ── patch 트리거 T1a: 교정 신호 (LEARNING_LOOP §5.2.2) ────────────────────────
CORRECTION_SIGNALS: tuple[str, ...] = (
    # English
    "actually,",
    "actually the",
    "correction:",
    "incorrect",
    "the correct way",
    "should be",
    "i made a mistake",
    "that was wrong",
    # 한국어
    "정정",
    "틀렸",
    "틀려",
    "잘못됐",
    "잘못 됐",
    "잘못했",
    "그게 아니라",
    "그건 아니",
    "아니라",
    "수정해",
    "바로잡",
    "고쳐",
    "올바른 방법",
    "맞는 방법은",
)

# ── patch 트리거 T1b: 실패 신호 ───────────────────────────────────────────────
FAILURE_SIGNALS: tuple[str, ...] = (
    # English
    "failed",
    "error occurred",
    "didn't work",
    "did not work",
    "could not",
    "couldn't",
    # 한국어
    "실패",
    "안 됐",
    "안됐",
    "안 돼",
    "안돼",
    "오류",
    "에러",
    "동작하지 않",
    "작동하지 않",
    "되지 않",
    "안 되네",
)

# ── 스킬 이름 추출: "skill: my-name" / "스킬: my-name" (kebab ascii) ──────────
_SKILL_NAME_RE = re.compile(r"(?:skill|스킬)[:\s]+([a-z][a-z0-9-]{2,40})", re.IGNORECASE)


def _contains_any(text: str, signals: tuple[str, ...]) -> bool:
    """text(소문자화된) 안에 신호 중 하나라도 부분일치하면 True.

    영어 신호는 소문자, 한국어 신호는 대소문자 무관이므로 소문자 비교로 일괄 처리한다.
    """
    low = text.lower()
    return any(sig in low for sig in signals)


def has_skill_creation_intent(message: str) -> bool:
    return _contains_any(message, SKILL_INTENT_KEYWORDS)


def has_correction_signal(message: str) -> bool:
    return _contains_any(message, CORRECTION_SIGNALS)


def has_failure_signal(message: str) -> bool:
    return _contains_any(message, FAILURE_SIGNALS)


def extract_skill_name(message: str) -> str | None:
    """메시지에서 'skill: <kebab>' / '스킬: <kebab>' 패턴의 이름을 추출한다.

    스킬 이름은 SKILL.md 규약상 kebab-case ascii 다(한·영 대화 모두 동일). 한국어
    문장이라도 사용자가 영문 kebab 이름을 줄 수 있으므로 한/영 키워드를 함께 본다.
    없으면 None.
    """
    m = _SKILL_NAME_RE.search(message)
    if m:
        return m.group(1).strip("-").lower()
    return None
