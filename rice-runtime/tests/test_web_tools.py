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
