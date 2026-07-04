"""test_office_tools.py — office 빌트인 (create_presentation/create_workbook) 검증.

수용 기준:
  1. formula 엔진: 산술/참조/교차시트/함수(NPV·IRR 포함)/에러(#DIV/0!·#NAME?)/순환.
  2. xlsx: 유효한 zip + read_extract 라운드트립 + 수식 캐시값 + 스펙 오류 메시지.
  3. pptx: 9개 레이아웃 전부 → 필수 파트 존재 + 모든 파트 XML 파싱 + 노트 매핑
     (노트가 일부 슬라이드에만 있어도 올바른 슬라이드에 붙는다) + read_extract.
  4. 도구 등록: build_builtin_tools에 포함 + requires_approval=True + 샌드박스 탈출 거부.

격리: tempfile.mkdtemp() 임시 루트 (CONVENTIONS §6). 외부 의존성 없음.
"""

from __future__ import annotations

import tempfile
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

import pytest

from rice_runtime.agent.builtins import build_builtin_tools
from rice_runtime.agent.builtins.office.formula import (
    CellError,
    EvaluatedCell,
    evaluate_grid,
)
from rice_runtime.agent.builtins.office.pptx_writer import DeckSpecError, build_presentation
from rice_runtime.agent.builtins.office.xlsx_writer import WorkbookSpecError, build_workbook
from rice_runtime.agent.builtins.read_extract import extract_document_text
from rice_runtime.core.paths import RicePaths


def _fresh_paths() -> RicePaths:
    return RicePaths.from_env(tempfile.mkdtemp(prefix="rice-office-test-")).ensure()


# ── 1. formula engine ─────────────────────────────────────────────────────────

def _grid(cells: dict[tuple[str, int, int], tuple]) -> dict:
    return {
        key: EvaluatedCell(value=v, formula=f)
        for key, (v, f) in cells.items()
    }


def test_formula_arithmetic_refs_and_functions():
    grid = _grid({
        ("inputs", 2, 1): (100.0, None),          # inputs!B1
        ("inputs", 2, 2): (0.2, None),            # inputs!B2
        ("calc", 1, 1): (None, "inputs!B1*(1+inputs!B2)"),      # 120
        ("calc", 1, 2): (None, "A1*(1+inputs!B2)"),             # 144 (시트-로컬 참조)
        ("calc", 1, 3): (None, "SUM(A1:A2)"),                   # 264
        ("calc", 1, 4): (None, 'IF(A3>200, "big", "small")'),
        ("calc", 1, 5): (None, "ROUND(AVERAGE(A1:A2), 0)"),     # 132
        ("calc", 1, 6): (None, "10% + 0.4"),                    # 0.5 (% 리터럴)
    })
    evaluate_grid(grid, ["inputs", "calc"])
    assert grid[("calc", 1, 1)].value == pytest.approx(120.0)
    assert grid[("calc", 1, 2)].value == pytest.approx(144.0)
    assert grid[("calc", 1, 3)].value == pytest.approx(264.0)
    assert grid[("calc", 1, 4)].value == "big"
    assert grid[("calc", 1, 5)].value == pytest.approx(132.0)
    assert grid[("calc", 1, 6)].value == pytest.approx(0.5)


def test_formula_financial_functions():
    grid = _grid({
        ("s", 2, 1): (-100.0, None),
        ("s", 2, 2): (50.0, None),
        ("s", 2, 3): (60.0, None),
        ("s", 2, 4): (70.0, None),
        ("s", 1, 1): (None, "NPV(10%, 100, 100)"),
        ("s", 1, 2): (None, "IRR(B1:B4)"),
    })
    evaluate_grid(grid, ["s"])
    assert grid[("s", 1, 1)].value == pytest.approx(100 / 1.1 + 100 / 1.21)
    irr = grid[("s", 1, 2)].value
    # NPV(irr)이 0에 수렴하는지 직접 확인
    flows = [-100.0, 50.0, 60.0, 70.0]
    assert abs(sum(v / (1 + irr) ** i for i, v in enumerate(flows))) < 1e-6


