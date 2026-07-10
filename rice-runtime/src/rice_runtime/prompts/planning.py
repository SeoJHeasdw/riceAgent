"""Planning prompts — intent triage + plan authoring + live plan rendering.

Plan-and-execute × ReAct (RICE_AGENT_EXECUTION.md §3)의 프롬프트 절반이 여기 있다:

  build_planning_messages  — 턴 시작 시 1회 호출되는 플래너 프롬프트. 의도를
                             해석해 "계획이 필요 없다"(simple) 또는 구조화된
                             계획(goal+steps)을 JSON으로 내놓는다. 하나의 호출이
                             분류와 계획 수립을 겸한다 — simple이면 출력이 몇
                             토큰이라 분류 전용 호출을 따로 둘 이유가 없다.
  PLAN_GUIDANCE            — 계획이 활성인 턴의 시스템 프롬프트에 1회 실리는
                             **정적** ReAct 작업 지침. 정적/동적을 나누는 이유:
                             시스템 프롬프트가 반복마다 변하면 프리픽스 캐시
                             (Ollama KV·Claude cache_control)가 매 반복 전면
                             무효화된다 — 지침은 변하지 않으므로 시스템에 두고,
                             변하는 상태만 아래 render_plan_status로 뺀다.
  render_plan_status       — 매 반복 히스토리 **끝에 ephemeral user 메시지**로
                             실리는 살아있는 계획 상태(+ drift 경고·스킬 승격
                             유도). 로컬 LLM이 멀티스텝 작업에서 길을 잃지 않게
                             매 호출 전체 상태를 다시 싣되(스냅샷 프롬프팅),
                             마지막 메시지 위치라 recency 주목도가 가장 높고
                             프리픽스 캐시는 tail 재계산만으로 유지된다.
  plan_to_markdown         — transcript에 남는 계획 생성 메시지 본문(다음 턴의
                             플래너/모델이 이전 계획을 문맥으로 보게 된다).

프롬프트는 영어, 내용(goal/step 제목)은 대화 언어를 따르게 지시한다 — 제목 생성
(title.py)과 같은 원칙: 언어 설정을 배선하지 않고 모델이 대화에서 읽는다.
"""

from __future__ import annotations

from typing import Sequence

from ..core.types import Message, PlanStep, ToolSpec, TurnPlan

# 플래너 출력은 계획 하나 — 낮은 온도로 구조 준수를 우선한다. thinking은 끄고
# 부르므로(planner.py) qwen 계열의 "저온+thinking = 무한 반복" 조합은 성립하지 않는다.
PLANNING_TEMPERATURE = 0.2

# 프롬프트는 짧고 긍정형으로 유지한다. 2026-07-04 다이어트: 소형(9b) 모델이 실제로
# 검증-루프에 빠졌던 세 가지 — "no code fence" 부정형 규칙(→ 디코딩 제약으로 대체),
# delegate_subagent fan-out 조건문(→ 실행 단계 관심사), propose_skill 마지막-단계
# 규칙(→ render_plan_block의 nudge_skill_capture와 중복) — 을 제거했다. 판단 규칙
# 하나하나가 소형 모델에겐 되새김질할 체크리스트 항목이다: 계획에 필요한 것만 남긴다.
_PLANNER_SYSTEM = """You are the planning stage of RICE, a personal agent. \
Decide whether the user's latest request needs a multi-step plan, and if so, write one.

Output a single JSON object, one of:

{"plan_needed": false}
  — for conversation, quick questions, or anything doable in a single action.

{"plan_needed": true, "goal": "...", "steps": [{"title": "...", "detail": "..."}, ...]}
  — for tasks with several distinct actions that build on each other \
(gather → analyze → produce → deliver).

Rules:
- goal: one sentence stating the user's intent, in the user's language. Step titles too.
- steps: 2 to %(max_steps)d, ordered by dependency. Each step is one verifiable unit of work.
- detail: which tool(s) the step uses and what it produces.
- Include the delivery/verification step (save it, send it, show it) — not just production."""


def plan_response_schema() -> dict:
    """플래너 출력의 JSON 스키마 — provider의 constrained decoding(`response_schema`)
    에 그대로 실린다. 프롬프트의 shape 서술과 같은 모양을 유지할 것. 단계 수 상한은
    스키마가 아니라 코드(_HARD_MAX_STEPS)로 자른다 — maxItems 등 스키마 확장 키워드는
    로컬 grammar 변환기 호환성이 제각각이라 기본 구문만 쓴다."""
    return {
        "type": "object",
        "properties": {
            "plan_needed": {"type": "boolean"},
            "goal": {"type": "string"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "detail": {"type": "string"},
                    },
                    "required": ["title"],
                },
            },
        },
        "required": ["plan_needed"],
    }


def _catalog_lines(tools: Sequence[ToolSpec], *, max_desc_chars: int = 100) -> str:
    """플래너용 도구 카탈로그 — 이름 + 짧은 설명. 승인 관련 문구는 걸러낸다:
    계획 수립에 무관한데 소형 모델이 계획 단계에서 승인 걱정을 되새김질하는
    연료가 된다(실측: write_file의 "Requires user approval"에 반복 고착)."""
    lines = []
    for t in tools:
        desc = " ".join(
            s for s in t.description.split(". ") if "approval" not in s.lower()
        )
        desc = " ".join(desc.split())
        if len(desc) > max_desc_chars:
            desc = desc[: max_desc_chars - 1].rstrip() + "…"
        lines.append(f"- {t.name}: {desc}")
    return "\n".join(lines) if lines else "- (no tools available this turn)"


