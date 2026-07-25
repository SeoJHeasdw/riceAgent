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

★ 확장은 리콜 보조지, 증거 증식이 아니다 (2026-07-25). 예전 `expand_query`는
확장어를 원래 토큰과 구분 없는 평평한 집합으로 돌려줬고, `field_score`가 그
전부를 **각각 독립된 1.0 hit**으로 셌다. 그래서 사용자가 실제로 친 단어 하나가
점수 여러 개로 불어났다 — "생성해줘"(raw 토큰 1개) → {create, new, 생성해줘}로
3배가 되어, relevance.py 의 `MIN_STRONG_HITS`(= 서로 **독립된** author-signal
2개를 요구하는 정밀도 게이트)를 단어 하나로 통과시켰다. 영어 "create it"은
같은 게이트에 정상적으로 막히므로, 게이트가 주 사용자 언어에서만 꺼져 있던 셈.

그래서 쿼리는 이제 평평한 집합이 아니라 **term(사용자가 친 토큰) 단위**로 묶인다
(`QueryTerms`): 한 term 의 여러 variant 는 서로 경쟁해 **최고 credit 하나만**
기여한다. 리콜은 그대로다(확장어는 여전히 매칭돼 점수를 만든다) — 달라지는 건
한 단어가 게이트 통과에 필요한 증거를 스스로 복제하지 못한다는 것뿐이다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence, Set as AbstractSet
from dataclasses import dataclass
from functools import lru_cache

