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
  image       title + workspace image (png/jpeg), aspect-fit; bullets를 주면
              좌 텍스트/우 이미지 분할, 없으면 전폭 + caption
  closing     dark thank-you/CTA slide {title, subtitle?, contact?}
Any slide may set `notes` (speaker notes, emitted as real notesSlides).

2026-07-10 quality pass:
  - 텍스트 오버플로 방어: 제목/불릿/인용 등은 추정 줄수 기반의 **결정론적 폰트
    다운스케일**(_fit_sz/_fit_bullets_sz)을 탄다 — 긴 문장이 상자를 넘쳐 다른
    요소를 덮는 "처참한 슬라이드"의 1원인 제거. CJK는 전각폭으로 가중한다.
  - 차트: 수평 그리드라인 + 좌측 눈금 값, 라인 차트 포인트 값 라벨.
  - 딥 스펙 임베드: 원본 스펙 JSON을 docProps/riceSpec.json으로 패키지에 심는다
    — edit_presentation(office/__init__.py)이 이를 읽어 슬라이드 단위
    replace/insert/remove/move 후 재빌드한다(파워포인트가 재저장하면 임베드가
    사라질 수 있고, 그때는 편집 도구가 명확히 거절한다).
"""

from __future__ import annotations

import json
import math
import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import Callable
from xml.sax.saxutils import escape

from .palette import PRESETS as _PRESETS
from .palette import resolve_palette

__all__ = ["DeckSpecError", "build_presentation", "read_embedded_spec", "SPEC_PART"]

_MAX_SLIDES = 30
_MAX_TEXT = 2_000
_MAX_IMAGE_BYTES = 8 * 1024 * 1024

# 임베디드 스펙 파트 경로 — office/__init__.py(edit_presentation)와 공유.
SPEC_PART = "docProps/riceSpec.json"

EMU_PER_IN = 914_400
SLIDE_W = int(13.333 * EMU_PER_IN)
SLIDE_H = int(7.5 * EMU_PER_IN)


def _in(v: float) -> int:
    return int(v * EMU_PER_IN)


# ── deterministic text fitting ────────────────────────────────────────────────
# 정확한 글리프 메트릭 없이(스탠드얼론 writer) 추정으로 줄수를 계산한다:
# 라틴 평균 ~0.52em, CJK 전각 ~1.0em. 목적은 타이포그래피 완벽이 아니라
# "상자를 넘치지 않는다"는 하한 보장 — 넘칠 바에야 한두 포인트 줄이는 게 낫다.


def _em_len(text: str) -> float:
    return sum(1.0 if ord(ch) > 0x2E80 else 0.52 for ch in text)


def _lines_needed(text: str, sz: int, w_in: float) -> int:
    if not text:
        return 0
    width_in = (sz / 72.0) * _em_len(text)
    return max(1, math.ceil(width_in / max(w_in, 0.1)))


def _fit_sz(text: str, base_sz: int, w_in: float, max_lines: int, min_sz: int) -> int:
    """`text`가 폭 `w_in`에서 `max_lines` 안에 들어가는 가장 큰 크기(포인트)."""
    for sz in range(base_sz, min_sz, -1):
        if _lines_needed(text, sz, w_in) <= max_lines:
            return sz
    return min_sz


def _fit_sz_height(text: str, base_sz: int, w_in: float, h_in: float, min_sz: int, line_pct: float = 1.08) -> int:
    """상자 높이 `h_in`이 제약일 때의 크기 결정 — 줄수 대신 총 높이로 판정한다."""
    for sz in range(base_sz, min_sz, -1):
        if _lines_needed(text, sz, w_in) * (sz / 72.0) * line_pct <= h_in:
            return sz
    return min_sz


def _fit_bullets_sz(
    entries: list[tuple[str, bool]], w_in: float, h_in: float, base_sz: int, min_sz: int
) -> int:
    """불릿 블록 전체가 높이 `h_in`에 들어가는 크기. entries = [(text, is_sub)];
    sub 항목은 본문보다 2pt 작게 렌더된다(호출부와 같은 규칙)."""
    for sz in range(base_sz, min_sz, -1):
        total_in = 0.0
        for text, is_sub in entries:
            item_sz = max(sz - 2, 8) if is_sub else sz
            lines = _lines_needed(text, item_sz, w_in - 0.35)  # 불릿 들여쓰기 몫
            total_in += lines * (item_sz / 72.0) * 1.18 + (9 if not is_sub else 4) / 72.0
        if total_in <= h_in:
            return sz
    return min_sz


# ── image sniffing (png/jpeg, stdlib) ────────────────────────────────────────


def _image_meta(data: bytes) -> tuple[str, int, int]:
    """(ext, width_px, height_px). PNG/JPEG만 지원 — 그 외는 DeckSpecError."""
    if len(data) > _MAX_IMAGE_BYTES:
        raise DeckSpecError(f"image too large ({len(data) // 1024} KB > {_MAX_IMAGE_BYTES // 1024} KB)")
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return "png", int.from_bytes(data[16:20]), int.from_bytes(data[20:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg_len = int.from_bytes(data[i + 2 : i + 4])
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return "jpeg", int.from_bytes(data[i + 7 : i + 9]), int.from_bytes(data[i + 5 : i + 7])
            i += 2 + seg_len
    raise DeckSpecError("unsupported image format — use PNG or JPEG")


class DeckSpecError(ValueError):
    """Invalid deck spec — message is surfaced to the model verbatim."""


# ── theme ────────────────────────────────────────────────────────────────────
# Palettes + preset resolution live in palette.py (shared with the .docx writer).
# Resolution is lenient: a bad color/preset degrades to the default + a warning
# rather than aborting the whole deck (small-model robustness — see palette.py).


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
    def from_spec(cls, raw: object, warnings: list[str] | None = None) -> "Theme":
        # `theme` accepts: "forest" (preset) · {"preset": "forest", ...} (preset +
        # overrides) · {"primary": "..", ...} (custom) · None (default). Never
        # raises — resolve_palette degrades bad input and notes it in `warnings`.
        colors = resolve_palette(raw, warnings)
        fonts = raw if isinstance(raw, dict) else {}
        return cls(
            primary=colors["primary"],
            secondary=colors["secondary"],
            accent=colors["accent"],
            heading_font=str(fonts.get("heading_font") or "Georgia"),
            body_font=str(fonts.get("body_font") or "Calibri"),
            ea_font=(str(fonts["ea_font"]) if fonts.get("ea_font") else None),
        )


# ── drawing primitives ───────────────────────────────────────────────────────

class _Shapes:
    """Builds the slide's <p:sp> list with unique shape ids."""

    def __init__(self, theme: Theme, load_asset: "Callable[[str], bytes] | None" = None) -> None:
        self.theme = theme
        self._id = 1
        self.xml: list[str] = []
        # image 레이아웃용: 워크스페이스 자산 로더(office/__init__ 주입)와 이 슬라이드가
        # 임베드한 이미지들 [(rel_id, ext, bytes)] — 패키징 시 media 파트/rels가 된다.
        self.load_asset = load_asset
        self.images: list[tuple[int, str, bytes]] = []

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def add_image(self, data: bytes, ext: str) -> int:
        """이미지를 이 슬라이드에 등록하고 relationship id를 돌려준다. rel id는
        rId100부터 — 레이아웃(rId1)/노트(rId2)와 절대 충돌하지 않는 대역."""
        rel_id = 100 + len(self.images)
        self.images.append((rel_id, ext, data))
        return rel_id

    def picture(self, x: float, y: float, w: float, h: float, rel_id: int) -> None:
        sid = self._next_id()
        self.xml.append(
            f'<p:pic><p:nvPicPr><p:cNvPr id="{sid}" name="Picture {sid}"/>'
            '<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
            f'<p:blipFill><a:blip r:embed="rId{rel_id}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
            f'<p:spPr><a:xfrm><a:off x="{_in(x)}" y="{_in(y)}"/><a:ext cx="{_in(w)}" cy="{_in(h)}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
        )

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

    def connector(self, x1: float, y1: float, x2: float, y2: float, color: str, *, weight_emu: int = 28000) -> None:
        """A straight line from (x1,y1) to (x2,y2) — used to draw line-chart series.
        Offset/extent is the bounding box; flipH/flipV encode the direction."""
        sid = self._next_id()
        ox, oy = min(x1, x2), min(y1, y2)
        flip = (' flipH="1"' if x2 < x1 else "") + (' flipV="1"' if y2 < y1 else "")
        self.xml.append(
            f'<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="{sid}" name="Conn {sid}"/>'
            "<p:cNvCxnSpPr/><p:nvPr/></p:nvCxnSpPr>"
            f'<p:spPr><a:xfrm{flip}><a:off x="{_in(ox)}" y="{_in(oy)}"/>'
            f'<a:ext cx="{_in(abs(x2 - x1))}" cy="{_in(abs(y2 - y1))}"/></a:xfrm>'
            '<a:prstGeom prst="line"><a:avLst/></a:prstGeom>'
            f'<a:ln w="{weight_emu}" cap="rnd"><a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
            "<a:round/></a:ln></p:spPr></p:cxnSp>"
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
    # Only genuinely-missing REQUIRED text raises (→ caught per-slide and salvaged,
    # so the deck still builds). Over-length text is clamped, never fatal — losing
    # a whole slide because one field ran long is exactly the failure mode small
    # models trip on.
    if raw is None:
        if required:
            raise DeckSpecError(f"{where} is required")
        return ""
    s = str(raw).strip()
    if required and not s:
        raise DeckSpecError(f"{where} is required")
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


def _str_list(raw: object, where: str, *, max_items: int) -> list[str]:
    # Lenient by design: None → empty, a bare scalar → a one-item list, extra
    # items clamped. A malformed array shape never aborts the render.
    if raw is None:
        return []
    if not isinstance(raw, list):
        return [_text(raw, where)]
    items = [_text(item, f"{where}[{i}]") for i, item in enumerate(raw[:max_items])]
    return [s for s in items if s]


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
        sz = _fit_sz(title, 27, _BODY_W, 2, 18)  # 긴 제목은 줄이되 상자를 넘치지 않는다
        sp.textbox(
            _MARGIN, 0.86, _BODY_W, 0.85,
            [sp.para(sp.run(title, sz=sz, color=color or t.primary, bold=True, font=t.heading_font))],
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
    cover_title = _text(slide.get("title"), "title slide title", required=True)
    sp.textbox(
        _MARGIN, 2.35, 11.0, 2.1,
        [sp.para(sp.run(cover_title, sz=_fit_sz(cover_title, 40, 11.0, 3, 24),
                        color="FFFFFF", bold=True, font=t.heading_font), line_pct=104)],
    )
    subtitle = _text(slide.get("subtitle"), "subtitle")
    if subtitle:
        sp.textbox(_MARGIN, 4.55, 9.8, 1.1,
                   [sp.para(sp.run(subtitle, sz=_fit_sz_height(subtitle, 16, 9.8, 1.1, 11, line_pct=1.18),
                                   color=t.secondary), line_pct=118)])
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
    section_title = _text(slide.get("title"), "section title", required=True)
    sp.textbox(_MARGIN, 3.95, 11.2, 1.3,
               [sp.para(sp.run(section_title, sz=_fit_sz(section_title, 30, 11.2, 2, 20),
                               color="FFFFFF", bold=True, font=t.heading_font))])
    subtitle = _text(slide.get("subtitle"), "subtitle")
    if subtitle:
        sp.textbox(_MARGIN, 5.15, 9.6, 1.2,
                   [sp.para(sp.run(subtitle, sz=14, color=t.secondary), line_pct=120)])


def _collect_bullets(items: list, where: str, *, max_items: int = 10) -> list[tuple[str, list[str]]]:
    """불릿 원시 배열 → [(text, subs)] — 렌더(_bullet_paras)와 사이징
    (_fit_bullets_sz)이 같은 코어션 결과를 공유한다(이중 파싱 드리프트 방지)."""
    collected: list[tuple[str, list[str]]] = []
    for i, item in enumerate(items[:max_items]):
        if isinstance(item, dict):
            text = _text(item.get("text"), f"{where}[{i}].text")
            subs = _str_list(item.get("sub"), f"{where}[{i}].sub", max_items=6)
        else:
            text, subs = _text(item, f"{where}[{i}]"), []
        if text:  # skip an empty bullet rather than emit a naked bullet glyph
            collected.append((text, subs))
    return collected


def _fit_entries(collected: list[tuple[str, list[str]]]) -> list[tuple[str, bool]]:
    entries: list[tuple[str, bool]] = []
    for text, subs in collected:
        entries.append((text, False))
        entries.extend((s, True) for s in subs)
    return entries


def _bullet_paras(sp: _Shapes, collected: list[tuple[str, list[str]]], *, sz: int = 15) -> list[str]:
    t = sp.theme
    paras: list[str] = []
    for i, (text, subs) in enumerate(collected):
        paras.append(sp.para(sp.run(text, sz=sz, color=t.INK), bullet_color=t.accent,
                             line_pct=112, before_pts=9 if i else 0))
        paras.extend(
            sp.para(sp.run(s, sz=max(sz - 2, 8), color=t.MUTED), bullet_color=t.MUTED, level=1,
                    line_pct=112, before_pts=4)
            for s in subs
        )
    return paras


def _render_bullets(sp: _Shapes, slide: dict, _idx: int, _total: int) -> None:
    _motif(sp, on_dark=False)
    _kicker_title(sp, _text(slide.get("kicker"), "kicker"),
                  _text(slide.get("title"), "bullets slide title", required=True))
    items = slide.get("bullets")
    if not isinstance(items, list):
        items = _str_list(items, "bullets", max_items=10)  # coerce scalar/None
    collected = _collect_bullets(items, "bullets")
    # An empty bullet list is design-poor but not fatal — a title-only slide is
    # still valid. (The default layout falls here, so it must never raise.)
    if collected:
        sz = _fit_bullets_sz(_fit_entries(collected), _BODY_W - 0.6, 4.9, 15, 11)
        sp.textbox(_MARGIN, 1.95, _BODY_W - 0.6, 4.9, _bullet_paras(sp, collected, sz=sz))


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
        collected = _collect_bullets(items, f"columns[{i}].bullets", max_items=6)
        sz = _fit_bullets_sz(_fit_entries(collected), col_w - 0.7, 3.05, 13, 10)
        sp.textbox(x + 0.35, 3.32, col_w - 0.7, 3.05, _bullet_paras(sp, collected, sz=sz))


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
                   [sp.para(sp.run(value, sz=_fit_sz(value, 38, card_w - 0.7, 1, 18),
                                   color=value_color, bold=True, font=t.heading_font))],
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
                   [sp.para(sp.run(title, sz=_fit_sz(title, 14, col_w - 0.24, 2, 10),
                                   color=t.primary, bold=True), align="ctr", line_pct=106)])
        if desc:
            sp.textbox(_MARGIN + col_w * i + 0.12, 4.6, col_w - 0.24, 1.7,
                       [sp.para(sp.run(desc, sz=11, color=t.MUTED), align="ctr", line_pct=114)])


def _render_quote(sp: _Shapes, slide: dict, _idx: int, _total: int) -> None:
    t = sp.theme
    _motif(sp, on_dark=False)
    sp.textbox(_MARGIN, 0.9, 2.2, 1.9,
               [sp.para(sp.run("“", sz=120, color=t.accent, bold=True, font=t.heading_font))],
               autofit=False)
    quote_text = _text(slide.get("quote"), "quote text", required=True)
    sp.textbox(1.7, 2.55, 9.9, 2.6,
               [sp.para(sp.run(quote_text,
                               sz=_fit_sz_height(quote_text, 22, 9.9, 2.6, 14, line_pct=1.24),
                               color=t.INK, font=t.heading_font), line_pct=124)])
    attribution = _text(slide.get("attribution"), "attribution")
    if attribution:
        sp.textbox(1.7, 5.5, 9.9, 0.5,
                   [sp.para(sp.run("— " + attribution, sz=13, color=t.MUTED, bold=True))])


def _render_closing(sp: _Shapes, slide: dict, _idx: int, _total: int) -> None:
    t = sp.theme
    sp.shape(0, 0, 13.333, 7.5, t.primary)
    _motif(sp, on_dark=True)
    sp.shape(_MARGIN, 2.42, 0.62, 0.09, t.accent)
    closing_title = _text(slide.get("title"), "closing title", required=True)
    sp.textbox(_MARGIN, 2.75, 11.4, 1.6,
               [sp.para(sp.run(closing_title, sz=_fit_sz(closing_title, 36, 11.4, 2, 22),
                               color="FFFFFF", bold=True, font=t.heading_font))])
    subtitle = _text(slide.get("subtitle"), "subtitle")
    if subtitle:
        sp.textbox(_MARGIN, 4.35, 9.8, 1.0,
                   [sp.para(sp.run(subtitle, sz=15, color=t.secondary), line_pct=118)])
    contact = _text(slide.get("contact"), "contact")
    if contact:
        sp.textbox(_MARGIN, 6.6, _BODY_W, 0.4,
                   [sp.para(sp.run(contact, sz=12, color="FFFFFF"))])


# ── chart layout (shape-based bar/line — no embedded OOXML chart part) ─────────

def _num(v: object) -> float | None:
    """Coerce a spec value to a plottable float. Strips currency/units/commas so a
    small model's "$3.4" or "1,200" still charts. Non-numeric → None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = "".join(ch for ch in str(v) if ch.isdigit() or ch in ".-")
    try:
        return float(s) if s not in ("", "-", ".", "-.", "") else None
    except ValueError:
        return None


def _fmt_val(v: float) -> str:
    return f"{int(v):,}" if v == int(v) else f"{v:,.1f}"


def _chart_data(chart: dict) -> tuple[str, list[str], list[tuple[str, list[float]]]]:
    ctype = str(chart.get("type") or "column").strip().lower()
    ctype = "line" if ctype == "line" else "column"  # 'bar' and everything else → columns
    cats = _str_list(chart.get("categories"), "chart.categories", max_items=8)

    # Keep None for unparseable cells so we can tell "genuinely 0" from "not a
    # number at all" — a chart where NOTHING parsed is unusable and should salvage.
    raw_series = chart.get("series")
    parsed: list[tuple[str, list[float | None]]] = []
    if isinstance(raw_series, list) and raw_series:
        for s in raw_series[:3]:
            if isinstance(s, dict):
                name = _text(s.get("name"), "series.name", max_len=40)
                raw_vals = s.get("values")
            else:
                name, raw_vals = "", s
            nums = [_num(v) for v in raw_vals] if isinstance(raw_vals, list) else []
            parsed.append((name, nums))
    else:
        raw_vals = chart.get("values")
        nums = [_num(v) for v in raw_vals] if isinstance(raw_vals, list) else []
        if nums:
            parsed.append(("", nums))

    if not any(n is not None for _, nums in parsed for n in nums):
        raise DeckSpecError("chart needs numeric `values` (or series[].values)")

    ncat = len(cats) or max((len(nums) for _, nums in parsed), default=0)
    if not cats:
        cats = [str(i + 1) for i in range(ncat)]
    # pad/truncate to the category count; unparseable/missing cells plot as 0
    series = [(name, [(v if v is not None else 0.0) for v in (nums + [None] * ncat)[:ncat]])
              for name, nums in parsed]
    return ctype, cats, series


def _render_chart(sp: _Shapes, slide: dict, _idx: int, _total: int) -> None:
    t = sp.theme
    _motif(sp, on_dark=False)
    _kicker_title(sp, _text(slide.get("kicker"), "kicker"),
                  _text(slide.get("title"), "chart slide title", required=True))
    chart = slide.get("chart")
    if not isinstance(chart, dict):
        raise DeckSpecError("chart layout needs a `chart` object {type, categories, series|values}")
    ctype, cats, series = _chart_data(chart)
    cycle = [t.primary, t.accent, t.MUTED]

    # 좌측에 눈금 값 자리를 확보한다(그리드라인 라벨).
    plot_x0, plot_x1 = _MARGIN + 0.62, 13.333 - _MARGIN
    plot_y0, plot_y1 = 2.55, 6.0
    plot_w, plot_h = plot_x1 - plot_x0, plot_y1 - plot_y0
    ncat, nser = len(cats), len(series)
    maxval = max((v for _, vals in series for v in vals), default=0.0) or 1.0

    # 수평 그리드라인 + 좌측 눈금 값 (25/50/75/100%) — 축 없이 값만 있는 차트는
    # 스케일을 읽을 수 없다. 옅은 중립 회색: 팔레트와 무관하게 배경으로 가라앉는다.
    for frac in (0.25, 0.5, 0.75, 1.0):
        gy = plot_y1 - plot_h * frac
        sp.shape(plot_x0, gy, plot_w, 0.012, "E4E7EE")
        sp.textbox(_MARGIN - 0.05, gy - 0.11, 0.6, 0.24,
                   [sp.para(sp.run(_fmt_val(maxval * frac), sz=8, color=t.MUTED), align="r")],
                   anchor="ctr", autofit=False)

    # optional legend (multi-series or named series)
    if nser > 1 or any(name for name, _ in series):
        lx = plot_x0
        for si, (name, _) in enumerate(series):
            label = name or f"Series {si + 1}"
            sp.shape(lx, 2.16, 0.2, 0.2, cycle[si], prst="roundRect", radius_pct=30)
            sp.textbox(lx + 0.28, 2.12, 3.2, 0.3,
                       [sp.para(sp.run(label, sz=11, color=t.INK))], anchor="ctr", autofit=False)
            lx += 0.28 + 0.11 * len(label) + 0.55

    sp.shape(plot_x0, plot_y1, plot_w, 0.018, t.MUTED)  # baseline axis
    group_w = plot_w / ncat

    if ctype == "column":
        gap = group_w * 0.2
        bar_w = (group_w - gap) / nser
        for ci in range(ncat):
            gx = plot_x0 + ci * group_w + gap / 2
            for si, (_, vals) in enumerate(series):
                h = max((vals[ci] / maxval) * plot_h, 0.0)
                x, y = gx + si * bar_w, plot_y1 - h
                if h > 0.02:
                    sp.shape(x + bar_w * 0.08, y, bar_w * 0.84, h, cycle[si], prst="roundRect", radius_pct=6)
                sp.textbox(x - 0.1, y - 0.32, bar_w + 0.2, 0.3,
                           [sp.para(sp.run(_fmt_val(vals[ci]), sz=9, color=t.INK, bold=True), align="ctr")],
                           anchor="b", autofit=False)
    else:  # line
        for si, (_, vals) in enumerate(series):
            pts = [(plot_x0 + ci * group_w + group_w / 2, plot_y1 - (vals[ci] / maxval) * plot_h)
                   for ci in range(ncat)]
            for a, b in zip(pts, pts[1:]):
                sp.connector(a[0], a[1], b[0], b[1], cycle[si])
            for ci, (px, py) in enumerate(pts):
                sp.shape(px - 0.07, py - 0.07, 0.14, 0.14, cycle[si], prst="ellipse")
                if nser == 1:  # 다시리즈에선 라벨이 겹친다 — 단일 시리즈만 값 표기
                    sp.textbox(px - 0.5, py - 0.42, 1.0, 0.3,
                               [sp.para(sp.run(_fmt_val(vals[ci]), sz=9, color=t.INK, bold=True), align="ctr")],
                               anchor="b", autofit=False)

    for ci in range(ncat):  # category labels along the baseline
        sp.textbox(plot_x0 + ci * group_w, plot_y1 + 0.08, group_w, 0.5,
                   [sp.para(sp.run(cats[ci], sz=11, color=t.INK), align="ctr")], anchor="t", autofit=False)

    footnote = _text(slide.get("footnote"), "footnote")
    if footnote:
        sp.textbox(_MARGIN, 6.85, _BODY_W, 0.35,
                   [sp.para(sp.run(footnote, sz=9, color=t.MUTED, italic=True))])


# ── image layout (workspace png/jpeg, aspect-fit) ────────────────────────────


def _fit_box(iw: int, ih: int, bx: float, by: float, bw: float, bh: float) -> tuple[float, float, float, float]:
    """이미지(px)를 박스(in) 안에 contain-fit — 중앙 정렬 좌표를 돌려준다.
    96dpi 기준 자연 크기보다 약간의 업스케일은 허용하되 2배로 캡(픽셀 뭉개짐 방지)."""
    natural_w, natural_h = iw / 96.0, ih / 96.0
    scale = min(bw / natural_w, bh / natural_h, 2.0)
    w, h = natural_w * scale, natural_h * scale
    return bx + (bw - w) / 2, by + (bh - h) / 2, w, h


def _render_image(sp: _Shapes, slide: dict, _idx: int, _total: int) -> None:
    t = sp.theme
    _kicker_title(sp, _text(slide.get("kicker"), "kicker"),
                  _text(slide.get("title"), "image slide title", required=True))
    rel_path = _text(slide.get("image"), "image path", required=True, max_len=400)
    if sp.load_asset is None:
        raise DeckSpecError("image assets are unavailable in this context")
    try:
        data = sp.load_asset(rel_path)
    except Exception as exc:
        raise DeckSpecError(f"could not load image {rel_path!r}: {exc}") from None
    ext, iw, ih = _image_meta(data)
    if iw <= 0 or ih <= 0:
        raise DeckSpecError(f"image has invalid dimensions: {rel_path!r}")
    rel_id = sp.add_image(data, ext)

    caption = _text(slide.get("caption"), "caption", max_len=300)
    raw_bullets = slide.get("bullets")
    collected = (
        _collect_bullets(raw_bullets, "bullets", max_items=6) if isinstance(raw_bullets, list) else []
    )
    if collected:
        # 분할: 좌 불릿 / 우 이미지 카드 — 텍스트와 근거 화면을 한 슬라이드에.
        text_w = 5.4
        img_x, img_w = _MARGIN + text_w + 0.45, _BODY_W - text_w - 0.45
        sz = _fit_bullets_sz(_fit_entries(collected), text_w, 4.4, 14, 10)
        sp.textbox(_MARGIN, 2.05, text_w, 4.4, _bullet_paras(sp, collected, sz=sz))
        sp.shape(img_x, 2.05, img_w, 4.4, t.CARD, prst="roundRect", radius_pct=5)
        x, y, w, h = _fit_box(iw, ih, img_x + 0.15, 2.2, img_w - 0.3, 4.1)
        sp.picture(x, y, w, h, rel_id)
        if caption:
            sp.textbox(img_x, 6.55, img_w, 0.35,
                       [sp.para(sp.run(caption, sz=10, color=t.MUTED, italic=True), align="ctr")])
    else:
        box_h = 4.35 if caption else 4.75
        x, y, w, h = _fit_box(iw, ih, _MARGIN, 2.0, _BODY_W, box_h)
        sp.picture(x, y, w, h, rel_id)
        if caption:
            sp.textbox(_MARGIN, 2.0 + box_h + 0.15, _BODY_W, 0.4,
                       [sp.para(sp.run(caption, sz=11, color=t.MUTED, italic=True), align="ctr")])


# ── salvage: render *something valid* from a slide that failed its layout ──────

def _salvage_lines(raw: dict) -> list[str]:
    out: list[str] = []
    for key in ("subtitle", "summary", "message", "body", "content", "desc"):
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    bullets = raw.get("bullets")
    if isinstance(bullets, list):
        for it in bullets:
            if isinstance(it, dict) and isinstance(it.get("text"), str):
                out.append(it["text"])
            elif isinstance(it, str):
                out.append(it)
    return [x for x in out if x][:8]


def _render_salvage(sp: _Shapes, raw: dict) -> None:
    """Last resort when a layout renderer failed: emit a valid title(+bullets)
    slide from whatever text we can recover, so content is never wholly lost and
    the deck always builds."""
    _motif(sp, on_dark=False)
    title = _text(raw.get("title") or raw.get("heading") or raw.get("quote"), "", max_len=200) or "Slide"
    _kicker_title(sp, _text(raw.get("kicker"), ""), title)
    lines = _salvage_lines(raw)
    if lines:
        collected = [(line, []) for line in lines]
        sz = _fit_bullets_sz(_fit_entries(collected), _BODY_W - 0.6, 4.9, 15, 11)
        sp.textbox(_MARGIN, 1.95, _BODY_W - 0.6, 4.9, _bullet_paras(sp, collected, sz=sz))


def _guess_layout(raw: dict) -> str:
    """Infer a layout from the fields present — for when `layout` is missing or
    unknown (a common small-model slip). Falls back to the most forgiving one."""
    for field, layout in (
        ("chart", "chart"), ("stats", "stats"), ("steps", "timeline"),
        ("columns", "two_column"), ("quote", "quote"), ("image", "image"),
    ):
        if raw.get(field):
            return layout
    if raw.get("headers") or raw.get("rows"):
        return "table"
    return "bullets"


_RENDERERS = {
    "title": _render_title,
    "section": _render_section,
    "bullets": _render_bullets,
    "two_column": _render_two_column,
    "stats": _render_stats,
    "table": _render_table,
    "timeline": _render_timeline,
    "quote": _render_quote,
    "chart": _render_chart,
    "image": _render_image,
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


def _content_types(n_slides: int, notes_indices: list[int], media_exts: frozenset[str] | set[str] = frozenset()) -> str:
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
    media_defaults = "".join(
        f'<Default Extension="{ext}" ContentType="image/{ext}"/>' for ext in sorted(media_exts)
    )
    return (
        _XMLDECL
        + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        + '<Default Extension="xml" ContentType="application/xml"/>'
        # 임베디드 RICE 스펙(docProps/riceSpec.json) — 파트마다 content type이 있어야
        # OOXML 검증기가 통과한다. 앱은 이 파트를 모르면 무시한다.
        + '<Default Extension="json" ContentType="application/json"/>'
        + media_defaults
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

def build_presentation(
    spec: dict,
    *,
    load_asset: "Callable[[str], bytes] | None" = None,
    embed_spec: bool = True,
) -> tuple[bytes, dict]:
    """Deck spec dict → (.pptx bytes, summary).

    Fault-tolerant by design (small-model robustness): a malformed slide never
    aborts the deck — it is rendered as a salvage slide and noted in
    `summary["warnings"]`. The only hard error is an entirely empty `slides`
    array (nothing to build).

    `load_asset`: image 레이아웃의 워크스페이스 이미지 로더(office/__init__ 주입).
    `embed_spec`: 원본 스펙을 docProps/riceSpec.json으로 패키지에 심는다 —
    edit_presentation의 슬라이드 단위 편집이 이 파트를 읽어 재빌드한다."""
    warnings: list[str] = []
    theme = Theme.from_spec(spec.get("theme"), warnings)
    raw_slides = spec.get("slides")
    if not isinstance(raw_slides, list) or not raw_slides:
        raise DeckSpecError("spec.slides must be a non-empty array of slide objects")
    if len(raw_slides) > _MAX_SLIDES:
        warnings.append(f"too many slides ({len(raw_slides)}) — kept the first {_MAX_SLIDES}")
        raw_slides = raw_slides[:_MAX_SLIDES]

    slides_xml: list[str] = []
    slide_meta: list[dict] = []
    slide_images: list[list[tuple[int, str, bytes]]] = []  # per slide: [(rel_id, ext, bytes)]
    notes: dict[int, str] = {}  # slide index (0-based) → notes text
    section_counter = 0
    for i, raw in enumerate(raw_slides):
        if not isinstance(raw, dict):
            warnings.append(f"slides[{i}] is not an object — replaced with a placeholder")
            raw = {}
        layout = str(raw.get("layout") or "").strip().lower()
        renderer = _RENDERERS.get(layout)
        if renderer is None:
            guessed = _guess_layout(raw)
            if layout:
                warnings.append(f"slides[{i}]: unknown layout {layout!r} → rendered as {guessed}")
            layout, renderer = guessed, _RENDERERS[guessed]
        if layout == "section":
            section_counter += 1
        shapes = _Shapes(theme, load_asset)
        try:
            renderer(shapes, raw, section_counter if layout == "section" else i + 1, len(raw_slides))
        except Exception as exc:  # noqa: BLE001 — never let one slide kill the deck
            warnings.append(f"slides[{i}] ({layout}): {exc} → rendered a salvage slide")
            shapes = _Shapes(theme, load_asset)
            _render_salvage(shapes, raw)
            layout = "bullets"  # salvage is a light title/bullets slide
        slides_xml.append(_slide_xml(shapes, dark_bg=layout in _DARK_LAYOUTS, theme=theme))
        slide_images.append(list(shapes.images))
        note_text = _text(raw.get("notes"), f"slides[{i}].notes", max_len=4_000)
        if note_text:
            notes[i] = note_text
        slide_meta.append({"layout": layout, "title": _text(raw.get("title") or raw.get("quote"), "")})

    notes_order = sorted(notes)  # slide idx → notesSlide number = position+1
    notes_number = {slide_i: n + 1 for n, slide_i in enumerate(notes_order)}

    # 미디어 파트 전역 번호 부여: slide i의 rel_id → ppt/media/imageN.ext
    media_parts: list[tuple[str, bytes]] = []  # (zip filename, bytes)
    media_by_slide: list[list[tuple[int, str]]] = []  # per slide: [(rel_id, part filename)]
    for images in slide_images:
        img_rels: list[tuple[int, str]] = []
        for rel_id, ext, data in images:
            filename = f"image{len(media_parts) + 1}.{ext}"
            media_parts.append((filename, data))
            img_rels.append((rel_id, filename))
        media_by_slide.append(img_rels)
    media_exts = {ext for images in slide_images for _, ext, _ in images}

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types(len(slides_xml), notes_order, media_exts))
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

        # slides + their rels (+ image rels — rId100+ 대역이라 layout/notes와 무충돌)
        for i, xml in enumerate(slides_xml):
            zf.writestr(f"ppt/slides/slide{i + 1}.xml", xml)
            rels = [("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml")]
            if i in notes_number:
                rels.append(("rId2", "notesSlide", f"../notesSlides/notesSlide{notes_number[i]}.xml"))
            rels.extend(
                (f"rId{rel_id}", "image", f"../media/{filename}") for rel_id, filename in media_by_slide[i]
            )
            zf.writestr(f"ppt/slides/_rels/slide{i + 1}.xml.rels", _rels(rels))

        # media parts
        for filename, data in media_parts:
            zf.writestr(f"ppt/media/{filename}", data)

        # 임베디드 스펙 — edit_presentation의 편집 원본. 파워포인트가 재저장하면
        # 이 파트는 사라질 수 있고, 그때 편집 도구는 명확히 거절한다(조용한
        # 어긋남보다 낫다).
        if embed_spec:
            zf.writestr(
                SPEC_PART,
                json.dumps(
                    {"title": spec.get("title"), "theme": spec.get("theme"), "slides": raw_slides},
                    ensure_ascii=False,
                    default=str,
                ),
            )

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
    if media_parts:
        summary["images_embedded"] = len(media_parts)
    if warnings:
        # Non-fatal issues the model may want to address on a regenerate. The file
        # is valid regardless — these are advisories, not errors.
        summary["warnings"] = warnings
    return buf.getvalue(), summary


def read_embedded_spec(pptx_bytes: bytes) -> dict | None:
    """패키지에서 임베디드 RICE 스펙을 읽는다 — 없거나(외부 생성/앱 재저장) 파손이면
    None. edit_presentation이 편집 가능 여부를 판단하는 단일 소스."""
    try:
        with zipfile.ZipFile(BytesIO(pptx_bytes)) as zf:
            data = json.loads(zf.read(SPEC_PART).decode("utf-8"))
    except (zipfile.BadZipFile, KeyError, ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None
