"""pptx v2 테스트 — autofit 폰트 스케일링 · image 레이아웃 · 차트 그리드/눈금 ·
스펙 임베드 · edit_presentation (슬라이드 단위 편집).

격리: tmp_path 워크스페이스. 외부 의존성 없음(1×1 PNG를 인라인 생성).
"""

from __future__ import annotations

import json
import struct
import zipfile
import zlib
from io import BytesIO
from xml.etree import ElementTree as ET

import pytest

from rice_runtime.agent.builtins.office import tools as office_tools
from rice_runtime.agent.builtins.office.pptx_writer import (
    SPEC_PART,
    _fit_bullets_sz,
    _fit_sz,
    _image_meta,
    build_presentation,
    read_embedded_spec,
)
from rice_runtime.core.paths import RicePaths


def _tiny_png(width: int = 4, height: int = 2) -> bytes:
    """유효한 무압축급 RGBA PNG를 stdlib로 합성한다."""
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x80\xff\xff" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _slide_xmls(data: bytes) -> list[str]:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = sorted(n for n in zf.namelist() if n.startswith("ppt/slides/slide"))
        return [zf.read(n).decode("utf-8") for n in names if n.endswith(".xml")]


def _all_parts_parse(data: bytes) -> None:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        for name in zf.namelist():
            if name.endswith((".xml", ".rels")):
                ET.fromstring(zf.read(name))


# ── deterministic fitting ─────────────────────────────────────────────────────


def test_fit_sz_shrinks_long_text_and_keeps_short():
    assert _fit_sz("Short", 27, 11.6, 2, 18) == 27
    long_title = "이 제목은 아주 길어서 한 줄에 절대 들어가지 않고 상자를 넘치기 직전까지 갑니다 — 자동으로 줄어야 한다" * 2
    assert _fit_sz(long_title, 27, 11.6, 2, 18) < 27


def test_fit_bullets_sz_shrinks_wall_of_text():
    few = [("포인트 하나", False), ("포인트 둘", False)]
    assert _fit_bullets_sz(few, 11.0, 4.9, 15, 11) == 15
    wall = [(f"아주 길고 장황한 불릿 텍스트가 계속 이어지는 항목 {i} — " * 4, False) for i in range(10)]
    assert _fit_bullets_sz(wall, 11.0, 4.9, 15, 11) < 15


def test_long_title_renders_smaller_font_in_xml():
    short, _ = build_presentation({"slides": [{"layout": "bullets", "title": "Short", "bullets": ["a"]}]})
    long, _ = build_presentation({
        "slides": [{
            "layout": "bullets",
            "title": (
                "아주 아주 긴 제목이 슬라이드 헤더 박스를 넘치지 않도록 자동으로 축소되어야 합니다 — "
                "이것이 v2 품질 개선의 핵심이며 오버플로 방어가 없으면 본문 영역을 덮어버린다"
            ),
            "bullets": ["a"],
        }]
    })
    assert 'sz="2700"' in _slide_xmls(short)[0]
    assert 'sz="2700"' not in _slide_xmls(long)[0]


# ── image layout ──────────────────────────────────────────────────────────────


def test_image_meta_sniffs_png_and_rejects_garbage():
    ext, w, h = _image_meta(_tiny_png(4, 2))
    assert (ext, w, h) == ("png", 4, 2)
    with pytest.raises(Exception):
        _image_meta(b"not an image at all")


