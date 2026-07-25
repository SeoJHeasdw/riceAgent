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
     Korean request finds an English-indexed skill (core/lexical.py) — the
     expansions of one typed word compete for a single hit, they do not each
     count (see MIN_STRONG_HITS below);
  5. negative triggers — a tag like `not:excel` subtracts when the query hits
     that word, letting authors carve off known false-positive neighborhoods.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

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
#
# "independent" 는 2026-07-25 까지 한국어에서 성립하지 않았다: intent 확장이 쿼리를
# 평평한 토큰 집합으로 펼쳐서, "생성해줘" 한 단어가 {create, new, 생성해줘} 로 strong
# 2.0 을 자력으로 만들어 이 게이트를 통과했다(영어 "create it" 은 1.0 으로 정상 차단 —
# 게이트가 주 사용자 언어에서만 꺼져 있었다). core/lexical.py 가 쿼리를 term(사용자가
# 친 토큰) 단위로 묶고 term 당 최고 credit 하나만 세면서 그 비대칭이 사라졌다.
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


# ── 판별력 가중(IDF) ──────────────────────────────────────────────────────────
# 사다리는 "이 말이 이 문서에 있나"만 봤다 — 코퍼스에서 그 말이 **얼마나 희소한가**는
# 보지 않았다. 그래서 카탈로그 전체에 퍼진 단어("report")가 아무것도 변별하지 못하면서
# 희소한 단어("payroll")와 같은 무게를 받았고, 그 단어를 여러 필드에 반복한 스킬이
# 유일하게 관련 있는 스킬을 눌렀다(실측: 'payroll report' → payroll-run이 꼴찌).
# 판별력은 임베딩/리랭커가 대신해주던 바로 그 신호이며, IDF는 그것을 **모델 없이**
# 결정론적으로 얻는 고전적 방법이다.
#
# 스케일 보존이 핵심: 가중치를 평균 1.0으로 정규화하므로 점수 규모가 이전과 같고,
# 실측으로 보정된 AUTO_INJECT_THRESHOLD/MIN_STRONG_HITS를 다시 맞출 필요가 없다.
# 즉 이 변경은 **순수 재랭킹**이다 — 주입 여부의 절대 기준은 건드리지 않는다.
_IDF_MIN_CANDIDATES = 3  # 후보가 이보다 적으면 df 통계가 무의미 → 전부 1.0


def term_weights(query: lexical.QueryTerms, skills: Sequence) -> tuple[float, ...] | None:
    """query.terms에 정렬된 판별력 가중치. 후보가 너무 적으면 None(가중 없음).

    df는 **저자 신호 필드(name+tags+description)** 기준으로 센다 — 본문은 길고
    잡토큰이 많아 df를 뭉개서 모든 단어를 흔하게 보이게 만든다.
    """
    n = len(skills)
    if n < _IDF_MIN_CANDIDATES or not query:
        return None
    docs = [
        lexical.field_tokens(f"{s.name.replace('-', ' ')} {' '.join(s.tags)} {s.description}")
        for s in skills
    ]
    raw: list[float] = []
    for i in range(len(query.terms)):
        df = sum(1 for d in docs if lexical.term_matches(query, i, d))
        # 표준 smoothed IDF. df=n(모든 문서에 존재) → 0에 가깝고, df=0/1 → 최대.
        raw.append(math.log(1.0 + (n - df + 0.5) / (df + 0.5)))
    mean = sum(raw) / len(raw)
    if mean <= 0:  # 모든 term이 모든 문서에 존재 — 변별 정보 없음, 균등 취급
        return None
    return tuple(w / mean for w in raw)  # 평균 1.0 정규화 → 점수 규모 불변


def _split_tags(tags: list[str]) -> tuple[list[str], list[str]]:
    """`not:`-prefixed tags are negative triggers, everything else is positive."""
    positive = [t for t in tags if not t.startswith(_NEGATIVE_TAG_PREFIX)]
    negative = [t[len(_NEGATIVE_TAG_PREFIX) :] for t in tags if t.startswith(_NEGATIVE_TAG_PREFIX)]
    return positive, negative


