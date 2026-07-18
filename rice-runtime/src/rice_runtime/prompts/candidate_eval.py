"""Candidate-eval prompts — task generation, rollout scaffold, pairwise judge
(LEARNING_LOOP §10.6, LEARNING_EVAL §10).

Three model-facing surfaces, all English (prompts/system.py convention — model
output mirrors the conversation language on its own):

  1. **Task generation** — paraphrase the held-out seed prompts (real user
     requests from the interval that produced the proposal) and derive a small
     success rubric per task. Paraphrasing matters: promoting a candidate that
     only handles the exact original wording would reward overfitting to one
     transcript.
  2. **Rollout scaffold** — the minimal system prompt both arms share. The ONLY
     difference between arms is the injected skill block (baseline body vs
     candidate body; absent for the baseline arm of a brand-new skill). Keeping
     the scaffold minimal and identical is the single-variable control the
     ablation depends on (LEARNING_EVAL §3).
  3. **Pairwise judge** — rubric-based comparison of two blinded transcripts
     (labels A/B, sides swapped deterministically per task so position bias
     averages out). Structured output via response_schema; temperature pinned
     low by the caller (구조화 생성 0.1 고정 — provider 온도와 독립).
"""

from __future__ import annotations

import json

# ── 1. task generation ───────────────────────────────────────────────────────

TASK_GEN_SYSTEM_PROMPT = (
    "You design evaluation tasks for an AI assistant's skill (a reusable "
    "procedure document). You are given real user requests that this skill is "
    "meant to handle, plus the skill's description.\n\n"
    "Produce:\n"
    "  - `paraphrases`: reworded variants of the given requests — same intent "
    "and difficulty, different wording. Keep each in the same language as the "
    "original request. Do not invent new requirements.\n"
    "  - `rubric`: 2-4 short, checkable criteria that a GOOD response to these "
    "requests must satisfy (e.g. 'states the deploy steps in order', 'uses the "
    "rollout-status check before declaring success'). Criteria must be "
    "observable from a response transcript alone, and must NOT mention the "
    "skill or its version — they describe task success, not skill usage.\n\n"
    "Return only the JSON object."
)

TASK_GEN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "paraphrases": {"type": "array", "items": {"type": "string"}},
        "rubric": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["paraphrases", "rubric"],
}


def build_task_gen_user_prompt(
    *, skill_id: str, description: str, seed_prompts: list[str], paraphrase_count: int
) -> str:
    seeds = "\n".join(f"  {i + 1}. {p}" for i, p in enumerate(seed_prompts)) or "  (none)"
    return (
        f"Skill: {skill_id}\n"
        f"Description: {description or '(none)'}\n\n"
        f"Real user requests this skill should handle:\n{seeds}\n\n"
        f"Generate {paraphrase_count} paraphrase task(s) and one shared rubric."
    )


# ── 2. rollout scaffold ──────────────────────────────────────────────────────

ROLLOUT_SYSTEM_PROMPT = (
    "You are a capable AI assistant. Complete the user's request.\n"
    "Tools may be available; call one when the task needs it. Tool results in "
    "this environment are simulated acknowledgements — treat a successful "
    "result as the action having been performed and continue. When the task "
    "is complete, answer the user directly with what you did and any next "
    "steps. Be concise."
)


def build_rollout_system_prompt(skill_block: str) -> str:
    """공유 scaffold + (arm 에 따라) 스킬 블록. `skill_block` 은 서비스가 실제
    주입 렌더러(_render_skills)로 만든 것을 그대로 받는다 — 평가 arm 이 실전
    주입과 같은 표면을 보게 하는 것이 공정성의 핵심이다."""
    if not skill_block:
        return ROLLOUT_SYSTEM_PROMPT
    return f"{ROLLOUT_SYSTEM_PROMPT}\n\n{skill_block}"


# ── 3. pairwise judge ────────────────────────────────────────────────────────

JUDGE_SYSTEM_PROMPT = (
    "You are an impartial evaluator. Two AI assistants (A and B) each handled "
    "the same user request. You receive the request, a success rubric, and "
    "both transcripts (tool calls + final answer).\n\n"
    "For each assistant, count how many rubric criteria its transcript "
    "satisfies. Judge only what is in the transcripts — do not reward length, "
    "confidence, or style. A criterion satisfied by a hallucinated claim that "
    "contradicts the transcript's own tool activity counts as NOT satisfied.\n\n"
    "Then pick the overall winner: the assistant satisfying more criteria; if "
    "equal, the one reaching the result with less unnecessary work; if still "
    "equal, 'tie'.\n\n"
    "Return only the JSON object."
)

JUDGE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "a_criteria_met": {"type": "integer"},
        "b_criteria_met": {"type": "integer"},
        "winner": {"type": "string", "enum": ["A", "B", "tie"]},
        "reason": {"type": "string"},
    },
    "required": ["a_criteria_met", "b_criteria_met", "winner"],
}


def build_judge_user_prompt(
    *, task_prompt: str, rubric: list[str], transcript_a: str, transcript_b: str
) -> str:
    rubric_lines = "\n".join(f"  {i + 1}. {c}" for i, c in enumerate(rubric))
    return (
        f"User request:\n{task_prompt}\n\n"
        f"Success rubric ({len(rubric)} criteria):\n{rubric_lines}\n\n"
        f"--- Assistant A transcript ---\n{transcript_a}\n\n"
        f"--- Assistant B transcript ---\n{transcript_b}\n\n"
        "Count satisfied criteria for A and B, then pick the winner."
    )


def render_transcript(tool_calls: list[dict], final_text: str) -> str:
    """롤아웃 결과 → 심판 입력 전사. 도구 호출은 이름+인자 요약 한 줄씩 —
    시뮬레이션 환경이라 결과는 전부 동일한 승인 응답이므로 싣지 않는다."""
    lines: list[str] = []
    for call in tool_calls:
        args = json.dumps(call.get("arguments") or {}, ensure_ascii=False)
        if len(args) > 300:
            args = args[:300] + "…"
        lines.append(f"[tool] {call.get('name')} {args}")
    lines.append(f"[final answer]\n{final_text.strip() or '(empty)'}")
    return "\n".join(lines)
