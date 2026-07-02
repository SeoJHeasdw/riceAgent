"""learning/skill/guard.py — 콘텐츠 검증/시크릿 스크러빙 (Phase D 하드닝).

책임(single): LLM이든 사용자든, 스킬 본문(그리고 memory/store.py를 통해 memory
본문에도)이 디스크에 영구 저장되기 직전에 통과하는 마지막 체크포인트.

**막으려는 진짜 위협 하나만**: "대화에서 본 시크릿을 LLM이 무심코 스킬/메모리
본문에 옮겨적어, 이후 매 턴 시스템 프롬프트에 다시 주입되는 사고." Hermes의
`skills_guard.py`가 갖고 있는 exfiltration/injection/persistence/network/
obfuscation/traversal 전체 분류체계는 **포팅하지 않는다** — 그건 신뢰 못 할
제3자 마켓플레이스 설치물을 검사하기 위한 것이고 RICE엔 마켓플레이스가 없다
(learning_loop 이식 관련 결정, RICE_LEARNING_LOOP_DESIGN.md 참고).

적용 지점:
  - 스킬: `LearningService.apply_patch`/`create_skill`/`update_skill` 세 곳
    (SkillStore.write() 직전) — SkillStore 자체는 순수 I/O 레이어로 남겨둔다.
  - 메모리: `MemoryStore.add()` 내부 — 스킬과 달리 메모리는 `observe()`(정규식
    경로)와 `ReviewEngine._memory_effect()`(LLM 경로) 두 곳에서 각각 직접
    호출되므로, 호출부마다 가드를 기억해야 하는 구조보다 저장소 경계에
    두는 쪽이 빠뜨릴 위험이 없다(memory/store.py 참고, 기존 "content는 호출자
    책임" 원칙에 대한 좁고 명시적인 예외 — 시크릿 여부는 "사실이 유효한가"와는
    다른 차원의 판단이라 저장 경계에서 걸러도 책임 분리를 해치지 않는다).
"""

from __future__ import annotations

import re

from ...core.errors import ValidationError

# 사용자 스킬 이름 규약: kebab-case ascii, 2~64자 (SKILL.md name 규약과 일치).
# service.py에 inline으로 있던 것을 여기로 이동 — 검증 로직은 한 곳에 모은다.
VALID_SKILL_NAME = re.compile(r"^[a-z][a-z0-9-]{1,63}$")

DEFAULT_MAX_BODY_CHARS = 20_000

# (정규식, 라벨) — 보수적으로: 흔한 실제 시크릿 포맷만. 오탐이 적어야 정상적인
# 스킬 본문(예: "API 키를 설정하세요" 같은 설명문)을 망가뜨리지 않는다.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"), "Anthropic API key"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "API key (sk-*)"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"), "GitHub token"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"AIza[0-9A-Za-z\-_]{35}"), "Google API key"),
    (re.compile(r"Bearer\s+[A-Za-z0-9\-_.=]{20,}"), "Bearer token"),
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "JWT"),
    (
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----"),
        "private key",
    ),
)


def validate_content_size(body: str, max_chars: int = DEFAULT_MAX_BODY_CHARS) -> None:
    """`max_chars`를 넘으면 ValidationError. 폭주한 LLM 응답에 대한 마지막 방어선
    — 정상적인 스킬 본문이 이 상한에 걸릴 일은 거의 없다(넉넉하게 잡음)."""
    if len(body) > max_chars:
        raise ValidationError(
            f"본문이 너무 깁니다 ({len(body)}자 > 최대 {max_chars}자) — 더 짧은 절차로 나눠주세요."
        )


def scrub_secrets(body: str) -> tuple[str, list[str]]:
    """`body`에서 알려진 시크릿 패턴을 찾아 `[REDACTED:<라벨>]`로 치환한다.

    반환: (치환된 본문, 발견한 라벨 목록 — 중복 제거, 발견 순서 유지).
    아무것도 안 걸리면 (원문 그대로, []) — 흔한 경우이므로 얕은 카피조차 피한다.
    """
    found: list[str] = []
    seen: set[str] = set()
    redacted = body
    for pattern, label in _SECRET_PATTERNS:
        if not pattern.search(redacted):
            continue
        redacted = pattern.sub(f"[REDACTED:{label}]", redacted)
        if label not in seen:
            seen.add(label)
            found.append(label)
    return redacted, found
