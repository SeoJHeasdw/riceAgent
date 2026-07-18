"""fetch_url v2 테스트 — 네트워크 없이 httpx.MockTransport로 전 구간을 검증한다.

커버: 마크다운형 추출(제목/리스트/표/코드/링크), 리다이렉트 홉별 SSRF 가드,
일시 장애 재시도, 4xx 구조화 결과, 페이지네이션 + TTL 캐시.
"""

from __future__ import annotations

import httpx
import pytest

from rice_runtime.agent.builtins import web_tools
from rice_runtime.agent.builtins.web_tools import _fetch_url_impl, _MarkdownExtractor


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    web_tools._cache.clear()
    # 재시도 backoff를 0으로 — 테스트가 실제로 잠들지 않게
    monkeypatch.setattr(web_tools, "_RETRY_BACKOFF_S", (0.0, 0.0))
    yield
    web_tools._cache.clear()


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# ── extractor structure ───────────────────────────────────────────────────────


def test_markdown_extractor_keeps_structure():
    html = (
        "<html><head><title>Doc</title>"
        '<meta name="description" content="  a   page  "></head><body>'
        "<nav><a href='/skip'>menu</a></nav>"
        "<h2>Install</h2><p>Run the steps:</p>"
        "<ol><li>download</li><li>unpack</li></ol>"
        "<ul><li>note<ul><li>nested</li></ul></li></ul>"
        "<pre>pip install rice</pre>"
        "<table><tr><th>k</th><th>v</th></tr><tr><td>a</td><td>1</td></tr></table>"
        "<blockquote>quoted wisdom</blockquote>"
        "</body></html>"
    )
    ex = _MarkdownExtractor(base_url="https://example.com", collect_links=True)
    ex.feed(html)
    text = ex.text()

    assert ex.title == "Doc"
    assert ex.description == "a page"
    assert "## Install" in text
    assert "1. download" in text and "2. unpack" in text
    assert "- note" in text and "  - nested" in text
    assert "```" in text and "pip install rice" in text
    assert "| k | v |" in text and "|---|---|" in text and "| a | 1 |" in text
    assert "> quoted wisdom" in text
    # nav 는 보일러플레이트 — 본문/링크 어디에도 없다
    assert "menu" not in text
    assert all("/skip" not in l["url"] for l in ex.links)


def test_markdown_extractor_collects_absolute_links():
    ex = _MarkdownExtractor(base_url="https://example.com/docs/", collect_links=True)
    ex.feed('<p><a href="guide.html">The Guide</a> <a href="mailto:x@y">m</a></p>')
    assert ex.links == [{"url": "https://example.com/docs/guide.html", "text": "The Guide"}]


# ── fetch flow ────────────────────────────────────────────────────────────────


async def test_fetch_url_returns_markdown_and_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content="<html><head><title>T</title></head><body><h1>Hi</h1><p>Body</p></body></html>",
        )

    out = await _fetch_url_impl({"url": "https://example.com/a"}, transport=_transport(handler))
    assert out["status"] == 200
    assert out["title"] == "T"
    assert "# Hi" in out["text"] and "Body" in out["text"]
    assert out["truncated"] is False and out["total_chars"] == len(out["text"])


async def test_fetch_url_guards_every_redirect_hop():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8127/steal"})

    with pytest.raises(ValueError, match="private address"):
        await _fetch_url_impl({"url": "https://example.com/r"}, transport=_transport(handler))


async def test_fetch_url_follows_public_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://example.com/new"})
        return httpx.Response(200, headers={"content-type": "text/plain"}, content="arrived")

    out = await _fetch_url_impl({"url": "https://example.com/old"}, transport=_transport(handler))
    assert out["url"].endswith("/new") and out["text"] == "arrived"


async def test_fetch_url_retries_transient_503_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, content="busy")
        return httpx.Response(200, headers={"content-type": "text/plain"}, content="ok now")

    out = await _fetch_url_impl({"url": "https://example.com/x"}, transport=_transport(handler))
    assert calls["n"] == 2 and out["text"] == "ok now" and "error" not in out


