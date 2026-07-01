"""`.mcp.json` 리더 — 로컬 MCP 서버 정의 (Claude/Claude Code 표준 포맷).

포맷:
    {
      "mcpServers": {
        "<name>": { "command": "npx", "args": ["-y", "pkg"], "env": {"K":"V"} },  # stdio
        "<name>": { "url": "http://localhost:3000/mcp", "headers": {...} }         # http
      }
    }

이 프로토타입은 **stdio**(command 기반) 서버만 실제 실행한다. url 항목은 파싱은 하되
transport="http" 로 표시만 하고 실행은 원격 커넥터 경로에서 다룬다(향후).
파일이 없거나 깨져도 조용히 빈 목록을 반환한다(fail-safe — CONVENTIONS §7).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("rice.connector.local")


@dataclass(frozen=True)
class LocalMcpServer:
    """`.mcp.json` 의 서버 한 항목 (정규화)."""

    name: str
    transport: str  # "stdio" | "http"
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""

    @property
    def connector_id(self) -> str:
        """로컬 MCP 서버의 합성 connector_id (remote 커넥터와 네임스페이스 분리)."""
        return f"local:{self.name}"


def _parse_server(name: str, raw: dict) -> LocalMcpServer | None:
    if not isinstance(raw, dict):
        return None
    url = raw.get("url")
    if url:
        return LocalMcpServer(name=name, transport="http", url=str(url))
    command = raw.get("command")
    if command:
        args = raw.get("args") or []
        env = raw.get("env") or {}
        return LocalMcpServer(
            name=name,
            transport="stdio",
            command=str(command),
            args=tuple(str(a) for a in args),
            env={str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
        )
    log.debug("mcp server %r has neither url nor command — skipped", name)
    return None


def load_local_mcp_servers(mcp_config_path: Path) -> list[LocalMcpServer]:
    """`.mcp.json` 을 읽어 LocalMcpServer 목록을 반환한다. 실패 시 []."""
    try:
        raw = json.loads(mcp_config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("failed to read %s: %s", mcp_config_path, exc)
        return []

    servers_raw = raw.get("mcpServers") if isinstance(raw, dict) else None
    if not isinstance(servers_raw, dict):
        return []

    servers: list[LocalMcpServer] = []
    for name, entry in servers_raw.items():
        parsed = _parse_server(str(name), entry)
        if parsed is not None:
            servers.append(parsed)
    return servers


def connector_id_to_name(connector_id: str) -> str | None:
    """`local:<name>` → `<name>`. 로컬 id 가 아니면 None."""
    prefix = "local:"
    return connector_id[len(prefix):] if connector_id.startswith(prefix) else None
