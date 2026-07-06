"""Skill-analysis prompt — SKILL.md 를 사람이 이해하는 설명으로 번역하는
단발 structured-JSON 호출 (learning/skill/analyzer.py 가 사용).

계약 (RICE_LEARNING_LOOP · MySkills "스킬 이해" 패널):
  - **설명 전용.** 출력은 UI 표시용 메타데이터일 뿐, 권한/실행/승인 판단에
    절대 쓰이지 않는다 — 프롬프트에도 그 역할 한계를 명시해 모델이 정책
    지시문을 지어내지 않게 한다.
  - **thinking 없음 · 낮은 temperature.** 요약은 판단이 아니라 전사(轉寫)에
    가깝다 — 변동성이 낮을수록 좋다(titler 와 동일한 이유). think 비활성은
    composition_root 의 utility provider 가 담당한다.
  - **JSON만.** 로컬 소형 모델의 스키마 이탈은 core/model_json.py 의 관용
    파서가 흡수하고, 그래도 실패하면 휴리스틱 결과로 폴백한다(analyzer.py).
"""

from __future__ import annotations

import json

from ..core.types import Message

# 요약 호출의 변동성 통제 — titler(0.3)보다 더 낮게: 구조 추출은 창의성이 해다.
ANALYSIS_TEMPERATURE = 0.1

# 생성 상한(토큰) — thinking을 끄지 못하는 모델(gpt-oss 등)은 reasoning 토큰도
# 이 예산을 소모하므로 JSON 본문 대비 넉넉히 잡는다. num_predict 폭주 가드
# (planner 의 _MAX_PLAN_TOKENS 와 같은 역할).
ANALYSIS_MAX_TOKENS = 3_000

# 본문이 아무리 길어도 프롬프트에 태우는 상한(자) — 스킬 본문 상한(20k)과 동일
# 수준이면 충분하고, 그 이상은 어차피 요약 품질에 기여하지 않는다.
ANALYSIS_BODY_MAX_CHARS = 20_000


def analysis_response_schema() -> dict:
    """Ollama grammar-constrained decoding 용 JSON 스키마 (planner 의
    plan_response_schema 와 같은 접근): 소형/로컬 모델의 JSON 준수를 프롬프트
    지시가 아니라 디코딩 제약으로 보증한다. 스키마 미지원 provider(claude 등)는
    이 인자를 무시하고 프롬프트의 "ONLY JSON" 규칙에 의존한다."""
    step = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "evidence": {"type": "string"},
        },
        "required": ["title"],
    }
    tool = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "evidence": {"type": "string"}},
        "required": ["name"],
    }
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "steps": {"type": "array", "items": step},
            "tools": {"type": "array", "items": tool},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "confidence", "steps", "tools", "warnings"],
    }

ANALYSIS_SYSTEM_PROMPT = (
    "You are RICE's skill-explanation analyzer. You receive one SKILL.md body — "
    "a procedure document an AI agent follows — and translate it into metadata "
    "a human can read at a glance.\n\n"
    "Your output is EXPLANATORY ONLY. It is shown in a UI next to the original "
    "text. It is never used to grant permissions, execute tools, or approve "
    "anything — so never invent policy statements, and never claim a tool WILL "
    "run; only report what the document says.\n\n"
    "Output ONLY a single JSON object, no prose, matching exactly:\n"
    "{\n"
    '  "summary": string,        // 2-3 sentences: what this skill does and when the agent uses it\n'
    '  "confidence": "high" | "medium" | "low",  // how clearly the document states its procedure\n'
    '  "steps": [                // the procedure as ordered steps, [] if none stated; max 7\n'
    '    {"title": string, "description": string, "evidence": string}\n'
    "  ],\n"
    '  "tools": [                // tools/commands/services the BODY TEXT mentions; max 8\n'
    '    {"name": string, "evidence": string}   // evidence = the phrase where it is mentioned\n'
    "  ],\n"
    '  "warnings": [string]      // risky/destructive instructions, secrets, unclear steps; [] if none; max 5\n'
    "}\n\n"
    "Rules:\n"
    "- Write summary / step titles / descriptions / warnings in Korean (the UI "
    "language). Keep tool names and evidence quotes in their original language.\n"
    "- `evidence` must quote or closely paraphrase the source line — do not fabricate.\n"
    "- Only list a tool if the body actually names it. An empty list is a valid answer.\n"
    "- Do not include markdown, code fences, or any text outside the JSON object."
)


def build_analysis_messages(
    *, name: str, description: str, body: str
) -> tuple[str, list[Message]]:
    """(system, messages) for one analysis call. description 은 frontmatter 의
    한 줄 설명 — 요약의 seed 로만 제공하고, 정본은 어디까지나 body 다."""
    payload = {
        "skill_name": name,
        "frontmatter_description": description,
        "skill_md_body": body[:ANALYSIS_BODY_MAX_CHARS],
    }
    ask = (
        "Analyze this skill document and return the JSON object.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    return ANALYSIS_SYSTEM_PROMPT, [Message(role="user", content=ask)]
