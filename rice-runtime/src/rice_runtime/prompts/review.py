"""Learning-review prompt — the LLM pass that replaces regex-based skill/memory
authoring (Phase A, Hermes learning_loop 이식, 2026-07-02).

Adapted from Hermes's `_COMBINED_REVIEW_PROMPT`/`_SKILL_REVIEW_PROMPT`
(learning_loop/background_review.py) with these deliberate RICE-specific deltas:

  - **No direct writes.** Hermes's review agent calls `skill_manage`/`memory` tools
    that write to disk immediately. RICE's HITL invariant (RICE_LEARNING_LOOP_DESIGN
    §4/§5.2.1 — "스킬은 사용자 확인 후 저장") means this model has NO write access at
    all: it can only call `propose_skill_create`/`propose_skill_patch` (staged for
    human approval) or `propose_memory_write` (memory has never been HITL-gated in
    RICE's design, so this one applies immediately — see engine.py).
  - **No references/templates/scripts.** RICE's SKILL.md is a flat single-file
    format (RICE_LEARNING_LOOP_DESIGN §2) with no support-file subdirectory concept
    — Hermes's whole "package integrity" section doesn't apply.
  - **No protected/bundled/hub/pinned three-tier model.** RICE has no skill
    marketplace yet; the only provenance distinction that matters is `source`
    (plugin-provided skills, `source` starting with "plugin:", are read-only to
    this reviewer — the RICE equivalent of Hermes's "don't touch bundled/hub").
  - **Language mirrors the conversation**, not a fixed locale (same principle as
    `prompts/title.py` — RICE is ko/en bilingual and doesn't force a locale into
    model-authored content).

Kept faithfully from the Hermes original (the genuinely valuable part): the four
first-class trigger signals, the update-before-create preference order, and —
especially — the explicit "do NOT capture" list. Model-facing prompt text itself is
English (matches `prompts/system.py`'s established convention — ko is a UI-locale
concern, not a prompt-authoring one).
"""

from __future__ import annotations

from typing import Sequence

from ..core.types import Message, ToolSpec

REVIEW_SYSTEM_PROMPT = (
    "You are RICE's learning reviewer. You read a conversation the main agent "
    "just had and decide what durable knowledge is worth keeping: reusable "
    "procedures (skills) and facts/preferences about the user (memory).\n\n"
    "Be ACTIVE — a substantive conversation (several tool calls, a corrected "
    "approach, a non-trivial fix) usually produces at least one worthwhile "
    "update. Doing nothing is a missed opportunity, not a safe default. But "
    "don't force it: a short exchange with no correction and no new technique "
    "warrants no calls at all — just don't call any tool in that case.\n\n"
    "Signals that warrant a skill update (any one is enough):\n"
    "  - The user corrected your style, tone, format, verbosity, or approach. "
    "Frustration ('stop doing X', 'don't format like this', 'that's not what "
    "I asked') is a FIRST-CLASS SKILL signal, not just a memory signal — "
    "embed the lesson in the skill that governs that class of task, not only "
    "in a memory fact.\n"
    "  - The user corrected your workflow, sequence of steps, or a specific "
    "value/command. Encode the correction as a fixed step or pitfall.\n"
    "  - A non-trivial technique, fix, or workaround emerged that a future "
    "conversation would benefit from.\n"
    "  - A skill that was shown to you below (its full current body) turned "
    "out to be wrong, missing a step, or outdated — patch it.\n\n"
    "Preference order for skill updates — prefer the earliest that fits:\n"
    "  1. PATCH a skill whose full current body is shown to you below. It's "
    "already in play; it's the right one to extend.\n"
    "  2. If none of those fit but the shown catalog of other existing "
    "skills suggests a close match, say so in your reasoning but do NOT "
    "guess at its content — you were not shown its body, so you cannot "
    "safely rewrite it. Create a new skill instead, or leave it for the "
    "curator's consolidation pass to catch the overlap later.\n"
    "  3. CREATE a new skill only when nothing existing covers this class of "
    "task. Name it at the CLASS level (e.g. 'k8s-service-deploy', not "
    "'fix-pr-482' or 'today-deploy-debug') — if the name only makes sense "
    "for this one conversation, it's the wrong name.\n\n"
    "Do NOT patch a skill whose source starts with 'plugin:' — those are "
    "provided by an installed plugin, not owned by the learning loop.\n\n"
    "Do NOT capture (these become persistent constraints that bite later, "
    "when the environment changes):\n"
    "  - Environment-dependent failures: missing binaries, unset credentials, "
    "an unconfigured connector. The user can fix these; they are not durable "
    "rules about how to do the task.\n"
    "  - Negative claims ('X tool doesn't work', 'Y is broken') — these "
    "harden into self-imposed refusals long after the real problem is fixed.\n"
    "  - Transient errors that were already resolved within this same "
    "conversation (if a retry worked, the lesson is the retry pattern, not "
    "the original failure).\n"
    "  - One-off task narratives ('summarize this PR', 'what's the weather') "
    "— not a reusable class of work.\n\n"
    "Skill proposals are reviewed by a human before they're saved — you are "
    "PROPOSING, not writing to disk. Memory proposals (facts/preferences) "
    "apply immediately (memory has always been ungated in this app), so be "
    "conservative there: only propose a fact/preference that is clearly "
    "durable and clearly stated, and skip anything already listed under "
    "'Already-known facts/preferences' below.\n\n"
    "Write skill bodies, descriptions, and changelogs in the SAME LANGUAGE "
    "the conversation below is in — don't force a fixed locale.\n\n"
    "When you patch a skill, set trigger_reason to whichever best describes "
    "why: T1a (the user explicitly corrected the procedure or a value), T1b "
    "(a tool call using the old approach failed and a different approach "
    "worked), or T1c (you noticed a missing step or stale value yourself, "
    "without an explicit user correction).\n\n"
    "Usage telemetry hints (shown next to each full-body skill below):\n"
    "  - high `friction` = turns where this skill was in play repeatedly "
    "coincided with user corrections or failures — scrutinize its procedure; "
    "if this conversation shows what went wrong, patch it.\n"
    "  - high `routing_miss` = the model keeps having to load this skill "
    "manually because the request wording never matches its name/tags/"
    "description — fix DISCOVERABILITY, not just the body: use the patch "
    "tool's `new_description`/`new_tags` to add the words users actually say "
    "(keep them short; both Korean and English trigger words help)."
)


