"""builtins/skill_tools.py — 스킬 온디맨드 로딩 + 미드턴 승격 도구.

세 개의 스킬 관련 빌트인을 등록한다 (learning 서비스가 배선됐을 때만):

  skill_search  — 키워드로 active 스킬 인덱스를 검색한다(BM25류 어휘 랭킹).
  skill_view    — 스킬 전문(SKILL.md 본문)을 온디맨드로 로딩한다.
  propose_skill — 재사용 절차를 스킬로 승격/개선 제안한다(아래).

skill_search/skill_view 는 하이브리드 스킬 주입의 '온디맨드' 절반이다. 시스템
프롬프트엔 이제 active 스킬의 **짧은 인덱스**만 항상 실리고(inject-all-active
폐기 — 로컬 모델의 좁은 컨텍스트를 매 턴 잠식했다), 관련도 높은 소수만 전문
자동 주입된다. 모델이 인덱스를 보고 필요한 전문을 skill_view 로 당기는 구조는
tool_search/tool_describe 의 progressive-disclosure 와 같은 철학이다.

propose_skill: SKILL_GUIDANCE는 "재사용 절차를 SKILL.md로 저장하라"고 지시하지만
정작 저장할 도구가 없었다. 이 도구가 그 간극을 닫는다 — 에이전트가 대화 도중
`propose_skill`을 호출해 승격 제안을 스테이징한다.

**세 도구 모두 파일에 쓰지 않는다.** skill_search/view 는 읽기이고, propose_skill 은
`LearningService.stage_proposal()`에 제안을 스테이징할 뿐이다 — 오케스트레이터가
턴 종료(step 7) 때 드레인해 메인 스트림 안에서 기존 HITL diff 카드로 서피싱한다
(`skill_patch_proposed`, trigger="T5"). 승인 파이프라인은 리뷰/발굴과 동일하고,
다른 것은 발의 주체(사후 리뷰 LLM ↔ 대화 중인 에이전트 자신)뿐.

`requires_approval=False`인 이유: 도구 실행 자체는 읽기/스테이징(무해)이고, 실제
파일 쓰기는 제안 카드의 승인(별도 HITL)이 지킨다 — 이중 승인은 UX만 나쁘게 한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core.errors import NotFoundError
from ...core.types import ToolSpec
from ..tool_dispatch import BuiltinTool

if TYPE_CHECKING:
    from ...learning.interfaces import ILearningService


def tools(learning: "ILearningService") -> list[tuple[ToolSpec, BuiltinTool]]:
    async def skill_search(args: dict) -> object:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "query is required"}
        limit = args.get("limit")
        limit = int(limit) if isinstance(limit, (int, float)) else 8
        results = learning.search_skills(query, limit=max(1, min(limit, 20)))
        return {"query": query, "results": results}

    async def skill_view(args: dict, session_id: str = "") -> object:
        name = str(args.get("name") or "").strip()
        if not name:
            return {"error": "name is required"}
        try:
            return learning.view_skill_body(session_id, name)
        except NotFoundError:
            # 있는 스킬 이름을 모델이 다시 고를 수 있게 후보를 함께 돌려준다.
            return {
                "error": f"skill {name!r} not found or not active",
                "did_you_mean": learning.search_skills(name, limit=5),
            }

    skill_view.needs_session = True  # type: ignore[attr-defined]

    search_spec = ToolSpec(
        name="skill_search",
        description=(
            "Search available skills by keyword and get their names + one-line "
            "descriptions (does NOT load the full procedure — use skill_view for "
            "that). Use when the skill index in your context doesn't obviously "
            "cover the task but a saved procedure might exist."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords describing the task."},
                "limit": {"type": "integer", "description": "Max results (default 8)."},
            },
            "required": ["query"],
        },
        source="builtin",
    )
    view_spec = ToolSpec(
        name="skill_view",
        description=(
            "Load a skill's full step-by-step procedure (SKILL.md body) by name. "
            "Call this BEFORE doing a task a skill covers — the skill index in your "
            "context lists names + summaries only; the actual steps/commands live "
            "here. Find names via the index or skill_search."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Exact skill name (kebab-case)."},
            },
            "required": ["name"],
        },
        source="builtin",
    )

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
        parallel_safe=False,  # 사용자 가시 효과(승인 카드) 스테이징 — 직렬 실행
    )
    return [
        (search_spec, skill_search),
        (view_spec, skill_view),
        (spec, propose_skill),
    ]
