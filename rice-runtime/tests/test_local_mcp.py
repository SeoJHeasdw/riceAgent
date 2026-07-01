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
    LocalMcpServerExistsError,
    add_local_mcp_server,
    connector_id_to_name,
    load_local_mcp_servers,
    remove_local_mcp_server,
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

    # local_server_exists — permission 라우트의 "존재 확인" 가드가 쓴다(연결 라우트).
    assert svc.local_server_exists("local:mock") is True
    assert svc.local_server_exists("local:unknown") is False


# ── .mcp.json 쓰기(add/remove) — McpRegisterModal "등록"이 실제로 여기 떨어진다 ──────

class TestLocalMcpConfigWrite:
    def test_add_creates_file_and_entry(self, tmp_path: Path):
        cfg = tmp_path / ".mcp.json"
        add_local_mcp_server(cfg, "fs", transport="stdio", command="npx", args=("-y", "pkg"))
        servers = load_local_mcp_servers(cfg)
        assert len(servers) == 1
        assert servers[0].name == "fs"
        assert servers[0].command == "npx"
        assert servers[0].args == ("-y", "pkg")

    def test_add_http_with_headers(self, tmp_path: Path):
        cfg = tmp_path / ".mcp.json"
        add_local_mcp_server(
            cfg, "web", transport="http", url="http://localhost:3000/mcp", headers={"X-Key": "v"}
        )
        servers = load_local_mcp_servers(cfg)
        assert servers[0].transport == "http"
        assert servers[0].url == "http://localhost:3000/mcp"
        assert servers[0].headers == {"X-Key": "v"}

    def test_add_preserves_existing_servers(self, tmp_path: Path):
        cfg = tmp_path / ".mcp.json"
        add_local_mcp_server(cfg, "a", transport="stdio", command="cmd-a")
        add_local_mcp_server(cfg, "b", transport="stdio", command="cmd-b")
        assert {s.name for s in load_local_mcp_servers(cfg)} == {"a", "b"}

    def test_add_duplicate_name_raises(self, tmp_path: Path):
        cfg = tmp_path / ".mcp.json"
        add_local_mcp_server(cfg, "fs", transport="stdio", command="npx")
        with pytest.raises(LocalMcpServerExistsError):
            add_local_mcp_server(cfg, "fs", transport="stdio", command="other")

    def test_add_to_corrupt_file_fails_closed_without_overwriting(self, tmp_path: Path):
        cfg = tmp_path / ".mcp.json"
        cfg.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ValueError):
            add_local_mcp_server(cfg, "fs", transport="stdio", command="npx")
        assert cfg.read_text(encoding="utf-8") == "{not valid json"  # 안 건드렸다

    def test_remove_existing_returns_true(self, tmp_path: Path):
        cfg = tmp_path / ".mcp.json"
        add_local_mcp_server(cfg, "fs", transport="stdio", command="npx")
        assert remove_local_mcp_server(cfg, "fs") is True
        assert load_local_mcp_servers(cfg) == []

    def test_remove_missing_returns_false_idempotent(self, tmp_path: Path):
        cfg = tmp_path / ".mcp.json"
        assert remove_local_mcp_server(cfg, "nope") is False

    def test_write_is_atomic_no_tmp_file_left_behind(self, tmp_path: Path):
        cfg = tmp_path / ".mcp.json"
        add_local_mcp_server(cfg, "fs", transport="stdio", command="npx")
        assert not (tmp_path / ".mcp.json.tmp").exists()


# ── ConnectorService: 등록/제거/테스트 — .mcp.json 을 실제로 건드리는 경로 ─────────

