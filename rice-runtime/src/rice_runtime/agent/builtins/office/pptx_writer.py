"""office/pptx_writer.py — stdlib .pptx writer (OOXML PresentationML).

Turns a JSON deck spec (the `create_presentation` tool's arguments) into a valid
16:9 .pptx. Slides are composed from absolutely-positioned shapes — no template
placeholders — so every layout is fully designed here and renders identically in
PowerPoint, Keynote and LibreOffice.

Design system (distilled from the hermes-agent `office/powerpoint` reference
skill): one dominant brand color with a light tint + sharp accent, a dark
cover/section/closing "sandwich" around light content slides, big stat callouts,
varied layouts, left-aligned body text, no accent lines under titles.

Layouts (`slides[].layout`):
  title       cover — kicker/title/subtitle/footer on the primary color
  section     dark divider — auto-numbered, oversized index digit
  bullets     title + up to ~7 bullets (each may carry `sub` items)
  two_column  title + 2–3 cards, each {heading, bullets}
  stats       title + 2–4 large stat callout cards {value, label, note?}
  table       title + styled table {headers, rows}
  timeline    title + 2–6 numbered steps {title, desc?} on a horizontal rail
  quote       oversized quotation mark + quote + attribution
  closing     dark thank-you/CTA slide {title, subtitle?, contact?}
Any slide may set `notes` (speaker notes, emitted as real notesSlides).
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from xml.sax.saxutils import escape

__all__ = ["DeckSpecError", "build_presentation"]

_MAX_SLIDES = 30
_MAX_TEXT = 2_000

EMU_PER_IN = 914_400
SLIDE_W = int(13.333 * EMU_PER_IN)
SLIDE_H = int(7.5 * EMU_PER_IN)


def _in(v: float) -> int:
    return int(v * EMU_PER_IN)


class DeckSpecError(ValueError):
    """Invalid deck spec — message is surfaced to the model verbatim."""


# ── theme ────────────────────────────────────────────────────────────────────

_HEX = re.compile(r"^#?([0-9A-Fa-f]{6})$")


def _hex(value: object, fallback: str) -> str:
    if value is None:
        return fallback
    m = _HEX.match(str(value).strip())
    if not m:
        raise DeckSpecError(f"invalid hex color {value!r} — use 6-digit hex like '1E2761'")
    return m.group(1).upper()


@dataclass(frozen=True)
class Theme:
    primary: str  # dominant brand color (dark enough for white text)
    secondary: str  # light tint of the brand
    accent: str  # sharp contrast accent
    heading_font: str
    body_font: str
    ea_font: str | None  # East-Asian typeface (optional)

    # fixed neutrals — content slides stay light with dark text
    BG = "FFFFFF"
    INK = "20242E"  # body text on light
    MUTED = "667085"  # captions, notes
    CARD = "F4F6FA"  # light card fill

    @classmethod
    def from_spec(cls, raw: object) -> "Theme":
        raw = raw if isinstance(raw, dict) else {}
        return cls(
            primary=_hex(raw.get("primary"), "1E2761"),
            secondary=_hex(raw.get("secondary"), "CADCFC"),
            accent=_hex(raw.get("accent"), "F96167"),
            heading_font=str(raw.get("heading_font") or "Georgia"),
            body_font=str(raw.get("body_font") or "Calibri"),
            ea_font=(str(raw["ea_font"]) if raw.get("ea_font") else None),
        )


# ── drawing primitives ───────────────────────────────────────────────────────

class _Shapes:
    """Builds the slide's <p:sp> list with unique shape ids."""

    def __init__(self, theme: Theme) -> None:
        self.theme = theme
        self._id = 1
        self.xml: list[str] = []

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    # text ------------------------------------------------------------------
    def run(
        self,
        text: str,
        *,
        sz: int,
        color: str,
        bold: bool = False,
        italic: bool = False,
        font: str | None = None,
        spacing: int | None = None,  # letter-spacing in 1/100 pt
    ) -> str:
        t = self.theme
        attrs = f' lang="en-US" sz="{sz * 100}"'
        if bold:
            attrs += ' b="1"'
        if italic:
            attrs += ' i="1"'
        if spacing:
            attrs += f' spc="{spacing}"'
        typeface = font or t.body_font
        ea = f'<a:ea typeface="{escape(t.ea_font, {chr(34): "&quot;"})}"/>' if t.ea_font else ""
        return (
            f"<a:r><a:rPr{attrs}><a:solidFill><a:srgbClr val=\"{color}\"/></a:solidFill>"
            f'<a:latin typeface="{escape(typeface, {chr(34): "&quot;"})}"/>{ea}</a:rPr>'
            f'<a:t>{escape(text)}</a:t></a:r>'
        )

    def para(
        self,
        runs: str,
        *,
        align: str = "l",
        bullet_color: str | None = None,  # set → bulleted paragraph
        level: int = 0,
        line_pct: int = 108,
        before_pts: int = 0,
    ) -> str:
        mar = ""
        bullet = "<a:buNone/>"
        if bullet_color is not None:
            indent = 285750 + level * 285750  # 0.3125" per level
            mar = f' marL="{indent}" indent="-285750"'
            bullet = (
                f'<a:buClr><a:srgbClr val="{bullet_color}"/></a:buClr>'
                '<a:buFont typeface="Arial"/>'
                f'<a:buChar char="{"•" if level == 0 else "–"}"/>'
            )
        spc = f'<a:spcBef><a:spcPts val="{before_pts * 100}"/></a:spcBef>' if before_pts else ""
        return (
            f'<a:p><a:pPr{mar} algn="{align}">'
            f'<a:lnSpc><a:spcPct val="{line_pct * 1000}"/></a:lnSpc>{spc}{bullet}</a:pPr>'
            f"{runs}</a:p>"
        )

    def textbox(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        paras: list[str],
        *,
        anchor: str = "t",
        autofit: bool = True,
    ) -> None:
        sid = self._next_id()
        fit = "<a:normAutofit/>" if autofit else ""
        self.xml.append(
            f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="Text {sid}"/>'
            '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr/></p:nvSpPr>'
            f'<p:spPr><a:xfrm><a:off x="{_in(x)}" y="{_in(y)}"/><a:ext cx="{_in(w)}" cy="{_in(h)}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>'
            f'<p:txBody><a:bodyPr wrap="square" lIns="0" tIns="0" rIns="0" bIns="0" anchor="{anchor}">{fit}</a:bodyPr>'
            f'<a:lstStyle/>{"".join(paras)}</p:txBody></p:sp>'
        )

    # geometry ----------------------------------------------------------------
    def shape(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: str,
        *,
        prst: str = "rect",
        alpha: int | None = None,  # percent 0-100
        radius_pct: int = 12,  # roundRect corner adjust (val/1000 %)
    ) -> None:
        sid = self._next_id()
        alpha_xml = f'<a:alpha val="{alpha * 1000}"/>' if alpha is not None else ""
        av = (
            f'<a:avLst><a:gd name="adj" fmla="val {radius_pct * 1000}"/></a:avLst>'
            if prst == "roundRect"
            else "<a:avLst/>"
        )
        self.xml.append(
            f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="Shape {sid}"/>'
            "<p:cNvSpPr/><p:nvPr/></p:nvSpPr>"
            f'<p:spPr><a:xfrm><a:off x="{_in(x)}" y="{_in(y)}"/><a:ext cx="{_in(w)}" cy="{_in(h)}"/></a:xfrm>'
            f'<a:prstGeom prst="{prst}">{av}</a:prstGeom>'
            f'<a:solidFill><a:srgbClr val="{fill}">{alpha_xml}</a:srgbClr></a:solidFill>'
            "<a:ln><a:noFill/></a:ln></p:spPr>"
            '<p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
        )

    def table(
        self,
        x: float,
        y: float,
        w: float,
        headers: list[str],
        rows: list[list[str]],
        row_h: float,
    ) -> None:
        t = self.theme
        sid = self._next_id()
        ncols = len(headers)
        col_w = _in(w) // ncols
        grid = "".join(f'<a:gridCol w="{col_w}"/>' for _ in range(ncols))

        def tc(text: str, *, header: bool, band: bool) -> str:
            color = "FFFFFF" if header else t.INK
            fill = t.primary if header else (t.CARD if band else "FFFFFF")
            r = self.run(text, sz=13 if header else 12, color=color, bold=header)
            return (
                f"<a:tc><a:txBody><a:bodyPr/><a:lstStyle/>{self.para(r)}</a:txBody>"
                f'<a:tcPr marL="109728" marR="109728" marT="54864" marB="54864" anchor="ctr">'
                f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill></a:tcPr></a:tc>'
            )

        tr_xml = [
            f'<a:tr h="{_in(row_h)}">' + "".join(tc(h, header=True, band=False) for h in headers) + "</a:tr>"
        ]
        for i, row in enumerate(rows):
            cells = list(row) + [""] * (ncols - len(row))
            tr_xml.append(
                f'<a:tr h="{_in(row_h)}">'
                + "".join(tc(str(c), header=False, band=i % 2 == 0) for c in cells[:ncols])
                + "</a:tr>"
            )
        self.xml.append(
            f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="{sid}" name="Table {sid}"/>'
            "<p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>"
            f'<p:xfrm><a:off x="{_in(x)}" y="{_in(y)}"/><a:ext cx="{_in(w)}" cy="{_in(row_h) * (len(rows) + 1)}"/></p:xfrm>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
            f'<a:tbl><a:tblPr firstRow="1" bandRow="1"/><a:tblGrid>{grid}</a:tblGrid>{"".join(tr_xml)}</a:tbl>'
            "</a:graphicData></a:graphic></p:graphicFrame>"
        )


