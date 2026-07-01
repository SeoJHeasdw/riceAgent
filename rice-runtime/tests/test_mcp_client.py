"""RemoteMcpClient — 로컬 HTTP MCP 지원을 위해 추가한 동작(무인증 + extra_headers +
테스트 transport seam)을 실제 JSON-RPC 왕복으로 검증한다. discovery.py 와 같은
`httpx.MockTransport` 철학 — 네트워크는 없지만 실제 프로토콜 로직을 거친다.
"""
from __future__ import annotations

import json

import httpx
import pytest

from rice_runtime.connector.mcp.client import RemoteMcpClient


def _transport(routes: dict[str, httpx.Response]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(str(request.url), httpx.Response(404))

    return httpx.MockTransport(handler)


def _tools_list_response(names: list[str]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": "tools-list",
            "result": {"tools": [{"name": n, "description": "", "inputSchema": {}} for n in names]},
        },
    )


class TestRemoteMcpClientNoAuth:
    @pytest.mark.asyncio
    async def test_empty_access_token_omits_authorization_header(self):
        """로컬 무인증 HTTP MCP 서버(access_token="") 는 Authorization 헤더 없이 나가야 한다."""
        seen_headers: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen_headers.update(request.headers)
            if json.loads(request.content).get("method") == "initialize":
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": "init", "result": {}})
            return _tools_list_response(["echo"])

        client = RemoteMcpClient("http://localhost:9999/mcp", transport=httpx.MockTransport(handler))
        client.set_owner("local:probe")
        tools = await client.list_tools("")
        assert {t.name for t in tools} == {"echo"}
        assert "authorization" not in seen_headers

    @pytest.mark.asyncio
    async def test_nonempty_access_token_sends_bearer_header(self):
        seen_headers: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen_headers.update(request.headers)
            if json.loads(request.content).get("method") == "initialize":
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": "init", "result": {}})
            return _tools_list_response(["gmail_search"])

        client = RemoteMcpClient("https://mcp.example.com/mcp", transport=httpx.MockTransport(handler))
        client.set_owner("c1")
        await client.list_tools("real-token")
        assert seen_headers.get("authorization") == "Bearer real-token"

    @pytest.mark.asyncio
    async def test_extra_headers_are_merged_into_every_request(self):
        """.mcp.json 의 headers(로컬 서버용 고정 헤더)가 실제로 실려 나가야 한다."""
        seen_headers: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen_headers.update(request.headers)
            if json.loads(request.content).get("method") == "initialize":
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": "init", "result": {}})
            return _tools_list_response([])

        client = RemoteMcpClient(
            "http://localhost:4000/mcp",
            extra_headers={"X-Api-Key": "local-dev-key"},
            transport=httpx.MockTransport(handler),
        )
        client.set_owner("local:probe")
        await client.list_tools("")
        assert seen_headers.get("x-api-key") == "local-dev-key"

    @pytest.mark.asyncio
    async def test_call_tool_roundtrip_via_mock_transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if body.get("method") == "initialize":
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": "init", "result": {}})
            if body.get("method") == "tools/call":
                return httpx.Response(
                    200,
                    json={"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": "ok"}]}},
                )
            return httpx.Response(404)

        client = RemoteMcpClient("http://localhost:4000/mcp", transport=httpx.MockTransport(handler))
        client.set_owner("local:probe")
        res = await client.call_tool("echo", {"text": "hi"}, "", "call_1")
        assert res.ok is True
        assert res.result["content"][0]["text"] == "ok"
