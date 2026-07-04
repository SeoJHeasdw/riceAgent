"""Planning prompts — intent triage + plan authoring + live plan rendering.

Plan-and-execute × ReAct (RICE_AGENT_EXECUTION.md §3)의 프롬프트 절반이 여기 있다:

  build_planning_messages  — 턴 시작 시 1회 호출되는 플래너 프롬프트. 의도를
                             해석해 "계획이 필요 없다"(simple) 또는 구조화된
                             계획(goal+steps)을 JSON으로 내놓는다. 하나의 호출이
                             분류와 계획 수립을 겸한다 — simple이면 출력이 몇
                             토큰이라 분류 전용 호출을 따로 둘 이유가 없다.
  render_plan_block        — 실행 중 매 LLM 호출의 시스템 프롬프트에 실리는
                             살아있는 계획 상태 + ReAct 작업 지침. 로컬 LLM이
                             멀티스텝 작업에서 길을 잃지 않게 하는 핵심 장치로,
                             토큰을 아끼지 않고 매 반복 전체 상태를 다시 싣는다.
  plan_to_markdown         — transcript에 남는 계획 생성 메시지 본문(다음 턴의
                             플래너/모델이 이전 계획을 문맥으로 보게 된다).

프롬프트는 영어, 내용(goal/step 제목)은 대화 언어를 따르게 지시한다 — 제목 생성
(title.py)과 같은 원칙: 언어 설정을 배선하지 않고 모델이 대화에서 읽는다.
"""

from __future__ import annotations

from typing import Sequence

from ..core.types import Message, PlanStep, ToolSpec, TurnPlan

# 플래너 출력은 계획 하나 — 낮은 온도로 구조 준수를 우선한다.
PLANNING_TEMPERATURE = 0.2

_PLANNER_SYSTEM = """You are the planning stage of RICE, a personal agent. \
Decide whether the user's latest request needs an explicit multi-step plan, and if so, write one.

Respond with ONLY a JSON object (no prose, no code fence).

Not every request needs a plan. Output {"plan_needed": false} when the request is:
- casual conversation, a greeting, or a quick factual question
- a single-step action (one lookup, one file read, one short edit, one message)
- a clarification or follow-up the previous answer already covers

Output a plan when the request involves several distinct actions that build on each \
other — e.g. gather/analyze across multiple files or sources, synthesize results, then \
deliver them somewhere (mail, calendar, document), or any task you would naturally do in \
3+ stages. When in doubt for a multi-tool task, prefer planning: an explicit plan is how \
you stay reliable over a long task.

Plan JSON shape:
{
  "plan_needed": true,
  "goal": "one sentence stating the user's intent as you understood it",
  "steps": [
    {"title": "short imperative step name", "detail": "how: which tools, what input, what output; success criteria"}
  ]
}

Planning rules:
- Write goal and step titles in the same language the user is writing in.
- 2 to %(max_steps)d steps. Each step = one verifiable unit of work, ordered by dependency.
- Plan against the ACTUAL tools listed below — name the tool(s) in each step's detail. \
If several independent analyses are needed, plan one step that fans them out in parallel \
with delegate_subagent.
- If the goal produces something reusable (a repeatable procedure), make the LAST step \
"propose this procedure as a skill (propose_skill)".
- Steps must cover verification/delivery, not just production (e.g. "send it" is a step).
- Do NOT execute anything now. Do NOT call tools. Output the JSON only."""


def _catalog_lines(tools: Sequence[ToolSpec], *, max_desc_chars: int = 160) -> str:
    lines = []
    for t in tools:
        desc = " ".join(t.description.split())
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

_REACT_GUIDANCE = """How to work this plan (ReAct):
- Work the first step that is not done; set it in_progress via update_plan when you start it.
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


def render_plan_block(
    plan: TurnPlan,
    *,
    rounds_without_update: int = 0,
    drift_guard_rounds: int = 4,
    nudge_skill_capture: bool = False,
) -> str:
    """시스템 프롬프트에 붙는 살아있는 계획 블록. 매 반복 전체 상태를 다시 렌더한다
    (스냅샷 프롬프팅) — 로컬 LLM은 대화 중반의 계획을 잊는다; 시스템 프롬프트의
    현재 상태가 유일한 진실이 되게 한다."""
    lines = [
        "## Active Plan (you MUST work this plan)",
        f"Goal: {plan.goal}",
        f"Plan revision: {plan.revision}",
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
    lines.append("")
    lines.append(_REACT_GUIDANCE)

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