async def test_fetch_url_surfaces_http_error_as_data_not_exception():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404, headers={"content-type": "text/html"}, content="<p>page gone, see /v2/docs</p>"
        )

    out = await _fetch_url_impl({"url": "https://example.com/missing"}, transport=_transport(handler))
    assert out["status"] == 404 and out["error"] == "HTTP 404"
    assert "page gone" in out["text"]  # 에러 페이지의 힌트는 발췌로 전달


async def test_fetch_url_reports_unsupported_binary():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.7")

    out = await _fetch_url_impl({"url": "https://example.com/f.pdf"}, transport=_transport(handler))
    assert "unsupported content type" in out["error"] and out["text"] == ""


async def test_fetch_url_pretty_prints_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, content='{"a":[1,2]}')

    out = await _fetch_url_impl({"url": "https://api.example.com/v1"}, transport=_transport(handler))
    assert '"a": [' in out["text"]


async def test_fetch_url_pagination_uses_cache():
    calls = {"n": 0}
    body = "<html><body><p>" + "x" * 5_000 + "</p></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, headers={"content-type": "text/html"}, content=body)

    first = await _fetch_url_impl(
        {"url": "https://example.com/long", "max_chars": 1_000}, transport=_transport(handler)
    )
    assert first["truncated"] and first["next_start"] == 1_000 and first["total_chars"] == 5_000

    second = await _fetch_url_impl(
        {"url": "https://example.com/long", "max_chars": 4_500, "start_index": first["next_start"]},
        transport=_transport(handler),
    )
    assert calls["n"] == 1  # 이어 읽기는 캐시를 탄다 — 재다운로드 없음
    assert second["from_cache"] is True
    assert second["truncated"] is False
    assert len(second["text"]) == 4_000


async def test_fetch_url_include_links():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content='<body><p><a href="/next">Next page</a></p></body>',
        )

    out = await _fetch_url_impl(
        {"url": "https://example.com/p1", "include_links": True}, transport=_transport(handler)
    )
    assert out["links"] == [{"url": "https://example.com/next", "text": "Next page"}]


async def test_fetch_url_rejects_bad_start_index():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, content="tiny")

    with pytest.raises(ValueError, match="beyond the document end"):
        await _fetch_url_impl(
            {"url": "https://example.com/t", "start_index": 10_000}, transport=_transport(handler)
        )


def test_charset_sniffing_from_meta():
    body = '<html><head><meta charset="euc-kr"></head></html>'.encode("ascii")
    assert web_tools._sniff_charset(body) == "euc-kr"


def test_error_responses_are_not_cached():
    doc = web_tools._Document(url="u", status=500, content_type="text/html")
    web_tools._cache_put("u", doc)
    assert web_tools._cache_get("u", need_links=False) is None


# ── v3: SSRF 숫자형 리터럴 · JS 껍데기 진단 · form 본문 · 링크 캐시 ────────────


@pytest.mark.parametrize("host", ["2130706433", "0x7f000001", "127.1"])
async def test_fetch_url_rejects_ambiguous_numeric_hosts(host):
    # getaddrinfo가 inet_aton 규칙으로 127.0.0.1로 해석하는 형태들 — 요청 전 거절
    with pytest.raises(ValueError, match="numeric host"):
        await _fetch_url_impl({"url": f"http://{host}/steal"})


async def test_fetch_url_reports_js_shell_and_search_hint():
    shell = "<html><head><title>Google Search</title></head><body><script>boot()</script></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=shell)

    out = await _fetch_url_impl(
        {"url": "https://www.google.com/search?q=%EB%8B%B9%EC%A7%84+%EB%A7%9B%EC%A7%91"},
        transport=_transport(handler),
    )
    assert "JavaScript" in out["error"]  # 빈 껍데기를 침묵하지 않는다
    assert "web_search" in out["hint"]  # 검색엔진 URL이면 도구 전환 안내


