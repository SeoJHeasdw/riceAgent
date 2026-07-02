"""Curator consolidation prompt — the LLM pass that merges overlapping skills
into class-level umbrellas (Phase B, adapted from Hermes's `CURATOR_REVIEW_PROMPT`
in learning_loop/curator.py).

Deltas from the Hermes original (same reasoning as prompts/review.py):
  - **No direct writes.** Proposals only (`propose_consolidate`/`propose_prune`) —
    RICE's curator never writes to disk unsupervised; `LearningService.consolidate()`
    returns proposals, the UI holds them, the user approves each individually.
  - **No references/templates/scripts demote-to-support-file mechanics.** RICE's
    SKILL.md is a flat single-file format — there is no third consolidation method
    beyond "merge into existing umbrella" / "create new umbrella".
  - **No protected/bundled/hub/pinned three-tier model.** RICE has no marketplace —
    the only provenance distinction is `source`; skills whose `source` starts with
    "plugin:" are excluded from the candidate list entirely (never shown to this
    prompt at all, not just "protected" — nothing to reason about).
  - **No terminal/mv file-move mechanics** — nothing to move, RICE consolidation
    only ever produces patch-shaped (umbrella) or archive-shaped (absorbed sibling)
    proposals.

Kept faithfully (the genuinely valuable part of the original): the "would a human
maintainer write N separate skills or one with N labeled subsections" merge bar,
"don't use low use-count alone as a merge/prune signal", "archive not delete",
and the `absorbed_into` field driving downstream consumers.
"""

from __future__ import annotations

from typing import Sequence

from ..core.types import Message, ToolSpec

CURATOR_REVIEW_PROMPT = (
    "You are RICE's background skill curator. This is an UMBRELLA-BUILDING "
    "consolidation pass, not a passive audit and not a duplicate-finder.\n\n"
    "The goal of the skill collection is a LIBRARY OF CLASS-LEVEL PROCEDURES. "
    "A collection of many narrow skills where each one captures one session's "
    "specific task is a FAILURE of the library, not a feature — a user "
    "searching skills matches on description, not on exact name; one broad "
    "umbrella skill with labeled sections beats several narrow siblings for "
    "discoverability.\n\n"
    "Hard rules — do not violate:\n"
    "1. DO NOT delete any skill. Archiving is the maximum destructive action "
    "you can propose. Archives are recoverable; deletion is not, and this tool "
    "has no delete capability at all.\n"
    "2. DO NOT propose touching a skill whose source starts with 'plugin:' — "
    "those are provided by an installed plugin (the candidate list below is "
    "already filtered to exclude them, so you won't see any).\n"
    "3. DO NOT use low use-count alone as a reason to merge OR to prune. "
    "Judge overlap on CONTENT. 'use=0' is absence of evidence, not evidence "
    "of low value — a recently-created skill may simply not have come up yet. "
    "Never prune a use=0 skill unless it is also genuinely obsolete or fully "
    "covered elsewhere in content.\n"
    "4. DO NOT reject a merge because 'each skill has a technically distinct "
    "trigger'. The right bar is: would a human maintainer write this as "
    "several separate skills, or as one skill with several labeled sections? "
    "When the answer is the latter, merge.\n\n"
    "How to work:\n"
    "1. Scan the full candidate list (each shown with its full current body). "
    "Identify clusters that share a domain or a first-word prefix.\n"
    "2. For each cluster of 2+, don't ask 'do these pairwise overlap?' — ask "
    "'what is the umbrella CLASS these all serve? Would a maintainer name "
    "that class and write one skill for it?'\n"
    "3. Two ways to consolidate:\n"
    "   a. MERGE INTO AN EXISTING member — one skill in the cluster is "
    "already broad enough to be the umbrella. Propose patching it (full new "
    "body covering every sibling's unique content as a labeled section) and "
    "absorbing the rest.\n"
    "   b. CREATE A NEW UMBRELLA — no existing member is broad enough. "
    "Propose a new class-level skill (name at the class level, not a "
    "session/bug-specific name) covering the shared workflow with labeled "
    "sections, absorbing every cluster member.\n"
    "4. Also flag skills whose name is too narrow (a bug id, a one-off task "
    "description) even outside a cluster — these usually belong folded into "
    "a broader umbrella once one exists, or pruned if truly one-off and "
    "already stale.\n"
    "5. Iterate across the whole candidate list — don't stop after one merge "
    "if more obvious clusters remain.\n\n"
    "Your tools: `propose_consolidate` (merge a cluster into one umbrella — "
    "may target an existing skill_id or a brand-new name) and `propose_prune` "
    "(archive one skill with no merge target — genuinely obsolete/superseded "
    "content only, not just 'narrow'). Everything you propose is reviewed by "
    "a human before anything is archived or written.\n\n"
    "'Nothing to consolidate' is a legitimate outcome ONLY when the "
    "collection is already at class-level granularity with no narrow/"
    "overlapping siblings. If the candidate list shows obvious clusters, "
    "propose the merges — a pass that finds nothing when clusters exist is "
    "a miss, not a neutral result."
)


