"""Builtin web tool: fetch_url — read a public web page as text.

리서치형 작업("이 문서 읽고 요약해줘", "공식 문서 확인해줘")의 기본기. connector
없이도 에이전트가 공개 웹을 읽을 수 있게 한다. 읽기 전용이므로 승인 게이트는
타지 않지만, 로컬 런타임을 향한 SSRF는 막는다: http(s) 스킴만 허용하고
localhost/사설 대역 호스트는 거절한다(런타임 API가 127.0.0.1에 떠 있다 —
RUNTIME_DESIGN §10.1).

HTML은 stdlib HTMLParser로 본문 텍스트만 추출한다(script/style 제거) — 원문
마크업을 그대로 돌려주면 소형 모델 컨텍스트가 태그로 넘친다.
"""

from __future__ import annotations

import ipaddress
from html.parser import HTMLParser

from ...core.types import ToolSpec
from ..tool_dispatch import BuiltinTool

_MAX_FETCH_BYTES = 512 * 1024
_MAX_TEXT_CHARS = 40_000
_TIMEOUT_SECONDS = 20.0
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "pre"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:  # noqa: ARG002
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


def _reject_private_host(host: str) -> None:
    """SSRF guard: 로컬 런타임/사내망을 향한 요청을 거절한다. DNS 재바인딩까지
    막는 완전한 방어는 아니지만(개인용 로컬 에이전트 위협 모델), 명백한 표적 —
    127.0.0.1의 런타임 API, 라우터 관리페이지 — 은 이름/리터럴 수준에서 끊는다."""
    lowered = host.lower().rstrip(".")
    if lowered in ("localhost",) or lowered.endswith(".local") or lowered.endswith(".internal"):
        raise ValueError(f"refusing to fetch local/internal host: {host}")
    try:
        addr = ipaddress.ip_address(lowered.strip("[]"))
    except ValueError:
        return  # not an IP literal — public hostname
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
        raise ValueError(f"refusing to fetch private address: {host}")


def _make_fetch() -> BuiltinTool:
    async def fetch_url(args: dict) -> object:
        import httpx  # noqa: PLC0415 - optional dependency by design

        url = str(args.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        parsed = httpx.URL(url)
        _reject_private_host(parsed.host)

        async with httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS, follow_redirects=True, headers={"User-Agent": "rice-agent/1.0"}
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                # 리다이렉트 도착지도 같은 가드를 태운다.
                _reject_private_host(response.url.host)
                content_type = response.headers.get("content-type", "")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) >= _MAX_FETCH_BYTES:
                        break

        text = bytes(body).decode(response.charset_encoding or "utf-8", errors="replace")
        if "html" in content_type:
            extractor = _TextExtractor()
            extractor.feed(text)
            text = extractor.text()
        truncated = len(text) > _MAX_TEXT_CHARS or len(body) >= _MAX_FETCH_BYTES
        return {
            "url": str(response.url),
            "status": response.status_code,
            "content_type": content_type,
            "text": text[:_MAX_TEXT_CHARS],
            "truncated": truncated,
        }

    return fetch_url


def tools() -> list[tuple[ToolSpec, BuiltinTool]]:
    return [
        (
            ToolSpec(
                name="fetch_url",
                description=(
                    "Fetch a public web page (http/https) and return its readable text "
                    "(HTML is stripped to plain text). Use for looking up documentation, "
                    "articles, or any URL the user mentions."
                ),
                parameters={
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "Absolute http(s) URL to fetch."}},
                    "required": ["url"],
                },
                source="builtin",
            ),
            _make_fetch(),
        ),
    ]
