"""번들 로컬 Gmail MCP 서버 테스트.

- 프로토콜(initialize/tools/list/tools/call 프레이밍)은 **실제 StdioMcpClient 로
  서브프로세스를 spawn** 해 검증한다 — 런타임이 쓰는 코드 경로 그대로.
- Gmail REST 핸들러는 httpx.MockTransport 로 네트워크 없이 검증한다.
"""
from __future__ import annotations

import base64
import json
import sys
from email import message_from_bytes

import httpx
import pytest

from rice_runtime.connector.local.gmail_mcp import (
    TOOLS,
    GmailAuthError,
    GmailCredentialSource,
    handle_rpc_sync,
    handle_tool,
    handle_tool_call,
)
from rice_runtime.connector.mcp.stdio_client import StdioMcpClient


class _FakeCreds(GmailCredentialSource):
    def __init__(self, token: str = "tok") -> None:
        super().__init__()
        self._token = token

    async def access_token(self) -> str:  # type: ignore[override]
        return self._token


class _FailingCreds(GmailCredentialSource):
    async def access_token(self) -> str:  # type: ignore[override]
        raise GmailAuthError("No Gmail connector found. Connect Gmail in RICE first.")


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


# ── 프로토콜 계층 ──────────────────────────────────────────────────────────────

def test_initialize_and_tools_list_are_synchronous_protocol_responses():
    init = handle_rpc_sync({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init is not None and init["result"]["serverInfo"]["name"] == "rice-gmail-local"

    listing = handle_rpc_sync({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert listing is not None
    names = {t["name"] for t in listing["result"]["tools"]}
    assert names == {"gmail_list_labels", "gmail_search_messages", "gmail_get_message", "gmail_create_draft"}
    # 원격 gmail MCP 카탈로그와의 이름 충돌 방지 접두 규약.
    assert all(n.startswith("gmail_") for n in names)

    assert handle_rpc_sync({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    unknown = handle_rpc_sync({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
    assert unknown is not None and unknown["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_tool_call_failure_is_mcp_iserror_not_jsonrpc_error():
    """자격증명 없음은 프로토콜 오류가 아니라 도구 실패(isError) — 클라이언트 규약."""
    resp = await handle_tool_call(
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
         "params": {"name": "gmail_list_labels", "arguments": {}}},
        _FailingCreds(),
    )
    assert "error" not in resp
    assert resp["result"]["isError"] is True
    assert "Connect Gmail in RICE" in resp["result"]["content"][0]["text"]


# ── Gmail REST 핸들러 (MockTransport) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_messages_joins_listing_with_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer tok"
        if request.url.path.endswith("/messages"):
            assert request.url.params["q"] == "from:billing newer_than:7d"
            return httpx.Response(200, json={"messages": [{"id": "m1"}, {"id": "m2"}]})
        return httpx.Response(200, json={
            "id": request.url.path.rsplit("/", 1)[-1],
            "snippet": "invoice attached",
            "payload": {"headers": [
                {"name": "From", "value": "billing@example.com"},
                {"name": "Subject", "value": "Invoice #42"},
                {"name": "Date", "value": "Wed, 15 Jul 2026 09:00:00 +0900"},
            ]},
        })

    result = await handle_tool(
        "gmail_search_messages",
        {"query": "from:billing newer_than:7d", "max_results": 2},
        _FakeCreds(),
        transport=httpx.MockTransport(handler),
    )
    assert result["count"] == 2
    assert result["messages"][0] == {
        "id": "m1", "from": "billing@example.com", "subject": "Invoice #42",
        "date": "Wed, 15 Jul 2026 09:00:00 +0900", "snippet": "invoice attached",
    }


@pytest.mark.asyncio
async def test_get_message_decodes_multipart_plain_text_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "m1",
            "payload": {
                "mimeType": "multipart/alternative",
                "headers": [{"name": "Subject", "value": "청구서"}],
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64url("본문 텍스트입니다")}},
                    {"mimeType": "text/html", "body": {"data": _b64url("<b>html</b>")}},
                ],
            },
        })

    result = await handle_tool(
        "gmail_get_message", {"message_id": "m1"}, _FakeCreds(),
        transport=httpx.MockTransport(handler),
    )
    assert result["subject"] == "청구서"
    assert result["body"] == "본문 텍스트입니다"  # text/plain 우선, html 미포함


@pytest.mark.asyncio
async def test_create_draft_posts_valid_mime_and_never_sends():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/drafts")  # send 엔드포인트 아님
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "d1", "message": {"id": "m9"}})

    result = await handle_tool(
        "gmail_create_draft",
        {"to": "boss@example.com", "subject": "회신: 청구서", "body": "확인했습니다."},
        _FakeCreds(),
        transport=httpx.MockTransport(handler),
    )
    assert result == {"draft_id": "d1", "message_id": "m9"}
    mime = message_from_bytes(base64.urlsafe_b64decode(captured["message"]["raw"]))
    assert mime["To"] == "boss@example.com"
    assert "회신" in str(mime["Subject"]) or "=?utf-8" in str(mime["Subject"]).lower()


@pytest.mark.asyncio
async def test_api_http_error_body_is_preserved():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED"}})

    with pytest.raises(RuntimeError, match="HTTP 403.*PERMISSION_DENIED"):
        await handle_tool("gmail_list_labels", {}, _FakeCreds(), transport=httpx.MockTransport(handler))


# ── 통합: 실제 서브프로세스 + 런타임의 StdioMcpClient ──────────────────────────

@pytest.mark.asyncio
async def test_spawned_server_lists_tools_and_normalizes_auth_failure(tmp_path):
    """런타임이 쓰는 spawn-per-op 경로 그대로: tools/list 4종, 자격증명 없는 RICE_HOME
    에서 tools/call 은 ok=False + 재연결 안내로 정규화된다(크래시/JSON 오염 없음)."""
    client = StdioMcpClient(
        sys.executable,
        ("-m", "rice_runtime.connector.local.gmail_mcp"),
        env={"RICE_HOME": str(tmp_path)},
        owner_id="local:gmail-local",
    )
    specs = await client.list_tools()
    assert {s.name for s in specs} == {t["name"] for t in TOOLS}
    assert all(s.requires_approval for s in specs)

    result = await client.call_tool("gmail_list_labels", {}, "call-1")
    assert result.ok is False
    assert "Connect Gmail in RICE" in (result.error or "")
