"""office/docx_writer.py — stdlib .docx writer (OOXML WordprocessingML).

Turns a JSON document spec (the `create_document` tool's arguments) into a valid
.docx: a title, styled headings, body paragraphs, bullet/numbered lists, tables,
quotes, callouts and dividers. Like the .pptx/.xlsx writers it is dependency-free
(hand-written OOXML) and confined to the workspace sandbox.

Design priority: **small-model robustness**. A local sLLM will hand us imperfect
specs — a block missing its `type`, a bare string where an object was expected,
a table row shorter than its header. None of that may abort the file. Every block
is rendered in isolation: a block that can't be built becomes a plain paragraph
of whatever text we can salvage, and the issue is noted in `summary["warnings"]`.
The document is always valid and always non-empty.

Block model (`spec.blocks`, in order — `type` is inferred when omitted):
  {"type": "heading", "level": 1-3, "text": "..."}
  {"type": "paragraph", "text": "..."}
  {"type": "bullets",  "items": ["..." | {"text": "...", "level": 0-2}]}
  {"type": "numbered", "items": [...]}
  {"type": "table",    "headers": ["..."], "rows": [["..."]]}
  {"type": "quote",    "text": "..."}
  {"type": "callout",  "text": "...", "label": "..."}
  {"type": "divider"}
A bare string block is treated as a paragraph.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from io import BytesIO
from xml.sax.saxutils import escape

from .palette import resolve_palette

__all__ = ["DocumentSpecError", "build_document"]

_MAX_BLOCKS = 400
_MAX_TEXT = 20_000
_MAX_TABLE_COLS = 12
_MAX_TABLE_ROWS = 200
_MAX_LIST_ITEMS = 200

# page geometry (US-Letter, 1" margins), twips (1/1440")
_PAGE_W, _PAGE_H, _MARGIN = 12240, 15840, 1440
_CONTENT_W = _PAGE_W - 2 * _MARGIN  # 9360

_INK = "20242E"
_MUTED = "667085"
_CARD = "F4F6FA"

_XMLDECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_PR = "http://schemas.openxmlformats.org/package/2006/relationships"

_ATTR = {chr(34): "&quot;"}


class DocumentSpecError(ValueError):
    """Invalid document spec — message surfaced to the model verbatim. Raised only
    for an unusable top-level shape; per-block problems degrade instead."""


@dataclass(frozen=True)
class _Style:
    primary: str
    secondary: str
    accent: str
    body_font: str
    heading_font: str
    ea_font: str | None

    @classmethod
    def from_spec(cls, spec: dict, warnings: list[str]) -> "_Style":
        colors = resolve_palette(spec.get("theme"), warnings)
        theme = spec.get("theme") if isinstance(spec.get("theme"), dict) else {}
        return cls(
            primary=colors["primary"],
            secondary=colors["secondary"],
            accent=colors["accent"],
            body_font=str(theme.get("body_font") or "Calibri"),
            heading_font=str(theme.get("heading_font") or "Calibri"),
            ea_font=(str(theme["ea_font"]) if theme.get("ea_font") else None),
        )


# ── coercion helpers (lenient) ────────────────────────────────────────────────

def _text(raw: object, *, max_len: int = _MAX_TEXT) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return "TRUE" if raw else "FALSE"
    s = str(raw).strip()
    return s if len(s) <= max_len else s[: max_len - 1].rstrip() + "…"


def _as_list(raw: object) -> list:
    if raw is None:
        return []
    return raw if isinstance(raw, list) else [raw]


# ── low-level XML builders ────────────────────────────────────────────────────

def _run(style: _Style, text: str, *, bold=False, italic=False, color: str | None = None,
         sz: int | None = None, heading=False) -> str:
    if not text:
        return ""
    face = style.heading_font if heading else style.body_font
    ea = style.ea_font or face
    props = []
    if bold:
        props.append("<w:b/>")
    if italic:
        props.append("<w:i/>")
    props.append(
        f'<w:rFonts w:ascii="{escape(face, _ATTR)}" w:hAnsi="{escape(face, _ATTR)}" '
        f'w:eastAsia="{escape(ea, _ATTR)}" w:cs="{escape(face, _ATTR)}"/>'
    )
    if color:
        props.append(f'<w:color w:val="{color}"/>')
    if sz:
        props.append(f'<w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/>')
    return (
        f"<w:r><w:rPr>{''.join(props)}</w:rPr>"
        f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'
    )


def _ppr(*, style: str | None = None, num: tuple[int, int] | None = None, align: str | None = None,
         before: int = 0, after: int = 120, shade: str | None = None,
         border_bottom: str | None = None, indent: int | None = None, keep_next=False) -> str:
    parts = []
    if style:
        parts.append(f'<w:pStyle w:val="{style}"/>')
    if keep_next:
        parts.append("<w:keepNext/>")
    if num is not None:
        parts.append(f'<w:numPr><w:ilvl w:val="{num[1]}"/><w:numId w:val="{num[0]}"/></w:numPr>')
    if shade:
        parts.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>')
    if border_bottom:
        parts.append(
            f'<w:pBdr><w:bottom w:val="single" w:sz="12" w:space="4" w:color="{border_bottom}"/></w:pBdr>'
        )
    if indent is not None:
        parts.append(f'<w:ind w:left="{indent}"/>')
    parts.append(f'<w:spacing w:before="{before}" w:after="{after}"/>')
    if align:
        parts.append(f'<w:jc w:val="{align}"/>')
    return f"<w:pPr>{''.join(parts)}</w:pPr>"


def _para(ppr: str, runs: str) -> str:
    return f"<w:p>{ppr}{runs}</w:p>"


# ── block renderers (each returns a list of body-level XML strings) ────────────

def _render_heading(style: _Style, block: dict) -> list[str]:
    level = block.get("level", 1)
    try:
        level = min(max(int(level), 1), 3)
    except (TypeError, ValueError):
        level = 1
    text = _text(block.get("text") or block.get("title"), max_len=2_000)
    if not text:
        return []
    sz = {1: 36, 2: 28, 3: 24}[level]
    before = {1: 260, 2: 200, 3: 160}[level]
    return [_para(
        _ppr(style=f"Heading{level}", before=before, after=100, keep_next=True),
        _run(style, text, bold=True, color=style.primary, sz=sz, heading=True),
    )]


def _render_paragraph(style: _Style, block: dict) -> list[str]:
    text = _text(block.get("text") or block.get("content"))
    if not text:
        return []
    # split on blank lines into separate paragraphs so \n\n reads naturally
    out = []
    for chunk in text.split("\n\n"):
        chunk = chunk.strip()
        if chunk:
            out.append(_para(_ppr(after=140), _run(style, chunk, color=_INK, sz=22)))
    return out


def _list_items(block: dict) -> list[tuple[str, int]]:
    items: list[tuple[str, int]] = []
    for it in _as_list(block.get("items"))[:_MAX_LIST_ITEMS]:
        if isinstance(it, dict):
            text = _text(it.get("text"), max_len=4_000)
            try:
                lvl = min(max(int(it.get("level", 0)), 0), 2)
            except (TypeError, ValueError):
                lvl = 0
        else:
            text, lvl = _text(it, max_len=4_000), 0
        if text:
            items.append((text, lvl))
    return items


def _render_bullets(style: _Style, block: dict) -> list[str]:
    return [
        _para(_ppr(style="ListParagraph", num=(1, lvl), after=60), _run(style, text, color=_INK, sz=22))
        for text, lvl in _list_items(block)
    ]


def _render_numbered(style: _Style, block: dict) -> list[str]:
    return [
        _para(_ppr(style="ListParagraph", num=(2, lvl), after=60), _run(style, text, color=_INK, sz=22))
        for text, lvl in _list_items(block)
    ]


def _render_quote(style: _Style, block: dict) -> list[str]:
    text = _text(block.get("text"))
    if not text:
        return []
    return [_para(
        _ppr(style="Quote", before=120, after=120, indent=360,
             border_bottom=None),
        _run(style, text, italic=True, color=_MUTED, sz=24),
    )]


def _render_divider(_style: _Style, _block: dict) -> list[str]:
    return [_para(_ppr(before=60, after=60, border_bottom=_style_accent(_style)), "")]


def _style_accent(style: _Style) -> str:
    return style.accent


def _render_callout(style: _Style, block: dict) -> list[str]:
    """A shaded single-cell box — good for a key takeaway / note."""
    text = _text(block.get("text"))
    if not text:
        return []
    label = _text(block.get("label"), max_len=80)
    runs = ""
    if label:
        runs += _run(style, label.upper() + "  ", bold=True, color=style.primary, sz=18)
    runs += _run(style, text, color=_INK, sz=22)
    cell = (
        '<w:tc><w:tcPr>'
        f'<w:tcW w:w="{_CONTENT_W}" w:type="dxa"/>'
        f'<w:shd w:val="clear" w:color="auto" w:fill="{style.secondary}"/>'
        '<w:tcMar><w:top w:w="120" w:type="dxa"/><w:left w:w="200" w:type="dxa"/>'
        '<w:bottom w:w="120" w:type="dxa"/><w:right w:w="200" w:type="dxa"/></w:tcMar>'
        '</w:tcPr>'
        f'{_para(_ppr(after=0), runs)}</w:tc>'
    )
    return [_single_table(style, f"<w:tr>{cell}</w:tr>", [_CONTENT_W], border=False)]


def _render_table(style: _Style, block: dict) -> list[str]:
    headers = [_text(h, max_len=2_000) for h in _as_list(block.get("headers"))[:_MAX_TABLE_COLS]]
    raw_rows = _as_list(block.get("rows"))[:_MAX_TABLE_ROWS]
    body_rows = [[_text(c, max_len=4_000) for c in _as_list(r)] for r in raw_rows]
    ncols = max([len(headers)] + [len(r) for r in body_rows] or [0])
    if ncols == 0:
        return []
    col_w = _CONTENT_W // ncols
    widths = [col_w] * ncols

    trs: list[str] = []
    if headers:
        cells = [_table_cell(style, (headers[i] if i < len(headers) else ""), col_w,
                              header=True, band=False) for i in range(ncols)]
        trs.append("<w:tr>" + "".join(cells) + "</w:tr>")
    for ri, row in enumerate(body_rows):
        cells = [_table_cell(style, (row[i] if i < len(row) else ""), col_w,
                             header=False, band=(ri % 2 == 0)) for i in range(ncols)]
        trs.append("<w:tr>" + "".join(cells) + "</w:tr>")
    if not trs:
        return []
    return [_single_table(style, "".join(trs), widths, border=True)]


def _table_cell(style: _Style, text: str, w: int, *, header: bool, band: bool) -> str:
    fill = style.primary if header else (_CARD if band else "FFFFFF")
    color = "FFFFFF" if header else _INK
    run = _run(style, text, bold=header, color=color, sz=20)
    return (
        '<w:tc><w:tcPr>'
        f'<w:tcW w:w="{w}" w:type="dxa"/>'
        f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>'
        '<w:tcMar><w:top w:w="60" w:type="dxa"/><w:left w:w="120" w:type="dxa"/>'
        '<w:bottom w:w="60" w:type="dxa"/><w:right w:w="120" w:type="dxa"/></w:tcMar>'
        '<w:vAlign w:val="center"/></w:tcPr>'
        f'{_para(_ppr(after=0), run)}</w:tc>'
    )


def _single_table(style: _Style, trs: str, widths: list[int], *, border: bool) -> str:
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    if border:
        edge = f'<w:{{}} w:val="single" w:sz="4" w:space="0" w:color="D9DEE6"/>'
        borders = (
            "<w:tblBorders>"
            + "".join(edge.format(e) for e in ("top", "left", "bottom", "right", "insideH", "insideV"))
            + "</w:tblBorders>"
        )
    else:
        borders = ""
    return (
        "<w:tbl><w:tblPr>"
        f'<w:tblW w:w="{sum(widths)}" w:type="dxa"/>'
        '<w:tblLayout w:type="fixed"/>'
        f"{borders}</w:tblPr>"
        f"<w:tblGrid>{grid}</w:tblGrid>{trs}</w:tbl>"
        # a table must be followed by a paragraph or Word complains at body end
        + _para(_ppr(before=0, after=80), "")
    )


_BLOCK_RENDERERS = {
    "heading": _render_heading,
    "paragraph": _render_paragraph,
    "bullets": _render_bullets,
    "numbered": _render_numbered,
    "table": _render_table,
    "quote": _render_quote,
    "callout": _render_callout,
    "divider": _render_divider,
}

# common aliases a model might reach for
_BLOCK_ALIASES = {
    "para": "paragraph", "p": "paragraph", "text": "paragraph", "body": "paragraph",
    "h1": "heading", "h2": "heading", "h3": "heading", "title": "heading",
    "bullet": "bullets", "list": "bullets", "ul": "bullets",
    "ordered": "numbered", "numbers": "numbered", "ol": "numbered",
    "hr": "divider", "rule": "divider", "note": "callout", "box": "callout",
}


def _infer_type(block: dict) -> str:
    if block.get("items"):
        return "bullets"
    if block.get("headers") or block.get("rows"):
        return "table"
    if "level" in block or block.get("heading"):
        return "heading"
    if block.get("quote"):
        return "quote"
    return "paragraph"


def _render_block(style: _Style, raw: object, index: int, warnings: list[str]) -> list[str]:
    if isinstance(raw, str):
        raw = {"type": "paragraph", "text": raw}
    if not isinstance(raw, dict):
        warnings.append(f"blocks[{index}] is not an object — skipped")
        return []

    btype = str(raw.get("type") or "").strip().lower()
    btype = _BLOCK_ALIASES.get(btype, btype)
    if btype not in _BLOCK_RENDERERS:
        inferred = _infer_type(raw)
        if btype:
            warnings.append(f"blocks[{index}]: unknown type {btype!r} → rendered as {inferred}")
        btype = inferred
    # map an aliased single-heading ({'type':'h2'}) to its level
    if btype == "heading" and "level" not in raw:
        orig = str(raw.get("type") or "").strip().lower()
        if orig in ("h1", "h2", "h3"):
            raw = {**raw, "level": int(orig[1])}

    try:
        out = _BLOCK_RENDERERS[btype](style, raw)
    except Exception as exc:  # noqa: BLE001 — one block must never kill the doc
        warnings.append(f"blocks[{index}] ({btype}): {exc} → salvaged as a paragraph")
        salvage = _text(raw.get("text") or raw.get("title") or raw.get("quote"))
        out = [_para(_ppr(after=140), _run(style, salvage, color=_INK, sz=22))] if salvage else []
    return out


# ── package parts ─────────────────────────────────────────────────────────────

def _styles_xml(style: _Style) -> str:
    hfont, bfont = escape(style.heading_font, _ATTR), escape(style.body_font, _ATTR)
    ea = escape(style.ea_font or bfont, _ATTR)

    def heading(sid: str, name: str, sz: int) -> str:
        return (
            f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/>'
            '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
            '<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="100"/></w:pPr>'
            f'<w:rPr><w:rFonts w:ascii="{hfont}" w:hAnsi="{hfont}" w:eastAsia="{ea}"/>'
            f'<w:b/><w:color w:val="{style.primary}"/><w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/></w:rPr></w:style>'
        )

    return (
        _XMLDECL
        + f'<w:styles xmlns:w="{_W}">'
        + '<w:docDefaults><w:rPrDefault><w:rPr>'
        + f'<w:rFonts w:ascii="{bfont}" w:hAnsi="{bfont}" w:eastAsia="{ea}" w:cs="{bfont}"/>'
        + '<w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:rPrDefault>'
        + '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
        + '</w:docDefaults>'
        + '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
        + f'<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>'
        + '<w:next w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="80"/></w:pPr>'
        + f'<w:rPr><w:rFonts w:ascii="{hfont}" w:hAnsi="{hfont}" w:eastAsia="{ea}"/><w:b/>'
        + f'<w:color w:val="{style.primary}"/><w:sz w:val="56"/><w:szCs w:val="56"/></w:rPr></w:style>'
        + heading("Heading1", "heading 1", 36)
        + heading("Heading2", "heading 2", 28)
        + heading("Heading3", "heading 3", 24)
        + '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/>'
        + '<w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:ind w:left="360"/></w:pPr></w:style>'
        + '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/>'
        + f'<w:next w:val="Normal"/><w:qFormat/><w:rPr><w:i/><w:color w:val="{_MUTED}"/></w:rPr></w:style>'
        + "</w:styles>"
    )


def _numbering_xml() -> str:
    def bullet_levels() -> str:
        glyphs = ["•", "◦", "–"]  # bullet, white bullet, en-dash
        out = []
        for lvl in range(3):
            ind = 360 + lvl * 360
            out.append(
                f'<w:lvl w:ilvl="{lvl}"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
                f'<w:lvlText w:val="{escape(glyphs[lvl])}"/><w:lvlJc w:val="left"/>'
                f'<w:pPr><w:ind w:left="{ind}" w:hanging="360"/></w:pPr>'
                '</w:lvl>'
            )
        return "".join(out)

    def decimal_levels() -> str:
        fmts = ["decimal", "lowerLetter", "lowerRoman"]
        texts = ["%1.", "%2.", "%3."]
        out = []
        for lvl in range(3):
            ind = 360 + lvl * 360
            out.append(
                f'<w:lvl w:ilvl="{lvl}"><w:start w:val="1"/><w:numFmt w:val="{fmts[lvl]}"/>'
                f'<w:lvlText w:val="{texts[lvl]}"/><w:lvlJc w:val="left"/>'
                f'<w:pPr><w:ind w:left="{ind}" w:hanging="360"/></w:pPr></w:lvl>'
            )
        return "".join(out)

    return (
        _XMLDECL
        + f'<w:numbering xmlns:w="{_W}">'
        + f'<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/>{bullet_levels()}</w:abstractNum>'
        + f'<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="hybridMultilevel"/>{decimal_levels()}</w:abstractNum>'
        + '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
        + '<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>'
        + "</w:numbering>"
    )


def _content_types() -> str:
    return (
        _XMLDECL
        + f'<Types xmlns="{_CT}">'
        + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        + '<Default Extension="xml" ContentType="application/xml"/>'
        + '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        + '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        + '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
        + '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        + '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        + "</Types>"
    )


def _root_rels() -> str:
    return (
        _XMLDECL
        + f'<Relationships xmlns="{_PR}">'
        + f'<Relationship Id="rId1" Type="{_R}/officeDocument" Target="word/document.xml"/>'
        + '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        + f'<Relationship Id="rId3" Type="{_R}/extended-properties" Target="docProps/app.xml"/>'
        + "</Relationships>"
    )


def _document_rels() -> str:
    return (
        _XMLDECL
        + f'<Relationships xmlns="{_PR}">'
        + f'<Relationship Id="rId1" Type="{_R}/styles" Target="styles.xml"/>'
        + f'<Relationship Id="rId2" Type="{_R}/numbering" Target="numbering.xml"/>'
        + "</Relationships>"
    )


def _core_props(title: str) -> str:
    safe = escape(title[:200]) if title else "Document"
    return (
        _XMLDECL
        + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        + 'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        + f"<dc:title>{safe}</dc:title><dc:creator>RICE</dc:creator></cp:coreProperties>"
    )


def _app_props() -> str:
    return (
        _XMLDECL
        + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
        + "<Application>RICE office tools</Application></Properties>"
    )


def _document_xml(body_blocks: list[str]) -> str:
    sect = (
        f'<w:sectPr><w:pgSz w:w="{_PAGE_W}" w:h="{_PAGE_H}"/>'
        f'<w:pgMar w:top="{_MARGIN}" w:right="{_MARGIN}" w:bottom="{_MARGIN}" w:left="{_MARGIN}" '
        'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
    )
    return (
        _XMLDECL
        + f'<w:document xmlns:w="{_W}" xmlns:r="{_R}"><w:body>'
        + "".join(body_blocks)
        + sect
        + "</w:body></w:document>"
    )


# ── result preview (fed back to the model) ────────────────────────────────────

def _outline(style_blocks: list[tuple[str, dict]]) -> list[str]:
    """A compact structural outline for the tool result — headings and block
    kinds, so the model can eyeball the document shape without re-reading."""
    out: list[str] = []
    for btype, raw in style_blocks:
        if btype == "heading":
            level = raw.get("level", 1)
            text = _text(raw.get("text") or raw.get("title"), max_len=80)
            out.append(f"{'#' * (int(level) if str(level).isdigit() else 1)} {text}")
        elif btype in ("bullets", "numbered"):
            out.append(f"[{btype}: {len(_as_list(raw.get('items')))} items]")
        elif btype == "table":
            out.append(f"[table: {len(_as_list(raw.get('rows')))} rows]")
    return out[:60]


# ── entry point ───────────────────────────────────────────────────────────────

def build_document(spec: dict) -> tuple[bytes, dict]:
    """Document spec dict → (.docx bytes, summary).

    Never raises on content problems: bad blocks degrade and are listed in
    `summary["warnings"]`, and the document is guaranteed valid + non-empty."""
    if not isinstance(spec, dict):
        spec = {}
    warnings: list[str] = []
    style = _Style.from_spec(spec, warnings)

    body: list[str] = []
    title = _text(spec.get("title"), max_len=300)
    if title:
        body.append(_para(_ppr(style="Title", after=60), _run(style, title, bold=True,
                                                               color=style.primary, sz=56, heading=True)))
        subtitle = _text(spec.get("subtitle"), max_len=400)
        if subtitle:
            body.append(_para(_ppr(after=200), _run(style, subtitle, italic=True, color=_MUTED, sz=26)))

    raw_blocks = spec.get("blocks")
    if raw_blocks is None:
        # tolerate the whole doc being passed as top-level fields (no `blocks`)
        raw_blocks = []
    elif not isinstance(raw_blocks, list):
        warnings.append("`blocks` was not an array — wrapped as a single block")
        raw_blocks = [raw_blocks]
    if len(raw_blocks) > _MAX_BLOCKS:
        warnings.append(f"too many blocks ({len(raw_blocks)}) — kept the first {_MAX_BLOCKS}")
        raw_blocks = raw_blocks[:_MAX_BLOCKS]

    outline_src: list[tuple[str, dict]] = []
    n_blocks = 0
    for i, raw in enumerate(raw_blocks):
        rendered = _render_block(style, raw, i, warnings)
        if rendered:
            body.extend(rendered)
            n_blocks += 1
            if isinstance(raw, dict):
                bt = _BLOCK_ALIASES.get(str(raw.get("type") or "").strip().lower(),
                                        str(raw.get("type") or "").strip().lower())
                outline_src.append((bt if bt in _BLOCK_RENDERERS else _infer_type(raw), raw))

    if not body:
        # guarantee a valid, non-empty document even from an empty/garbage spec
        warnings.append("no renderable content — wrote a placeholder document")
        body.append(_para(_ppr(), _run(style, title or "Untitled document", color=_INK, sz=22)))

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types())
        zf.writestr("_rels/.rels", _root_rels())
        zf.writestr("docProps/core.xml", _core_props(title or str(spec.get("title") or "")))
        zf.writestr("docProps/app.xml", _app_props())
        zf.writestr("word/document.xml", _document_xml(body))
        zf.writestr("word/_rels/document.xml.rels", _document_rels())
        zf.writestr("word/styles.xml", _styles_xml(style))
        zf.writestr("word/numbering.xml", _numbering_xml())

    summary = {
        "title": title,
        "blocks": n_blocks,
        "outline": _outline(outline_src),
        "theme": {"primary": style.primary, "secondary": style.secondary, "accent": style.accent},
    }
    if warnings:
        summary["warnings"] = warnings
    return buf.getvalue(), summary
