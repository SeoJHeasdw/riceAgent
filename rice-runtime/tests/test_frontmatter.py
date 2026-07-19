"""core/frontmatter.py 테스트 — 공용 파서 승격(2026-07-19)의 계약 검증.

store.py(정본 의미론)·loader.py(관대한 매핑)가 공유하는 메커니즘이므로,
PyYAML 경로와 **폴백 최소 파서**가 같은 형태의 dict 를 내는지가 핵심이다 —
폴백은 두 원본 파서의 합집합(타입 강제 + 블록 스칼라 + 대시/인라인 리스트).
"""

from __future__ import annotations

import pytest

from rice_runtime.core.errors import ValidationError
from rice_runtime.core.frontmatter import (
    _minimal_yaml_parse,
    dump_yaml,
    parse_yaml,
    split_frontmatter,
)
from rice_runtime.learning.skill.store import parse_skill_md

SAMPLE = """\
---
name: my-skill
version: 2
description: A one-liner
state: active
tags: [alpha, beta]
---
## Steps
1. do the thing
"""


def test_split_frontmatter_returns_dict_and_raw_body():
    fm, body = split_frontmatter(SAMPLE)
    assert fm["name"] == "my-skill"
    assert fm["version"] == 2
    assert fm["tags"] == ["alpha", "beta"]
    assert "## Steps" in body and "---" not in body


def test_split_frontmatter_none_without_delimiters():
    assert split_frontmatter("just a body\nno frontmatter") is None
    # 줄 전체가 --- 인 경우만 구분자다 — 본문 속 "a---b" 는 오분리되지 않는다.
    assert split_frontmatter("a---b---c") is None
    # 문서 중간의 --- 블록은 frontmatter 가 아니다. 앞 preamble 을 버리면 안 된다.
    assert split_frontmatter("preamble\n---\nname: hidden\n---\nbody") is None


def test_minimal_parser_matches_pyyaml_on_flat_fields():
    fm_text = "name: x\nversion: 3\nenabled: false\nsource: null\ntags: [a, b]"
    assert _minimal_yaml_parse(fm_text) == parse_yaml(fm_text)


def test_minimal_parser_block_scalar_and_dash_list():
    # 블록 스칼라(Claude Code 스킬의 흔한 description 형식) — 연속 줄 공백 join.
    fm = _minimal_yaml_parse("description: |\n  first line\n  second line\nstate: active")
    assert fm["description"] == "first line second line"
    assert fm["state"] == "active"
    # 대시 리스트 — 값 없는 키 뒤의 들여쓴 항목들.
    fm = _minimal_yaml_parse("tags:\n  - alpha\n  - beta\nname: n")
    assert fm["tags"] == ["alpha", "beta"]
    assert fm["name"] == "n"


def test_dump_roundtrips_through_parse():
    data = {"name": "x", "version": 1, "state": "active", "tags": ["a", "b"], "enabled": False}
    assert parse_yaml(dump_yaml(data)) == data


def test_strict_skill_parser_wraps_malformed_yaml_as_validation_error():
    with pytest.raises(ValidationError, match="valid YAML"):
        parse_skill_md("---\nname: [broken\n---\nbody")
