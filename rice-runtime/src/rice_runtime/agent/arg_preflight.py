"""Tool-argument preflight — 승인/실행 전에 소형 모델의 인자 실수를 교정하거나
정확한 오류로 되돌린다 (RICE step-1 hardening, 2026-07-15).

배경: 로컬 sLLM(Qwen 등)은 배열/객체 도구 인자를 이따금 **JSON 문자열로 감싼다** —
예: create_presentation을 부르며 `slides: "[{...}]"` (배열이 아니라 문자열, 그
안의 JSON마저 깨진 경우가 관찰됨). 현재는 이 잘못된 호출이 (a) 승인창까지 띄운 뒤
(b) 실행 단계에서 실패하고, 루프는 그 실패를 눈감고 재시도한다 — 같은 실수를
반복하기 쉽다(모델의 고확률 습관이면 더더욱).

이 preflight는 도구가 이미 선언한 JSON Schema(spec.parameters)만으로 **최상위**
인자를 검사한다:
  - 문자열인데 declared type이 array/object이면 json.loads로 salvage(디코드),
  - 디코드에 실패하거나 타입이 안 맞으면 **승인을 띄우지 않고** 모델에게 정확한
    오류(어떤 인자가 왜 틀렸는지 + 올바른 형태)를 되돌린다 → 정보 있는 재시도.

의도적으로 얕다(깊은 스키마 검증 없음). 깊은 레이아웃/필드 구조 강제는
step-2(제약 디코딩용 response_schema)의 몫이다 — 여기서는 관찰된 실패(문자열로
감싼 배열/객체)만, 새 모델 호출 없이, 모든 도구에 대해 잡는다.

같은 습관의 한 단계 깊은 변형도 잡는다: 배열 인자 자체는 네이티브인데 **원소
하나하나가 JSON 문자열**인 경우(`slides: ["{...}", "{...}"]`). 선언된
items 스키마가 문자열을 허용하지 않을 때만, 디코드가 declared type으로 성공하는
원소를 교체한다. 원소 레벨은 **salvage-only — 절대 거절하지 않는다**: 빌더들이
원소 단위 fault-tolerance(placeholder/salvage 렌더)를 이미 갖추고 있으므로,
원소 하나가 깨졌다고 호출 전체를 되돌리면 오히려 퇴행이다.

Fail-soft: parameters가 없거나 형태가 이상하면 아무것도 하지 않는다(no-op) —
preflight가 정상 호출을 막는 일은 절대 없어야 한다.
"""

from __future__ import annotations

import json
from typing import Any

from ..core.types import ToolSpec

# 파이썬 값 → JSON 타입 이름(오류 메시지용). "parsed to a JSON number" 처럼
# 모델이 아는 어휘로 무엇이 잘못됐는지 정확히 알려준다.
_JSON_TYPENAME: dict[type, str] = {
    list: "array",
    dict: "object",
    str: "string",
    bool: "boolean",
    int: "number",
    float: "number",
    type(None): "null",
}


def _allowed_types(schema: dict) -> set[str]:
    """JSON Schema의 `type`를 집합으로. 문자열이면 {t}, 리스트면 그 원소들,
    없거나 이상하면 빈 집합(→ 이 인자는 건드리지 않는다)."""
    t = schema.get("type")
    if isinstance(t, str):
        return {t}
    if isinstance(t, list):
        return {x for x in t if isinstance(x, str)}
    return set()


def _coercion_target(schema: dict) -> str | None:
    """이 인자가 문자열로 왔을 때 디코드해줄 목표 타입("array"/"object") 또는 None.

    핵심 가드: declared type이 문자열도 허용하면(union 예: theme=["object","string"])
    그 문자열은 유효한 값이므로(bare preset 이름 등) 절대 건드리지 않는다."""
    allowed = _allowed_types(schema)
    if "string" in allowed:
        return None
    if "array" in allowed:
        return "array"
    if "object" in allowed:
        return "object"
    return None


def _json_typename(value: object) -> str:
    return _JSON_TYPENAME.get(type(value), type(value).__name__)