class TestLocalMcpServiceRegistration:
    @pytest.mark.asyncio
    async def test_register_writes_file_warms_and_flows_into_list_tools_and_invoke(
        self, tmp_path: Path, mock_server_path: Path
    ):
        from rice_runtime.connector.service import ConnectorService
        from rice_runtime.core.paths import RicePaths
        from rice_runtime.core.storage.sqlite import Database

        root = tmp_path / "rice"
        root.mkdir()
        db = Database(":memory:").connect()
        svc = ConnectorService(db, RicePaths(root=root))

        info = await svc.register_local_mcp_server(
            "mock", transport="stdio", command=sys.executable, args=(str(mock_server_path),)
        )
        assert info["connector_id"] == "local:mock"
        assert info["running"] is True
        assert {t["name"] for t in info["tools"]} == {"echo"}

        # 실제로 .mcp.json 에 쓰였다 — 다음 부팅에도 살아남는다.
        assert [s.name for s in load_local_mcp_servers(root / ".mcp.json")] == ["mock"]

        # 기존 .mcp.json 서버와 동일한 경로로 즉시 흐른다(재시작 없이).
        assert {t.name for t in svc.list_tools()} == {"echo"}
        res = await svc.invoke(ToolCall(tool_name="echo", arguments={"text": "hi"}, id="c1"))
        assert res.ok is True and res.result["content"][0]["text"] == "hi"

    @pytest.mark.asyncio
    async def test_register_duplicate_name_raises_conflict(self, tmp_path: Path, mock_server_path: Path):
        from rice_runtime.connector.service import ConnectorService
        from rice_runtime.core.errors import ConflictError
        from rice_runtime.core.paths import RicePaths
        from rice_runtime.core.storage.sqlite import Database

        root = tmp_path / "rice"
        root.mkdir()
        svc = ConnectorService(Database(":memory:").connect(), RicePaths(root=root))
        await svc.register_local_mcp_server(
            "mock", transport="stdio", command=sys.executable, args=(str(mock_server_path),)
        )
        with pytest.raises(ConflictError):
            await svc.register_local_mcp_server("mock", transport="stdio", command="whatever")

    @pytest.mark.asyncio
    async def test_register_only_warms_the_new_server(self, tmp_path: Path, mock_server_path: Path):
        """다른 stdio 서버가 이미 있어도 등록 시 걔는 안 건드린다(전체 재-spawn 아님)."""
        from rice_runtime.connector.service import ConnectorService
        from rice_runtime.core.paths import RicePaths
        from rice_runtime.core.storage.sqlite import Database

        root = tmp_path / "rice"
        root.mkdir()
        (root / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"other": {"command": "this-cmd-does-not-exist-xyz"}}}),
            encoding="utf-8",
        )
        svc = ConnectorService(Database(":memory:").connect(), RicePaths(root=root))
        await svc.register_local_mcp_server(
            "mock", transport="stdio", command=sys.executable, args=(str(mock_server_path),)
        )
        by_name = {s["name"]: s for s in svc.local_servers_info()}
        assert by_name["mock"]["running"] is True
        assert by_name["other"]["running"] is False  # 존재하지 않는 커맨드라 warm 안 됐다(=건드리지도 않았다)

    @pytest.mark.asyncio
    async def test_unregister_removes_file_entry_cache_and_permissions(
        self, tmp_path: Path, mock_server_path: Path
    ):
        from rice_runtime.connector.service import ConnectorService
        from rice_runtime.core.paths import RicePaths
        from rice_runtime.core.storage.sqlite import Database

        root = tmp_path / "rice"
        root.mkdir()
        svc = ConnectorService(Database(":memory:").connect(), RicePaths(root=root))
        await svc.register_local_mcp_server(
            "mock", transport="stdio", command=sys.executable, args=(str(mock_server_path),)
        )
        svc.set_tool_permission("local:mock", "echo", "block")

        assert svc.unregister_local_mcp_server("mock") is True
        assert load_local_mcp_servers(root / ".mcp.json") == []
        assert svc.list_tools() == []
        assert svc.tool_permissions("local:mock") == {}
        assert svc.unregister_local_mcp_server("mock") is False  # idempotent

    @pytest.mark.asyncio
    async def test_test_connection_succeeds_without_touching_mcp_json(
        self, tmp_path: Path, mock_server_path: Path
    ):
        """연결 테스트는 부작용이 없어야 한다 — .mcp.json 이 생기면 안 된다."""
        from rice_runtime.connector.service import ConnectorService
        from rice_runtime.core.paths import RicePaths
        from rice_runtime.core.storage.sqlite import Database

        root = tmp_path / "rice"
        root.mkdir()
        svc = ConnectorService(Database(":memory:").connect(), RicePaths(root=root))
        result = await svc.test_local_mcp_server(
            transport="stdio", command=sys.executable, args=(str(mock_server_path),)
        )
        assert result == {"ok": True, "error": None, "tool_count": 1, "tools": ["echo"]}
        assert not (root / ".mcp.json").exists()

    @pytest.mark.asyncio
    async def test_test_connection_reports_failure(self, tmp_path: Path):
        from rice_runtime.connector.service import ConnectorService
        from rice_runtime.core.paths import RicePaths
        from rice_runtime.core.storage.sqlite import Database

        svc = ConnectorService(Database(":memory:").connect(), RicePaths(root=tmp_path / "rice"))
        result = await svc.test_local_mcp_server(transport="stdio", command="this-cmd-does-not-exist-xyz")
        assert result["ok"] is False and result["error"]


