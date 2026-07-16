"""Step-1 hardening: tool-argument preflight (agent/arg_preflight.py).

관찰된 실패(로컬 sLLM이 `slides`를 JSON 문자열로 감쌈)를 승인 전에 salvage 하거나
정밀 오류로 되돌리는지 검증한다. 깊은 구조 검증은 범위 밖(step-2)."""

from __future__ import annotations

from rice_runtime.agent.arg_preflight import preflight_arguments
from rice_runtime.core.types import ToolSpec


def _spec(properties: dict) -> ToolSpec:
    return ToolSpec(
        name="t", description="", parameters={"type": "object", "properties": properties}
    )


# create_presentation를 본뜬 스펙: slides=배열, theme=object|string union, path=string.
PPT = _spec(
    {
        "path": {"type": "string"},
        "theme": {"type": ["object", "string"]},
        "slides": {"type": "array", "items": {"type": "object"}},
    }
)


def test_native_array_untouched_identity_preserved() -> None:
    args = {"path": "a.pptx", "slides": [{"layout": "title"}]}
    out, err = preflight_arguments(PPT, args)
    assert err is None
    assert out is args  # 사본을 만들지 않는다 → 디스패처가 replace를 건너뛴다


def test_stringified_array_is_salvaged() -> None:
    args = {"path": "a.pptx", "slides": '[{"layout": "title"}, {"layout": "closing"}]'}
    out, err = preflight_arguments(PPT, args)
    assert err is None
    assert out["slides"] == [{"layout": "title"}, {"layout": "closing"}]
    assert args["slides"] == '[{"layout": "title"}, {"layout": "closing"}]'  # 원본 불변


def test_malformed_string_is_rejected_with_precise_error() -> None:
    args = {"path": "a.pptx", "slides": '[{"layout": "title"'}  # 잘린 JSON
    out, err = preflight_arguments(PPT, args)
    assert err is not None
    assert "`slides`" in err
    assert "native JSON array" in err
    assert "JSON.stringify" in err  # 무엇을 하지 말지 명시
    assert out is args  # 거부 시 인자 불변


def test_quoted_scalar_wrong_type_is_rejected() -> None:
    args = {"path": "a.pptx", "slides": '"just a heading"'}  # 유효 JSON이나 배열 아님
    out, err = preflight_arguments(PPT, args)
    assert err is not None
    assert "parsed to a JSON string" in err


def test_union_with_string_left_alone() -> None:
    # theme은 bare preset 문자열("forest")을 허용 → 문자열을 절대 coerce하지 않는다.
    args = {"path": "a.pptx", "slides": [{"layout": "title"}], "theme": "forest"}
    out, err = preflight_arguments(PPT, args)
    assert err is None
    assert out["theme"] == "forest"


def test_object_param_is_salvaged() -> None:
    spec = _spec({"cfg": {"type": "object"}})
    out, err = preflight_arguments(spec, {"cfg": '{"a": 1, "b": [2, 3]}'})
    assert err is None
    assert out["cfg"] == {"a": 1, "b": [2, 3]}


def test_object_param_given_array_is_rejected() -> None:
    spec = _spec({"cfg": {"type": "object"}})
    out, err = preflight_arguments(spec, {"cfg": "[1, 2, 3]"})
    assert err is not None
    assert "native JSON object" in err
    assert "parsed to a JSON array" in err


def test_string_param_never_coerced() -> None:
    spec = _spec({"note": {"type": "string"}})
    args = {"note": '{"looks": "like json"}'}  # string 파라미터 → 그대로 둔다
    out, err = preflight_arguments(spec, args)
    assert err is None
    assert out is args


def test_missing_properties_is_noop() -> None:
    spec = ToolSpec(name="t", description="", parameters={"type": "object"})
    args = {"slides": '"untouched"'}
    out, err = preflight_arguments(spec, args)
    assert err is None
    assert out is args


def test_non_dict_arguments_noop() -> None:
    out, err = preflight_arguments(PPT, None)
    assert err is None
    assert out is None


def test_multiple_params_reports_first_failure_only() -> None:
    spec = _spec({"a": {"type": "array"}, "b": {"type": "array"}})
    out, err = preflight_arguments(spec, {"a": "[broken", "b": "[1]"})
    assert err is not None
    assert "`a`" in err  # 한 번에 하나씩 — 첫 불복구 인자만


# ── 원소 레벨 salvage: 네이티브 배열 안의 JSON-문자열 원소 ─────────────────────


def test_stringified_elements_in_native_array_are_salvaged() -> None:
    args = {"path": "a.pptx", "slides": ['{"layout": "title"}', {"layout": "closing"}]}
    out, err = preflight_arguments(PPT, args)
    assert err is None
    assert out["slides"] == [{"layout": "title"}, {"layout": "closing"}]
    assert args["slides"][0] == '{"layout": "title"}'  # 원본 배열 불변


def test_stringified_elements_inside_salvaged_array() -> None:
    # 최상위와 원소가 동시에 감싸인 이중 변형: slides="[\"{...}\"]"
    args = {"path": "a.pptx", "slides": '["{\\"layout\\": \\"title\\"}"]'}
    out, err = preflight_arguments(PPT, args)
    assert err is None
    assert out["slides"] == [{"layout": "title"}]


def test_undecodable_element_left_alone_never_rejected() -> None:
    # 원소 레벨은 salvage-only — 빌더의 원소 단위 fault-tolerance가 처리한다.
    args = {"path": "a.pptx", "slides": ["{broken json", {"layout": "title"}]}
    out, err = preflight_arguments(PPT, args)
    assert err is None
    assert out is args  # 바뀐 게 없으면 identity 보존


def test_prose_string_element_left_alone() -> None:
    # create_document의 blocks는 bare 문자열(문단)이 유효 — JSON 객체로 디코드되지
    # 않는 문자열 원소는 절대 건드리지 않는다.
    doc = _spec({"blocks": {"type": "array", "items": {"type": "object"}}})
    args = {"blocks": ["Plain paragraph text.", {"type": "heading", "text": "T"}]}
    out, err = preflight_arguments(doc, args)
    assert err is None
    assert out is args


def test_element_decoding_to_wrong_type_left_alone() -> None:
    # "[1, 2]"는 유효 JSON이나 items가 object → 교체하지 않는다(문자열 유지).
    args = {"path": "a.pptx", "slides": ["[1, 2]", {"layout": "title"}]}
    out, err = preflight_arguments(PPT, args)
    assert err is None
    assert out is args


def test_string_allowing_items_never_coerced() -> None:
    # items가 문자열을 허용하는 배열(예: 태그 목록)은 원소를 절대 디코드하지 않는다.
    spec = _spec({"tags": {"type": "array", "items": {"type": ["object", "string"]}}})
    args = {"tags": ['{"looks": "like json"}']}
    out, err = preflight_arguments(spec, args)
    assert err is None
    assert out is args


def test_items_without_schema_left_alone() -> None:
    spec = _spec({"xs": {"type": "array"}})  # items 미선언 → 대상 아님
    args = {"xs": ['{"a": 1}']}
    out, err = preflight_arguments(spec, args)
    assert err is None
    assert out is args
