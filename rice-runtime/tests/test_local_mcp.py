"""로컬 MCP: `.mcp.json` 리더 + stdio 트랜스포트 왕복 테스트.

stdio 클라이언트는 실제 서브프로세스(파이썬으로 만든 미니 MCP 서버)를 띄워 initialize →
tools/list → tools/call 왕복을 검증한다. 외부 의존성 없이 로컬에서 완결된다.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

from rice_runtime.connector.local.config import (
    connector_id_to_name,
    load_local_mcp_servers,
)
from rice_runtime.connector.mcp.stdio_client import StdioMcpClient
from rice_runtime.core.types import ToolCall

# ── 미니 MCP stdio 서버 (fixture) — 개행 구분 JSON-RPC 로 initialize/tools 응답 ──
_MOCK_SERVER = r'''
import json, sys
def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    mid = msg.get("id"); method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc":"2.0","id":mid,"result":{"protocolVersion":"2025-06-18",
              "capabilities":{},"serverInfo":{"name":"mock","version":"0.0.1"}}})
    elif method == "notifications/initialized":
        pass  # 알림 — 응답 없음
    elif method == "tools/list":
        send({"jsonrpc":"2.0","id":mid,"result":{"tools":[
            {"name":"echo","description":"echo back","inputSchema":
             {"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}}]}})
    elif method == "tools/call":
        args = (msg.get("params") or {}).get("arguments") or {}
        send({"jsonrpc":"2.0","id":mid,"result":{"content":[{"type":"text","text":args.get("text","")}]}})
    else:
        send({"jsonrpc":"2.0","id":mid,"error":{"code":-32601,"message":"method not found"}})
'''


@pytest.fixture()
def mock_server_path(tmp_path: Path) -> Path:
    p = tmp_path / "mock_mcp_server.py"
    p.write_text(_MOCK_SERVER, encoding="utf-8")
    return p


# ── stdio 트랜스포트 왕복 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stdio_list_tools_roundtrip(mock_server_path: Path):
    client = StdioMcpClient(sys.executable, (str(mock_server_path),), owner_id="local:mock")
    tools = await client.list_tools()
    assert len(tools) == 1
    t = tools[0]
    assert t.name == "echo"
    assert t.source == "connector"
    assert t.owner_id == "local:mock"
    assert t.requires_approval is True
    assert t.parameters["required"] == ["text"]


@pytest.mark.asyncio
async def test_stdio_call_tool_roundtrip(mock_server_path: Path):
    client = StdioMcpClient(sys.executable, (str(mock_server_path),), owner_id="local:mock")
    call = ToolCall(tool_name="echo", arguments={"text": "hi rice"}, id="call_1")
    res = await client.call_tool(call.tool_name, call.arguments, call.id)
    assert res.ok is True
    assert res.result["content"][0]["text"] == "hi rice"


@pytest.mark.asyncio
async def test_stdio_bad_command_returns_error_result():
    client = StdioMcpClient("this-cmd-does-not-exist-xyz", (), owner_id="local:x")
    # list_tools 는 예외를 던질 수 있고, call_tool 은 ok=False 로 정규화한다.
    res = await client.call_tool("echo", {}, "call_2")
    assert res.ok is False and res.error


# ── .mcp.json 리더 ─────────────────────────────────────────────────────────────

def test_load_local_mcp_servers_parses_stdio_and_http():
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / ".mcp.json"
        cfg.write_text(json.dumps({
            "mcpServers": {
                "fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                       "env": {"FOO": "bar"}},
                "remote": {"url": "http://localhost:9000/mcp"},
                "bad": {"nope": 1},
            }
        }), encoding="utf-8")
        servers = load_local_mcp_servers(cfg)
        by_name = {s.name: s for s in servers}
        assert set(by_name) == {"fs", "remote"}  # bad(정의 불완전) 제외
        assert by_name["fs"].transport == "stdio"
        assert by_name["fs"].command == "npx"
        assert by_name["fs"].args == ("-y", "@modelcontextprotocol/server-filesystem", "/tmp")
        assert by_name["fs"].env == {"FOO": "bar"}
        assert by_name["fs"].connector_id == "local:fs"
        assert by_name["remote"].transport == "http"
        assert by_name["remote"].url == "http://localhost:9000/mcp"


def test_load_local_mcp_servers_missing_file_is_empty():
    assert load_local_mcp_servers(Path("/nonexistent/.mcp.json")) == []


def test_connector_id_to_name():
    assert connector_id_to_name("local:fs") == "fs"
    assert connector_id_to_name("conn_123") is None


# ── ConnectorService 통합: 로컬 MCP 도구가 list_tools/invoke 로 흐르는가 ─────────

@pytest.mark.asyncio
async def test_connector_service_warms_and_invokes_local(tmp_path: Path, mock_server_path: Path):
    from rice_runtime.connector.service import ConnectorService
    from rice_runtime.core.paths import RicePaths
    from rice_runtime.core.storage.sqlite import Database

    root = tmp_path / "rice"
    root.mkdir()
    (root / ".mcp.json").write_text(json.dumps({
        "mcpServers": {"mock": {"command": sys.executable, "args": [str(mock_server_path)]}}
    }), encoding="utf-8")

    db = Database(":memory:").connect()
    svc = ConnectorService(db, RicePaths(root=root))

    # warm 전에는 로컬 도구가 없다.
    assert svc.list_tools() == []
    n = await svc.warm_local()
    assert n == 1

    tools = svc.list_tools()
    assert {t.name for t in tools} == {"echo"}
    assert tools[0].owner_id == "local:mock"

    # 에이전트가 이 도구를 호출하면 stdio 서버로 라우팅된다.
    res = await svc.invoke(ToolCall(tool_name="echo", arguments={"text": "yo"}, id="c1"))
    assert res.ok is True and res.result["content"][0]["text"] == "yo"

    # 기본 권한: echo 는 읽기류 접두사가 아니므로 approval(외부 실행 안전기본).
    assert svc.permission_for("local:mock", "echo") == "approval"
