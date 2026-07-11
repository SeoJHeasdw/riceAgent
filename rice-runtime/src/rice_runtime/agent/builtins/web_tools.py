"""Builtin web tool: fetch_url — read the public web as clean, pageable text.

리서치형 작업("이 문서 읽고 요약해줘", "공식 문서 확인해줘")의 기본기. connector
없이도 에이전트가 공개 웹을 읽을 수 있게 한다. 읽기 전용이므로 승인 게이트는
타지 않지만, 로컬 런타임을 향한 SSRF는 막는다(§SSRF 아래).

2026-07-10 v2 — 안정성/능력 개편:

  안정성
  - 리다이렉트를 **수동으로** 따라가며 매 홉의 호스트를 SSRF 가드에 태운다.
    이전엔 follow_redirects=True 후 최종 URL만 검사했다 — 공개 URL이 127.0.0.1로
    302하면 검사 *전에* 로컬 요청이 이미 나간 구멍이었다.
  - 일시 장애(연결/타임아웃, 429/502/503/504)는 짧은 backoff로 유한 재시도.
  - HTTP 4xx/5xx는 예외가 아니라 **구조화 결과**({status, error, 본문 발췌})로
    돌려준다 — 404는 모델이 경로를 바꿔야 할 데이터지 스택트레이스가 아니다.
  - 헤더 charset이 없으면 <meta charset> 스니핑 → utf-8 폴백.

  능력
  - HTML은 마크다운형 텍스트로 추출한다: 제목(#), 리스트(-/1.), 표(|…|),
    코드펜스(```), 인용(>). 평문 뭉치보다 구조가 살아 있어야 모델이 문서를
    항행한다. <title>/meta description도 별도 필드로 준다.
  - **페이지네이션**: start_index로 긴 문서를 이어 읽는다(total_chars/next_start).
    잘린 문서를 다시 통째로 받는 대신 다음 조각만 받는다.
  - 짧은 TTL 인프로세스 캐시 — 같은 URL의 이어 읽기/재참조가 재다운로드를
    일으키지 않는다(페이지네이션의 전제).
  - include_links=true면 본문 링크(절대 URL + 앵커 텍스트)를 수집해 돌려준다 —
    크롤링/후속 항행용.

§SSRF: http(s) 스킴만 허용하고 localhost/사설 대역 호스트는 이름/리터럴 수준에서
거절한다(런타임 API가 127.0.0.1에 떠 있다 — RUNTIME_DESIGN §10.1). DNS 재바인딩
까지 막는 완전한 방어는 아니지만(개인용 로컬 에이전트 위협 모델), 명백한 표적은
매 리다이렉트 홉에서 끊는다.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from ...core.types import ToolSpec
from ..tool_dispatch import BuiltinTool

_MAX_FETCH_BYTES = 1024 * 1024  # 응답 바디 수신 상한(스트리밍 컷)
_DEFAULT_MAX_CHARS = 20_000
_MIN_MAX_CHARS = 1_000
_MAX_MAX_CHARS = 60_000
_ERROR_BODY_CHARS = 2_000  # 4xx/5xx 본문 발췌 상한 — 에러 페이지의 힌트만
_MAX_REDIRECTS = 5
_MAX_LINKS = 40
_CONNECT_TIMEOUT_S = 10.0
_READ_TIMEOUT_S = 20.0

# 일시 장애 재시도: 시도 횟수 = 1 + len(backoff). 결정론적·유한 — 재시도 폭풍 금지.
_RETRY_BACKOFF_S: tuple[float, ...] = (0.5, 1.5)
_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
_MAX_RETRY_AFTER_S = 10.0  # Retry-After가 이보다 크면 기다리지 않고 그대로 실패

# 짧은 TTL 캐시 — 페이지네이션(이어 읽기)과 턴 내 재참조가 재다운로드를 만들지
# 않게 한다. 프로세스 로컬, bounded.
_CACHE_TTL_S = 300.0
_CACHE_MAX = 16
_cache: dict[str, tuple[float, "_Document"]] = {}

# 바닥 UA는 차단이 잦다 — 브라우저형 UA에 에이전트 표식을 함께 싣는다(정직+실용).
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) rice-agent/2.0",
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,text/*;q=0.8,*/*;q=0.5",
    "Accept-Language": "ko, en;q=0.8",
}


def _reject_private_host(host: str | None) -> None:
    """SSRF guard: 로컬 런타임/사내망을 향한 요청을 거절한다. 리다이렉트 체인의
    매 홉이 요청 **전에** 이 가드를 통과해야 한다."""
    if not host:
        raise ValueError("refusing to fetch: URL has no host")
    lowered = host.lower().rstrip(".")
    if lowered in ("localhost",) or lowered.endswith(".local") or lowered.endswith(".internal"):
        raise ValueError(f"refusing to fetch local/internal host: {host}")
    try:
        addr = ipaddress.ip_address(lowered.strip("[]"))
    except ValueError:
        return  # not an IP literal — public hostname
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
        raise ValueError(f"refusing to fetch private address: {host}")


# ── HTML → markdown-ish text ─────────────────────────────────────────────────

# 실행/표현 리소스 — 텍스트 아님.
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "canvas", "iframe", "object", "embed"}
# 보일러플레이트 — 본문이 아니라 사이트 뼈대(readability-lite).
_BOILERPLATE_TAGS = {"nav", "aside", "footer", "form", "button", "select", "dialog"}
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_FLUSH_TAGS = {"p", "div", "section", "article", "main", "header", "figure", "figcaption", "dt", "dd"}


class _MarkdownExtractor(HTMLParser):
    """HTML을 구조가 살아 있는 마크다운형 라인들로 변환한다.

    stdlib HTMLParser 기반(의존성 0), 파손된 마크업에 관대. 완벽한 마크다운이
    목표가 아니라 **모델이 문서 구조를 읽을 수 있는 텍스트**가 목표다."""

    def __init__(self, *, base_url: str = "", collect_links: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self._lines: list[str] = []
        self._inline: list[str] = []
        self._skip = 0
        self._pre = 0
        self._pre_buf: list[str] = []
        self._quote = 0
        self._prefix = ""  # 다음 flush 라인의 프리픽스(제목/리스트 마커)
        self._list_stack: list[int | None] = []  # None=ul, int=다음 ol 번호
        # table state (단일 레벨 — 중첩 표는 평탄화)
        self._cell: list[str] | None = None
        self._row: list[str] | None = None
        self._row_is_header = False
        self._table_header_done = False
        # metadata
        self.title = ""
        self._in_title = False
        self.description = ""
        # links
        self._base = base_url
        self._collect = collect_links
        self.links: list[dict] = []
        self._seen_links: set[str] = set()
        self._anchor: dict | None = None

    # ── line assembly ────────────────────────────────────────────────
    def _flush(self) -> None:
        text = " ".join("".join(self._inline).split())
        self._inline.clear()
        prefix, self._prefix = self._prefix, ""
        if not text:
            return
        line = prefix + text
        if self._quote:
            line = "> " + line
        if prefix.startswith("#") and self._lines and self._lines[-1] != "":
            self._lines.append("")  # 제목 앞 빈 줄 — 문서 리듬
        self._lines.append(line)

    # ── tags ─────────────────────────────────────────────────────────
    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP_TAGS or tag in _BOILERPLATE_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            a = dict(attrs)
            if (a.get("name") or a.get("property")) in ("description", "og:description") and not self.description:
                self.description = " ".join(str(a.get("content") or "").split())[:300]
        elif tag == "br":
            self._flush()
        elif tag in _HEADINGS:
            self._flush()
            self._prefix = "#" * _HEADINGS[tag] + " "
        elif tag in ("ul", "ol"):
            self._flush()
            self._list_stack.append(1 if tag == "ol" else None)
        elif tag == "li":
            self._flush()
            depth = max(0, len(self._list_stack) - 1)
            marker = "-"
            if self._list_stack and self._list_stack[-1] is not None:
                marker = f"{self._list_stack[-1]}."
                self._list_stack[-1] += 1
            self._prefix = "  " * depth + marker + " "
        elif tag == "pre":
            self._flush()
            self._pre += 1
        elif tag == "blockquote":
            self._flush()
            self._quote += 1
        elif tag == "table":
            self._flush()
            self._table_header_done = False
        elif tag == "tr":
            self._row = []
            self._row_is_header = False
        elif tag in ("td", "th"):
            self._cell = []
            if tag == "th":
                self._row_is_header = True
        elif tag == "a":
            href = next((v for k, v in attrs if k == "href" and v), "")
            if self._collect and href:
                self._anchor = {"href": href, "parts": []}
        elif tag in _FLUSH_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS or tag in _BOILERPLATE_TAGS:
            if self._skip > 0:
                self._skip -= 1
            return
        if self._skip:
            return
        if tag == "title":
            self._in_title = False
        elif tag in _HEADINGS or tag == "li" or tag in _FLUSH_TAGS:
            self._flush()
        elif tag in ("ul", "ol"):
            self._flush()
            if self._list_stack:
                self._list_stack.pop()
        elif tag == "pre":
            if self._pre > 0:
                self._pre -= 1
            if not self._pre and self._pre_buf:
                code = "".join(self._pre_buf).strip("\n")
                self._pre_buf.clear()
                if code.strip():
                    self._lines.append("```")
                    self._lines.extend(code.splitlines())
                    self._lines.append("```")
        elif tag == "blockquote":
            self._flush()
            if self._quote > 0:
                self._quote -= 1
        elif tag in ("td", "th"):
            if self._cell is not None and self._row is not None:
                self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr":
            if self._row:
                self._lines.append("| " + " | ".join(self._row) + " |")
                if self._row_is_header and not self._table_header_done:
                    self._lines.append("|" + "---|" * len(self._row))
                    self._table_header_done = True
            self._row = None
        elif tag == "a":
            anchor, self._anchor = self._anchor, None
            if anchor is not None and len(self.links) < _MAX_LINKS:
                url = urljoin(self._base, anchor["href"])
                if url.startswith(("http://", "https://")) and url not in self._seen_links:
                    self._seen_links.add(url)
                    text = " ".join("".join(anchor["parts"]).split())[:80]
                    self.links.append({"url": url, "text": text})

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title += data
        elif self._cell is not None:
            self._cell.append(data)
        elif self._pre:
            self._pre_buf.append(data)
        else:
            self._inline.append(data)
            if self._anchor is not None:
                self._anchor["parts"].append(data)

    def text(self) -> str:
        self._flush()
        self.title = " ".join(self.title.split())[:300]
        # 연속 빈 줄 1개로 압축
        out: list[str] = []
        for line in self._lines:
            if line == "" and (not out or out[-1] == ""):
                continue
            out.append(line)
        return "\n".join(out).strip()


# ── fetching (manual redirects + bounded retries) ─────────────────────────────


class _Document:
    """추출이 끝난 fetch 결과 — 캐시에 담기는 단위."""

    __slots__ = ("url", "status", "content_type", "title", "description", "text", "links", "error")

    def __init__(
        self,
        *,
        url: str,
        status: int,
        content_type: str,
        title: str = "",
        description: str = "",
        text: str = "",
        links: list[dict] | None = None,
        error: str = "",
    ) -> None:
        self.url = url
        self.status = status
        self.content_type = content_type
        self.title = title
        self.description = description
        self.text = text
        self.links = links or []
        self.error = error


def _sniff_charset(body: bytes) -> str | None:
    """헤더에 charset이 없을 때 첫 2KB에서 <meta charset>을 찾는다."""
    head = body[:2048].decode("ascii", errors="ignore")
    m = re.search(r'charset=["\']?\s*([a-zA-Z0-9_\-]+)', head)
    return m.group(1) if m else None


def _decode(body: bytes, header_charset: str | None) -> str:
    for enc in (header_charset, _sniff_charset(body), "utf-8"):
        if not enc:
            continue
        try:
            return body.decode(enc, errors="replace")
        except LookupError:  # 미지의 인코딩 이름
            continue
    return body.decode("utf-8", errors="replace")


async def _request_once(client, url: str) -> tuple[int, dict, bytes, str]:
    """GET 1회 (리다이렉트 안 따름). (status, headers, body, final_url) 반환."""
    body = bytearray()
    async with client.stream("GET", url, headers=_HEADERS, follow_redirects=False) as response:
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) >= _MAX_FETCH_BYTES:
                break
        return response.status_code, dict(response.headers), bytes(body), str(response.url)


async def _fetch_document(url: str, *, collect_links: bool, transport=None) -> _Document:
    """리다이렉트를 수동으로 따라가며(홉마다 SSRF 가드) 문서를 받아 추출한다.
    일시 장애는 유한 재시도. 4xx/5xx는 예외 없이 error 필드로 구조화한다."""
    import httpx  # noqa: PLC0415 - optional dependency by design

    timeout = httpx.Timeout(_READ_TIMEOUT_S, connect=_CONNECT_TIMEOUT_S)
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        current = url
        for _hop in range(_MAX_REDIRECTS + 1):
            _reject_private_host(urlsplit(current).hostname)

            status = 0
            headers: dict = {}
            body = b""
            last_error: Exception | None = None
            for attempt in range(len(_RETRY_BACKOFF_S) + 1):
                try:
                    status, headers, body, current = await _request_once(client, current)
                    last_error = None
                except httpx.TransportError as exc:  # 연결/타임아웃 — 일시 장애 후보
                    last_error = exc
                if last_error is None and status not in _RETRYABLE_STATUS:
                    break
                if attempt >= len(_RETRY_BACKOFF_S):
                    break
                delay = _RETRY_BACKOFF_S[attempt]
                retry_after = (headers or {}).get("retry-after", "")
                if retry_after.strip().isdigit():
                    ra = float(retry_after)
                    if ra > _MAX_RETRY_AFTER_S:
                        break  # 오래 기다리라는 서버 — 그대로 실패로 보고
                    delay = max(delay, ra)
                await asyncio.sleep(delay)
            if last_error is not None:
                raise ValueError(f"fetch failed after retries: {last_error}")

            if status in (301, 302, 303, 307, 308) and headers.get("location"):
                current = urljoin(current, headers["location"])
                if not current.startswith(("http://", "https://")):
                    raise ValueError(f"refusing non-http redirect target: {current}")
                continue
            return _extract(current, status, headers, body, collect_links=collect_links)
        raise ValueError(f"too many redirects (> {_MAX_REDIRECTS}): {url}")


def _extract(url: str, status: int, headers: dict, body: bytes, *, collect_links: bool) -> _Document:
    content_type = (headers.get("content-type") or "").lower()
    charset = None
    if "charset=" in content_type:
        charset = content_type.split("charset=", 1)[1].split(";")[0].strip()
    mime = content_type.split(";", 1)[0].strip()

    is_html = "html" in mime or (not mime and body[:256].lstrip()[:1] == b"<")
    is_json = "json" in mime
    is_text = mime.startswith("text/") or mime in ("application/xml", "application/xhtml+xml")

    title = description = ""
    links: list[dict] = []
    if is_html:
        extractor = _MarkdownExtractor(base_url=url, collect_links=collect_links)
        extractor.feed(_decode(body, charset))
        text = extractor.text()
        title, description, links = extractor.title, extractor.description, extractor.links
    elif is_json:
        raw = _decode(body, charset)
        try:
            text = json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
        except ValueError:
            text = raw
    elif is_text:
        text = _decode(body, charset)
    else:
        # 바이너리(PDF/이미지/아카이브 등)는 추출 불가 — 상황을 데이터로 알린다.
        text = ""
        size_kb = len(body) / 1024
        return _Document(
            url=url, status=status, content_type=mime,
            error=f"unsupported content type for text extraction: {mime or 'unknown'} ({size_kb:.0f} KB)",
        )

    error = ""
    if status >= 400:
        error = f"HTTP {status}"
        text = text[:_ERROR_BODY_CHARS]  # 에러 페이지는 힌트 발췌만
    return _Document(
        url=url, status=status, content_type=mime, title=title,
        description=description, text=text, links=links, error=error,
    )


# ── cache + result shaping ────────────────────────────────────────────────────


def _cache_get(url: str, *, need_links: bool) -> _Document | None:
    entry = _cache.get(url)
    if entry is None:
        return None
    expires, doc = entry
    if time.monotonic() > expires:
        _cache.pop(url, None)
        return None
    if need_links and not doc.links:
        return None  # 링크 없이 캐시된 문서 — 링크 요청은 다시 받아온다
    return doc


def _cache_put(url: str, doc: _Document) -> None:
    if doc.status >= 400:
        return  # 실패 응답은 캐시하지 않는다 — 재시도가 캐시에 막히면 안 된다
    if len(_cache) >= _CACHE_MAX:
        oldest = min(_cache, key=lambda k: _cache[k][0])
        _cache.pop(oldest, None)
    _cache[url] = (time.monotonic() + _CACHE_TTL_S, doc)


async def _fetch_url_impl(args: dict, *, transport=None) -> dict:
    url = str(args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    max_chars = args.get("max_chars")
    max_chars = int(max_chars) if isinstance(max_chars, (int, float)) else _DEFAULT_MAX_CHARS
    max_chars = max(_MIN_MAX_CHARS, min(max_chars, _MAX_MAX_CHARS))
    start = args.get("start_index")
    start = max(0, int(start)) if isinstance(start, (int, float)) else 0
    include_links = bool(args.get("include_links"))

    doc = _cache_get(url, need_links=include_links)
    from_cache = doc is not None
    if doc is None:
        doc = await _fetch_document(url, collect_links=include_links, transport=transport)
        _cache_put(url, doc)

    total = len(doc.text)
    if start and start >= total:
        raise ValueError(f"start_index {start} is beyond the document end ({total} chars)")
    piece = doc.text[start : start + max_chars]
    truncated = start + len(piece) < total

    result: dict = {
        "url": doc.url,
        "status": doc.status,
        "content_type": doc.content_type,
        "text": piece,
        "truncated": truncated,
        "total_chars": total,
    }
    if truncated:
        result["next_start"] = start + len(piece)
    if doc.title:
        result["title"] = doc.title
    if doc.description:
        result["description"] = doc.description
    if doc.error:
        result["error"] = doc.error
    if include_links:
        result["links"] = doc.links
    if from_cache:
        result["from_cache"] = True
    return result


def _make_fetch() -> BuiltinTool:
    async def fetch_url(args: dict) -> object:
        return await _fetch_url_impl(args)

    return fetch_url


def tools() -> list[tuple[ToolSpec, BuiltinTool]]:
    return [
        (
            ToolSpec(
                name="fetch_url",
                description=(
                    "Fetch a public web page (http/https) and return its readable text — "
                    "HTML is converted to markdown-ish text with headings, lists, tables "
                    "and code blocks preserved; JSON is pretty-printed. Use for looking up "
                    "documentation, articles, or any URL the user mentions. Long pages are "
                    "paged: when `truncated` is true, call again with `start_index` = "
                    "`next_start` to continue reading (re-reads hit a short-lived cache, "
                    "not the network). Set `include_links` to also get the page's links "
                    "(absolute URL + anchor text) for follow-up navigation. HTTP errors "
                    "(404 etc.) come back as a result with `error` set — adjust the URL "
                    "instead of retrying blindly."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Absolute http(s) URL to fetch."},
                        "max_chars": {
                            "type": "integer",
                            "description": "Max characters of text to return in this call (default 20000).",
                        },
                        "start_index": {
                            "type": "integer",
                            "description": "Character offset to continue a long document from (use next_start).",
                        },
                        "include_links": {
                            "type": "boolean",
                            "description": "Also return the page's links for follow-up fetches (default false).",
                        },
                    },
                    "required": ["url"],
                },
                source="builtin",
                is_web_tool=True,
            ),
            _make_fetch(),
        ),
    ]
