"""Skill AI-edit prompt — 자연어 지시 한 줄을 대상 파일의 완성된 새 본문으로
바꾸는 단발 structured-JSON 호출 (learning/service.py 의 propose_skill_edit 가 사용).

계약 (MySkills "AI로 수정" 드로어 · SkillSourceWorkspace "AI로 이 파일 수정"):
  - **한 파일 전체를 다시 쓴다.** 입력은 대상 파일의 현재 전문 + 사용자 지시(+ 직전
    대화)이고, 출력은 그 파일의 완성된 새 전문(new_body) + 한 줄 changelog 다.
    diff 계산·버전 증가·히스토리 보관은 저장 경로(update_skill/_file)가 담당하고,
    이 호출은 오직 "고쳐진 본문"만 만든다.
  - **구조 보존.** SKILL.md 면 frontmatter(---)와 헤딩 구조를, 스크립트/문서면 그
    형식을 유지한 채 지시가 요구한 부분만 바꾼다 — 요청하지 않은 재작성 금지.
  - **JSON만.** 로컬 소형 모델의 스키마 이탈은 core/model_json.py 의 관용 파서가
    흡수한다. thinking 없음 + response_schema + max_tokens 는 analysis 와 같은
    소형 모델 안정화 3종 세트(무한 thinking 루프·폭주 가드).
"""

from __future__ import annotations

import json

from ..core.types import Message

# 편집은 요약(0.1)보다 약간의 표현 여지가 필요하지만 여전히 낮게 — 지시 밖의
# 창작을 억제한다.
SKILL_EDIT_TEMPERATURE = 0.2

# 출력 토큰 예산의 바닥/천장 — 파일 전체를 다시 쓰므로 입력 크기에 비례해야 한다
# (한국어는 자당 ~1토큰까지 갈 수 있어 8k 고정이면 큰 파일에서 JSON 이 잘린다).
# 천장은 폭주 가드: thinking 을 끄지 못하는 모델의 reasoning 토큰까지 이 예산 안.
SKILL_EDIT_MIN_TOKENS = 8_000
SKILL_EDIT_MAX_TOKENS_CEILING = 24_000

# 프롬프트에 태우는 현재 본문 상한(자) — 스킬 본문 상한과 동일 수준.
SKILL_EDIT_BODY_MAX_CHARS = 20_000

# 직전 대화(반복 지시)에서 프롬프트에 싣는 최근 메시지 수 상한.
SKILL_EDIT_HISTORY_MAX_TURNS = 8

# 사용자 지시 상한(자) — 초과는 오타/오붙임일 가능성이 높고 프롬프트 예산을 깬다.
SKILL_EDIT_INSTRUCTION_MAX_CHARS = 4_000

# 스트림 수집 상한(자) — max_tokens 를 무시하는 provider 대비 이중 가드
# (analyzer 의 _MAX_RESPONSE_CHARS 와 같은 역할).
SKILL_EDIT_MAX_RESPONSE_CHARS = 120_000

# 저작 가이드(skill-authoring 빌트인)를 시스템 프롬프트에 싣는 상한(자).
SKILL_EDIT_GUIDE_MAX_CHARS = 6_000


def skill_edit_max_tokens(current_content_len: int) -> int:
    """입력 파일 크기에 비례한 출력 토큰 예산 — 전문 재작성이므로 최소한 원문을
    다시 쓸 수 있어야 한다. 한국어 최악 케이스(자당 ~1토큰)에 여유분을 더하되
    천장으로 폭주를 막는다."""
    return min(SKILL_EDIT_MAX_TOKENS_CEILING, max(SKILL_EDIT_MIN_TOKENS, current_content_len + 2_000))


def relevant_authoring_guide(authoring_body: str) -> str:
    """skill-authoring 빌트인 본문에서 **편집 품질에 기여하는 섹션만** 추린다 —
    §2(이름·메타데이터=발견성), §3(본문 작성), §5(상시 규칙). §1(스킬로 만들지
    판단)과 §4(propose_skill 워크플로)는 이 편집기와 무관하고, 특히 §4는 소형
    모델이 도구 호출을 흉내내게 만들 혼선 리스크가 있어 제외한다. 섹션 구조를
    못 찾으면(빌트인 개정 등) 전문을 상한까지 그대로 쓴다(품질 저하보다 누락이
    낫다는 판단이 아니라, 프레이밍 문구가 워크플로 지시를 무시하라고 막는다)."""
    sections = authoring_body.split("\n## ")
    picked = [s for s in sections[1:] if s.lstrip().startswith(("2.", "3.", "5."))]
    text = "\n## ".join(["", *picked]).strip() if picked else authoring_body
    return text[:SKILL_EDIT_GUIDE_MAX_CHARS]