# ── spec helpers ─────────────────────────────────────────────────────────────

def _text(raw: object, where: str, *, required: bool = False, max_len: int = _MAX_TEXT) -> str:
    if raw is None:
        if required:
            raise DeckSpecError(f"{where} is required")
        return ""
    s = str(raw).strip()
    if required and not s:
        raise DeckSpecError(f"{where} is required")
    if len(s) > max_len:
        raise DeckSpecError(f"{where} exceeds {max_len} chars")
    return s


def _str_list(raw: object, where: str, *, max_items: int) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise DeckSpecError(f"{where} must be an array")
    if len(raw) > max_items:
        raise DeckSpecError(f"{where}: too many items ({len(raw)} > {max_items})")
    return [_text(item, f"{where}[{i}]") for i, item in enumerate(raw)]


# ── layout renderers ─────────────────────────────────────────────────────────
# Content zone on light slides: x 0.85", title block ~0.62–1.7", body below.

_MARGIN = 0.85
_BODY_W = 13.333 - 2 * _MARGIN


def _motif(sp: _Shapes, *, on_dark: bool) -> None:
    """Repeated visual motif: one oversized quarter-circle, bottom-right."""
    color = "FFFFFF" if on_dark else sp.theme.secondary
    sp.shape(11.15, 5.3, 4.4, 4.4, color, prst="ellipse", alpha=10 if on_dark else 28)