def test_image_slide_embeds_media_and_rels():
    png = _tiny_png()
    data, summary = build_presentation(
        {"slides": [
            {"layout": "title", "title": "Deck"},
            {"layout": "image", "title": "Screenshot", "image": "shots/a.png", "caption": "그림 1"},
        ]},
        load_asset=lambda rel: png if rel == "shots/a.png" else (_ for _ in ()).throw(ValueError(rel)),
    )
    assert summary["images_embedded"] == 1
    assert summary["slides"][1]["layout"] == "image"
    with zipfile.ZipFile(BytesIO(data)) as zf:
        assert zf.read("ppt/media/image1.png") == png
        rels = zf.read("ppt/slides/_rels/slide2.xml.rels").decode()
        assert 'Id="rId100"' in rels and "../media/image1.png" in rels
        assert 'Extension="png"' in zf.read("[Content_Types].xml").decode()
    assert "r:embed=\"rId100\"" in _slide_xmls(data)[1]
    _all_parts_parse(data)


def test_image_slide_with_bullets_splits_layout():
    png = _tiny_png()
    data, _ = build_presentation(
        {"slides": [{"layout": "image", "title": "T", "image": "a.png", "bullets": ["좌측 포인트"]}]},
        load_asset=lambda rel: png,
    )
    xml = _slide_xmls(data)[0]
    assert "<p:pic>" in xml and "좌측 포인트" in xml


def test_missing_image_salvages_slide_not_deck():
    data, summary = build_presentation(
        {"slides": [{"layout": "image", "title": "T", "image": "missing.png"}]},
        load_asset=lambda rel: (_ for _ in ()).throw(ValueError("not found")),
    )
    assert summary["warnings"] and "salvage" in summary["warnings"][0]
    _all_parts_parse(data)  # 살아있는 덱


def test_guessed_image_layout_from_field():
    png = _tiny_png()
    data, summary = build_presentation(
        {"slides": [{"title": "no layout key", "image": "a.png"}]}, load_asset=lambda rel: png
    )
    assert summary["slides"][0]["layout"] == "image"


# ── chart polish ─────────────────────────────────────────────────────────────


def test_chart_has_gridlines_and_axis_values():
    data, _ = build_presentation({
        "slides": [{
            "layout": "chart", "title": "Growth",
            "chart": {"type": "column", "categories": ["A", "B"], "values": [100, 200]},
        }]
    })
    xml = _slide_xmls(data)[0]
    assert xml.count('val="E4E7EE"') == 4  # 그리드라인 4개
    assert ">200<" in xml and ">50<" in xml  # 최대/25% 눈금 값


def test_line_chart_single_series_gets_point_labels():
    data, _ = build_presentation({
        "slides": [{
            "layout": "chart", "title": "Trend",
            "chart": {"type": "line", "categories": ["1", "2"], "values": [10, 30]},
        }]
    })
    xml = _slide_xmls(data)[0]
    assert ">30<" in xml  # 포인트 값 라벨


# ── embedded spec + edit_presentation ────────────────────────────────────────


def test_spec_is_embedded_and_readable():
    spec = {"title": "D", "theme": "forest", "slides": [{"layout": "title", "title": "Hello"}]}
    data, _ = build_presentation(spec)
    embedded = read_embedded_spec(data)
    assert embedded == {"title": "D", "theme": "forest", "slides": spec["slides"]}
    assert read_embedded_spec(b"garbage") is None


async def _office_handlers(tmp_path):
    paths = RicePaths.from_env(tmp_path).ensure()
    return paths, {spec.name: handler for spec, handler in office_tools(paths)}


async def test_edit_presentation_full_cycle(tmp_path):
    paths, handlers = await _office_handlers(tmp_path)
    await handlers["create_presentation"]({
        "path": "deck.pptx",
        "title": "v1",
        "slides": [
            {"layout": "title", "title": "Cover"},
            {"layout": "bullets", "title": "Old body", "bullets": ["stale"]},
            {"layout": "closing", "title": "Bye"},
        ],
    })

    out = await handlers["edit_presentation"]({
        "path": "deck.pptx",
        "operations": [
            {"action": "replace", "index": 2, "slide": {"layout": "bullets", "title": "New body", "bullets": ["fresh"]}},
            {"action": "insert", "index": 3, "slide": {"layout": "quote", "quote": "inserted wisdom"}},
            {"action": "move", "index": 1, "to": 2},
        ],
        "title": "v2",
    })

    assert out["operations_applied"] == ["replace slide 2", "insert at 3", "move slide 1 → 2"]
    assert out["slide_count"] == 4
    data = (paths.workspace / "deck.pptx").read_bytes()
    embedded = read_embedded_spec(data)
    assert embedded["title"] == "v2"
    assert [s.get("layout") for s in embedded["slides"]] == ["bullets", "title", "quote", "closing"]
    assert embedded["slides"][0]["bullets"] == ["fresh"]
    _all_parts_parse(data)


