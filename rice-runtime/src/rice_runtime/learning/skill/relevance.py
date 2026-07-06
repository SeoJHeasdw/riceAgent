"""skill/relevance.py — lexical relevance scoring for on-demand skill loading.

The learning loop moved off "inject every active skill's full body every turn"
(inject-all-active): with builtin + learned skills that quietly eats the local
model's narrow context, and injects business-planning's ~1.1k tokens even on
"hello". The replacement (LearningService.prefetch) is a hybrid:

  - a short **index** (name + one-line description) of every active skill is
    always in the system prompt — cheap, and enough for the model to know a
    procedure exists;
  - the **full body** is loaded only when relevant: either auto-injected for a
    high-confidence lexical match with the user's message, or pulled on demand by
    the model via the `skill_view` tool (found through `skill_search`).

This module owns the matching. It is deliberately a small BM25-flavoured lexical
scorer, NOT semantic search — no embeddings, no vector DB, no cold-start or
packaging cost, which for a local-first product would be a large architectural
tax for marginal gain at this scale. Cross-language recall (Korean prompt vs an
English skill) is intentionally left to the model reading the always-on English
index and calling `skill_view`; auto-inject only fires on clear lexical hits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["ScoredSkill", "score_skills", "AUTO_INJECT_THRESHOLD"]

# A distinctive name/tag hit scores 3.0, so a single unambiguous keyword
# ("pitch", "재무모델", "kubernetes") clears the bar and auto-injects the body.
# Incidental body-text overlap (0.5) never does on its own — that path is what
# `skill_view` is for.
AUTO_INJECT_THRESHOLD = 3.0

# Alphanumeric runs OR CJK runs. Korean is space-delimited at the word (eojeol)
# level, so whitespace tokenisation is serviceable; the substring fallback below
# absorbs the common josa/suffix variations ("투자" vs "투자용").
_WORD = re.compile(r"[a-z0-9]+|[가-힣]+")

# Field weights: what a query token hitting each field is worth. Name and tags
# are the author's own "this is what I'm about" signal → strongest.
_W_NAME = 3.0
_W_TAGS = 3.0
_W_DESC = 1.5
_W_BODY = 0.5

_BODY_SCAN_CHARS = 2_000  # only the top of the body — enough for headings/intent
_MIN_TOKEN_LEN = 2  # ignore 1-char noise ("a", "은", "이")
_PARTIAL_MIN_LEN = 4  # substring credit only for tokens long enough to be distinctive


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.lower()) if len(t) >= _MIN_TOKEN_LEN}


@dataclass(frozen=True)
class ScoredSkill:
    """One skill scored against a query. `name`/`description` feed both the index
    render and the `skill_search` tool result; `score` drives auto-inject."""

    name: str
    description: str
    score: float


def _field_score(query: set[str], field_text: str) -> float:
    """How many query tokens land in this field. Exact token match scores full;
    a long query token that is a substring of (or contains) a field token scores
    half — this is what bridges '투자용' → '투자' and 'ppt' → 'pptx'."""
    field = _tokens(field_text)
    if not field:
        return 0.0
    hits = 0.0
    for q in query:
        if q in field:
            hits += 1.0
        elif len(q) >= _PARTIAL_MIN_LEN and any(q in f or f in q for f in field):
            hits += 0.5
    return hits


def _score_one(query: set[str], name: str, tags: list[str], description: str, body: str) -> float:
    return (
        _W_NAME * _field_score(query, name.replace("-", " "))
        + _W_TAGS * _field_score(query, " ".join(tags))
        + _W_DESC * _field_score(query, description)
        + _W_BODY * _field_score(query, body[:_BODY_SCAN_CHARS])
    )


def score_skills(query: str, skills: list) -> list[ScoredSkill]:
    """Score `skills` (anything with .name/.tags/.description/.body) against
    `query`, returned sorted by descending score. Skills that share no tokens with
    the query score 0.0 and are still returned (callers filter by threshold or
    take the top N)."""
    q = _tokens(query)
    scored = [
        ScoredSkill(
            name=s.name,
            description=s.description,
            score=(_score_one(q, s.name, s.tags, s.description, s.body) if q else 0.0),
        )
        for s in skills
    ]
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored
