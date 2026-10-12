"""skill/analyzer.py — SKILL.md → SkillAnalysisData 산출 엔진.

두 층으로 산출한다:

  1. **휴리스틱 층 (결정적, 항상 실행)** — 마크다운 구조에서 절차 단계
     (workflow/steps/절차 섹션의 목록)와 도구 언급 후보(도구 섹션 목록 +
     키워드 패턴)를 추출한다. provider 미배선/오프라인이어도 사이드카가
     항상 만들어지는 바닥이다. 원래 프론트(MySkillsScreen)가 렌더 시점마다
     하던 파싱을 서버로 옮긴 것 — 분석은 본문이 바뀔 때 한 번이면 되지,
     열 때마다 할 일이 아니다.

  2. **LLM 층 (있으면 덮어씀)** — thinking 없는 utility provider 로 low
     temperature 단발 structured-JSON 호출(prompts/analysis.py). 성공하면
     summary/steps/warnings 를 LLM 산출로 교체하고 도구 언급을 병합한다.
     실패/echo 폴백이면 휴리스틱 결과 그대로(source='heuristic').

**설명 전용** — 산출물은 UI 메타데이터다. 권한/실행/승인 판단에 쓰지 않는다
(analysis.py 모듈 docstring 의 계약). 그래서 실패해도 어떤 기능도 깨지지
않고, LLM 이 환각해도 보이는 설명만 틀린다.

tool_mentions 는 kind='mentioned' 만 만든다 — observed(실행 로그)/declared
(frontmatter 선언)는 각자의 정본 소스에서 API 응답 시점에 합류한다.
"""

from __future__ import annotations

import logging
import re
from typing import Callable

from ...agent.providers.interfaces import ILLMProvider
from ...core.model_json import extract_json_object
from ...prompts.analysis import (
    ANALYSIS_MAX_TOKENS,
    ANALYSIS_TEMPERATURE,
    analysis_response_schema,
    build_analysis_messages,
)
from .analysis import ANALYZER_VERSION, SkillAnalysisData, body_hash
from .store import SkillData

log = logging.getLogger("rice.learning.analyzer")

# 산출물 상한 — LLM 폭주/장문 스킬이 사이드카를 비대하게 만들지 않게.
_MAX_STEPS = 7
_MAX_TOOLS = 10
_MAX_WARNINGS = 5
_MAX_SUMMARY_CHARS = 600
_MAX_EVIDENCE_CHARS = 160
# 스트림 수집 상한(자) — JSON 하나 받자고 무한정 읽지 않는다.
_MAX_RESPONSE_CHARS = 8_000

_HEADING_RE = re.compile(r"^#{2,4}\s+")
_LIST_ITEM_RE = re.compile(r"^\s{0,3}(?:[-*+]\s+|\d+[.)]\s+)")
_STEP_SECTION_RE = re.compile(
    r"(workflow|how it works|steps|procedure|절차|작동 방식|사용 방법|흐름)", re.IGNORECASE
)
_TOOL_SECTION_RE = re.compile(r"(tools|tooling|사용 도구|도구)", re.IGNORECASE)

# 본문 키워드 → 도구 후보 (프론트의 KNOWN_TOOL_PATTERNS 이관)
_KNOWN_TOOL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("terminal", re.compile(r"\b(terminal|shell|bash|zsh|command line|CLI|npm|pnpm|yarn|pytest|git|rg|grep)\b", re.I)),
    ("file tools", re.compile(r"\b(read_file|write_file|edit_file|list_dir|filesystem|workspace)\b", re.I)),
    ("web/search", re.compile(r"\b(web search|browser|fetch_url|url|http|검색|브라우저)\b", re.I)),
    ("database", re.compile(r"\b(sql|postgres|mysql|database|schema|query)\b", re.I)),
    ("python", re.compile(r"\b(python|pandas|notebook|script\.py)\b", re.I)),
    ("office", re.compile(r"\b(create_presentation|create_workbook|excel|xlsx|pptx|powerpoint|스프레드시트)\b", re.I)),
    ("connector", re.compile(r"\b(gmail|calendar|drive|slack|salesforce|connector|oauth)\b", re.I)),
)


def _clean_md_line(line: str) -> str:
    line = _LIST_ITEM_RE.sub("", line)
    line = re.sub(r"[`*_>#]", "", line)
    return re.sub(r"\s+", " ", line).strip()


def _title_of(text: str) -> str:
    cleaned = _clean_md_line(text)
    first = re.split(r"[.!?。]", cleaned, maxsplit=1)[0]
    return (first or cleaned)[:54]


def _section_lines(body: str, heading: re.Pattern[str]) -> list[str]:
    """heading 패턴에 걸리는 첫 ##~#### 섹션의 본문 줄들."""
    lines = body.split("\n")
    start = next(
        (i for i, ln in enumerate(lines) if _HEADING_RE.match(ln) and heading.search(ln)), -1
    )
    if start == -1:
        return []
    out: list[str] = []
    for ln in lines[start + 1 :]:
        if _HEADING_RE.match(ln):
            break
        out.append(ln)
    return out


def _heuristic_steps(body: str) -> list[dict]:
    section = _section_lines(body, _STEP_SECTION_RE)
    source = section if section else body.split("\n")
    in_section = bool(section)
    steps: list[dict] = []
    for ln in source:
        if not _LIST_ITEM_RE.match(ln):
            continue
        title = _title_of(ln)
        if not title:
            continue
        steps.append(
            {
                "title": title,
                "description": "원문의 절차 섹션에서 감지됨" if in_section else "원문 목록 구조에서 감지된 단계 후보",
                "confidence": "medium" if in_section else "low",
                "evidence": _clean_md_line(ln)[:_MAX_EVIDENCE_CHARS],
            }
        )
        if len(steps) >= _MAX_STEPS:
            break
    return steps


def _heuristic_tools(body: str) -> list[dict]:
    tools: list[dict] = []
    seen: set[str] = set()
    # ① 도구 섹션의 목록 항목 — 본문이 명시적으로 나열한 후보 (신뢰 medium)
    for ln in _section_lines(body, _TOOL_SECTION_RE):
        if not _LIST_ITEM_RE.match(ln):
            continue
        name = _title_of(ln)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        tools.append(
            {
                "name": name,
                "kind": "mentioned",
                "confidence": "medium",
                "basis": "SKILL.md 도구 섹션",
                "evidence": _clean_md_line(ln)[:_MAX_EVIDENCE_CHARS],
            }
        )
    # ② 키워드 패턴 — 느슨한 추정 (신뢰 low)
    for name, pattern in _KNOWN_TOOL_PATTERNS:
        if name.lower() in seen:
            continue
        m = pattern.search(body)
        if m is None:
            continue
        seen.add(name.lower())
        tools.append(
            {
                "name": name,
                "kind": "mentioned",
                "confidence": "low",
                "basis": "본문 키워드 감지",
                "evidence": m.group(0)[:_MAX_EVIDENCE_CHARS],
            }
        )
    return tools[:_MAX_TOOLS]


def _heuristic_summary(skill: SkillData) -> str:
    if skill.description.strip():
        return skill.description.strip()[:_MAX_SUMMARY_CHARS]
    # 첫 비어있지 않은 비제목 문단
    for para in skill.body.split("\n\n"):
        text = _clean_md_line(para.strip())
        if text and not para.lstrip().startswith("#"):
            return text[:_MAX_SUMMARY_CHARS]
    return "설명이 비어 있는 스킬입니다 — 원문은 소스 보기에서 확인하세요."


def heuristic_analysis(skill: SkillData) -> SkillAnalysisData:
    """결정적 바닥 분석 — provider 없이도 항상 성립한다."""
    steps = _heuristic_steps(skill.body)
    return SkillAnalysisData(
        skill=skill.name,
        body_hash=body_hash(skill.body),
        analyzer_version=ANALYZER_VERSION,
        status="fresh",
        source="heuristic",
        summary=_heuristic_summary(skill),
        confidence="medium" if steps else "low",
        procedural_steps=steps,
        tool_mentions=_heuristic_tools(skill.body),
        warnings=[],
    )


