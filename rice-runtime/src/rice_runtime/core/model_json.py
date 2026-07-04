"""Best-effort JSON extraction from LLM text output.

로컬 LLM(Ollama 등)은 "JSON만 출력하라"는 지시를 자주 어긴다 — 코드펜스로 감싸거나,
앞뒤에 잡담을 붙이거나, 사고 흔적을 남긴다. 구조화 출력을 소비하는 모든 자리
(서브에이전트 리포트, 턴 플래너)가 같은 관용 파서를 쓰도록 여기로 모은다.
파싱 실패는 예외가 아니라 None이다 — 구조 강제 때문에 턴을 깨선 안 된다(fail-soft).
"""

from __future__ import annotations

import json
import re

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_BRACE_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json_object(raw: str) -> dict | None:
    """모델 출력에서 첫 JSON 객체를 최선 노력으로 파싱한다.

    허용하는 형태: 순수 JSON / ```json 코드펜스 / 앞뒤 잡담이 붙은 JSON.
    dict가 아니거나 파싱 불가면 None."""
    text = raw.strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1)
    elif not text.startswith("{"):
        brace = _BRACE_RE.search(text)
        if brace is None:
            return None
        text = brace.group(0)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None