async def test_fetch_url_iframe_wrapper_points_to_frame_url():
    # 네이버 블로그형 래퍼: 본문 전체가 iframe 안 — 실제 콘텐츠 URL을 error에 싣는다
    wrapper = '<html><body><iframe src="/PostView.naver?blogId=u&logNo=1" name="mainFrame"></iframe></body></html>'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=wrapper)

    out = await _fetch_url_impl({"url": "https://blog.example.com/u/1"}, transport=_transport(handler))
    assert "embedded frame" in out["error"]
    assert "https://blog.example.com/PostView.naver?blogId=u&logNo=1" in out["error"]


async def test_fetch_url_no_search_hint_on_normal_short_page():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content="<p>ok</p>")

    out = await _fetch_url_impl({"url": "https://example.com/a"}, transport=_transport(handler))
    assert "hint" not in out


def test_form_wrapped_body_is_not_dropped():
    # 구형 ASP.NET처럼 본문 전체가 <form> 안에 있어도 텍스트는 살아야 한다
    ex = _MarkdownExtractor()
    ex.feed("<body><form><p>real content</p><button>submit</button></form></body>")
    text = ex.text()
    assert "real content" in text and "submit" not in text


async def test_include_links_cache_hits_for_linkless_page():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, headers={"content-type": "text/html"}, content="<p>no links here</p>")

    args = {"url": "https://example.com/bare", "include_links": True}
    await _fetch_url_impl(args, transport=_transport(handler))
    second = await _fetch_url_impl(args, transport=_transport(handler))
    assert calls["n"] == 1 and second["from_cache"] is True and second["links"] == []


# ── §hygiene: 소형 모델 오염 방지 ────────────────────────────────────────────


def test_link_dense_lines_are_dropped_but_prose_with_links_kept():
    ex = _MarkdownExtractor()
    prose = "이 집의 대표 메뉴는 <a href='/m'>활어회</a>인데 당진항에서 매일 아침 들여온다는 점이 특별하다."
    menu = "<ul><li><a href='/home'>홈</a></li><li><a href='/cat'>카테고리</a></li><li><a href='/guest'>방명록</a></li></ul>"
    ex.feed(f"<body>{menu}<p>{prose}</p></body>")
    text = ex.text()
    assert "활어회" in text and "당진항" in text  # 본문 속 링크는 문장이므로 유지
    assert "홈" not in text and "방명록" not in text  # 내비 링크 행은 제거


def test_hidden_text_is_skipped():
    html = (
        "<body>"
        '<span class="blind">블로그 메뉴</span>'  # 네이버식 스크린리더 전용
        '<div aria-hidden="true"><p>장식용 텍스트</p></div>'
        "<div hidden><p>DOM 잔재</p></div>"
        '<p>보이는 <span class="blind">숨김레이블</span>본문은 이렇게 충분히 길게 남아야 한다.</p>'
        "</body>"
    )
    ex = _MarkdownExtractor()
    ex.feed(html)
    text = ex.text()
    assert "블로그 메뉴" not in text and "장식용" not in text and "잔재" not in text
    assert "숨김레이블" not in text and "보이는" in text and "남아야 한다" in text


def test_consecutive_anchor_menus_in_one_line_are_dropped():
    # "메뉴 바로가기 본문 바로가기" — 앵커 사이 공백이 비율을 희석하면 안 된다
    ex = _MarkdownExtractor()
    ex.feed("<body><div><a href='#m'>메뉴 바로가기</a> <a href='#c'>본문 바로가기</a></div></body>")
    assert "바로가기" not in ex.text()