def _kicker_title(sp: _Shapes, kicker: str, title: str, *, color: str | None = None) -> None:
    t = sp.theme
    if kicker:
        sp.textbox(
            _MARGIN, 0.52, _BODY_W, 0.32,
            [sp.para(sp.run(kicker.upper(), sz=12, color=t.accent, bold=True, spacing=160))],
        )
    if title:
        sp.textbox(
            _MARGIN, 0.86, _BODY_W, 0.85,
            [sp.para(sp.run(title, sz=27, color=color or t.primary, bold=True, font=t.heading_font))],
            anchor="t",
        )


def _render_title(sp: _Shapes, slide: dict, _idx: int, _total: int) -> None:
    t = sp.theme
    sp.shape(0, 0, 13.333, 7.5, t.primary)
    _motif(sp, on_dark=True)
    sp.shape(_MARGIN, 2.02, 0.62, 0.09, t.accent)  # small accent block above the title
    kicker = _text(slide.get("kicker"), "kicker")
    if kicker:
        sp.textbox(_MARGIN, 1.5, _BODY_W, 0.4,
                   [sp.para(sp.run(kicker.upper(), sz=13, color=t.secondary, bold=True, spacing=200))])
    sp.textbox(
        _MARGIN, 2.35, 11.0, 2.1,
        [sp.para(sp.run(_text(slide.get("title"), "title slide title", required=True),
                        sz=40, color="FFFFFF", bold=True, font=t.heading_font), line_pct=104)],
    )
    subtitle = _text(slide.get("subtitle"), "subtitle")
    if subtitle:
        sp.textbox(_MARGIN, 4.55, 9.8, 1.1,
                   [sp.para(sp.run(subtitle, sz=16, color=t.secondary), line_pct=118)])
    footer = _text(slide.get("footer"), "footer")
    if footer:
        sp.textbox(_MARGIN, 6.72, _BODY_W, 0.4,
                   [sp.para(sp.run(footer, sz=11, color="FFFFFF"))])


def _render_section(sp: _Shapes, slide: dict, idx: int, _total: int) -> None:
    t = sp.theme
    sp.shape(0, 0, 13.333, 7.5, t.primary)
    _motif(sp, on_dark=True)
    number = _text(slide.get("number"), "number") or f"{idx:02d}"
    sp.textbox(_MARGIN, 1.15, 5.0, 2.2,
               [sp.para(sp.run(number, sz=88, color="FFFFFF", bold=True, font=t.heading_font))])
    # the accent block sits between the index and the title — a scale cue, not an underline
    sp.shape(_MARGIN + 0.04, 3.62, 0.62, 0.09, t.accent)
    sp.textbox(_MARGIN, 3.95, 11.2, 1.3,
               [sp.para(sp.run(_text(slide.get("title"), "section title", required=True),
                               sz=30, color="FFFFFF", bold=True, font=t.heading_font))])
    subtitle = _text(slide.get("subtitle"), "subtitle")
    if subtitle:
        sp.textbox(_MARGIN, 5.15, 9.6, 1.2,
                   [sp.para(sp.run(subtitle, sz=14, color=t.secondary), line_pct=120)])