# ── 로컬 http MCP: RemoteMcpClient 로 라우팅되는지(무인증) — 경계에서 모킹 ──────────

class TestLocalMcpHttpTransport:
    @pytest.mark.asyncio
    async def test_warm_local_uses_remote_mcp_client_for_http_servers(self, tmp_path: Path):
        from unittest.mock import AsyncMock, patch

        from rice_runtime.connector.service import ConnectorService
        from rice_runtime.core.paths import RicePaths
        from rice_runtime.core.storage.sqlite import Database
        from rice_runtime.core.types import ToolSpec

        root = tmp_path / "rice"
        root.mkdir()
        (root / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"web": {"url": "http://localhost:4000/mcp", "headers": {"X-Key": "v"}}}}),
            encoding="utf-8",
        )
        svc = ConnectorService(Database(":memory:").connect(), RicePaths(root=root))
        fake_spec = ToolSpec(name="web_tool", description="", parameters={}, source="connector", owner_id="local:web")

        with patch("rice_runtime.connector.service.RemoteMcpClient") as MockClient:
            instance = MockClient.return_value
            instance.list_tools = AsyncMock(return_value=[fake_spec])
            n = await svc.warm_local()

        assert n == 1
        MockClient.assert_called_once_with("http://localhost:4000/mcp", extra_headers={"X-Key": "v"})
        instance.set_owner.assert_called_once_with("local:web")
        instance.list_tools.assert_awaited_once_with("")  # 무인증
        assert {t.name for t in svc.list_tools()} == {"web_tool"}

    @pytest.mark.asyncio
    async def test_invoke_routes_http_local_tool_to_remote_mcp_client(self, tmp_path: Path):
        from unittest.mock import AsyncMock, patch

        from rice_runtime.connector.service import ConnectorService
        from rice_runtime.core.paths import RicePaths
        from rice_runtime.core.storage.sqlite import Database
        from rice_runtime.core.types import ToolResult, ToolSpec

        root = tmp_path / "rice"
        root.mkdir()
        (root / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"web": {"url": "http://localhost:4000/mcp"}}}),
            encoding="utf-8",
        )
        svc = ConnectorService(Database(":memory:").connect(), RicePaths(root=root))
        fake_spec = ToolSpec(name="web_tool", description="", parameters={}, source="connector", owner_id="local:web")

        with patch("rice_runtime.connector.service.RemoteMcpClient") as MockClient:
            instance = MockClient.return_value
            instance.list_tools = AsyncMock(return_value=[fake_spec])
            instance.call_tool = AsyncMock(return_value=ToolResult("c1", ok=True, result={"done": True}))
            await svc.warm_local()
            res = await svc.invoke(ToolCall(tool_name="web_tool", arguments={"a": 1}, id="c1"))

        assert res.ok is True and res.result == {"done": True}
        instance.call_tool.assert_awaited_once_with("web_tool", {"a": 1}, "", "c1")
