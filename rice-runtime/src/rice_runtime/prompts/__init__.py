"""Model-facing prompt templates (the agent's authored "voice"), centralized.

WHY A PACKAGE: RICE is an agentic service whose system prompt is *section-composed*
(base identity ⊕ skill guidance ⊕ injected skills/memories ⊕ user preferences) and
whose features each carry their own instruction (titling, …). Keeping those strings
next to the code that fires them scatters the one surface that most defines the
agent's behavior — and lets inconsistencies creep in (e.g. one section in English,
another hardcoded to Korean). This package is the single home for that surface.

TIER: a leaf, like `core` — depends only on `core.types`, so any module may import
it freely. It is data/templates, not a collaborator behind an interface, so it sits
*below* the no-direct-import line (RUNTIME_DIRECTORY §5).

NOT a framework: no registry/versioning/hot-reload — that's overkill for a single
local user. Just named templates + a small `compose_system_prompt` synthesis fn.
"""

from __future__ import annotations

from .curator import CURATOR_REVIEW_PROMPT, build_curator_messages, build_curator_tool_specs
from .review import (
    LEARN_SYSTEM_PROMPT,
    REVIEW_SYSTEM_PROMPT,
    build_learn_instruction,
    build_review_messages,
    build_review_tool_specs,
)
from .system import BASE_SYSTEM_PROMPT, SKILL_GUIDANCE, compose_system_prompt
from .title import TITLE_MAX_CHARS, build_title_messages, clean_title

__all__ = [
    "BASE_SYSTEM_PROMPT",
    "SKILL_GUIDANCE",
    "compose_system_prompt",
    "TITLE_MAX_CHARS",
    "build_title_messages",
    "clean_title",
    "REVIEW_SYSTEM_PROMPT",
    "LEARN_SYSTEM_PROMPT",
    "build_review_messages",
    "build_review_tool_specs",
    "build_learn_instruction",
    "CURATOR_REVIEW_PROMPT",
    "build_curator_messages",
    "build_curator_tool_specs",
]