def _bullet_paras(sp: _Shapes, items: list, where: str, *, sz: int = 15) -> list[str]:
    t = sp.theme
    paras: list[str] = []
    if len(items) > 10:
        raise DeckSpecError(f"{where}: keep bullets ≤ 10 per slide (got {len(items)})")
    for i, item in enumerate(items):
        if isinstance(item, dict):
            text = _text(item.get("text"), f"{where}[{i}].text", required=True)
            subs = _str_list(item.get("sub"), f"{where}[{i}].sub", max_items=6)
        else:
            text, subs = _text(item, f"{where}[{i}]", required=True), []
        paras.append(sp.para(sp.run(text, sz=sz, color=t.INK), bullet_color=t.accent,
                             line_pct=112, before_pts=9 if i else 0))
        paras.extend(
            sp.para(sp.run(s, sz=sz - 2, color=t.MUTED), bullet_color=t.MUTED, level=1,
                    line_pct=112, before_pts=4)
            for s in subs
        )
    return paras


def _render_bullets(sp: _Shapes, slide: dict, _idx: int, _total: int) -> None:
    _motif(sp, on_dark=False)
    _kicker_title(sp, _text(slide.get("kicker"), "kicker"),
                  _text(slide.get("title"), "bullets slide title", required=True))
    items = slide.get("bullets")
    if not isinstance(items, list) or not items:
        raise DeckSpecError("bullets layout needs a non-empty `bullets` array")
    sp.textbox(_MARGIN, 1.95, _BODY_W - 0.6, 4.9, _bullet_paras(sp, items, "bullets"))


def _render_two_column(sp: _Shapes, slide: dict, _idx: int, _total: int) -> None:
    t = sp.theme
    _kicker_title(sp, _text(slide.get("kicker"), "kicker"),
                  _text(slide.get("title"), "two_column slide title", required=True))
    columns = slide.get("columns")
    if not isinstance(columns, list) or not 2 <= len(columns) <= 3:
        raise DeckSpecError("two_column layout needs a `columns` array of 2–3 {heading, bullets} objects")
    gap = 0.45
    col_w = (_BODY_W - gap * (len(columns) - 1)) / len(columns)
    for i, col in enumerate(columns):
        if not isinstance(col, dict):
            raise DeckSpecError(f"columns[{i}] must be an object")
        x = _MARGIN + i * (col_w + gap)
        sp.shape(x, 2.0, col_w, 4.55, t.CARD, prst="roundRect", radius_pct=7)
        sp.shape(x + 0.35, 2.42, 0.5, 0.075, t.accent)
        heading = _text(col.get("heading"), f"columns[{i}].heading", required=True)
        sp.textbox(x + 0.35, 2.62, col_w - 0.7, 0.62,
                   [sp.para(sp.run(heading, sz=17, color=t.primary, bold=True, font=t.heading_font))])
        items = col.get("bullets") or []
        if not isinstance(items, list):
            raise DeckSpecError(f"columns[{i}].bullets must be an array")
        sp.textbox(x + 0.35, 3.32, col_w - 0.7, 3.05,
                   _bullet_paras(sp, items[:6], f"columns[{i}].bullets", sz=13))


def _render_stats(sp: _Shapes, slide: dict, _idx: int, _total: int) -> None:
    t = sp.theme
    _kicker_title(sp, _text(slide.get("kicker"), "kicker"),
                  _text(slide.get("title"), "stats slide title", required=True))
    stats = slide.get("stats")
    if not isinstance(stats, list) or not 1 <= len(stats) <= 4:
        raise DeckSpecError("stats layout needs a `stats` array of 1–4 {value, label, note?} objects")
    gap = 0.4
    card_w = (_BODY_W - gap * (len(stats) - 1)) / len(stats)
    for i, stat in enumerate(stats):
        if not isinstance(stat, dict):
            raise DeckSpecError(f"stats[{i}] must be an object")
        x = _MARGIN + i * (card_w + gap)
        primary_card = i == 0
        sp.shape(x, 2.25, card_w, 3.4, t.primary if primary_card else t.CARD,
                 prst="roundRect", radius_pct=8)
        value = _text(stat.get("value"), f"stats[{i}].value", required=True, max_len=40)
        label = _text(stat.get("label"), f"stats[{i}].label", required=True, max_len=120)
        note = _text(stat.get("note"), f"stats[{i}].note", max_len=160)
        value_color = "FFFFFF" if primary_card else t.primary
        label_color = t.secondary if primary_card else t.INK
        note_color = t.secondary if primary_card else t.MUTED
        sp.textbox(x + 0.35, 2.78, card_w - 0.7, 1.35,
                   [sp.para(sp.run(value, sz=38, color=value_color, bold=True, font=t.heading_font))],
                   anchor="b")
        sp.textbox(x + 0.35, 4.24, card_w - 0.7, 0.62,
                   [sp.para(sp.run(label, sz=13, color=label_color, bold=True))])
        if note:
            sp.textbox(x + 0.35, 4.86, card_w - 0.7, 0.62,
                       [sp.para(sp.run(note, sz=10, color=note_color), line_pct=112)])
    footnote = _text(slide.get("footnote"), "footnote")
    if footnote:
        sp.textbox(_MARGIN, 6.85, _BODY_W, 0.35,
                   [sp.para(sp.run(footnote, sz=9, color=t.MUTED, italic=True))])