def _history_lines(history: Sequence[Message], *, max_chars_each: int = 400) -> str:
    lines: list[str] = []
    for m in history:
        if m.role == "tool":
            continue  # 도구 원문은 의도 파악에 소음 — 요청/응답 흐름만 싣는다
        text = " ".join(m.content.split())
        if not text and m.tool_calls:
            text = f"(called tools: {', '.join(c.tool_name for c in m.tool_calls)})"
        if not text:
            continue
        if len(text) > max_chars_each:
            text = text[:max_chars_each].rstrip() + "…"
        lines.append(f"{m.role}: {text}")
    return "\n".join(lines)


def build_planning_messages(
    *,
    user_message: str,
    recent_history: Sequence[Message],
    tools: Sequence[ToolSpec],
    skill_names: Sequence[str] = (),
    max_steps: int = 8,
) -> tuple[str, tuple[Message, ...]]:
    """플래너 1회 호출의 (system, messages). recent_history는 마지막 user 메시지
    *이전*까지의 문맥 — user_message 자체는 별도 섹션으로 명시해 무엇을 계획해야
    하는지 모호하지 않게 한다."""
    system = _PLANNER_SYSTEM % {"max_steps": max(2, max_steps)}
    sections = [f"## The user's request (plan for THIS)\n{user_message.strip()}"]
    history = _history_lines(recent_history)
    if history:
        sections.append(f"## Recent conversation (context)\n{history}")
    sections.append(f"## Tools available this turn\n{_catalog_lines(tools)}")
    if skill_names:
        sections.append(
            "## Learned skills already injected (their procedures are in the main prompt)\n"
            + "\n".join(f"- {n}" for n in skill_names)
        )
    return system, (Message(role="user", content="\n\n".join(sections)),)


# ── live plan rendering (every execution-loop iteration) ─────────────────────

_STATUS_MARK = {
    "pending": "[ ]",
    "in_progress": "[>]",
    "done": "[v]",
    "failed": "[x]",
    "skipped": "[-]",
}

# 계획이 활성인 턴의 시스템 프롬프트에 1회 붙는 정적 지침. 여기 문장 하나를 바꾸면
# 그 턴의 프리픽스 캐시가 깨진다는 사실은 없다(턴 시작에 고정) — 단, 반복마다
# 달라지는 어떤 것도 이 텍스트에 넣지 말 것: 그것이 render_plan_status의 몫이다.
PLAN_GUIDANCE = """## Active Plan mode
A live plan for this task is appended as the LAST message of the conversation, \
updated every round. Work it like this (ReAct):
- Before each tool call, tell the user in one short sentence what you're doing and \
why (especially when redoing something), then call the tool.
- Work the first step that is not done. The runtime already marks the first step \
in_progress when the plan starts; report the transitions YOU make via update_plan.
- After EVERY tool result, briefly reassess before acting again: did it complete the current \
step (→ mark done, with a one-line note), did it fail (→ retry differently or mark failed), \
or did it change what the plan should be (→ revise via update_plan: add steps, skip obsolete ones)?
- Never silently drift from the plan. If reality disagrees with the plan, CHANGE the plan — \
update_plan is cheap.
- If the same tool fails twice in a row, do not repeat it a third time unchanged; revise the \
plan or ask the user.
- Quality over brevity: verify results (read back what you produced) before marking a step done.
- Only give your final answer when every step is done, failed, or skipped — and say what was \
done and what wasn't."""


def render_plan_status(
    plan: TurnPlan,
    *,
    rounds_without_update: int = 0,
    drift_guard_rounds: int = 4,
    nudge_skill_capture: bool = False,
) -> str:
    """매 반복 히스토리 끝에 ephemeral로 실리는 살아있는 계획 상태(스냅샷 프롬프팅).
    로컬 LLM은 대화 중반의 계획을 잊는다 — 마지막 메시지의 현재 상태가 유일한
    진실이 되게 한다. 작업 지침(정적)은 PLAN_GUIDANCE로 분리돼 시스템에 1회 실린다."""
    lines = [
        "[Active Plan — live status, revision %d]" % plan.revision,
        f"Goal: {plan.goal}",
        "Steps:",
    ]
    for s in plan.steps:
        mark = _STATUS_MARK.get(s.status, "[ ]")
        line = f"  {mark} {s.id}. {s.title}"
        if s.detail:
            line += f" — {s.detail}"
        if s.note:
            line += f" (note: {s.note})"
        lines.append(line)

    if rounds_without_update >= max(1, drift_guard_rounds):
        lines.append(
            f"\n⚠ REASSESS NOW: you have run {rounds_without_update} tool rounds without "
            "updating the plan. Before your next action, call update_plan to record real "
            "progress (mark finished steps done) or to revise the plan if your observations "
            "changed it. Do not keep calling tools on autopilot."
        )
    if nudge_skill_capture:
        lines.append(
            "\nAll plan steps are complete. If this was a reusable multi-step procedure, "
            "call propose_skill NOW to capture it (numbered steps, exact commands/tools), "
            "then give the user your final answer summarizing what was done."
        )
    return "\n".join(lines)


def plan_to_markdown(plan: TurnPlan) -> str:
    """transcript에 남는 계획 생성 메시지 본문 — 다음 턴/리로드에서 사람이 읽고,
    프로바이더 히스토리로도 실려 모델이 자기 계획을 문맥으로 본다."""
    lines = [f"**계획 수립** — {plan.goal}", ""]
    for s in plan.steps:
        lines.append(f"{s.id}. {s.title}" + (f" — {s.detail}" if s.detail else ""))
    return "\n".join(lines)
