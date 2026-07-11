"""learning/skill/verifier.py — 스킬 승격 검증 harness (2026-07-07 적대적 리뷰 P1).

WHY: 기존 승격 경로는 guard(이름/크기/시크릿/위험 패턴)만 통과하면 됐다 — 그것은
proposal *gate*지 verification *harness*가 아니다. HITL 승인은 "그럴듯하지만 틀린
스킬"을 막지 못한다: 사용자는 diff를 훑고 승인을 누르지, 참조된 도구가 실재하는지
본문이 절단됐는지 검사하지 않는다. 이 모듈은 스킬이 active로 승격되기 전에
통과해야 하는 검증 사다리다.

WHY NOT LLM 재검증: 바닥 사양이 8GB 노트북 + 20B 로컬 모델인 제품에서 "20B가 쓴
것을 20B가 재채점"은 지연 비용만 크고 신호가 약하다(시멘틱 기각과 같은 원칙 —
core/lexical.py). 그래서 결정론 lint가 기본이고, 판단이 필요한 항목은 **warn**으로
HITL 카드에 실어 사용자가 보게 하며, **객관적 무결성 위반만 fail**로 차단한다.
fail의 기준: "이 판정이 틀릴 수 있는가?"에 아니오라고 답할 수 있는 것만.

리뷰 제안 → 결정론 대응:
  static lint      → 절단(코드 펜스 미폐합)·이중 frontmatter·빈 본문·플레이스홀더
  mocked dry-run   → 본문이 "호출하라"고 지시하는 도구가 카탈로그에 실재하는지
                     (fabricated tool 참조 = 가장 흔한 '그럴듯하지만 틀림')
  trigger eval     → 발견가능성 lint: 설명 없는 스킬은 인덱스(유일한 발견 경로)에서
                     죽은 스킬이다
  regression eval  → patch가 기존 본문을 과도하게 삭제하는지
  quarantine       → fail 제안은 ledger에 `verifier_rejected`로 영속(감사 가능),
                     미드턴 발의는 실패 사유가 도구 결과로 모델에 돌아가 같은 턴에
                     고쳐 재제안한다(피드백 루프)

적용 지점(모두 LearningService — 서비스 경계 한 곳에 모아 새 경로가 우회 불가):
  1. stage_proposal(미드턴 T5) — fail이면 스테이징 자체가 거부되고 사유가 모델에
     반환된다. warn은 proposal.warnings로 승인 카드에 표시.
  2. _screen_proposals(review/learn/sweep/discovery — LLM이 낳은 모든 제안) —
     fail은 드랍+ledger, warn은 카드/제안함 payload에 병합.
  3. apply_patch / create_skill / update_skill — 최종 관문. 어떤 경로로 왔든
     (수동 편집 포함) fail 본문은 디스크에 닿지 못한다(ValidationError).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Collection, Sequence

from ...core import lexical

__all__ = ["Finding", "VerificationReport", "verify_candidate", "tool_call_refs"]


@dataclass(frozen=True)
class Finding:
    check: str  # kebab-case 검사 슬러그 (ledger detail/텔레메트리용)
    severity: str  # "fail" | "warn"
    message: str  # 사용자·모델 겸용 문구 — 카드/도구결과/에러에 그대로 실린다


@dataclass(frozen=True)
class VerificationReport:
    findings: tuple[Finding, ...]

    @property
    def failed(self) -> bool:
        return any(f.severity == "fail" for f in self.findings)

    @property
    def verdict(self) -> str:
        if self.failed:
            return "fail"
        return "warn" if self.findings else "pass"

    def failure_text(self) -> str:
        return " / ".join(f.message for f in self.findings if f.severity == "fail")

    def warning_lines(self) -> tuple[str, ...]:
        """승인 카드의 warnings 채널에 병합할 문구들 — 새 SSE 필드가 아니라 기존
        PatchProposal.warnings를 재사용한다(프론트 카드가 이미 렌더한다)."""
        return tuple(f"검증 경고: {f.message}" for f in self.findings if f.severity == "warn")

    def to_detail(self) -> dict:
        """ledger `verifier_rejected` 이벤트의 detail 직렬화."""
        return {
            "verdict": self.verdict,
            "findings": [
                {"check": f.check, "severity": f.severity, "message": f.message}
                for f in self.findings
            ],
        }


# ── 검사 상수 ─────────────────────────────────────────────────────────────────

_MIN_BODY_CHARS = 8  # 이 밑은 절차가 아니라 잡음 — 객관적 fail
_SHORT_BODY_CHARS = 60  # 이 밑은 "절차라기엔 빈약" — warn
_MIN_DESCRIPTION_CHARS = 8  # 인덱스 한 줄(발견 경로)이 성립하는 최소 길이
_PATCH_SHRINK_RATIO = 0.3  # 새 본문이 기존의 30% 미만이면 과도 삭제 의심
_PATCH_SHRINK_MIN_BASE = 400  # 짧은 스킬의 정당한 압축까지 플래그하지 않도록
_NEAR_DUP_JACCARD = 0.6  # 이름 토큰 자카드 유사도 임계

# 절차성 신호: 번호/불릿/헤딩/코드펜스 중 하나라도 있으면 "방법"으로 인정
_STEP_LINE = re.compile(r"(?m)^\s*(?:\d+[.)]\s|[-*•]\s|#{1,6}\s|```)")
# 미완성 본문 신호(작성 중 끊김) — 문장이 연결어/구두점으로 끝남
_DANGLING_ENDINGS = (",", ":", ";", "…", "—", "->", "→")
_PLACEHOLDER = re.compile(r"\b(?:TODO|TBD|FIXME)\b|\{\{[^}]{0,80}\}\}|<(?:placeholder|여기에)[^>]{0,40}>")
# frontmatter 이중 포함(모델이 body 인자에 SKILL.md 전체를 붙여넣은 경우)
_FM_IN_BODY = re.compile(r"\A\s*---\s*\n(?:[^\n]*\n){0,10}?\s*name\s*:")

# 도구 참조: 백틱 식별자 + 같은 지점의 호출 문맥("`X` 도구", "call `X`")만 본다 —
# 파라미터/변수 백틱(`file_path` 등)을 오탐하지 않기 위한 좁은 규칙.
_BACKTICK_IDENT = re.compile(r"`([a-z][a-z0-9_.]{2,63})`")
_CALL_BEFORE = re.compile(r"(?:call|invoke|run|use)\s*$", re.IGNORECASE)
# NOTE: 한글엔 \b 를 쓸 수 없다 — "도구를"처럼 조사가 붙으면 도구/를 사이에
# word boundary 가 생기지 않는다(둘 다 word char). 한글 대안은 접두 매칭으로 푼다.
_CALL_AFTER = re.compile(r"^\s*(?:도구|툴)|^\s*tool\b|^\s*(?:을|를|이|가|로|으로)?\s*(?:호출|실행)")


def tool_call_refs(body: str) -> list[str]:
    """호출 문맥에 놓인 백틱 식별자들(중복 제거, 등장 순서 유지). 공개 헬퍼 —
    observe()의 관여(engagement) 판정도 같은 규칙을 쓴다(주입된 스킬이 지시한
    도구가 실제로 호출됐는가)."""
    refs: list[str] = []
    for m in _BACKTICK_IDENT.finditer(body):
        before = body[max(0, m.start() - 12) : m.start()]
        after = body[m.end() : m.end() + 12]
        if _CALL_BEFORE.search(before) or _CALL_AFTER.search(after):
            if m.group(1) not in refs:
                refs.append(m.group(1))
    return refs


def _name_jaccard(a: str, b: str) -> float:
    ta, tb = lexical.tokens(a.replace("-", " ")), lexical.tokens(b.replace("-", " "))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def verify_candidate(
    *,
    name: str,
    body: str,
    description: str = "",
    tags: Sequence[str] = (),
    current_body: str | None = None,
    existing_names: Sequence[str] = (),
    known_tool_names: Collection[str] | None = None,
) -> VerificationReport:
    """스킬 후보(신규 생성 또는 patch 결과 본문)를 검증한다. 순수 함수 — IO 없음.

    `current_body`: patch면 기존 본문(regression 검사용), 생성이면 None.
    `existing_names`: 유사도 비교 대상(active 스킬 이름들, 자기 자신 제외).
    `known_tool_names`: 현재 도구 카탈로그. None = 카탈로그를 모름 → 도구 검사 스킵
    (검증이 배선 부재 때문에 오탐하면 안 된다 — fail-open은 이 검사에 한정).
    """
    findings: list[Finding] = []
    stripped = body.strip()

    # ── static lint: 객관적 무결성 (fail) ────────────────────────────────
    if len(stripped) < _MIN_BODY_CHARS:
        findings.append(Finding("empty-body", "fail", "본문이 사실상 비어 있습니다 — 절차 단계를 담아주세요."))
    if body.count("```") % 2 != 0:
        findings.append(
            Finding("unbalanced-fence", "fail", "코드 펜스(```)가 닫히지 않았습니다 — 본문이 중간에 잘렸을 가능성이 높습니다.")
        )
    if _FM_IN_BODY.search(body):
        findings.append(
            Finding("frontmatter-in-body", "fail", "본문이 SKILL.md frontmatter(---/name:)로 시작합니다 — body에는 절차 본문만 넣으세요.")
        )

    # ── static lint: 품질 신호 (warn) ────────────────────────────────────
    if _MIN_BODY_CHARS <= len(stripped) < _SHORT_BODY_CHARS:
        findings.append(Finding("thin-body", "warn", "본문이 매우 짧습니다 — 재사용 가능한 절차라기엔 빈약합니다."))
    if stripped and not _STEP_LINE.search(body):
        findings.append(
            Finding("no-steps", "warn", "번호/불릿 단계가 없습니다 — 스킬은 선언적 사실이 아니라 절차(method)여야 합니다. 사실은 메모리에 맡기세요.")
        )
    if stripped.endswith(_DANGLING_ENDINGS):
        findings.append(Finding("dangling-ending", "warn", "본문이 문장 중간에서 끝난 것으로 보입니다 — 마지막 단계를 완성하세요."))
    if _PLACEHOLDER.search(body):
        findings.append(Finding("placeholder", "warn", "TODO/TBD/플레이스홀더가 남아 있습니다 — 실제 값·단계로 채워주세요."))

    # ── mocked dry-run lite: 참조 도구 실재 검사 (warn — 카탈로그는 동적이다:
    #    지금 없는 커넥터가 나중에 연결될 수 있으므로 차단하지 않고 표시한다) ──
    if known_tool_names is not None:
        for ref in tool_call_refs(body):
            if ("_" in ref or "." in ref) and ref not in known_tool_names:
                findings.append(
                    Finding("unknown-tool", "warn", f"본문이 호출을 지시하는 도구 `{ref}` 가 현재 카탈로그에 없습니다 — 이름 오타이거나 존재하지 않는 도구일 수 있습니다.")
                )

    is_creation = current_body is None
    if is_creation:
        # ── trigger eval: 발견가능성 — 인덱스 한 줄이 유일한 발견 경로다 ──
        if len(description.strip()) < _MIN_DESCRIPTION_CHARS:
            findings.append(
                Finding("no-description", "warn", "설명이 없거나 너무 짧습니다 — 스킬 인덱스에서 발견되지 못해 사실상 죽은 스킬이 됩니다.")
            )
        # ── near-duplicate: 기존 스킬의 재발명 신호 ────────────────────────
        for other in existing_names:
            if other != name and _name_jaccard(name, other) >= _NEAR_DUP_JACCARD:
                findings.append(
                    Finding("near-duplicate", "warn", f"기존 스킬 '{other}'와(과) 이름이 유사합니다 — 새 스킬 대신 그 스킬의 patch를 고려하세요.")
                )
                break
    else:
        # ── regression eval: patch의 과도 삭제 ─────────────────────────────
        base = len(current_body.strip())
        if base >= _PATCH_SHRINK_MIN_BASE and len(stripped) < base * _PATCH_SHRINK_RATIO:
            findings.append(
                Finding("patch-shrink", "warn", f"이 patch는 기존 본문의 {100 - int(len(stripped) / base * 100)}%를 삭제합니다 — 의도한 축약인지 확인하세요.")
            )

    return VerificationReport(findings=tuple(findings))