def _render_table(sp: _Shapes, slide: dict, _idx: int, _total: int) -> None:
    t = sp.theme
    _kicker_title(sp, _text(slide.get("kicker"), "kicker"),
                  _text(slide.get("title"), "table slide title", required=True))
    headers = _str_list(slide.get("headers"), "headers", max_items=6)
    if not headers:
        raise DeckSpecError("table layout needs a non-empty `headers` array")
    raw_rows = slide.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise DeckSpecError("table layout needs a non-empty `rows` array of row arrays")
    if len(raw_rows) > 9:
        raise DeckSpecError(f"table rows must fit on one slide (≤ 9, got {len(raw_rows)})")
    rows = [_str_list(r, f"rows[{i}]", max_items=len(headers)) for i, r in enumerate(raw_rows)]
    row_h = min(0.52, 4.6 / (len(rows) + 1))
    sp.table(_MARGIN, 2.05, _BODY_W, headers, rows, row_h)
    footnote = _text(slide.get("footnote"), "footnote")
    if footnote:
        sp.textbox(_MARGIN, 6.85, _BODY_W, 0.35,
                   [sp.para(sp.run(footnote, sz=9, color=t.MUTED, italic=True))])


def _render_timeline(sp: _Shapes, slide: dict, _idx: int, _total: int) -> None:
    t = sp.theme
    _kicker_title(sp, _text(slide.get("kicker"), "kicker"),
                  _text(slide.get("title"), "timeline slide title", required=True))
    steps = slide.get("steps")
    if not isinstance(steps, list) or not 2 <= len(steps) <= 6:
        raise DeckSpecError("timeline layout needs a `steps` array of 2–6 {title, desc?} objects")
    n = len(steps)
    col_w = _BODY_W / n
    rail_y = 3.06
    sp.shape(_MARGIN + col_w * 0.5, rail_y + 0.21, col_w * (n - 1), 0.035, t.secondary)
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise DeckSpecError(f"steps[{i}] must be an object")
        cx = _MARGIN + col_w * i + col_w * 0.5
        sp.shape(cx - 0.23, rail_y, 0.46, 0.46, t.primary if i == 0 else t.accent, prst="ellipse")
        sp.textbox(cx - 0.23, rail_y + 0.052, 0.46, 0.36,
                   [sp.para(sp.run(str(i + 1), sz=15, color="FFFFFF", bold=True), align="ctr")],
                   anchor="ctr", autofit=False)
        title = _text(step.get("title"), f"steps[{i}].title", required=True, max_len=90)
        desc = _text(step.get("desc"), f"steps[{i}].desc", max_len=240)
        sp.textbox(_MARGIN + col_w * i + 0.12, 3.85, col_w - 0.24, 0.75,
                   [sp.para(sp.run(title, sz=14, color=t.primary, bold=True), align="ctr", line_pct=106)])
        if desc:
            sp.textbox(_MARGIN + col_w * i + 0.12, 4.6, col_w - 0.24, 1.7,
                       [sp.para(sp.run(desc, sz=11, color=t.MUTED), align="ctr", line_pct=114)])


def _render_quote(sp: _Shapes, slide: dict, _idx: int, _total: int) -> None:
    t = sp.theme
    _motif(sp, on_dark=False)
    sp.textbox(_MARGIN, 0.9, 2.2, 1.9,
               [sp.para(sp.run("“", sz=120, color=t.accent, bold=True, font=t.heading_font))],
               autofit=False)
    sp.textbox(1.7, 2.55, 9.9, 2.6,
               [sp.para(sp.run(_text(slide.get("quote"), "quote text", required=True),
                               sz=22, color=t.INK, font=t.heading_font), line_pct=124)])
    attribution = _text(slide.get("attribution"), "attribution")
    if attribution:
        sp.textbox(1.7, 5.5, 9.9, 0.5,
                   [sp.para(sp.run("— " + attribution, sz=13, color=t.MUTED, bold=True))])