__all__ = [
    "tokens",
    "prepare_query",
    "field_score",
    "name_mentioned",
    "QueryTerms",
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


# ── fuzzy (char-bigram dice) ──────────────────────────────────────────────────


def _bigrams(s: str) -> frozenset[str]:
    return frozenset(s[i : i + 2] for i in range(len(s) - 1))


# ── query preparation ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Term:
    """One token the user actually typed, plus its intent-table expansions.

    `variants[0]` is the typed token itself. Each entry carries its precomputed
    bigrams (None when the variant is too short for meaningful fuzzy matching) so
    the dice ladder never rebuilds them per candidate field — the same prepared
    query is scored against every skill/tool in the catalog."""

    variants: tuple[tuple[str, frozenset[str] | None], ...]


@dataclass(frozen=True)
class QueryTerms:
    """A query prepared once and scored against many fields.

    `raw` is the untouched typed tokens — `name_mentioned` uses it so an
    intent-table expansion can never fabricate an explicit mention."""

    raw: frozenset[str]
    terms: tuple[_Term, ...]

    def __bool__(self) -> bool:
        return bool(self.terms)


def prepare_query(query: str) -> QueryTerms:
    """Tokenise + expand + precompute bigrams, once per query.

    Expansion is query-side only — field/document text is NEVER expanded, so an
    English index stays findable from a Korean request deterministically. The
    grouping into terms is what keeps one typed word worth at most one hit
    (module docstring ★)."""
    raw = tokens(query)
    terms = []
    for t in raw:
        variants = [t]
        if _HANGUL.match(t):
            # 한 토큰이 여러 stem 에 걸릴 수 있고(예: "보고서"→report/document,
            # "보내"→send) 확장어가 겹칠 수 있다 — 같은 variant 를 두 번 담아도
            # term 단위 최고점 규칙상 점수는 안 변하지만, 중복 비교는 낭비다.
            seen = {t}
            for stem, en_words in _INTENT_KO2EN.items():
                if not t.startswith(stem):
                    continue
                for w in en_words:
                    if w not in seen:
                        seen.add(w)
                        variants.append(w)
        terms.append(
            _Term(
                variants=tuple(
                    (v, _bigrams(v) if len(v) >= _FUZZY_MIN_LEN else None) for v in variants
                )
            )
        )
    # 정렬로 term 순서를 고정한다 — 점수는 순서 독립이지만, 결정론적 순서는
    # 디버깅/골든 테스트에서 같은 쿼리가 늘 같은 구조를 만들게 한다.
    return QueryTerms(raw=frozenset(raw), terms=tuple(sorted(terms, key=lambda x: x.variants[0][0])))


# ── field scoring ─────────────────────────────────────────────────────────────

# 필드 인덱스 캐시 크기. 필드 텍스트는 **문서**(스킬 이름/태그/설명/본문 상단,
# 도구 이름/설명)라 카탈로그 크기로 유계다 — 스킬 40개 × 4필드 + 도구 수백 개면
# 수백 엔트리다. 쿼리는 절대 키가 되지 않으므로 세션이 길어져도 자라지 않는다.
_FIELD_CACHE_SIZE = 512


@lru_cache(maxsize=_FIELD_CACHE_SIZE)
def _field_index(field_text: str) -> tuple[frozenset[str], tuple[tuple[str, frozenset[str]], ...]]:
    """(토큰 집합, fuzzy 대상 토큰+bigram). 같은 카탈로그가 턴마다 새 쿼리로 다시
    스코어링되므로 필드 쪽은 캐시가 그대로 산다.

    캐시 키는 **필드 텍스트 자체**다 — 내용이 바뀌면 키가 바뀌어 자동으로
    무효화되므로 stale 이 원천적으로 불가능하다(스킬 편집/버전업 포함). 이름이나
    id 로 키를 잡았다면 무효화 훅이 필요했을 자리."""
    field = tokens(field_text)
    return frozenset(field), tuple(
        (f, _bigrams(f)) for f in field if len(f) >= _FUZZY_MIN_LEN
    )


def field_credits(query: QueryTerms, field_text: str) -> tuple[float, ...]:
    """Per-term credit against this field, positionally aligned to `query.terms`.

    `field_score` is this summed. Callers that must weight terms individually
    (IDF — see `learning/skill/relevance.py::term_weights`) need the breakdown;
    summing first would destroy it."""
    if not query:
        return ()
    field, fuzzy_field = _field_index(field_text)
    if not field:
        return (0.0,) * len(query.terms)
    return tuple(_term_credit(term, field, fuzzy_field) for term in query.terms)


def field_tokens(field_text: str) -> frozenset[str]:
    """이 필드의 토큰 집합(캐시된 인덱스에서). document-frequency 집계처럼 점수가
    아니라 '이 문서가 이 말을 담고 있나'만 필요한 호출자용."""
    return _field_index(field_text)[0]


def term_matches(query: QueryTerms, index: int, tokens_: AbstractSet[str]) -> bool:
    """query.terms[index]가 이 토큰 집합에 (사다리 어느 단계로든) 걸리나.
    df 집계용 — 점수 없이 존재 여부만 본다."""
    if index >= len(query.terms) or not tokens_:
        return False
    fuzzy = [(f, _bigrams(f)) for f in tokens_ if len(f) >= _FUZZY_MIN_LEN]
    return _term_credit(query.terms[index], tokens_, fuzzy) > 0.0


def _term_credit(
    term: _Term,
    field: AbstractSet[str],
    fuzzy_field: Sequence[tuple[str, frozenset[str]]],
) -> float:
    """사다리 한 term 분: exact 1.0 → substring 0.5 → bigram fuzzy 0.25.
    term 의 variant 들은 서로 경쟁해 최고점 하나만 낸다(모듈 docstring ★)."""
    best = 0.0
    for variant, vgrams in term.variants:
        if variant in field:
            return 1.0  # 사다리 최고점 — 나머지 variant 를 볼 필요가 없다
        if len(variant) >= _PARTIAL_MIN_LEN and any(
            # `variant in f` 는 f 가 variant 보다 길므로 자동으로 안전하다.
            # 반대 방향(`f in variant`)은 f 에도 길이 하한이 필요하다 — 없으면
            # MIN_TOKEN_LEN(2) 짜리 불용어가 긴 쿼리어의 부분문자열이라는 이유로
            # 0.5 를 준다: "invoice" 가 "…items **in** gmail…" 을 담은 문서 전부에
            # 걸려 df 가 1/193 → 73/193 으로 뭉개졌다(2026-07-25 실측). 상수의 원래
            # 취지가 "구별될 만큼 긴 토큰만"이므로 양쪽에 대칭으로 적용한다.
            variant in f or (len(f) >= _PARTIAL_MIN_LEN and f in variant)
            for f in field
        ):
            best = max(best, 0.5)
            continue
        if best < 0.25 and vgrams:
            nv = len(vgrams)
            for _, fgrams in fuzzy_field:
                if 2.0 * len(vgrams & fgrams) / (nv + len(fgrams)) >= _FUZZY_THRESHOLD:
                    best = 0.25
                    break
    return best


def field_score(
    query: QueryTerms, field_text: str, weights: Sequence[float] | None = None
) -> float:
    """How many of the user's typed words land in this field, best credit wins:
    exact token 1.0 → substring containment 0.5 (bridges '투자용'→'투자',
    'ppt'→'pptx') → char-bigram fuzzy 0.25 (bridges typos and near-forms,
    'workbok'→'workbook'). Deterministic and order-independent.

    Credit is per **term**, not per variant: a term's expansions compete with each
    other and only the strongest contributes (module docstring ★). A pure-English
    query has one variant per term, so its scoring is unchanged.

    `weights` (per-term, aligned to `query.terms`) scales each term's credit —
    this is where IDF enters. `None` means every term counts 1.0, i.e. exactly the
    pre-IDF behaviour."""
    if not query:
        return 0.0
    # 필드 토큰의 bigram 은 필드당 한 번만 만든다 — 예전 `_dice(q, f)` 는
    # (쿼리토큰 × 필드토큰) 쌍마다 양쪽을 새로 만들어 O(|Q|·|F|) 번 집합을
    # 생성했다. 실제로 서로 다른 집합은 O(|Q|+|F|) 개뿐이다.
    field, fuzzy_field = _field_index(field_text)
    if not field:
        return 0.0
    total = 0.0
    for i, term in enumerate(query.terms):
        credit = _term_credit(term, field, fuzzy_field)
        if credit and weights is not None:
            credit *= weights[i]
        total += credit
    return total


def name_mentioned(query_tokens: AbstractSet[str], name: str) -> bool:
    """Explicit-mention routing: did the user literally name this skill/tool?
    True when EVERY token of the name appears in the raw (unexpanded) query —
    '$business-planning 스킬 써줘' or 'run create_presentation' route exactly,
    ahead of any scoring."""
    name_tokens = tokens(name.replace("-", " ").replace("_", " ").replace(".", " "))
    return bool(name_tokens) and name_tokens.issubset(query_tokens)