def _render_skill_catalog(
    full_body_skills: Sequence[tuple[str, str, str]],  # (skill_id, description, body)
    compact_skills: Sequence[tuple[str, str]],  # (skill_id, description)
    usage_hints: dict[str, str] | None = None,  # skill_id → "use=…, friction=…, …"
) -> str:
    """Render the skill library into the review's context block.

    Full bodies are shown ONLY for skills that fit the token budget (mirrors
    `LearningService.prefetch`'s own budget-fit — see engine.py) — those are
    exactly the skill_ids `propose_skill_patch` is allowed to target (the tool
    schema's `skill_id` enum is built from the same list), so the model can
    never be asked to rewrite a skill it never actually saw the content of.

    `usage_hints` (2026-07-11): 실측 텔레메트리 한 줄(use/friction/routing_miss)을
    헤딩에 붙인다 — 시스템 프롬프트의 telemetry-hints 지침이 이 줄을 읽는다.
    """
    lines: list[str] = []
    if full_body_skills:
        lines.append("## Existing skills (full body shown — you may PATCH these)")
        for skill_id, description, body in full_body_skills:
            hint = (usage_hints or {}).get(skill_id, "")
            hint_part = f" ({hint})" if hint else ""
            lines.append(f"\n### [{skill_id}] {description}{hint_part}\n{body}")
    if compact_skills:
        lines.append(
            "\n## Other existing skills (name/description only — do NOT patch "
            "these, you haven't seen their content; a close match here just "
            "means don't create a near-duplicate)"
        )
        for skill_id, description in compact_skills:
            lines.append(f"- [{skill_id}] {description}")
    if not lines:
        lines.append("## Existing skills\n(none yet)")
    return "\n".join(lines)


def _render_known_facts(facts: Sequence[str]) -> str:
    if not facts:
        return "## Already-known facts/preferences\n(none yet)"
    lines = ["## Already-known facts/preferences (don't re-propose these)"]
    lines.extend(f"- {f}" for f in facts)
    return "\n".join(lines)


def _render_rejections(rejected: Sequence[str]) -> str:
    """Self-Improvement v3: 사용자가 최근 거부한 제안 목록. LLM이 같은 제안을
    반복하지 않게 하는 사전(prompt) 방어 — 사후 방어(엔진의 쿨다운 억제)와 짝."""
    if not rejected:
        return ""
    lines = [
        "## Recently REJECTED proposals (the user said no — do NOT re-propose "
        "the same change; only touch these skills again for a clearly different reason)"
    ]
    lines.extend(f"- {r}" for r in rejected)
    return "\n".join(lines)


