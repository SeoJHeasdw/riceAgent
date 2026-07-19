"""core/guard.py — 콘텐츠 안전 검사 공용 메커니즘 (시크릿 스크러빙 + 위험 신호 스캔).

원래 learning/skill/guard.py 에 있던 구현을 core 로 승격했다(2026-07-19).
WHY: 스킬/메모리 학습 경로(learning)뿐 아니라 **플러그인 설치 경로(execution/plugin)**
도 같은 스캐너가 필요해졌는데, execution→learning 직접 import 는 모듈 경계 위반이다
(RUNTIME_DIRECTORY #14 — core 는 모듈 간 직접 import 금지선의 유일한 공용 인프라).
learning/skill/guard.py 는 재수출 심으로 남아 기존 소비자의 import 경로를 보존한다.

책임(single): 외부에서 온(혹은 LLM 이 만든) 텍스트가 **영구 표면**(스킬 본문,
memory, 설치 플러그인의 SKILL.md → 시스템 프롬프트 주입 후보)이 되기 직전의
마지막 체크포인트. 정책(플래그/차단/비활성)은 각 소비자 모듈이 결정하고,
여기는 탐지 메커니즘만 둔다:

  1. `scrub_secrets` — 시크릿이 학습 본문에 옮겨적혀 매 턴 시스템 프롬프트로
     재주입되는 사고를 막는다(치환).
  2. `scan_risks` — 외부 콘텐츠가 영구 프롬프트 표면으로 승격될 때의 prompt
     injection / 파괴 명령 / 유출 절차 탐지. 차단이 아니라 **라벨 코드 반환**:
     라벨은 안정 코드(snake_case)이고 로케일 문장은 프론트(learningWarnings.ts)가
     결정한다 — 런타임의 자연어 합성 금지.

Hermes `skills_guard.py`의 전체 분류체계는 여전히 포팅하지 않는다 — 여기 패턴은
보수적 부분집합으로, 오탐이 정상 콘텐츠의 사용 경험을 망치지 않도록 명백히
수상한 조합만 잡는다.

소비자별 정책(정본은 각 모듈):
  - 스킬 학습: 플래그 → PatchProposal.warnings 로 승인 카드 표시(HITL 판단).
  - memory: HITL 없이 즉시 주입되므로 위험 플래그 시 저장 거부(fail-closed).
  - 플러그인/스킬 artifact 설치: 플래그 영속(installed_artifacts.risk_flags) +
    플러그인은 자동 비활성 — 활성화는 사용자 판단(execution/plugin/loader.py).
"""

from __future__ import annotations

import re

from .errors import ValidationError

# 사용자 스킬 이름 규약: kebab-case ascii, 2~64자 (SKILL.md name 규약과 일치).
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


# (정규식, 라벨 코드) — 안전 스캐너(scan_risks). 라벨은 안정 코드(snake_case)로,
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
    """본문에서 위험 신호를 찾아 라벨 코드 목록을 반환한다.

    치환하지 않는다 — 정책(플래그 표시/저장 거부/설치 비활성)은 호출자 몫.
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