def _render_closing(sp: _Shapes, slide: dict, _idx: int, _total: int) -> None:
    t = sp.theme
    sp.shape(0, 0, 13.333, 7.5, t.primary)
    _motif(sp, on_dark=True)
    sp.shape(_MARGIN, 2.42, 0.62, 0.09, t.accent)
    sp.textbox(_MARGIN, 2.75, 11.4, 1.6,
               [sp.para(sp.run(_text(slide.get("title"), "closing title", required=True),
                               sz=36, color="FFFFFF", bold=True, font=t.heading_font))])
    subtitle = _text(slide.get("subtitle"), "subtitle")
    if subtitle:
        sp.textbox(_MARGIN, 4.35, 9.8, 1.0,
                   [sp.para(sp.run(subtitle, sz=15, color=t.secondary), line_pct=118)])
    contact = _text(slide.get("contact"), "contact")
    if contact:
        sp.textbox(_MARGIN, 6.6, _BODY_W, 0.4,
                   [sp.para(sp.run(contact, sz=12, color="FFFFFF"))])


_RENDERERS = {
    "title": _render_title,
    "section": _render_section,
    "bullets": _render_bullets,
    "two_column": _render_two_column,
    "stats": _render_stats,
    "table": _render_table,
    "timeline": _render_timeline,
    "quote": _render_quote,
    "closing": _render_closing,
}

_DARK_LAYOUTS = {"title", "section", "closing"}


# ── package parts ────────────────────────────────────────────────────────────

_XMLDECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_NS = (
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
)
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_EMPTY_SPTREE_HEADER = (
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
    '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
)


def _rels(entries: list[tuple[str, str, str]]) -> str:
    """`kind` is an officeDocument relationship short name, or a full type URI
    (needed for core-properties, which lives in the package namespace)."""
    body = "".join(
        f'<Relationship Id="{rid}" Type="{kind if kind.startswith("http") else f"{_RT}/{kind}"}" Target="{target}"/>'
        for rid, kind, target in entries
    )
    return _XMLDECL + f'<Relationships xmlns="{_REL_NS}">{body}</Relationships>'


def _slide_xml(shapes: _Shapes, *, dark_bg: bool, theme: Theme) -> str:
    bg = theme.primary if dark_bg else Theme.BG
    return (
        _XMLDECL
        + f"<p:sld {_NS}><p:cSld>"
        + f'<p:bg><p:bgPr><a:solidFill><a:srgbClr val="{bg}"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>'
        + f"<p:spTree>{_EMPTY_SPTREE_HEADER}{''.join(shapes.xml)}</p:spTree></p:cSld>"
        + "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
    )


def _notes_slide_xml(notes: str) -> str:
    text = "".join(
        f'<a:p><a:r><a:rPr lang="en-US" sz="1200"/><a:t>{escape(line)}</a:t></a:r></a:p>'
        for line in notes.splitlines() or [""]
    )
    return (
        _XMLDECL
        + f"<p:notes {_NS}><p:cSld><p:spTree>{_EMPTY_SPTREE_HEADER}"
        + '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Notes"/>'
        + '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
        + '<p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr>'
        + "<p:spPr/>"
        + f"<p:txBody><a:bodyPr/><a:lstStyle/>{text}</p:txBody></p:sp>"
        + "</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>"
    )


def _theme_xml(theme: Theme, name: str) -> str:
    def font_block(tag: str, typeface: str) -> str:
        ea = theme.ea_font or ""
        return (
            f"<a:{tag}><a:latin typeface=\"{escape(typeface, {chr(34): '&quot;'})}\"/>"
            f"<a:ea typeface=\"{escape(ea, {chr(34): '&quot;'})}\"/><a:cs typeface=\"\"/></a:{tag}>"
        )

    fills = (
        '<a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
    )
    lines = (
        '<a:lnStyleLst>'
        + '<a:ln w="9525" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>' * 3
        + "</a:lnStyleLst>"
    )
    effects = "<a:effectStyleLst>" + "<a:effectStyle><a:effectLst/></a:effectStyle>" * 3 + "</a:effectStyleLst>"
    bgfills = (
        "<a:bgFillStyleLst>"
        + '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' * 3
        + "</a:bgFillStyleLst>"
    )
    return (
        _XMLDECL
        + f'<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="{name}">'
        + "<a:themeElements>"
        + f'<a:clrScheme name="{name}">'
        + f'<a:dk1><a:srgbClr val="{Theme.INK}"/></a:dk1>'
        + '<a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
        + f'<a:dk2><a:srgbClr val="{theme.primary}"/></a:dk2>'
        + f'<a:lt2><a:srgbClr val="{theme.secondary}"/></a:lt2>'
        + f'<a:accent1><a:srgbClr val="{theme.accent}"/></a:accent1>'
        + f'<a:accent2><a:srgbClr val="{theme.primary}"/></a:accent2>'
        + f'<a:accent3><a:srgbClr val="{theme.secondary}"/></a:accent3>'
        + f'<a:accent4><a:srgbClr val="{theme.accent}"/></a:accent4>'
        + f'<a:accent5><a:srgbClr val="{theme.primary}"/></a:accent5>'
        + f'<a:accent6><a:srgbClr val="{theme.secondary}"/></a:accent6>'
        + '<a:hlink><a:srgbClr val="0563C1"/></a:hlink>'
        + '<a:folHlink><a:srgbClr val="954F72"/></a:folHlink>'
        + "</a:clrScheme>"
        + f'<a:fontScheme name="{name}">'
        + font_block("majorFont", theme.heading_font)
        + font_block("minorFont", theme.body_font)
        + "</a:fontScheme>"
        + f'<a:fmtScheme name="Office">{fills}{lines}{effects}{bgfills}</a:fmtScheme>'
        + "</a:themeElements></a:theme>"
    )