def test_formula_errors_and_cycles():
    grid = _grid({
        ("s", 1, 1): (None, "1/0"),
        ("s", 1, 2): (None, "NOSUCHFN(1)"),
        ("s", 1, 3): (None, "A3"),            # self-cycle
        ("s", 1, 4): (None, "A1+1"),          # 에러 전파
        ("s", 1, 5): (None, "IFERROR(1/0, 42)"),
        ("s", 1, 6): (None, "Missing!A1"),    # 존재하지 않는 시트
    })
    evaluate_grid(grid, ["s"])
    assert str(grid[("s", 1, 1)].value) == "#DIV/0!"
    assert str(grid[("s", 1, 2)].value) == "#NAME?"
    assert str(grid[("s", 1, 3)].value) == "#CYCLE!"
    assert str(grid[("s", 1, 4)].value) == "#DIV/0!"
    assert grid[("s", 1, 5)].value == pytest.approx(42.0)
    assert str(grid[("s", 1, 6)].value) == "#REF!"


# ── 2. xlsx writer ────────────────────────────────────────────────────────────

def _model_spec() -> dict:
    return {
        "title": "Model",
        "sheets": [
            {
                "name": "Inputs",
                "widths": [24, 14],
                "freeze": "A2",
                "merges": ["A1:B1"],
                "rows": [
                    [{"value": "INPUTS", "style": "header"}, {"style": "header"}],
                    ["Users", {"value": 1000, "style": "input", "format": "int"}],
                    ["Price", {"value": 12.5, "style": "input", "format": "usd"}],
                ],
            },
            {
                "name": "Model",
                "rows": [
                    ["Revenue", {"formula": "=Inputs!B2*Inputs!B3", "format": "usd"}],
                    ["Check", {"formula": "=B1>0"}],
                ],
            },
        ],
    }


def test_xlsx_roundtrip_and_cached_values(tmp_path):
    data, summary = build_workbook(_model_spec())
    # 유효한 zip + 필수 파트
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = set(zf.namelist())
        assert {"[Content_Types].xml", "xl/workbook.xml", "xl/styles.xml",
                "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml"} <= names
        for name in names:
            if name.endswith((".xml", ".rels")):
                ET.fromstring(zf.read(name))  # 전 파트 well-formed

    # 수식이 로컬 평가되어 summary와 캐시값 양쪽에 반영된다
    assert summary["formulas"] == 2
    assert summary["formula_errors"] == []
    model_preview = "\n".join(summary["sheets"][1]["computed_preview"])
    assert "12500" in model_preview  # 1000 * 12.5
    assert "TRUE" in model_preview

    # read_extract 라운드트립 — read_file이 캐시값을 그대로 읽는다
    out = tmp_path / "model.xlsx"
    out.write_bytes(data)
    text = extract_document_text(str(out))
    assert "Sheet: Inputs" in text and "12500" in text


def test_xlsx_spec_errors():
    with pytest.raises(WorkbookSpecError, match="sheets"):
        build_workbook({"sheets": []})
    with pytest.raises(WorkbookSpecError, match="unknown cell style"):
        build_workbook({"sheets": [{"rows": [[{"value": 1, "style": "nope"}]]}]})
    with pytest.raises(WorkbookSpecError, match="duplicate sheet name"):
        build_workbook({"sheets": [{"name": "A", "rows": []}, {"name": "a", "rows": []}]})
    with pytest.raises(WorkbookSpecError, match="freeze"):
        build_workbook({"sheets": [{"rows": [], "freeze": "not-a-ref"}]})


# ── 3. pptx writer ────────────────────────────────────────────────────────────

