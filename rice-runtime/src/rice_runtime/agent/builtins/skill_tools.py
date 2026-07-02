"""builtins/skill_tools.py — 미드턴 스킬 승격 도구 (Self-Improvement v3, T5).

지금까지 SKILL_GUIDANCE는 메인 에이전트에게 "재사용 절차를 SKILL.md로 저장하라"고
지시했지만 정작 저장할 도구가 없었다(공중 지시 — 에이전트가 "저장하겠습니다"라고
말만 하고 아무 일도 안 일어남). 이 모듈이 그 간극을 닫는다: 에이전트는 대화
도중 `propose_skill`을 호출해 "이걸 스킬로 승격할까요?"를 실제 행동으로 옮긴다.

**쓰기 권한은 여전히 없다.** 이 도구는 파일에 닿지 않는다 —
`LearningService.stage_proposal()`에 제안을 스테이징할 뿐이고, 오케스트레이터가
턴 종료(step 7) 때 스테이징을 드레인해 메인 스트림 안에서 기존 HITL diff 카드로
서피싱한다(`skill_patch_proposed`, trigger="T5"). 즉 승인 파이프라인은 리뷰/발굴과
완전히 동일하고, 다른 것은 발의 주체(사후 리뷰 LLM ↔ 대화 중인 에이전트 자신)뿐.

`requires_approval=False`인 이유: 도구 실행 자체는 스테이징(무해)이고, 실제 파일
쓰기는 제안 카드의 승인(별도 HITL)이 지킨다 — 이중 승인은 UX만 나쁘게 한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core.types import ToolSpec
from ..tool_dispatch import BuiltinTool

if TYPE_CHECKING:
    from ...learning.interfaces import ILearningService


def tools(learning: "ILearningService") -> list[tuple[ToolSpec, BuiltinTool]]:
    async def propose_skill(args: dict, session_id: str = "") -> object:
        return learning.stage_proposal(
            session_id,
            name=str(args.get("name") or ""),
            body=str(args.get("body") or ""),
            description=str(args.get("description") or ""),
            tags=tuple(str(t) for t in (args.get("tags") or ())),
            changelog=str(args.get("changelog") or ""),
        )

    # dispatcher가 이 마커를 보고 session_id를 두 번째 인자로 넘긴다
    # (tool_dispatch.py — 스테이징은 세션 단위로 격리돼야 한다).
    propose_skill.needs_session = True  # type: ignore[attr-defined]

    spec = ToolSpec(
        name="propose_skill",
        description=(
            "Propose saving a reusable procedure from this conversation as a skill "
            "(or improving an existing skill by that name). Use when you completed a "
            "reusable multi-step procedure, when the user asks to save/promote "
            "something as a skill mid-conversation, or when you notice an existing "
            "skill is wrong/outdated. This does NOT write anything — the user sees "
            "an approval card with a diff after your answer and decides. Mention in "
            "your answer that you've proposed it."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "kebab-case, class-level skill name (e.g. 'k8s-service-deploy'). "
                        "Use an existing skill's name to propose improving it."
                    ),
                },
                "body": {
                    "type": "string",
                    "description": (
                        "Full SKILL.md body (markdown, no frontmatter) — numbered steps, "
                        "exact commands, pitfalls. For an existing skill this REPLACES "
                        "the whole body, so carry over still-valid content."
                    ),
                },
                "description": {"type": "string", "description": "One-line summary (new skills only)."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "0-5 short tags (new skills only)."},
                "changelog": {
                    "type": "string",
                    "description": "One line: what changed / why this is worth saving. Same language as the conversation.",
                },
            },
            "required": ["name", "body"],
        },
        source="builtin",
    )
    return [(spec, propose_skill)]