def _master_xml(tag: str, layout_rel: bool) -> str:
    """Shared skeleton for slideMaster1 / notesMaster1 (both are near-empty —
    slides own their full design; masters exist because the format requires them)."""
    open_tag = "p:sldMaster" if tag == "slide" else "p:notesMaster"
    layout_lst = (
        '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
        if layout_rel
        else ""
    )
    tx_styles = "<p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles>" if tag == "slide" else ""
    notes_style = "<p:notesStyle/>" if tag == "notes" else ""
    return (
        _XMLDECL
        + f"<{open_tag} {_NS}><p:cSld>"
        + '<p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>'
        + f"<p:spTree>{_EMPTY_SPTREE_HEADER}</p:spTree></p:cSld>"
        + '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
        + 'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
        + layout_lst
        + tx_styles
        + notes_style
        + f"</{open_tag}>"
    )


def _layout_xml() -> str:
    return (
        _XMLDECL
        + f'<p:sldLayout {_NS} type="blank" preserve="1"><p:cSld name="Blank">'
        + f"<p:spTree>{_EMPTY_SPTREE_HEADER}</p:spTree></p:cSld>"
        + "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>"
    )


def _presentation_xml(n_slides: int, has_notes: bool) -> str:
    slide_ids = "".join(
        f'<p:sldId id="{256 + i}" r:id="rId{2 + i}"/>' for i in range(n_slides)
    )
    notes_master = (
        f'<p:notesMasterIdLst><p:notesMasterId r:id="rId{2 + n_slides}"/></p:notesMasterIdLst>'
        if has_notes
        else ""
    )
    return (
        _XMLDECL
        + f"<p:presentation {_NS}>"
        + '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        + notes_master
        + f"<p:sldIdLst>{slide_ids}</p:sldIdLst>"
        + f'<p:sldSz cx="{SLIDE_W}" cy="{SLIDE_H}"/><p:notesSz cx="6858000" cy="9144000"/>'
        + "</p:presentation>"
    )


def _content_types(n_slides: int, notes_indices: list[int]) -> str:
    slides = "".join(
        f'<Override PartName="/ppt/slides/slide{i + 1}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        for i in range(n_slides)
    )
    notes = "".join(
        f'<Override PartName="/ppt/notesSlides/notesSlide{n}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>'
        for n in range(1, len(notes_indices) + 1)
    )
    notes_master = (
        '<Override PartName="/ppt/notesMasters/notesMaster1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml"/>'
        '<Override PartName="/ppt/theme/theme2.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
        if notes_indices
        else ""
    )
    return (
        _XMLDECL
        + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        + '<Default Extension="xml" ContentType="application/xml"/>'
        + '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
        + '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
        + '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
        + '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
        + slides
        + notes_master
        + notes
        + '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        + '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        + "</Types>"
    )


# ── entry point ──────────────────────────────────────────────────────────────

