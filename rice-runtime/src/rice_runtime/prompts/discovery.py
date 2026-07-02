"""Cross-session discovery prompt (Self-Improvement v3, 2026-07-03).

`prompts/review.py`가 "방금 끝난 대화 하나"를 보는 것과 달리, 이 패스는 **여러
세션의 다이제스트**를 한꺼번에 보고 세션 경계를 넘는 반복 패턴("이 사람은 매번
이 절차를 다시 설명하고 있다")을 스킬 후보로 발굴한다. 단일 세션 리뷰는 구조적으로
이걸 볼 수 없다 — 각 세션 안에서는 그 작업이 '한 번' 일어났을 뿐이므로.

리뷰 프롬프트와 의도적으로 다른 점:
  - **생성만 제안한다** (`propose_skill_create`만 노출). 다이제스트는 압축본이라
    기존 스킬 본문을 안전하게 고쳐 쓸 근거가 못 된다(리뷰 프롬프트의 "본문을 못
    봤으면 patch하지 마라"와 같은 원칙을 도구 수준에서 강제).
  - **2회 반복 최소선** — 한 세션에만 나온 절차는 리뷰 패스의 몫이다. 여기서
    잡을 것은 "여러 대화에 걸쳐 반복되는" 패턴뿐. 그래서 기준을 프롬프트에 명시.
  - 산출물은 파킹 없이 제안함(skill_suggestions)으로 들어간다 — 백그라운드에서
    돌 수 있으므로 사용자를 실시간으로 방해하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..core.types import Message, ToolSpec


@dataclass(frozen=True)
class SessionDigest:
    """discovery 입력 단위 — 한 세션의 압축 전사."""

    session_id: str
    title: str
    excerpt: str


DISCOVERY_SYSTEM_PROMPT = (
    "You are RICE's cross-conversation pattern miner. You read compact digests "
    "of several recent conversations plus the catalog of skills that already "
    "exist, and you identify RECURRING procedures — a class of work the user "
    "has done in TWO OR MORE separate conversations — that is not yet captured "
    "as a skill.\n\n"
    "Rules:\n"
    "  - Only propose a skill for a pattern that appears in at least two "
    "different conversations below. A one-off task is not a pattern.\n"
    "  - Do not duplicate an existing skill from the catalog. If a close "
    "match exists, propose nothing for that pattern.\n"
    "  - Propose at most 2 skills per run — pick the strongest patterns.\n"
    "  - Name skills at the CLASS level ('weekly-sales-report', not "
    "'report-2026-07-01'). kebab-case.\n"
    "  - Write the body as numbered, executable steps with exact commands/"
    "values where the digests show them. Write in the SAME LANGUAGE the "
    "conversations are in.\n"
    "  - Do NOT capture environment-dependent failures, negative claims, or "
    "one-off narratives (same exclusions as the learning reviewer).\n"
    "  - These are suggestions reviewed later by the user in an inbox — if "
    "nothing recurs, calling no tool is the correct answer.\n"
)


def _render_digests(digests: Sequence[SessionDigest]) -> str:
    lines = ["## Recent conversations (digests)"]
    for d in digests:
        lines.append(f"\n### [{d.session_id}] {d.title}\n{d.excerpt}")
    return "\n".join(lines)


def _render_catalog(catalog: Sequence[tuple[str, str]]) -> str:
    if not catalog:
        return "## Existing skills\n(none yet)"
    lines = ["## Existing skills (do NOT duplicate these)"]
    lines.extend(f"- [{name}] {desc}" for name, desc in catalog)
    return "\n".join(lines)


def build_discovery_messages(
    digests: Sequence[SessionDigest],
    catalog: Sequence[tuple[str, str]],
    *,
    rejected_recently: Sequence[str] = (),
) -> tuple[str, list[Message]]:
    parts = [_render_digests(digests), _render_catalog(catalog)]
    if rejected_recently:
        lines = ["## Recently REJECTED proposals (the user said no — don't re-propose)"]
        lines.extend(f"- {r}" for r in rejected_recently)
        parts.append("\n".join(lines))
    parts.append(
        "Identify recurring cross-conversation procedures and propose new skills "
        "for the strongest patterns (or none, if nothing truly recurs)."
    )
    return DISCOVERY_SYSTEM_PROMPT, [Message(role="user", content="\n\n".join(parts))]


def build_discovery_tool_specs() -> tuple[ToolSpec, ...]:
    """리뷰 엔진의 propose_skill_create와 같은 스키마 — 발굴 패스는 생성만 한다."""
    return (
        ToolSpec(
            name="propose_skill_create",
            description=(
                "Propose a brand-new class-level skill for a procedure that recurs "
                "across two or more of the conversations shown. Goes to the user's "
                "suggestion inbox for review."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "kebab-case, class-level (e.g. 'weekly-sales-report'), 2-64 chars.",
                    },
                    "description": {"type": "string", "description": "One-line summary."},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "0-5 short tags."},
                    "body": {
                        "type": "string",
                        "description": "Full SKILL.md body (markdown, no frontmatter) — numbered steps, exact commands, pitfalls.",
                    },
                },
                "required": ["name", "description", "body"],
            },
        ),
    )


def build_session_digest(
    session_id: str,
    title: str,
    messages: Sequence[Message],
    *,
    max_chars: int = 2_000,
    max_messages: int = 30,
) -> SessionDigest:
    """전사 → 압축 다이제스트. 최근 메시지 우선(오래된 턴부터 잘림), 메시지당
    240자 — 발굴에 필요한 건 '무슨 종류의 일을 어떤 절차로 했는가'지 전문이 아니다.
    tool 역할 메시지는 결과 원문이 길고 노이즈라 이름만 남긴다."""
    lines: list[str] = []
    for m in messages[-max_messages:]:
        if m.role == "tool":
            tool_name = (m.metadata or {}).get("tool_name", "tool")
            lines.append(f"[tool:{tool_name} 결과 생략]")
            continue
        content = (m.content or "").strip()
        if not content:
            continue
        if len(content) > 240:
            content = content[:240] + "…"
        lines.append(f"{m.role}: {content}")
    excerpt = "\n".join(lines)
    if len(excerpt) > max_chars:
        excerpt = excerpt[-max_chars:]
    return SessionDigest(session_id=session_id, title=title or "(제목 없음)", excerpt=excerpt)