def _render_candidates(candidates: Sequence[tuple[str, str, str, int]]) -> str:
    """candidates: (skill_id, description, body, use_count) — 항상 전체 본문을
    보여준다(리뷰와 달리 이 패스는 수동 트리거+저빈도라 지연에 관대함, Phase B 계획)."""
    if not candidates:
        return "## Candidate skills\n(none — nothing to consolidate)"
    lines = ["## Candidate skills (curator-managed only — plugin-provided skills excluded)"]
    for skill_id, description, body, use_count in candidates:
        lines.append(f"\n### [{skill_id}] {description} (use={use_count})\n{body}")
    return "\n".join(lines)


def build_curator_messages(
    candidates: Sequence[tuple[str, str, str, int]],
) -> tuple[str, list[Message]]:
    """Build (system, messages) for a one-shot consolidation analysis call. No
    prior conversation history involved (unlike review/learn) — this looks at
    the skill library as a whole, not any one conversation."""
    ask = f"{_render_candidates(candidates)}\n\nAnalyze the candidate list above and propose consolidations."
    return CURATOR_REVIEW_PROMPT, [Message(role="user", content=ask)]


def build_curator_tool_specs(candidate_ids: Sequence[str]) -> tuple[ToolSpec, ...]:
    """`propose_prune`'s `skill_id` is constrained to the shown candidates (same
    structural guarantee as review.py's propose_skill_patch) — the model can't
    prune something it was never shown. `propose_consolidate`'s `umbrella_id` is
    NOT constrained the same way — it may legitimately be a brand-new name
    (`is_new=true`), so an enum would wrongly forbid that case."""
    return (
        ToolSpec(
            name="propose_consolidate",
            description=(
                "Propose merging one or more candidate skills into a single "
                "class-level umbrella. The umbrella may be an existing candidate "
                "(is_new=false, umbrella_id must match one of the candidates) or "
                "brand new (is_new=true, umbrella_id is a new kebab-case name)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "umbrella_id": {"type": "string"},
                    "is_new": {"type": "boolean"},
                    "umbrella_description": {
                        "type": "string",
                        "description": "One-line summary — only used when is_new=true.",
                    },
                    "umbrella_body": {
                        "type": "string",
                        "description": "Full body covering the shared workflow, with a labeled section per absorbed sibling's unique content.",
                    },
                    "changelog": {"type": "string"},
                    "absorbed_skill_ids": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(candidate_ids)},
                        "description": "Candidates being merged INTO the umbrella and archived (do not include umbrella_id itself here).",
                    },
                },
                "required": ["umbrella_id", "is_new", "umbrella_body", "changelog", "absorbed_skill_ids"],
            },
        ),
        ToolSpec(
            name="propose_prune",
            description="Propose archiving one candidate skill with no merge target — genuinely obsolete or fully superseded content only.",
            parameters={
                "type": "object",
                "properties": {
                    "skill_id": {"type": "string", "enum": list(candidate_ids)},
                    "reason": {"type": "string"},
                },
                "required": ["skill_id", "reason"],
            },
        ),
    )