async def test_edit_presentation_rejects_foreign_pptx(tmp_path):
    paths, handlers = await _office_handlers(tmp_path)
    # 임베디드 스펙 없는 zip(외부 생성 흉내)
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ppt/presentation.xml", "<p/>")
    (paths.workspace / "foreign.pptx").write_bytes(buf.getvalue())

    with pytest.raises(ValueError, match="no embedded RICE spec"):
        await handlers["edit_presentation"]({
            "path": "foreign.pptx",
            "operations": [{"action": "remove", "index": 1}],
        })


async def test_edit_presentation_validates_operations(tmp_path):
    paths, handlers = await _office_handlers(tmp_path)
    await handlers["create_presentation"]({
        "path": "d.pptx", "slides": [{"layout": "title", "title": "One"}],
    })
    original = (paths.workspace / "d.pptx").read_bytes()

    for bad_ops, msg in [
        ([{"action": "remove", "index": 9}], "out of range"),
        ([{"action": "replace", "index": 1}], "slide object is required"),
        ([{"action": "replace", "index": 1, "slide": "{not json"}], "slide object is required"),
        ([{"action": "explode", "index": 1}], "must be replace/insert/remove/move"),
        ([], "non-empty"),
    ]:
        with pytest.raises(ValueError, match=msg):
            await handlers["edit_presentation"]({"path": "d.pptx", "operations": bad_ops})
    # 실패한 편집은 파일을 건드리지 않는다
    assert (paths.workspace / "d.pptx").read_bytes() == original


async def test_edit_salvages_stringified_slide_object(tmp_path):
    # sLLM이 op.slide를 JSON 문자열로 감싼 경우(arg_preflight가 잡는 실패 계열의
    # 중첩 변형) — 디코드해 그대로 적용하고, 임베디드 스펙에도 dict로 남는다.
    paths, handlers = await _office_handlers(tmp_path)
    await handlers["create_presentation"]({
        "path": "d.pptx", "slides": [{"layout": "title", "title": "One"}],
    })

    out = await handlers["edit_presentation"]({
        "path": "d.pptx",
        "operations": [
            {"action": "insert", "slide": '{"layout": "quote", "quote": "wrapped"}'},
        ],
    })

    assert out["operations_applied"] == ["insert at 2"]
    embedded = read_embedded_spec((paths.workspace / "d.pptx").read_bytes())
    assert embedded["slides"][1] == {"layout": "quote", "quote": "wrapped"}


async def test_edit_preserves_images_via_reload(tmp_path):
    paths, handlers = await _office_handlers(tmp_path)
    (paths.workspace / "img.png").write_bytes(_tiny_png())
    await handlers["create_presentation"]({
        "path": "deck.pptx",
        "slides": [
            {"layout": "image", "title": "Shot", "image": "img.png"},
            {"layout": "closing", "title": "End"},
        ],
    })
    out = await handlers["edit_presentation"]({
        "path": "deck.pptx",
        "operations": [{"action": "remove", "index": 2}],
    })
    assert out["slide_count"] == 1 and out["images_embedded"] == 1

    spec_json = json.loads(
        zipfile.ZipFile(BytesIO((paths.workspace / "deck.pptx").read_bytes())).read(SPEC_PART)
    )
    assert spec_json["slides"][0]["image"] == "img.png"