def test_layout_table_menus_and_empty_rows_are_dropped():
    html = (
        "<body><table>"
        "<tr><td></td><td></td><td></td></tr>"  # 빈 셀 행(레이아웃 뼈대)
        "<tr><td><a href='/p'>프롤로그</a></td><td><a href='/m'>지도 서재</a></td></tr>"  # 링크 메뉴 행
        "</table>"
        "<table><tr><th>식당</th><th>대표메뉴</th></tr>"
        "<tr><td><a href='/r1'>한판댁</a></td><td>숙성 고기 구이와 된장찌개 세트가 유명한 곳</td></tr>"
        "</table></body>"
    )
    ex = _MarkdownExtractor()
    ex.feed(html)
    text = ex.text()
    assert "|  |" not in text and "프롤로그" not in text  # 레이아웃 표 잡음 제거
    assert "| 식당 | 대표메뉴 |" in text and "한판댁" in text  # 데이터 표는 유지


def test_main_scoping_drops_out_of_article_noise():
    body = "당진 여행에서 꼭 들러야 할 맛집을 소개합니다. " * 12  # ≥ 300자 본문
    html = (
        "<body><header><p>사이트 배너 광고 문구</p></header>"
        "<div><p>사이드바 위젯 잡음 텍스트</p></div>"
        f"<article><h1>당진 맛집</h1><p>{body}</p></article>"
        "<div><p>추천 글 위젯 잡음</p></div></body>"
    )
    ex = _MarkdownExtractor()
    ex.feed(html)
    text = ex.text()
    assert "꼭 들러야 할 맛집" in text
    assert "사이드바" not in text and "배너" not in text and "추천 글 위젯" not in text


def test_main_scoping_falls_back_when_article_is_tiny():
    # article이 껍데기(<300자)면 스코핑을 버리고 전체 문서를 쓴다
    ex = _MarkdownExtractor()
    ex.feed("<body><p>본문은 사실 여기 바깥에 있는 구조의 페이지다.</p><article><p>짧음</p></article></body>")
    assert "바깥에 있는" in ex.text()


async def test_fetch_url_low_content_page_is_reported_as_failed_source():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content="<p>메뉴 몇 조각뿐</p>")

    out = await _fetch_url_impl({"url": "https://example.com/thin"}, transport=_transport(handler))
    assert "failed source" in out["error"]  # 빈약한 페이지는 명시적 실패로 보고
    assert out["text"]  # 있는 텍스트 자체는 버리지 않는다(판단 근거)


# ── §hygiene hardening: 품질 판정·오탐 방지·캐시 ─────────────────────────


async def test_fetch_url_rejects_high_volume_anonymous_widget_wall_without_echoing_it():
    # class/id/role 없는 익명 div 위젯. 숫자가 매번 달라 exact-line
    # dedupe로는 잡히지 않고, raw text는 200자를 훨씬 넘는다.
    widget_wall = "".join(
        f"<div>일상 {i:,}개의 글 공감 {i % 7} 댓글 {i % 5} 공유 이웃추가 이전 다음</div>"
        for i in range(40)
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=f"<body>{widget_wall}</body>",
        )

    out = await _fetch_url_impl(
        {"url": "https://example.com/widgets"}, transport=_transport(handler)
    )
    assert "error" in out
    assert out["text"] == ""  # 실패한 쓰레기 본문을 소형 모델에 다시 노출하지 않는다
    assert out["truncated"] is False and "next_start" not in out


async def test_fetch_url_rejects_low_entropy_length_padding_without_echoing_it():
    # 길이만 늘린 저정보량 문자열은 _MIN_CONTENT_CHARS를 넘어도 본문이 아니다.
    padded = "<body><div>" + ("광고 " * 120) + "</div></body>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=padded)

    out = await _fetch_url_impl(
        {"url": "https://example.com/padded"}, transport=_transport(handler)
    )
    assert "error" in out
    assert out["text"] == ""


async def test_fetch_url_rejects_heading_link_farm_without_echoing_it():
    # 제목은 링크 밀집 라인 예외지만, 제목 링크만 모은 페이지 전체는
    # 유효 본문이 아니다. include_links 채널로 항행해야 한다.
    link_farm = "".join(
        f"<h3><a href='/related/{i}'>당진 지역별 추천 맛집 모음 카테고리 {i}</a></h3>"
        for i in range(20)
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=f"<body>{link_farm}</body>",
        )

    out = await _fetch_url_impl(
        {"url": "https://example.com/link-farm"}, transport=_transport(handler)
    )
    assert "error" in out
    assert out["text"] == ""