def _shape_hint(name: str, expected: str) -> str:
    if expected == "array":
        return f'"{name}": [ ... ]  (a real JSON array — NOT a "[ ... ]" string)'
    return f'"{name}": {{ ... }}  (a real JSON object — NOT a "{{ ... }}" string)'


def _reject(name: str, expected: str, reason: str) -> str:
    return (
        f"argument `{name}` must be a native JSON {expected}, but {reason} "
        f"Do not put the {expected} in quotes or JSON.stringify it. "
        f"Re-issue the call with {_shape_hint(name, expected)}."
    )


def _salvage_items(value: list, schema: dict) -> list | None:
    """배열 인자의 원소 레벨 salvage: 문자열 원소가 items의 declared type
    (array/object)으로 디코드되면 교체한 새 리스트를, 바뀐 게 없으면 None을
    돌려준다. 디코드 실패/타입 불일치 원소는 그대로 둔다(거절 없음 — 빌더의
    원소 단위 fault-tolerance 몫). items가 문자열을 허용하면 건드리지 않는다."""
    items = schema.get("items")
    if not isinstance(items, dict):
        return None
    expected = _coercion_target(items)
    if expected is None:
        return None
    out: list | None = None
    for i, el in enumerate(value):
        if not isinstance(el, str):
            continue
        try:
            parsed = json.loads(el.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        matched = isinstance(parsed, list) if expected == "array" else isinstance(parsed, dict)
        if not matched:
            continue
        if out is None:
            out = list(value)  # 첫 교정 때만 사본 — 원본 배열은 불변
        out[i] = parsed
    return out


def preflight_arguments(spec: ToolSpec, arguments: Any) -> tuple[Any, str | None]:
    """(coerced_arguments, error)를 돌려준다.

    - error가 not None: 이 호출을 실행하면 안 된다. 디스패처가 이 문자열을 도구
      결과(ok=False)로 모델에 되돌린다(승인창은 띄우지 않는다) → 정보 있는 재시도.
    - error가 None: coerced_arguments를 실행한다. 아무것도 안 바뀌었으면 원본
      dict를 그대로(identity 보존) 돌려준다 — 디스패처가 불필요한 replace를 건너뛴다.

    첫 번째 불복구 인자에서 즉시 반환한다(오류 하나만 정확히 — 소형 모델이
    한 번에 하나씩 고치도록). 원본 arguments는 변형하지 않는다(salvage는 사본에).
    """
    if not isinstance(arguments, dict):
        return arguments, None
    params = spec.parameters if isinstance(spec.parameters, dict) else None
    props = params.get("properties") if isinstance(params, dict) else None
    if not isinstance(props, dict):
        return arguments, None

    coerced: dict[str, Any] | None = None
    for name, value in arguments.items():
        schema = props.get(name)
        if not isinstance(schema, dict):
            continue
        if isinstance(value, list):
            # 배열 자체는 네이티브 — 원소가 JSON 문자열로 감싸인 변형만 salvage.
            salvaged = _salvage_items(value, schema)
            if salvaged is not None:
                if coerced is None:
                    coerced = dict(arguments)
                coerced[name] = salvaged
            continue
        if not isinstance(value, str):
            continue  # 그 외 네이티브 타입 — 관찰된 실패는 "문자열로 감쌌다"뿐
        expected = _coercion_target(schema)
        if expected is None:
            continue  # 스칼라/문자열허용/미지 스키마 — 대상 아님
        try:
            parsed = json.loads(value.strip())
        except (json.JSONDecodeError, ValueError) as exc:
            return arguments, _reject(
                name, expected, f"a string was received that is not valid JSON ({exc})."
            )
        matched = isinstance(parsed, list) if expected == "array" else isinstance(parsed, dict)
        if not matched:
            return arguments, _reject(
                name, expected, f"the received string parsed to a JSON {_json_typename(parsed)}."
            )
        if isinstance(parsed, list):
            # 최상위 salvage로 얻은 배열에도 같은 원소 레벨 교정을 적용한다.
            parsed = _salvage_items(parsed, schema) or parsed
        if coerced is None:
            coerced = dict(arguments)  # 첫 교정 때만 얕은 사본
        coerced[name] = parsed

    return (coerced if coerced is not None else arguments), None