def build_presentation(spec: dict) -> tuple[bytes, dict]:
    """Deck spec dict → (.pptx bytes, summary). Raises DeckSpecError on bad input."""
    theme = Theme.from_spec(spec.get("theme"))
    raw_slides = spec.get("slides")
    if not isinstance(raw_slides, list) or not raw_slides:
        raise DeckSpecError("spec.slides must be a non-empty array of slide objects")
    if len(raw_slides) > _MAX_SLIDES:
        raise DeckSpecError(f"too many slides ({len(raw_slides)} > {_MAX_SLIDES})")

    slides_xml: list[str] = []
    slide_meta: list[dict] = []
    notes: dict[int, str] = {}  # slide index (0-based) → notes text
    section_counter = 0
    for i, raw in enumerate(raw_slides):
        if not isinstance(raw, dict):
            raise DeckSpecError(f"slides[{i}] must be an object")
        layout = str(raw.get("layout") or "").strip()
        renderer = _RENDERERS.get(layout)
        if renderer is None:
            raise DeckSpecError(
                f"slides[{i}]: unknown layout {layout!r} — use one of {sorted(_RENDERERS)}"
            )
        if layout == "section":
            section_counter += 1
        shapes = _Shapes(theme)
        try:
            renderer(shapes, raw, section_counter if layout == "section" else i + 1, len(raw_slides))
        except DeckSpecError as exc:
            raise DeckSpecError(f"slides[{i}] ({layout}): {exc}") from None
        slides_xml.append(_slide_xml(shapes, dark_bg=layout in _DARK_LAYOUTS, theme=theme))
        note_text = _text(raw.get("notes"), f"slides[{i}].notes", max_len=4_000)
        if note_text:
            notes[i] = note_text
        slide_meta.append({"layout": layout, "title": _text(raw.get("title") or raw.get("quote"), "")})

    notes_order = sorted(notes)  # slide idx → notesSlide number = position+1
    notes_number = {slide_i: n + 1 for n, slide_i in enumerate(notes_order)}

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types(len(slides_xml), notes_order))
        zf.writestr("_rels/.rels", _rels([
            ("rId1", "officeDocument", "ppt/presentation.xml"),
            ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties", "docProps/core.xml"),
            ("rId3", "extended-properties", "docProps/app.xml"),
        ]))
        title = str(spec.get("title") or "Presentation")
        zf.writestr(
            "docProps/core.xml",
            _XMLDECL
            + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            + 'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            + f"<dc:title>{escape(title[:200])}</dc:title><dc:creator>RICE</dc:creator></cp:coreProperties>",
        )
        zf.writestr(
            "docProps/app.xml",
            _XMLDECL
            + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
            + "<Application>RICE office tools</Application></Properties>",
        )

        # presentation + its rels
        pres_rels = [("rId1", "slideMaster", "slideMasters/slideMaster1.xml")]
        pres_rels += [
            (f"rId{2 + i}", "slide", f"slides/slide{i + 1}.xml") for i in range(len(slides_xml))
        ]
        if notes_order:
            pres_rels.append((f"rId{2 + len(slides_xml)}", "notesMaster", "notesMasters/notesMaster1.xml"))
        zf.writestr("ppt/presentation.xml", _presentation_xml(len(slides_xml), bool(notes_order)))
        zf.writestr("ppt/_rels/presentation.xml.rels", _rels(pres_rels))

        # master / layout / theme
        zf.writestr("ppt/slideMasters/slideMaster1.xml", _master_xml("slide", layout_rel=True))
        zf.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", _rels([
            ("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"),
            ("rId2", "theme", "../theme/theme1.xml"),
        ]))
        zf.writestr("ppt/slideLayouts/slideLayout1.xml", _layout_xml())
        zf.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", _rels([
            ("rId1", "slideMaster", "../slideMasters/slideMaster1.xml"),
        ]))
        zf.writestr("ppt/theme/theme1.xml", _theme_xml(theme, "RICE"))

        # slides + their rels
        for i, xml in enumerate(slides_xml):
            zf.writestr(f"ppt/slides/slide{i + 1}.xml", xml)
            rels = [("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml")]
            if i in notes_number:
                rels.append(("rId2", "notesSlide", f"../notesSlides/notesSlide{notes_number[i]}.xml"))
            zf.writestr(f"ppt/slides/_rels/slide{i + 1}.xml.rels", _rels(rels))

        # notes
        if notes_order:
            zf.writestr("ppt/notesMasters/notesMaster1.xml", _master_xml("notes", layout_rel=False))
            zf.writestr("ppt/notesMasters/_rels/notesMaster1.xml.rels", _rels([
                ("rId1", "theme", "../theme/theme2.xml"),
            ]))
            zf.writestr("ppt/theme/theme2.xml", _theme_xml(theme, "RICE Notes"))
            for slide_i in notes_order:
                n = notes_number[slide_i]
                zf.writestr(f"ppt/notesSlides/notesSlide{n}.xml", _notes_slide_xml(notes[slide_i]))
                zf.writestr(f"ppt/notesSlides/_rels/notesSlide{n}.xml.rels", _rels([
                    ("rId1", "slide", f"../slides/slide{slide_i + 1}.xml"),
                    ("rId2", "notesMaster", "../notesMasters/notesMaster1.xml"),
                ]))

    summary = {
        "slides": [
            {"n": i + 1, **meta, **({"has_notes": True} if i in notes else {})}
            for i, meta in enumerate(slide_meta)
        ],
        "slide_count": len(slide_meta),
        "theme": {"primary": theme.primary, "secondary": theme.secondary, "accent": theme.accent},
    }
    return buf.getvalue(), summary