def _score_one(
    query: lexical.QueryTerms,
    name: str,
    tags: list[str],
    description: str,
    body: str,
    weights: tuple[float, ...] | None = None,
) -> tuple[float, float, bool]:
    """(score, strong_hits, explicit). `strong_hits` = raw name+tag hit credit —
    the author-signal evidence the auto-inject gate requires."""
    # 1. explicit mention — checked against the RAW query (an intent-table
    #    expansion must never fabricate a "mention").
    if lexical.name_mentioned(query.raw, name):
        return _EXPLICIT_MENTION_BONUS, _EXPLICIT_MENTION_BONUS, True
    positive_tags, negative_tags = _split_tags(tags)
    # name/tag 는 credit 을 한 번만 계산해 **두 가지로** 쓴다:
    #   score  = IDF 가중합 (랭킹 — 판별력 있는 단어가 더 무겁다)
    #   strong = 무가중 합  (게이트 — MIN_STRONG_HITS 는 실측으로 보정된 절대
    #            기준이라 가중치가 섞이면 의미가 바뀐다. IDF 는 순수 재랭킹이다.)
    name_credits = lexical.field_credits(query, name.replace("-", " "))
    tag_credits = lexical.field_credits(query, " ".join(positive_tags))
    strong = sum(name_credits) + sum(tag_credits)
    if weights is None:
        name_hits, tag_hits = sum(name_credits), sum(tag_credits)
    else:
        name_hits = sum(c * w for c, w in zip(name_credits, weights))
        tag_hits = sum(c * w for c, w in zip(tag_credits, weights))
    score = (
        _W_NAME * name_hits
        + _W_TAGS * tag_hits
        + _W_DESC * lexical.field_score(query, description, weights)
        + _W_BODY * lexical.field_score(query, body[:_BODY_SCAN_CHARS], weights)
    )
    if negative_tags:
        score -= _NEGATIVE_HIT_PENALTY * lexical.field_score(
            query, " ".join(negative_tags), weights
        )
    # strong = name/tag **필드 커버리지**(저자가 스스로 밝힌 신호를 몇 군데나 때렸나).
    # 한 단어가 name 과 tag 를 모두 때려 2.0 이 되는 건 의도된 동작이다 — 영어도
    # 똑같이 동작한다("xlsx" 는 name·tag 를 모두 때려 2.0, "excel" 은 tag 만이라 1.0).
    # 이걸 "서로 다른 입력 단어 수"로 바꿔보면 한국어가 구조적으로 손해를 본다:
    # 한국어 단어는 intent 테이블에 있는 것만 영어 인덱스에 닿으므로("체크리스트"→
    # checklist 는 테이블에 없다), 정당한 요청("배포 체크리스트대로 진행해줘" →
    # deploy-checklist)까지 게이트에 막힌다. 실제 비대칭은 여기가 아니라 확장어가
    # **한 필드 안에서** 여러 hit 으로 세지던 것이었고, 그건 core/lexical.py 의
    # term 묶음이 닫았다.
    return max(0.0, score), strong, False


def score_skills(query: str, skills: list) -> list[ScoredSkill]:
    """Score `skills` (anything with .name/.tags/.description/.body) against
    `query`, returned sorted by descending score with **explicit mentions first**.
    Skills that share no tokens with the query score 0.0 and are still returned
    (callers filter by threshold or take the top N).

    explicit-first 정렬(2026-07-12): 점수만으로 정렬하면 범용 어휘를 가진 스킬
    (예: 이름/태그에 'skill'이 들어가는 메타 스킬)의 유기 점수가 명시 언급 보너스
    (9.0)를 넘어 — 사용자가 이름을 콕 집은 스킬이 단일 주입 슬롯
    (skill_max_auto_inject=1)을 빼앗기는 역전이 났다. "사용자가 THIS를 시켰다"는
    어떤 유기 매칭보다 강한 신호이므로 정렬 키에서 명시적으로 우선한다."""
    q = lexical.prepare_query(query)
    # 판별력 가중은 **후보 집합 전체**를 봐야 나온다 — 희소성은 코퍼스 상대적이다.
    # 카탈로그가 같으면 결과도 같다(결정론 유지).
    weights = term_weights(q, skills) if q else None
    scored = []
    for s in skills:
        score, strong, explicit = (
            _score_one(q, s.name, s.tags, s.description, s.body, weights)
            if q
            else (0.0, 0.0, False)
        )
        scored.append(
            ScoredSkill(
                name=s.name, description=s.description, score=score, strong=strong, explicit=explicit
            )
        )
    scored.sort(key=lambda s: (s.explicit, s.score), reverse=True)
    return scored
