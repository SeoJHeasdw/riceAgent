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