def build_review_messages(
    history: Sequence[Message],
    *,
    full_body_skills: Sequence[tuple[str, str, str]],
    compact_skills: Sequence[tuple[str, str]],
    known_facts: Sequence[str],
    rejected_recently: Sequence[str] = (),
    usage_hints: dict[str, str] | None = None,
    instruction: str = "Review the conversation above and propose any durable skill/memory updates.",
) -> tuple[str, list[Message]]:
    """Build (system, messages) for a one-shot review call. `history` is the
    actual conversation transcript (not a compact excerpt like `title.py` uses —
    the review needs the real detail to judge nuance), followed by one final
    user-role message carrying the skill catalog + known facts + recent
    rejections (meta-learning, Self-Improvement v3) + the ask.
    `instruction` is overridden by Phase C's explicit "save this as a skill"
    action (learn()) — same engine, more directive final message.
    """
    rejection_block = _render_rejections(rejected_recently)
    parts = [
        _render_skill_catalog(full_body_skills, compact_skills, usage_hints),
        _render_known_facts(known_facts),
    ]
    if rejection_block:
        parts.append(rejection_block)
    parts.append(instruction)
    context = "\n\n".join(parts)
    messages = list(history) + [Message(role="user", content=context)]
    return REVIEW_SYSTEM_PROMPT, messages


def build_review_tool_specs(patchable_skill_ids: Sequence[str]) -> tuple[ToolSpec, ...]:
    """The 2-3 pseudo-tools the review model may call. Never dispatched/executed —
    `ReviewEngine` reads the resulting `ToolCall`s directly as structured proposals
    (no `ToolDispatcher` in this path at all, so these need no real permission
    model). `propose_skill_patch` is omitted entirely when there is nothing it
    could validly target (no skill shown with a full body) — a stronger guarantee
    than a prompt instruction: the model structurally cannot call it."""
    specs: list[ToolSpec] = [
        ToolSpec(
            name="propose_skill_create",
            description=(
                "Propose a brand-new class-level skill. Only when no existing skill "
                "(shown above) covers this class of task. A human reviews the full "
                "proposed body before it's saved."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "kebab-case, class-level (e.g. 'k8s-service-deploy'), 2-64 chars.",
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
        ToolSpec(
            name="propose_memory_write",
            description=(
                "Record a durable fact about the user/project or a stated preference. "
                "Applies immediately (no human review) — only call this for something "
                "clearly durable and not already listed as known."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["fact", "preference"]},
                    "content": {"type": "string", "description": "A single declarative sentence."},
                },
                "required": ["kind", "content"],
            },
        ),
    ]
    if patchable_skill_ids:
        specs.insert(
            1,
            ToolSpec(
                name="propose_skill_patch",
                description=(
                    "Propose a full replacement body for an existing skill shown above "
                    "with its full content. A human reviews the diff before it's saved."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "skill_id": {"type": "string", "enum": list(patchable_skill_ids)},
                        "changelog": {"type": "string", "description": "One-line summary of what changed and why."},
                        "new_body": {"type": "string", "description": "Full replacement SKILL.md body (not a diff)."},
                        "trigger_reason": {"type": "string", "enum": ["T1a", "T1b", "T1c"]},
                        "new_description": {
                            "type": "string",
                            "description": (
                                "Optional replacement one-line description — use when the "
                                "skill's routing_miss telemetry shows it isn't being found "
                                "(add the words users actually say)."
                            ),
                        },
                        "new_tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional replacement tags (max 5) — same discoverability purpose.",
                        },
                    },
                    "required": ["skill_id", "changelog", "new_body", "trigger_reason"],
                },
            ),
        )
    return tuple(specs)


LEARN_SYSTEM_PROMPT = REVIEW_SYSTEM_PROMPT + (
    "\n\nThe user explicitly asked you to distill this conversation into a "
    "skill right now — unlike a routine review pass, 'nothing to propose' "
    "should be rare here. If the conversation contains any reusable "
    "procedure at all, propose it (create or patch); only skip if the "
    "conversation truly has no procedural content to capture (e.g. it was "
    "pure Q&A with no steps taken)."
)


def build_learn_instruction(user_instruction: str) -> str:
    """The final user-message ask for Phase C's explicit learn() call — mirrors
    Hermes's `learn_prompt.py` empty-input fallback ('the workflow we just went
    through... distill it into a reusable skill')."""
    if user_instruction and user_instruction.strip():
        return (
            "The user explicitly asked you to learn a skill from this "
            f"conversation, with this instruction: {user_instruction.strip()}\n"
            "Propose the skill (create or patch) now."
        )
    return (
        "The user explicitly asked you to save this conversation as a "
        "reusable skill. Review the steps actually taken and distill them "
        "into one skill proposal (create or patch) now."
    )
