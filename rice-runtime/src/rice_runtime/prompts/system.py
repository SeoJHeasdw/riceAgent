"""System-prompt sections + section-based composition.

Static, app-authored model-facing prompt the agent always carries: its identity
(`BASE_SYSTEM_PROMPT`) and the standing guidance we inject (`SKILL_GUIDANCE`).
`compose_system_prompt` joins the base with the dynamic, per-turn sections the
learning service assembles (injected skills/memories, learned preferences) — the
"base ⊕ additions" synthesis the orchestrator used to inline.
"""

from __future__ import annotations

BASE_SYSTEM_PROMPT = (
    "You are RICE, a personal agent that learns from experience. "
    "Be precise and concise. Use tools when they help."
)

# Standing nudge toward the learning loop's skill-capture behavior. The learning
# service injects it into the per-turn system-prompt additions — it owns *when*,
# this package owns the *text*.
SKILL_GUIDANCE = (
    "If you complete a reusable, multi-step procedure (e.g. 5+ tool calls), save it "
    "as a SKILL.md — capture repeatable *methods*, not declarative facts."
)


def compose_system_prompt(*sections: str | None) -> str:
    """Join the base prompt with any non-empty additional `sections` (in order),
    separated by blank lines. Empty/whitespace sections are dropped so the final
    prompt never carries dangling blank blocks."""
    parts = [BASE_SYSTEM_PROMPT]
    parts.extend(s.strip() for s in sections if s and s.strip())
    return "\n\n".join(parts)