def skill_edit_response_schema() -> dict:
    """Ollama grammar-constrained decoding 용 JSON 스키마 (analysis 와 동일 접근).
    스키마 미지원 provider(claude 등)는 무시하고 프롬프트의 "ONLY JSON" 규칙에
    의존한다."""
    return {
        "type": "object",
        "properties": {
            "changelog": {"type": "string"},
            "new_body": {"type": "string"},
        },
        "required": ["changelog", "new_body"],
    }


SKILL_EDIT_SYSTEM_PROMPT = (
    "You are RICE's skill file editor. You receive ONE file that belongs to an "
    "AI agent Skill (a SKILL.md instruction document, or a bundled "
    "script/reference/template file), plus a natural-language instruction from "
    "the user describing how they want it changed.\n\n"
    "Return the COMPLETE revised file — not a diff, not a fragment. The caller "
    "replaces the whole file with your `new_body`, so anything you omit is lost.\n\n"
    "Output ONLY a single JSON object, no prose, matching exactly:\n"
    "{\n"
    '  "changelog": string,   // one short line summarizing the change, in the SAME language as the instruction\n'
    '  "new_body": string     // the full revised file content\n'
    "}\n\n"
    "Rules:\n"
    "- Change only what the instruction asks for. Preserve everything else "
    "verbatim — headings, ordering, wording, and (for SKILL.md) the YAML "
    "frontmatter between --- fences.\n"
    "- Keep the file's format: SKILL.md stays valid Markdown with its "
    "frontmatter; a shell/python script stays runnable; JSON stays valid JSON.\n"
    "- `new_body` is the raw file text. Do NOT wrap it in Markdown code fences "
    "and do NOT escape it beyond normal JSON string escaping.\n"
    "- If the instruction is unclear or cannot be applied, make the smallest "
    "reasonable change and say so in `changelog`; never return an empty file.\n"
    "- No text of any kind outside the JSON object."
)


def build_skill_edit_messages(
    *,
    skill_name: str,
    description: str,
    target_path: str,
    current_content: str,
    instruction: str,
    history: list[dict] | None = None,
    authoring_guide: str | None = None,
) -> tuple[str, list[Message]]:
    """(system, messages) for one skill-edit call.

    history 는 드로어의 직전 대화 [{"role": "user"|"agent", "text": str}] — 반복
    지시("좀 더 짧게", "아까 그거 되돌려")를 이해하도록 최근 몇 턴만 싣는다.
    정본 입력은 어디까지나 current_content + 마지막 instruction 이다.

    authoring_guide 는 SKILL.md 편집일 때만 — skill-authoring 빌트인(저작 정본)의
    품질 규칙을 시스템 프롬프트에 덧붙여 house style 을 지키게 한다."""
    system = SKILL_EDIT_SYSTEM_PROMPT
    if authoring_guide:
        system += (
            "\n\n--- HOUSE STYLE (RICE skill-authoring rules) ---\n"
            "When the target file is a SKILL.md, your edit must also respect "
            "these authoring rules. They are quality guidance ONLY — if they "
            "mention tools or a proposal workflow, ignore that part; your only "
            "output contract remains the JSON object above.\n\n" + authoring_guide
        )
    messages: list[Message] = []
    for turn in (history or [])[-SKILL_EDIT_HISTORY_MAX_TURNS:]:
        text = str(turn.get("text") or "").strip()
        if not text:
            continue
        # agent 측 안내문은 assistant 로 실어 대화 맥락만 준다(재작성 지시는 아니다).
        role = "assistant" if turn.get("role") == "agent" else "user"
        messages.append(Message(role=role, content=text))

    payload = {
        "skill_name": skill_name,
        "frontmatter_description": description,
        "target_file": target_path,
        "current_content": current_content[:SKILL_EDIT_BODY_MAX_CHARS],
        "instruction": instruction,
    }
    ask = (
        "Apply the instruction to the target file and return the JSON object "
        "with the complete revised file.\n\n" + json.dumps(payload, ensure_ascii=False)
    )
    messages.append(Message(role="user", content=ask))
    return system, messages
