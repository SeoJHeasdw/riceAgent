"""core/lexical.py — deterministic lexical-matching primitives (no embeddings).

Shared by the skill-relevance scorer (learning/skill/relevance.py) and the tool
router (agent/tool_dispatch.py) so "what matches this query" is ONE rule, not two
drifting copies.

WHY NOT SEMANTIC SEARCH (final decision, 2026-07-07 adversarial review): an
embedding stack is the wrong trade for a local-first product whose floor is an
8GB customer laptop — model download, RAM, index build, and cold-start cost buy
marginal recall here. The deterministic ladder below covers the practical gap
instead:

    exact token match → substring credit → char-bigram fuzzy →
    tiny ko→en intent table (generic verbs/nouns only, NOT per-skill aliases)

The intent table is deliberately small and global: users are Korean/English, and
skills/tools are indexed in English. Expanding a handful of universal intent
words ("만들어줘"→create, "메일"→mail) is what bridges the ko-query → en-index
gap without per-skill alias curation or a semantic model. Explicit mentions
($skill / tool names typed by the user) always route exactly regardless.
"""

from __future__ import annotations

import re

__all__ = [
    "tokens",
    "expand_query",
    "field_score",
    "name_mentioned",
    "MIN_TOKEN_LEN",
]

# Alphanumeric runs OR CJK runs. Korean is space-delimited at the eojeol level,
# so whitespace tokenisation is serviceable; substring/fuzzy credit below absorbs
# the common josa/suffix variations ("투자" vs "투자용").
_WORD = re.compile(r"[a-z0-9]+|[가-힣]+")
_HANGUL = re.compile(r"^[가-힣]+$")

MIN_TOKEN_LEN = 2  # ignore 1-char noise ("a", "은", "이")
_PARTIAL_MIN_LEN = 4  # substring credit only for tokens long enough to be distinctive
_FUZZY_MIN_LEN = 4  # bigram fuzzy only for tokens where 2-grams are meaningful
_FUZZY_THRESHOLD = 0.6  # dice coefficient floor for fuzzy credit ("recieve"→"receive")


def tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.lower()) if len(t) >= MIN_TOKEN_LEN}


# ── tiny global intent table (ko stem → en capability words) ──────────────────
# Generic verbs/nouns ONLY — this is a router aid, not an alias registry. Keys
# are Korean stems matched by prefix (so conjugation/josa forms hit: "만들어줘",
# "검색해서"). Adding a per-skill or per-tool alias here is a smell: name the
# skill/tool so its own name/tags/description carries the match instead.
_INTENT_KO2EN: dict[str, tuple[str, ...]] = {
    "생성": ("create", "new"),
    "만들": ("create", "make", "build"),
    "작성": ("write", "create", "draft"),
    "수정": ("edit", "update", "fix"),
    "변경": ("change", "update"),
    "삭제": ("delete", "remove"),
    "검색": ("search", "find"),
    "찾": ("find", "search", "lookup"),
    "조회": ("get", "list", "search"),
    "요약": ("summary", "summarize"),
    "번역": ("translate", "translation"),
    "읽": ("read",),
    "저장": ("save", "write"),
    "보내": ("send",),
    "전송": ("send",),
    "파일": ("file",),
    "폴더": ("folder", "directory"),
    "문서": ("document", "doc"),
    "보고서": ("report", "document"),
    "발표": ("presentation", "slides", "deck"),
    "슬라이드": ("slides", "presentation"),
    "피피티": ("ppt", "pptx", "presentation"),
    "엑셀": ("excel", "xlsx", "spreadsheet", "workbook"),
    "시트": ("sheet", "spreadsheet"),
    "메일": ("mail", "email"),
    "이메일": ("email", "mail"),
    "일정": ("calendar", "event", "schedule"),
    "캘린더": ("calendar", "event"),
    "브라우저": ("browser", "web"),
    "웹": ("web", "browser"),
    "테스트": ("test",),
    "배포": ("deploy", "release"),
    "계획": ("plan",),
    "사업": ("business",),
    "투자": ("investor", "pitch", "funding"),
}


def expand_query(query_tokens: set[str]) -> set[str]:
    """Query tokens + intent-table expansions for any Korean stems present.
    Field/document text is NEVER expanded — expansion is a query-side recall aid,
    so an English index can be found from a Korean request deterministically."""
    expanded = set(query_tokens)
    for t in query_tokens:
        if not _HANGUL.match(t):
            continue
        for stem, en_words in _INTENT_KO2EN.items():
            if t.startswith(stem):
                expanded.update(en_words)
    return expanded


# ── fuzzy (char-bigram dice) ──────────────────────────────────────────────────


def _bigrams(s: str) -> set[str]:
    return {s[i : i + 2] for i in range(len(s) - 1)}


def _dice(a: str, b: str) -> float:
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    return 2.0 * len(ba & bb) / (len(ba) + len(bb))


# ── field scoring ─────────────────────────────────────────────────────────────


def field_score(query_tokens: set[str], field_text: str) -> float:
    """How many query tokens land in this field. The ladder, best credit wins:
    exact token 1.0 → substring containment 0.5 (bridges '투자용'→'투자',
    'ppt'→'pptx') → char-bigram fuzzy 0.25 (bridges typos and near-forms,
    'workbok'→'workbook'). Deterministic and order-independent."""
    field = tokens(field_text)
    if not field or not query_tokens:
        return 0.0
    hits = 0.0
    for q in query_tokens:
        if q in field:
            hits += 1.0
            continue
        if len(q) >= _PARTIAL_MIN_LEN and any(q in f or f in q for f in field):
            hits += 0.5
            continue
        if len(q) >= _FUZZY_MIN_LEN and any(
            len(f) >= _FUZZY_MIN_LEN and _dice(q, f) >= _FUZZY_THRESHOLD for f in field
        ):
            hits += 0.25
    return hits


def name_mentioned(query_tokens: set[str], name: str) -> bool:
    """Explicit-mention routing: did the user literally name this skill/tool?
    True when EVERY token of the name appears in the raw (unexpanded) query —
    '$business-planning 스킬 써줘' or 'run create_presentation' route exactly,
    ahead of any scoring."""
    name_tokens = tokens(name.replace("-", " ").replace("_", " ").replace(".", " "))
    return bool(name_tokens) and name_tokens.issubset(query_tokens)