def _deck_spec() -> dict:
    return {
        "title": "Pitch",
        "theme": {"primary": "1E2761", "secondary": "CADCFC", "accent": "F96167"},
        "slides": [
            {"layout": "title", "title": "Cover", "subtitle": "sub", "kicker": "K"},
            {"layout": "section", "title": "Part"},
            {"layout": "bullets", "title": "B", "bullets": ["one", {"text": "two", "sub": ["s"]}]},
            {"layout": "two_column", "title": "2C", "columns": [
                {"heading": "L", "bullets": ["a"]}, {"heading": "R", "bullets": ["b"]}]},
            {"layout": "stats", "title": "S", "stats": [{"value": "42%", "label": "conv"}]},
            {"layout": "table", "title": "T", "headers": ["a", "b"], "rows": [["1", "2"]]},
            {"layout": "timeline", "title": "R", "steps": [{"title": "s1"}, {"title": "s2"}]},
            {"layout": "quote", "quote": "wisdom", "attribution": "someone"},
            # 노트가 마지막 슬라이드에만 있다 → notesSlide1 ↔ slide9 매핑 검증
            {"layout": "closing", "title": "Thanks", "notes": "only-here"},
        ],
    }


def test_pptx_all_layouts_valid_package(tmp_path):
    data, summary = build_presentation(_deck_spec())
    assert summary["slide_count"] == 9
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = set(zf.namelist())
        required = {
            "[Content_Types].xml", "ppt/presentation.xml",
            "ppt/slideMasters/slideMaster1.xml", "ppt/slideLayouts/slideLayout1.xml",
            "ppt/theme/theme1.xml", "ppt/slides/slide1.xml", "ppt/slides/slide9.xml",
            "ppt/notesMasters/notesMaster1.xml", "ppt/notesSlides/notesSlide1.xml",
        }
        assert required <= names
        for name in names:
            if name.endswith((".xml", ".rels")):
                ET.fromstring(zf.read(name))  # 전 파트 well-formed

    # read_extract: 슬라이드 텍스트 + 노트가 올바른 슬라이드(9)에 붙는다
    out = tmp_path / "deck.pptx"
    out.write_bytes(data)
    text = extract_document_text(str(out))
    assert "Cover" in text and "wisdom" in text
    slide9 = text.split("# ── Slide 9 ──")[1]
    assert "[speaker notes]" in slide9 and "only-here" in slide9
    assert "only-here" not in text.split("# ── Slide 9 ──")[0]


def test_pptx_spec_errors():
    with pytest.raises(DeckSpecError, match="unknown layout"):
        build_presentation({"slides": [{"layout": "fancy"}]})
    with pytest.raises(DeckSpecError, match="title"):
        build_presentation({"slides": [{"layout": "title"}]})
    with pytest.raises(DeckSpecError, match="stats"):
        build_presentation({"slides": [{"layout": "stats", "title": "t", "stats": []}]})
    with pytest.raises(DeckSpecError, match="hex color"):
        build_presentation({"theme": {"primary": "blue"}, "slides": [{"layout": "title", "title": "t"}]})


# ── 4. tool registration + sandbox ───────────────────────────────────────────

async def test_office_tools_registered_and_sandboxed():
    paths = _fresh_paths()
    specs, handlers = build_builtin_tools(paths)
    by_name = {s.name: s for s in specs}
    assert "create_presentation" in by_name and "create_workbook" in by_name
    # 디스크 쓰기 도구 — write_file과 동일하게 승인 게이트를 탄다
    assert by_name["create_presentation"].requires_approval is True
    assert by_name["create_workbook"].requires_approval is True

    # 샌드박스 탈출 거부
    with pytest.raises(ValueError, match="escapes workspace"):
        await handlers["create_workbook"]({"path": "../evil.xlsx", "sheets": [{"rows": [[1]]}]})
    with pytest.raises(ValueError, match=".pptx"):
        await handlers["create_presentation"]({"path": "deck.txt", "slides": [{"layout": "title", "title": "t"}]})

    # 정상 경로: 생성 → read_file 추출 라운드트립
    result = await handlers["create_presentation"](
        {"path": "out/deck.pptx", "slides": [{"layout": "title", "title": "Hello"}]}
    )
    assert result["slide_count"] == 1
    read = await handlers["read_file"]({"path": "out/deck.pptx"})
    assert read["extracted"] is True and "Hello" in read["content"]

    wb = await handlers["create_workbook"](
        {"path": "out/m.xlsx", "sheets": [{"name": "S", "rows": [[{"formula": "=1+1"}]]}]}
    )
    assert wb["formulas"] == 1 and wb["formula_errors"] == []
    assert "2" in wb["sheets"][0]["computed_preview"][0]