async def test_fetch_url_accepts_short_structured_fact_sheet():
    # 짧지만 제목·날짜·시간·주소·전화가 있는 고밀도 정보는
    # 단순 200자 문턱 때문에 실패하면 안 된다.
    facts = (
        "<main><h1>임시 휴무 안내</h1>"
        "<p>7월 20일은 내부 공사로 쉽니다.</p>"
        "<p>정상 영업은 7월 21일 오전 11시부터입니다.</p>"
        "<p>주소: 충남 당진시 시장길 1</p>"
        "<p>문의: 041-000-0000</p></main>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=facts)

    out = await _fetch_url_impl(
        {"url": "https://example.com/closure"}, transport=_transport(handler)
    )
    assert "error" not in out
    assert "# 임시 휴무 안내" in out["text"]
    assert "7월 21일 오전 11시" in out["text"]
    assert "충남 당진시 시장길 1" in out["text"]
    assert "041-000-0000" in out["text"]


async def test_fetch_url_short_article_outranks_large_anonymous_widget_wall():
    # article이 300자 미만이어도 구조와 문장 품질이 충분하면 본문으로
    # 선택해야 한다. 밖의 위젯 길이가 판정을 뒤집으면 안 된다.
    widgets = "".join(
        f"<div>일상 {i}개의 글 공감 {i % 3} 댓글 {i % 4} 공유 이웃추가 이전 다음</div>"
        for i in range(25)
    )
    article = (
        "<article><h1>바다횟집</h1>"
        "<p>당진항에서 매일 아침 들여온 생선을 사용합니다. "
        "대표 메뉴는 모둠회이며 식사와 함께 매운탕이 나옵니다. "
        "영업 시간은 오전 11시부터 오후 9시까지이고 예약은 전화로 가능합니다.</p></article>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=f"<body>{widgets}{article}{widgets}</body>",
        )

    out = await _fetch_url_impl(
        {"url": "https://example.com/restaurant"}, transport=_transport(handler)
    )
    assert "error" not in out
    assert "# 바다횟집" in out["text"] and "당진항에서 매일 아침" in out["text"]
    assert "일상 0개의 글" not in out["text"]
    assert "이웃추가" not in out["text"]


def test_style_hidden_and_semantic_noise_containers_are_skipped():
    html = (
        "<body>"
        '<div style="display: none"><p>display 숨김 잡음</p></div>'
        '<div style="visibility:hidden"><p>visibility 숨김 잡음</p></div>'
        '<div role="banner"><p>사이트 배너 잡음</p></div>'
        '<div role="navigation"><p>메뉴 내비게이션 잡음</p></div>'
        '<div role="complementary"><p>사이드바 위젯 잡음</p></div>'
        '<div role="contentinfo"><p>푸터 정보 잡음</p></div>'
        '<div role="dialog"><p>쿠키 동의 모달 잡음</p></div>'
        "<main><h1>보이는 본문</h1><p>이 문장은 모델이 읽어야 하는 실제 정보입니다.</p></main>"
        "</body>"
    )
    ex = _MarkdownExtractor()
    ex.feed(html)
    text = ex.text()

    assert "보이는 본문" in text and "실제 정보" in text
    noise_fragments = (
        "display 숨김",
        "visibility 숨김",
        "배너 잡음",
        "내비게이션 잡음",
        "사이드바",
        "푸터 정보",
        "쿠키 동의",
    )
    assert all(noise not in text for noise in noise_fragments)


async def test_low_quality_status_200_response_is_not_cached():
    calls = {"n": 0}
    widget_wall = "".join(
        f"<div>일상 {i}개의 글 공감 {i % 5} 댓글 {i % 3} 이전 다음</div>"
        for i in range(40)
    )
    good_body = "당진 여행자를 위한 검증된 맛집 정보입니다. " * 20

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        content = (
            f"<body>{widget_wall}</body>"
            if calls["n"] == 1
            else f"<main><h1>당진 맛집 가이드</h1><p>{good_body}</p></main>"
        )
        return httpx.Response(200, headers={"content-type": "text/html"}, content=content)

    args = {"url": "https://example.com/transient-challenge"}
    first = await _fetch_url_impl(args, transport=_transport(handler))
    second = await _fetch_url_impl(args, transport=_transport(handler))

    assert "error" in first and first["text"] == ""
    assert calls["n"] == 2  # status 200이어도 품질 실패를 TTL 캐시에 넣지 않는다
    assert "error" not in second
    assert "당진 맛집 가이드" in second["text"]
    assert "from_cache" not in second


_DDG_PAGE = """
<html><body>
  <div class="result results_links web-result">
    <h2 class="result__title">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fblog.example.com%2Fdangjin&amp;rut=abc">당진 <b>맛집</b> 베스트 10</a>
    </h2>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fblog.example.com%2Fdangjin">당진항 <b>회</b>부터 국밥까지 정리</a>
  </div>
  <div class="result result--ad">
    <a class="result__a" href="https://duckduckgo.com/y.js?ad_provider=x">광고 링크</a>
    <a class="result__snippet">광고 스니펫</a>
  </div>
  <div class="result results_links web-result">
    <h2 class="result__title"><a class="result__a" href="https://direct.example.com/food">직접 링크 결과</a></h2>
    <div class="result__snippet">두 번째 결과 요약</div>
  </div>
</body></html>
"""


async def test_web_search_parses_results_unwraps_uddg_and_skips_ads():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "html.duckduckgo.com"
        assert request.url.params["q"] == "당진 맛집"
        return httpx.Response(200, headers={"content-type": "text/html"}, content=_DDG_PAGE)

    out = await web_tools._web_search_impl({"query": "당진 맛집"}, transport=_transport(handler))
    assert [r["url"] for r in out["results"]] == [
        "https://blog.example.com/dangjin",  # uddg 래퍼 해제
        "https://direct.example.com/food",
    ]  # y.js 광고는 빠진다
    assert out["results"][0]["title"] == "당진 맛집 베스트 10"
    assert "국밥" in out["results"][0]["snippet"]
    assert out["results"][1]["snippet"] == "두 번째 결과 요약"
    assert "error" not in out


async def test_web_search_truncates_runaway_queries():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["q"] = request.url.params["q"]
        return httpx.Response(200, headers={"content-type": "text/html"}, content=_DDG_PAGE)

    await web_tools._web_search_impl({"query": "당진 " * 200}, transport=_transport(handler))
    assert len(seen["q"]) <= web_tools._MAX_QUERY_CHARS


async def test_web_search_respects_max_results():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=_DDG_PAGE)

    out = await web_tools._web_search_impl(
        {"query": "당진 맛집", "max_results": 1}, transport=_transport(handler)
    )
    assert len(out["results"]) == 1


async def test_web_search_empty_page_surfaces_error_not_exception():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content="<html>anomaly</html>")

    out = await web_tools._web_search_impl({"query": "x"}, transport=_transport(handler))
    assert out["results"] == [] and "no results" in out["error"]


async def test_web_search_http_error_surfaces_as_data():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content="blocked")

    out = await web_tools._web_search_impl({"query": "x"}, transport=_transport(handler))
    assert out["results"] == [] and "HTTP 403" in out["error"]


def test_unwrap_ddg_href_variants():
    unwrap = web_tools._unwrap_ddg_href
    assert unwrap("//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.com%2Fp%3Fx%3D1") == "https://a.com/p?x=1"
    assert unwrap("https://direct.com/page") == "https://direct.com/page"
    assert unwrap("https://duckduckgo.com/y.js?ad_provider=bing") is None  # 광고
    assert unwrap("/internal/nav") is None
