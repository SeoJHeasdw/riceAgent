"""learning/skill/guard.py — 콘텐츠 검증/시크릿 스크러빙 + 위험 신호 스캔.

책임(single): LLM이든 사용자든, 스킬 본문(그리고 memory/store.py를 통해 memory
본문에도)이 디스크에 영구 저장되기 직전에 통과하는 마지막 체크포인트.

두 층위의 방어:
  1. `scrub_secrets` — "대화에서 본 시크릿을 LLM이 무심코 스킬/메모리 본문에
     옮겨적어, 이후 매 턴 시스템 프롬프트에 다시 주입되는 사고"를 막는다(치환).
  2. `scan_risks` — Gmail/웹/도구 결과 같은 **외부
     콘텐츠가 스킬로 승격되면 prompt injection이 영구 시스템 프롬프트가 되는**
     경로에 대한 탐지. 차단이 아니라 **플래그**다: 결과는 PatchProposal.warnings로
     승인 카드에 표시되고, 저장 여부는 사용자가 판단한다(스킬은 HITL이 있으므로).
     단 하나의 예외: memory는 HITL 없이 즉시 주입되므로 위험 플래그가 있으면
     저장 자체를 거부한다(fail-closed — review/engine.py::_memory_effect).

Hermes `skills_guard.py`의 전체 분류체계(마켓플레이스 설치물 검사용)는 여전히
포팅하지 않는다 — 여기 패턴은 "학습 루프가 외부 콘텐츠를 영속화하는 순간"에
특화된 보수적 부분집합이다. 오탐이 정상 스킬의 승인 경험을 망치지 않도록
명백히 수상한 패턴만 잡는다.

적용 지점:
  - 스킬: `LearningService.apply_patch`/`create_skill`/`update_skill` 세 곳
    (SkillStore.write() 직전) — SkillStore 자체는 순수 I/O 레이어로 남겨둔다.
  - 메모리: `MemoryStore.add()` 내부 — 현재 메모리 작성 판단은
    `ReviewEngine._memory_effect()` 경로로 단일화됐지만, 메모리는 HITL 없이 즉시
    저장·주입 후보가 되므로 저장소 경계에 가드를 둔다(memory/store.py 참고,
    기존 "content는 호출자 책임" 원칙에 대한 좁고 명시적인 예외 — 시크릿 여부는
    "사실이 유효한가"와는 다른 차원의 판단이라 저장 경계에서 걸러도 책임 분리를
    해치지 않는다).
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
            f"body too long ({len(body)} chars > max {max_chars})",
            code="skill.body_too_long",
            params={"length": len(body), "max": max_chars},
        )


# (정규식, 라벨 코드) — 학습 안전 스캐너(scan_risks). 라벨은 안정 코드(snake_case)로,
# 로케일 문장은 프론트(learningWarnings.ts)가 결정한다 — 런타임의 자연어 합성 금지.
# 보수적으로: 정상 스킬("cron 등록 절차", "DB 마이그레이션")을 플래그하지 않도록,
# 명백히 수상한 조합만 잡는다.
_RISK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # ── prompt injection: 이후 매 턴 시스템 프롬프트에 주입될 본문이 모델의
    #    기존 지시를 무력화하려는 패턴 ─────────────────────────────────────────
    (
        re.compile(r"(?i)(ignore|disregard|forget)\s+(all\s+|any\s+)?(previous|prior|above|earlier|your)\s+(instructions?|rules?|prompts?|guidelines?)"),
        "prompt_injection",
    ),
    (
        re.compile(r"(?i)(이전|위의?|기존)\s*(지시|명령|규칙|프롬프트)\s*(를|은|을)?\s*(무시|잊)"),
        "prompt_injection",
    ),
    (
        re.compile(r"(?i)you\s+are\s+no\s+longer|new\s+system\s+prompt|override\s+(the\s+)?system"),
        "system_prompt_override",
    ),
    (
        re.compile(r"(?i)do\s+not\s+(tell|inform|show|reveal\s+this\s+to)\s+the\s+user|사용자에게\s*(알리지|보여주지|말하지)\s*마"),
        "hidden_instruction",
    ),
    # ── credential exfiltration: 키/토큰을 외부로 보내는 절차 ────────────────
    (
        re.compile(r"(?i)(curl|wget|fetch|requests?\.(post|get)|httpx)[^\n]{0,120}(api[_-]?key|token|secret|password|credential)"),
        "credential_exfiltration",
    ),
    (
        re.compile(r"(?i)(send|upload|post|전송|업로드)[^\n]{0,60}(api[_-]?key|secret|credential|자격증명|비밀번호)"),
        "credential_exfiltration",
    ),
    # ── destructive shell: 복구 불가능한 파괴 명령 ───────────────────────────
    (
        re.compile(r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)[a-zA-Z]*\s+(/|~|\$HOME)(\s|$|/\*)"),
        "destructive_shell_rm",
    ),
    (re.compile(r"(?i)\bmkfs(\.\w+)?\s"), "destructive_shell_mkfs"),
    (re.compile(r"\bdd\s+[^\n]*of=/dev/"), "destructive_shell_dd"),
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;"), "fork_bomb"),
    # ── remote-exec / persistence / backdoor ─────────────────────────────────
    (
        re.compile(r"(?i)(curl|wget)\s+[^\n|]{0,160}\|\s*(sudo\s+)?(ba)?sh"),
        "remote_script_execution",
    ),
    (re.compile(r"(?i)\bnc\s+(-[a-zA-Z]*l|.*\s-l\b)"), "network_listener"),
    (re.compile(r"/dev/tcp/"), "reverse_shell"),
    (re.compile(r"(?i)bash\s+-i\s+>&"), "reverse_shell"),
    (re.compile(r"(?i)>>\s*[^\n]*authorized_keys"), "ssh_key_persistence"),
    # ── hidden content: 사람 눈에 안 보이는 지시를 심는 통로 ─────────────────
    (
        re.compile("[\\u200b-\\u200f\\u2060-\\u2064\\ufeff]"),
        "hidden_characters",
    ),
)


def scan_risks(body: str) -> list[str]:
    """학습 본문(스킬/메모리)에서 위험 신호를 찾아 라벨 코드 목록을 반환한다.

    치환하지 않는다 — 스킬 경로는 코드를 PatchProposal.warnings에 실어 승인 카드에
    위험 플래그로 표시하고(로케일 문장은 프론트 몫, 판단은 사용자), memory 경로는
    코드가 하나라도 있으면 저장을 거부한다(HITL이 없으므로 fail-closed).
    반환: 중복 제거, 발견 순서 유지.
    """
    found: list[str] = []
    seen: set[str] = set()
    for pattern, label in _RISK_PATTERNS:
        if label in seen:
            continue
        if pattern.search(body):
            seen.add(label)
            found.append(label)
    return found


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
