"""core/frontmatter.py — 마크다운 frontmatter(YAML) 파싱/직렬화 공용 메커니즘.

원래 두 곳에 의도적으로 중복돼 있던 파서를 core 로 승격했다(2026-07-19):
  - learning/skill/store.py — 정본 파서(PyYAML + 최소 폴백, 타입 강제)
  - execution/plugin/loader.py — 미니 파서(블록 스칼라·대시 리스트 지원, 무의존)
모듈 간 직접 import 금지선(RUNTIME_DIRECTORY #14) 때문에 공유하지 못하던 것으로,
core 이동이 정식 해소책이었다(guard 와 같은 경로). 필드 의미론(name 필수·state
enum 등)은 여기 두지 않는다 — 그건 각 소비자(store.parse_skill_md, loader 매핑)의
계약이고, 여기는 "--- 블록 분리 + YAML dict 화 + 직렬화" 메커니즘만 소유한다.

PyYAML 은 pyproject 필수 의존성이지만(>=6.0), 폴백 최소 파서를 유지한다 —
embedded/축소 배포에서 의존성이 빠져도 SKILL.md 코어 필드는 계속 읽혀야 한다
(fail-soft). 폴백은 두 원본 파서의 합집합: 평탄 key-value + 타입 강제(int/bool/
null) + 인라인 리스트 + 대시 리스트 + 블록 스칼라(`|`/`>`, Claude Code 스킬의
흔한 description 형식 — 연속 줄을 공백으로 잇는다).
"""

from __future__ import annotations

import re
from typing import Any

# frontmatter 구분자 — 줄 전체가 --- 인 경우만(본문 속 "a---b" 오분리 방지).
# ``\s`` 는 개행까지 먹으므로 쓰지 않는다: 구분자 한 줄만 정확히 소비해야 body
# 원문 경계가 예측 가능하다.
_FM_DELIM = re.compile(r"^---[ \t]*\r?$", re.MULTILINE)


def split_frontmatter(text: str) -> tuple[dict[str, Any], str] | None:
    """`--- ... ---` 첫 블록을 frontmatter 로 분리해 (dict, 원문 body)를 반환.

    frontmatter 블록이 없으면 None — "전체를 body 로 볼지, 오류로 볼지"는 소비자
    판단이다(store 는 ValidationError, loader 는 관대한 기본값). body 는 닫는
    구분자 뒤 원문 그대로 반환한다(선행 개행 strip 여부도 소비자 몫 — store 는
    lstrip("\\n"), loader 는 strip()).
    """
    # Frontmatter 는 문서 첫 줄에서만 성립한다. 종전 store 정규식은 중간의 ---도
    # 첫 구분자로 받아 앞의 preamble 을 조용히 버렸고, loader 의 기존
    # ``text.startswith('---')`` 계약과도 달랐다. 공용 파서는 더 엄격한 교집합을
    # 택한다 — BOM/숨김문자가 앞에 있으면 설치 위험 스캔이 원문에서 잡는다.
    opening = _FM_DELIM.match(text)
    if opening is None:
        return None
    closing = _FM_DELIM.search(text, opening.end())
    if closing is None:
        return None
    return parse_yaml(text[opening.end() : closing.start()]), text[closing.end() :]


def parse_yaml(text: str) -> dict[str, Any]:
    """YAML 문자열 → dict. PyYAML 없으면 최소 파서로 폴백."""
    try:
        import yaml  # lazy import — 폴백 경로가 있으므로 모듈 로드는 지연

        loaded = yaml.safe_load(text)
        return loaded if isinstance(loaded, dict) else {}
    except ImportError:
        return _minimal_yaml_parse(text)


def dump_yaml(data: dict[str, Any]) -> str:
    """dict → YAML 문자열. PyYAML 없으면 최소 직렬화로 폴백."""
    try:
        import yaml

        return yaml.safe_dump(
            data, allow_unicode=True, sort_keys=False, default_flow_style=False
        )
    except ImportError:
        return _minimal_yaml_dump(data)


def _coerce_scalar(val: str) -> Any:
    """폴백 파서의 스칼라 타입 강제 — PyYAML 결과와 최대한 동형이 되게."""
    if val.isdigit():
        return int(val)
    if val.lower() in ("true", "false"):
        return val.lower() == "true"
    if val in ("null", "~"):
        return None
    return val.strip("'\"")


def _minimal_yaml_parse(text: str) -> dict[str, Any]:
    """PyYAML 없이 쓰는 최소 파서 — 평탄 key-value + 인라인/대시 리스트 +
    블록 스칼라. 중첩 맵·앵커 등은 미지원(SKILL.md frontmatter 는 의도적으로
    단순 평탄 구조만 쓴다 — LEARNING_LOOP §2)."""
    result: dict[str, Any] = {}
    in_list_key: str | None = None
    in_block_key: str | None = None
    for line in text.splitlines():
        line = line.rstrip()
        if in_block_key is not None:
            # `description: |` / `>` 블록 스칼라 — 들여쓰인 연속 줄을 한 문자열로
            # 잇는다(빈 줄은 블록 유지). 리스트 대기 상태로 오인하면 빈 값이 된다.
            if not line:
                continue
            if line.startswith((" ", "\t")):
                joined = f"{result[in_block_key]} {line.strip()}".strip()
                result[in_block_key] = joined
                continue
            in_block_key = None
        if not line:
            in_list_key = None
            continue
        stripped = line.lstrip()
        if stripped.startswith("- ") and in_list_key:
            result.setdefault(in_list_key, []).append(_coerce_scalar(stripped[2:].strip()))
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if val.startswith("[") and val.endswith("]"):
            result[key] = [
                _coerce_scalar(v.strip()) for v in val[1:-1].split(",") if v.strip()
            ]
            in_list_key = None
        elif val in ("|", ">", "|-", ">-", "|+", ">+"):
            result[key] = ""
            in_block_key = key
            in_list_key = None
        elif val == "":
            # 값 없는 키 — 다음 줄들이 대시 리스트일 수 있다(대기 상태).
            in_list_key = key
            result[key] = []
        else:
            result[key] = _coerce_scalar(val)
            in_list_key = None
    return result


def _minimal_yaml_dump(data: dict[str, Any]) -> str:
    """PyYAML 없이 쓰는 최소 직렬화 — parse 폴백과 왕복 가능한 부분집합."""
    lines = []
    for k, v in data.items():
        if v is None:
            lines.append(f"{k}: null")
        elif isinstance(v, list):
            items = ", ".join(str(i) for i in v)
            lines.append(f"{k}: [{items}]")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            lines.append(f"{k}: {v}")
    return "\n".join(lines) + "\n"
