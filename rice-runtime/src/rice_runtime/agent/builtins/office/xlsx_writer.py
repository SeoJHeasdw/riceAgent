"""office/xlsx_writer.py — stdlib .xlsx writer (OOXML SpreadsheetML).

Turns a JSON workbook spec (the `create_workbook` tool's arguments) into a valid
.xlsx: multiple sheets, live formulas, number formats, the banker-grade
blue/black/green styling convention (input/formula/link — from the excel-author
reference skill), column widths, merges, and frozen panes.

Formulas are precomputed by `formula.evaluate_grid` and written as cached `<v>`
values, so Excel/Numbers/`read_file` show real numbers immediately and the tool
result can report them back to the model (no LibreOffice recalc step needed).

Strings are written inline (`t="inlineStr"`) — no sharedStrings part; our own
`read_extract` and every mainstream viewer support it, and it keeps the package
to one part per sheet.

Cell spec (one entry in a row array):
  scalar                     → literal (str/number/bool/null)
  {"value": scalar}          → literal with styling
  {"formula": "=SUM(B2:B9)"} → formula ('=' optional)
  optional keys: "style": default|input|link|header|title|bold|note|highlight
                 "format": general|int|decimal|percent|percent2|usd|krw|eur|date
                           or a raw Excel number-format code ("#,##0.0,,\"M\"")
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from xml.sax.saxutils import escape

from .formula import (
    CellError,
    EvaluatedCell,
    Grid,
    evaluate_grid,
    index_to_col,
    parse_cell_ref,
)

__all__ = ["WorkbookSpecError", "build_workbook"]

_MAX_SHEETS = 20
_MAX_ROWS = 5_000
_MAX_COLS = 200
_MAX_FORMULA_CHARS = 2_000
_MAX_TEXT_CHARS = 10_000

_XMLDECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class WorkbookSpecError(ValueError):
    """Invalid workbook spec — message is surfaced to the model verbatim."""


# ── styling ──────────────────────────────────────────────────────────────────
# Fixed font/fill palette; cellXfs are interned per (style, numfmt) combination.

_FONTS: dict[str, str] = {
    # id order matters — index = position in this dict.
    "default": '<font><sz val="11"/><name val="Calibri"/></font>',
    "bold": '<font><b/><sz val="11"/><name val="Calibri"/></font>',
    "input": '<font><sz val="11"/><color rgb="FF0000FF"/><name val="Calibri"/></font>',
    "link": '<font><sz val="11"/><color rgb="FF006100"/><name val="Calibri"/></font>',
    "header": '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>',
    "title": '<font><b/><sz val="14"/><color rgb="FF1F4E79"/><name val="Calibri"/></font>',
    "note": '<font><i/><sz val="9"/><color rgb="FF808080"/><name val="Calibri"/></font>',
}
_FONT_INDEX = {name: i for i, name in enumerate(_FONTS)}

_FILLS: dict[str, str] = {
    # fills[0]=none and fills[1]=gray125 are an OOXML convention Excel expects.
    "none": '<fill><patternFill patternType="none"/></fill>',
    "gray125": '<fill><patternFill patternType="gray125"/></fill>',
    "header": '<fill><patternFill patternType="solid"><fgColor rgb="FF1F4E79"/><bgColor indexed="64"/></patternFill></fill>',
    "highlight": '<fill><patternFill patternType="solid"><fgColor rgb="FFBDD7EE"/><bgColor indexed="64"/></patternFill></fill>',
}
_FILL_INDEX = {name: i for i, name in enumerate(_FILLS)}

# style name → (font, fill). "result"/"default" both mean plain black formula text.
_STYLES: dict[str, tuple[str, str]] = {
    "default": ("default", "none"),
    "result": ("default", "none"),
    "bold": ("bold", "none"),
    "input": ("input", "none"),
    "link": ("link", "none"),
    "header": ("header", "header"),
    "title": ("title", "none"),
    "note": ("note", "none"),
    "highlight": ("bold", "highlight"),
}

# named number formats → builtin numFmtId, or a custom code (id assigned ≥164).
_BUILTIN_FORMATS: dict[str, int] = {
    "general": 0,
    "int": 3,        # #,##0
    "decimal": 4,    # #,##0.00
    "percent": 9,    # 0%
    "percent2": 10,  # 0.00%
    "date": 14,      # m/d/yyyy
}
_NAMED_CUSTOM_FORMATS: dict[str, str] = {
    "usd": '"$"#,##0',
    "krw": '"₩"#,##0',
    "eur": '"€"#,##0',
}


@dataclass
class _StyleTable:
    """Interns (style, numfmt) pairs into cellXfs indices; collects custom numFmts."""

    custom_formats: dict[str, int] = field(default_factory=dict)  # code → numFmtId
    xfs: dict[tuple[int, int, int], int] = field(default_factory=dict)  # (font,fill,numfmt) → xf idx
    _order: list[tuple[int, int, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.xf_index("default", None)  # xf 0 = plain default

    def _numfmt_id(self, fmt: str | None) -> int:
        if fmt is None:
            return 0
        key = fmt.strip()
        if key.lower() in _BUILTIN_FORMATS:
            return _BUILTIN_FORMATS[key.lower()]
        code = _NAMED_CUSTOM_FORMATS.get(key.lower(), key)
        if code not in self.custom_formats:
            self.custom_formats[code] = 164 + len(self.custom_formats)
        return self.custom_formats[code]

    def xf_index(self, style: str, fmt: str | None) -> int:
        if style not in _STYLES:
            raise WorkbookSpecError(
                f"unknown cell style {style!r} — use one of {sorted(_STYLES)}"
            )
        font_name, fill_name = _STYLES[style]
        key = (_FONT_INDEX[font_name], _FILL_INDEX[fill_name], self._numfmt_id(fmt))
        if key not in self.xfs:
            self.xfs[key] = len(self._order)
            self._order.append(key)
        return self.xfs[key]

    def to_xml(self) -> str:
        numfmts = "".join(
            f'<numFmt numFmtId="{fid}" formatCode="{escape(code, {chr(34): "&quot;"})}"/>'
            for code, fid in sorted(self.custom_formats.items(), key=lambda kv: kv[1])
        )
        numfmt_block = (
            f'<numFmts count="{len(self.custom_formats)}">{numfmts}</numFmts>'
            if self.custom_formats
            else ""
        )
        fonts = f'<fonts count="{len(_FONTS)}">{"".join(_FONTS.values())}</fonts>'
        fills = f'<fills count="{len(_FILLS)}">{"".join(_FILLS.values())}</fills>'
        borders = '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        base = '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        xfs = "".join(
            f'<xf numFmtId="{nf}" fontId="{fo}" fillId="{fi}" borderId="0" xfId="0"'
            f'{" applyNumberFormat=\"1\"" if nf else ""}'
            f'{" applyFont=\"1\"" if fo else ""}'
            f'{" applyFill=\"1\"" if fi else ""}/>'
            for (fo, fi, nf) in self._order
        )
        cellxfs = f'<cellXfs count="{len(self._order)}">{xfs}</cellXfs>'
        return (
            _XMLDECL
            + f'<styleSheet xmlns="{_NS_MAIN}">'
            + numfmt_block + fonts + fills + borders + base + cellxfs
            + "</styleSheet>"
        )


# ── spec parsing ─────────────────────────────────────────────────────────────

@dataclass
class _Cell:
    value: object = None  # literal scalar (pre-evaluation)
    formula: str | None = None
    xf: int = 0


@dataclass
class _Sheet:
    name: str
    rows: list[list[_Cell]]
    widths: list[float]
    freeze: str | None
    merges: list[str]


_INVALID_SHEET_CHARS = set('[]*?/\\:')


def _parse_sheet_name(raw: object, index: int, seen: set[str]) -> str:
    name = str(raw).strip() if raw is not None and str(raw).strip() else f"Sheet{index + 1}"
    if len(name) > 31 or any(ch in _INVALID_SHEET_CHARS for ch in name) or name.startswith("'"):
        raise WorkbookSpecError(
            f"invalid sheet name {name!r} — max 31 chars, no []*?/\\: characters"
        )
    if name.casefold() in seen:
        raise WorkbookSpecError(f"duplicate sheet name {name!r}")
    seen.add(name.casefold())
    return name


def _parse_cell(raw: object, styles: _StyleTable, where: str) -> _Cell:
    if raw is None:
        return _Cell()
    if isinstance(raw, (int, float, bool, str)):
        raw = {"value": raw}
    if not isinstance(raw, dict):
        raise WorkbookSpecError(f"{where}: cell must be a scalar or object, got {type(raw).__name__}")

    style = str(raw.get("style") or "default")
    fmt = raw.get("format")
    fmt = str(fmt) if fmt is not None else None
    xf = styles.xf_index(style, fmt)

    formula = raw.get("formula")
    if formula is not None:
        formula = str(formula).lstrip("=").strip()
        if not formula:
            raise WorkbookSpecError(f"{where}: empty formula")
        if len(formula) > _MAX_FORMULA_CHARS:
            raise WorkbookSpecError(f"{where}: formula exceeds {_MAX_FORMULA_CHARS} chars")
        return _Cell(formula=formula, xf=xf)

    value = raw.get("value")
    if isinstance(value, str):
        if len(value) > _MAX_TEXT_CHARS:
            raise WorkbookSpecError(f"{where}: text exceeds {_MAX_TEXT_CHARS} chars")
    elif isinstance(value, (int, float, bool)) or value is None:
        pass
    else:
        raise WorkbookSpecError(f"{where}: unsupported value type {type(value).__name__}")
    return _Cell(value=value, xf=xf)


def _parse_sheets(spec: dict, styles: _StyleTable) -> list[_Sheet]:
    raw_sheets = spec.get("sheets")
    if not isinstance(raw_sheets, list) or not raw_sheets:
        raise WorkbookSpecError("spec.sheets must be a non-empty array of sheet objects")
    if len(raw_sheets) > _MAX_SHEETS:
        raise WorkbookSpecError(f"too many sheets ({len(raw_sheets)} > {_MAX_SHEETS})")

    sheets: list[_Sheet] = []
    seen: set[str] = set()
    for si, raw in enumerate(raw_sheets):
        if not isinstance(raw, dict):
            raise WorkbookSpecError(f"sheets[{si}] must be an object")
        name = _parse_sheet_name(raw.get("name"), si, seen)

        raw_rows = raw.get("rows") or []
        if not isinstance(raw_rows, list):
            raise WorkbookSpecError(f"sheet {name!r}: rows must be an array of row arrays")
        if len(raw_rows) > _MAX_ROWS:
            raise WorkbookSpecError(f"sheet {name!r}: too many rows ({len(raw_rows)} > {_MAX_ROWS})")
        rows: list[list[_Cell]] = []
        for ri, raw_row in enumerate(raw_rows):
            if raw_row is None:
                rows.append([])
                continue
            if not isinstance(raw_row, list):
                raise WorkbookSpecError(f"sheet {name!r} row {ri + 1}: must be an array of cells")
            if len(raw_row) > _MAX_COLS:
                raise WorkbookSpecError(f"sheet {name!r} row {ri + 1}: too many columns (> {_MAX_COLS})")
            rows.append([
                _parse_cell(c, styles, f"sheet {name!r} cell {index_to_col(ci + 1)}{ri + 1}")
                for ci, c in enumerate(raw_row)
            ])

        widths: list[float] = []
        for wi, w in enumerate(raw.get("widths") or []):
            try:
                width = float(w)
            except (TypeError, ValueError):
                raise WorkbookSpecError(f"sheet {name!r}: widths[{wi}] must be a number") from None
            widths.append(min(max(width, 2.0), 120.0))

        freeze = raw.get("freeze")
        if freeze is not None:
            freeze = str(freeze).strip().upper()
            try:
                parse_cell_ref(freeze)
            except ValueError:
                raise WorkbookSpecError(
                    f"sheet {name!r}: freeze must be a cell ref like 'A2' (freeze rows above / cols left of it)"
                ) from None

        merges: list[str] = []
        for m in raw.get("merges") or []:
            ref = str(m).strip().upper().replace("$", "")
            try:
                first, _, second = ref.partition(":")
                parse_cell_ref(first), parse_cell_ref(second or first)
            except ValueError:
                raise WorkbookSpecError(f"sheet {name!r}: invalid merge range {m!r}") from None
            if second:
                merges.append(ref)

        sheets.append(_Sheet(name=name, rows=rows, widths=widths, freeze=freeze, merges=merges))
    return sheets


# ── XML parts ────────────────────────────────────────────────────────────────

def _fmt_number(v: float) -> str:
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return format(v, ".15g")


def _cell_xml(ref: str, cell: _Cell) -> str:
    s_attr = f' s="{cell.xf}"' if cell.xf else ""
    v = cell.value
    if cell.formula is not None:
        f = f"<f>{escape(cell.formula)}</f>"
        if isinstance(v, CellError):
            return f'<c r="{ref}"{s_attr} t="e">{f}<v>{escape(str(v))}</v></c>'
        if isinstance(v, bool):
            return f'<c r="{ref}"{s_attr} t="b">{f}<v>{1 if v else 0}</v></c>'
        if isinstance(v, str):
            return f'<c r="{ref}"{s_attr} t="str">{f}<v>{escape(v)}</v></c>'
        if isinstance(v, (int, float)):
            return f'<c r="{ref}"{s_attr}>{f}<v>{_fmt_number(float(v))}</v></c>'
        return f'<c r="{ref}"{s_attr}>{f}</c>'
    if v is None:
        return f'<c r="{ref}"{s_attr}/>' if cell.xf else ""
    if isinstance(v, bool):
        return f'<c r="{ref}"{s_attr} t="b"><v>{1 if v else 0}</v></c>'
    if isinstance(v, (int, float)):
        return f'<c r="{ref}"{s_attr}><v>{_fmt_number(float(v))}</v></c>'
    return f'<c r="{ref}"{s_attr} t="inlineStr"><is><t xml:space="preserve">{escape(str(v))}</t></is></c>'


def _freeze_xml(freeze: str) -> str:
    col, row = parse_cell_ref(freeze)
    x, y = col - 1, row - 1
    if x == 0 and y == 0:
        return ""
    pane = "bottomRight" if x and y else ("bottomLeft" if y else "topRight")
    attrs = (f' xSplit="{x}"' if x else "") + (f' ySplit="{y}"' if y else "")
    return (
        f'<pane{attrs} topLeftCell="{freeze}" activePane="{pane}" state="frozen"/>'
        f'<selection pane="{pane}"/>'
    )


def _sheet_xml(sheet: _Sheet) -> str:
    cols = ""
    if sheet.widths:
        col_defs = "".join(
            f'<col min="{i + 1}" max="{i + 1}" width="{w:g}" customWidth="1"/>'
            for i, w in enumerate(sheet.widths)
        )
        cols = f"<cols>{col_defs}</cols>"

    row_parts: list[str] = []
    for ri, row in enumerate(sheet.rows, start=1):
        cells = "".join(
            xml
            for ci, cell in enumerate(row, start=1)
            if (xml := _cell_xml(f"{index_to_col(ci)}{ri}", cell))
        )
        if cells:
            row_parts.append(f'<row r="{ri}">{cells}</row>')

    merges = ""
    if sheet.merges:
        refs = "".join(f'<mergeCell ref="{m}"/>' for m in sheet.merges)
        merges = f'<mergeCells count="{len(sheet.merges)}">{refs}</mergeCells>'

    view = f'<sheetView workbookViewId="0">{_freeze_xml(sheet.freeze) if sheet.freeze else ""}</sheetView>'
    return (
        _XMLDECL
        + f'<worksheet xmlns="{_NS_MAIN}" xmlns:r="{_NS_R}">'
        + f"<sheetViews>{view}</sheetViews>"
        + cols
        + f"<sheetData>{''.join(row_parts)}</sheetData>"
        + merges
        + "</worksheet>"
    )


def _content_types(n_sheets: int) -> str:
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(n_sheets)
    )
    return (
        _XMLDECL
        + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        + '<Default Extension="xml" ContentType="application/xml"/>'
        + '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        + overrides
        + '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        + '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        + "</Types>"
    )


def _root_rels() -> str:
    return (
        _XMLDECL
        + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        + '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        + '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        + "</Relationships>"
    )


def _workbook_xml(sheets: list[_Sheet]) -> str:
    entries = "".join(
        f'<sheet name="{escape(s.name, {chr(34): "&quot;"})}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
        for i, s in enumerate(sheets)
    )
    return (
        _XMLDECL
        + f'<workbook xmlns="{_NS_MAIN}" xmlns:r="{_NS_R}">'
        + f"<sheets>{entries}</sheets>"
        + "</workbook>"
    )


def _workbook_rels(sheets: list[_Sheet]) -> str:
    rels = "".join(
        f'<Relationship Id="rId{i + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i + 1}.xml"/>'
        for i in range(len(sheets))
    )
    styles = (
        f'<Relationship Id="rId{len(sheets) + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    return (
        _XMLDECL
        + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + rels + styles
        + "</Relationships>"
    )


def _core_props(title: str) -> str:
    safe = escape(title[:200]) if title else "Workbook"
    return (
        _XMLDECL
        + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        + 'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        + f"<dc:title>{safe}</dc:title><dc:creator>RICE</dc:creator>"
        + "</cp:coreProperties>"
    )


def _app_props() -> str:
    return (
        _XMLDECL
        + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
        + "<Application>RICE office tools</Application>"
        + "</Properties>"
    )


# ── result preview (fed back to the model) ───────────────────────────────────

_PREVIEW_ROWS = 40
_PREVIEW_COLS = 12


def _display(cell: _Cell) -> str:
    v = cell.value
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return _fmt_number(float(v))
    return str(v)


def _summarize(sheets: list[_Sheet]) -> dict:
    out_sheets = []
    errors: list[str] = []
    formula_count = 0
    for sheet in sheets:
        preview: list[list[str]] = []
        for ri, row in enumerate(sheet.rows):
            for ci, cell in enumerate(row):
                if cell.formula is not None:
                    formula_count += 1
                    if isinstance(cell.value, CellError):
                        errors.append(
                            f"{sheet.name}!{index_to_col(ci + 1)}{ri + 1} ={cell.formula} → {cell.value}"
                        )
            if ri < _PREVIEW_ROWS:
                preview.append([_display(c) for c in row[:_PREVIEW_COLS]])
        while preview and not any(s.strip() for s in preview[-1]):
            preview.pop()
        out_sheets.append({
            "name": sheet.name,
            "rows": len(sheet.rows),
            "computed_preview": ["\t".join(r) for r in preview],
        })
    return {"sheets": out_sheets, "formulas": formula_count, "formula_errors": errors}


# ── entry point ──────────────────────────────────────────────────────────────

def build_workbook(spec: dict) -> tuple[bytes, dict]:
    """Spec dict → (.xlsx bytes, summary). Raises WorkbookSpecError on bad input."""
    styles = _StyleTable()
    sheets = _parse_sheets(spec, styles)

    # Evaluate formulas against the whole workbook (cross-sheet refs allowed).
    grid: Grid = {}
    for sheet in sheets:
        key = sheet.name.strip().casefold()
        for ri, row in enumerate(sheet.rows, start=1):
            for ci, cell in enumerate(row, start=1):
                if cell.formula is not None or cell.value is not None:
                    grid[(key, ci, ri)] = EvaluatedCell(
                        value=(float(cell.value) if isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool) else cell.value),
                        formula=cell.formula,
                    )
    evaluate_grid(grid, [s.name for s in sheets])
    for sheet in sheets:
        key = sheet.name.strip().casefold()
        for ri, row in enumerate(sheet.rows, start=1):
            for ci, cell in enumerate(row, start=1):
                if cell.formula is not None:
                    cell.value = grid[(key, ci, ri)].value

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types(len(sheets)))
        zf.writestr("_rels/.rels", _root_rels())
        zf.writestr("docProps/core.xml", _core_props(str(spec.get("title") or "")))
        zf.writestr("docProps/app.xml", _app_props())
        zf.writestr("xl/workbook.xml", _workbook_xml(sheets))
        zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(sheets))
        zf.writestr("xl/styles.xml", styles.to_xml())
        for i, sheet in enumerate(sheets):
            zf.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(sheet))

    return buf.getvalue(), _summarize(sheets)