class SkillAnalyzer:
    """휴리스틱 + (배선돼 있으면) LLM 층. resolve_provider 는 composition_root 의
    utility provider(think 비활성)를 받는다 — None 이면 휴리스틱 전용."""

    def __init__(self, *, resolve_provider: Callable[[], ILLMProvider] | None = None) -> None:
        self._resolve_provider = resolve_provider

    async def analyze(self, skill: SkillData) -> SkillAnalysisData:
        base = heuristic_analysis(skill)
        provider = self._resolve_provider() if self._resolve_provider else None
        if provider is None or getattr(provider, "name", "") == "echo":
            return base
        try:
            raw = await self._call(provider, skill)
            data = extract_json_object(raw)
            if data is None:
                log.warning(
                    "skill analysis returned unparseable output for %s (%d chars)",
                    skill.name,
                    len(raw),
                )
        except Exception:  # noqa: BLE001 — 분석 실패는 기능 실패가 아니다
            log.warning("skill analysis LLM call failed for %s", skill.name, exc_info=True)
            data = None
        if data is None:
            # LLM 이 죽어도 휴리스틱 결과는 성립한다 — status 는 failed 로 남겨
            # UI 가 "분석 실패(원문 기반 표시 중)"를 구분해 보여줄 수 있게 한다.
            base.status = "failed"
            base.warnings = [*base.warnings, "AI 분석 호출이 실패해 규칙 기반 추출 결과만 표시합니다."]
            return base
        return self._merge(base, data)

    async def _call(self, provider: ILLMProvider, skill: SkillData) -> str:
        """planner 와 동일한 소형 모델 안정화 3종 세트로 부른다(2026-07-04 방침):
        per-call think=False(무한 thinking 루프 차단) + response_schema(JSON 준수를
        프롬프트가 아니라 grammar 로 보증) + max_tokens(폭주 가드 — thinking 을
        끄지 못하는 모델의 reasoning 토큰까지 이 예산 안). 스트리밍 경로를 쓰므로
        긴 생성이 단일 요청 타임아웃에 걸리지 않는다."""
        system, messages = build_analysis_messages(
            name=skill.name, description=skill.description, body=skill.body
        )
        parts: list[str] = []
        total = 0
        async for chunk in provider.stream(
            system=system,
            messages=messages,
            tools=(),
            temperature=ANALYSIS_TEMPERATURE,
            response_schema=analysis_response_schema(),
            think=False,
            max_tokens=ANALYSIS_MAX_TOKENS,
        ):
            if chunk.kind == "text" and chunk.text:
                parts.append(chunk.text)
                total += len(chunk.text)
                if total >= _MAX_RESPONSE_CHARS:
                    break
        return "".join(parts)

    @staticmethod
    def _merge(base: SkillAnalysisData, data: dict) -> SkillAnalysisData:
        """LLM JSON 을 검증·상한 적용해 휴리스틱 위에 덮는다. 필드가 비었으면
        휴리스틱 값을 유지한다(부분 실패 허용)."""
        summary = str(data.get("summary") or "").strip()[:_MAX_SUMMARY_CHARS]
        confidence = data.get("confidence")
        if confidence not in ("high", "medium", "low"):
            confidence = base.confidence

        steps: list[dict] = []
        for s in data.get("steps") or []:
            if not isinstance(s, dict):
                continue
            title = str(s.get("title") or "").strip()[:80]
            if not title:
                continue
            steps.append(
                {
                    "title": title,
                    "description": str(s.get("description") or "").strip()[:240],
                    "confidence": confidence if confidence != "low" else "medium",
                    "evidence": str(s.get("evidence") or "").strip()[:_MAX_EVIDENCE_CHARS],
                }
            )
            if len(steps) >= _MAX_STEPS:
                break

        mentioned: list[dict] = []
        seen = {t["name"].lower() for t in mentioned}
        for t in data.get("tools") or []:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name") or "").strip()[:60]
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            mentioned.append(
                {
                    "name": name,
                    "kind": "mentioned",
                    "confidence": "medium",
                    "basis": "본문에서 AI 감지",
                    "evidence": str(t.get("evidence") or "").strip()[:_MAX_EVIDENCE_CHARS],
                }
            )
            if len(mentioned) >= _MAX_TOOLS:
                break
        # LLM 감지 + 휴리스틱 감지 병합 (LLM 우선, 이름 중복 제거)
        for t in base.tool_mentions:
            if t["name"].lower() in seen or len(mentioned) >= _MAX_TOOLS:
                continue
            seen.add(t["name"].lower())
            mentioned.append(t)

        warnings = [str(w).strip()[:200] for w in (data.get("warnings") or []) if str(w).strip()]

        return SkillAnalysisData(
            skill=base.skill,
            body_hash=base.body_hash,
            analyzer_version=ANALYZER_VERSION,
            status="fresh",
            source="llm",
            summary=summary or base.summary,
            confidence=confidence,  # type: ignore[arg-type]
            procedural_steps=steps or base.procedural_steps,
            tool_mentions=mentioned,
            warnings=warnings[:_MAX_WARNINGS],
        )
