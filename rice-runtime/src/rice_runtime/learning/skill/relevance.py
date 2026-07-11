"""skill/relevance.py — deterministic relevance routing for on-demand skill loading.

The learning loop moved off "inject every active skill's full body every turn"
(inject-all-active): with builtin + learned skills that quietly eats the local
model's narrow context, and injects business-planning's ~1.1k tokens even on
"hello". The replacement (LearningService.prefetch) is a hybrid:

  - a short **index** (name + one-line description) of every active skill is
    always in the system prompt — cheap, and enough for the model to know a
    procedure exists;
  - the **full body** is loaded only when relevant: either auto-injected for a
    high-confidence match with the user's message, or pulled on demand by the
    model via the `skill_view` tool (found through `skill_search`).

This module owns the matching. It is deliberately a deterministic lexical
router, NOT semantic search — no embeddings, no vector DB, no cold-start or
packaging cost (final decision 2026-07-07: the floor device is an 8GB laptop;
see core/lexical.py). The routing ladder, strongest first:

  1. explicit mention — the user literally names the skill → routes exactly;
  2. name/tag token match (the author's own "this is what I'm about" signal);
  3. description/body token match, with substring + char-bigram fuzzy credit;
  4. a tiny global ko→en intent table expands generic Korean verbs/nouns so a
     Korean request finds an English-indexed skill (core/lexical.py);
  5. negative triggers — a tag like `not:excel` subtracts when the query hits
     that word, letting authors carve off known false-positive neighborhoods.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core import lexical

__all__ = [
    "ScoredSkill",
    "score_skills",
    "auto_inject_eligible",
    "AUTO_INJECT_THRESHOLD",
    "MIN_STRONG_HITS",
]

# Score floor for auto-injection. NOTE: since the strong-hit gate below, the
# threshold alone no longer decides — see `auto_inject_eligible`.
AUTO_INJECT_THRESHOLD = 3.0

# Precision gate for auto-injection (2026-07-11): a single name/tag token hit
# used to clear AUTO_INJECT_THRESHOLD on its own (tag hit = 3.0), so ANY query
# sharing one incidental keyword with a tag ("엑셀"→xlsx) injected a full ~1k-token
# body. Field data showed the cost: bodies injected on barely-related turns, and
# every injection polluting use/last_used_at telemetry downstream. Auto-inject now
# additionally requires ≥2 independent author-signal hits (name/tag credit; exact
# hit = 1.0, substring = 0.5) — one keyword puts a skill in the index, where it is
# one `skill_view` away; it does not put its body in the prompt. Explicit mention
# still routes unconditionally.
MIN_STRONG_HITS = 2.0

# Explicit mention outranks any organic score — the user asked for THIS skill.
_EXPLICIT_MENTION_BONUS = 3 * AUTO_INJECT_THRESHOLD
# One negative-trigger hit cancels a name/tag hit; the total is floored at 0.
_NEGATIVE_HIT_PENALTY = 3.0

# Field weights: what a query token hitting each field is worth. Name and tags
# are the author's own "this is what I'm about" signal → strongest.
_W_NAME = 3.0
_W_TAGS = 3.0
_W_DESC = 1.5
_W_BODY = 0.5

_BODY_SCAN_CHARS = 2_000  # only the top of the body — enough for headings/intent
_NEGATIVE_TAG_PREFIX = "not:"


@dataclass(frozen=True)
class ScoredSkill:
    """One skill scored against a query. `name`/`description` feed both the index
    render and the `skill_search` tool result; `score`+`strong`+`explicit` drive
    auto-inject (see `auto_inject_eligible`). `strong` is the raw name/tag hit
    credit (unweighted), `explicit` is a literal mention of the skill's name."""

    name: str
    description: str
    score: float
    strong: float = 0.0
    explicit: bool = False


def auto_inject_eligible(scored: ScoredSkill) -> bool:
    """Should this skill's FULL BODY be auto-injected for the query?
    Explicit mention → always. Otherwise the score must clear the threshold AND
    the strong-hit gate (≥ MIN_STRONG_HITS of name/tag credit) — incidental
    single-keyword overlap stays in the index instead."""
    if scored.explicit:
        return True
    return scored.score >= AUTO_INJECT_THRESHOLD and scored.strong >= MIN_STRONG_HITS


def _split_tags(tags: list[str]) -> tuple[list[str], list[str]]:
    """`not:`-prefixed tags are negative triggers, everything else is positive."""
    positive = [t for t in tags if not t.startswith(_NEGATIVE_TAG_PREFIX)]
    negative = [t[len(_NEGATIVE_TAG_PREFIX) :] for t in tags if t.startswith(_NEGATIVE_TAG_PREFIX)]
    return positive, negative


def _score_one(
    raw_query: set[str], query: set[str], name: str, tags: list[str], description: str, body: str
) -> tuple[float, float, bool]:
    """(score, strong_hits, explicit). `strong_hits` = raw name+tag hit credit —
    the author-signal evidence the auto-inject gate requires."""
    # 1. explicit mention — checked against the RAW query (an intent-table
    #    expansion must never fabricate a "mention").
    if lexical.name_mentioned(raw_query, name):
        return _EXPLICIT_MENTION_BONUS, _EXPLICIT_MENTION_BONUS, True
    positive_tags, negative_tags = _split_tags(tags)
    name_hits = lexical.field_score(query, name.replace("-", " "))
    tag_hits = lexical.field_score(query, " ".join(positive_tags))
    score = (
        _W_NAME * name_hits
        + _W_TAGS * tag_hits
        + _W_DESC * lexical.field_score(query, description)
        + _W_BODY * lexical.field_score(query, body[:_BODY_SCAN_CHARS])
    )
    if negative_tags:
        score -= _NEGATIVE_HIT_PENALTY * lexical.field_score(query, " ".join(negative_tags))
    return max(0.0, score), name_hits + tag_hits, False


def score_skills(query: str, skills: list) -> list[ScoredSkill]:
    """Score `skills` (anything with .name/.tags/.description/.body) against
    `query`, returned sorted by descending score. Skills that share no tokens with
    the query score 0.0 and are still returned (callers filter by threshold or
    take the top N)."""
    raw_q = lexical.tokens(query)
    q = lexical.expand_query(raw_q)
    scored = []
    for s in skills:
        score, strong, explicit = (
            _score_one(raw_q, q, s.name, s.tags, s.description, s.body) if q else (0.0, 0.0, False)
        )
        scored.append(
            ScoredSkill(
                name=s.name, description=s.description, score=score, strong=strong, explicit=explicit
            )
        )
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored
