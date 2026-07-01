"""`.mcp.json` 리더/라이터 — 로컬 MCP 서버 정의 (Claude/Claude Code 표준 포맷).

포맷:
    {
      "mcpServers": {
        "<name>": { "command": "npx", "args": ["-y", "pkg"], "env": {"K":"V"} },  # stdio
        "<name>": { "url": "http://localhost:3000/mcp", "headers": {...} }         # http
      }
    }

stdio(command 기반)·http(url 기반) 둘 다 실제 실행한다(stdio→StdioMcpClient, http→
RemoteMcpClient — service.py). 파일이 없거나 깨져도 **읽기**는 조용히 빈 목록을 반환한다
(fail-safe — CONVENTIONS §7). **쓰기**(add/remove)는 반대로 파일이 있는데 깨져 있으면
fail-closed(ValueError) 한다 — 사용자가 손으로 편집했을 수 있는 파일을 조용히 덮어쓰지
않기 위해서다. 쓰기는 임시파일→rename 으로 원자적이다(중간에 죽어도 파일이 안 깨짐).
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
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def connector_id(self) -> str:
        """로컬 MCP 서버의 합성 connector_id (remote 커넥터와 네임스페이스 분리)."""
        return f"local:{self.name}"


def _parse_server(name: str, raw: dict) -> LocalMcpServer | None:
    if not isinstance(raw, dict):
        return None
    url = raw.get("url")
    if url:
        headers = raw.get("headers") or {}
        return LocalMcpServer(
            name=name, transport="http", url=str(url),
            headers={str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else {},
        )
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


def _read_raw_for_write(path: Path) -> dict:
    """쓰기 전 현재 내용을 읽는다. 없으면 새로 만들 빈 dict, 있는데 깨져 있으면
    ValueError(fail-closed — 조용히 덮어쓰지 않는다)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON — fix it by hand before registering a server") from exc
    return raw if isinstance(raw, dict) else {}


def _write_raw(path: Path, raw: dict) -> None:
    """임시파일→rename 으로 원자적 쓰기(POSIX)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


class LocalMcpServerExistsError(ValueError):
    """add_local_mcp_server 가 이미 등록된 이름에 호출됐을 때."""


def add_local_mcp_server(
    mcp_config_path: Path,
    name: str,
    *,
    transport: str,  # "stdio" | "http"
    command: str = "",
    args: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
    url: str = "",
    headers: dict[str, str] | None = None,
) -> None:
    """`.mcp.json` 의 `mcpServers` 에 새 항목을 추가한다. 이름이 이미 있으면
    LocalMcpServerExistsError. 파일이 없으면 새로 만든다."""
    raw = _read_raw_for_write(mcp_config_path)
    servers = raw.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        raw["mcpServers"] = servers
    if name in servers:
        raise LocalMcpServerExistsError(f"local mcp server {name!r} already registered")

    if transport == "stdio":
        entry: dict = {"command": command}
        if args:
            entry["args"] = list(args)
        if env:
            entry["env"] = dict(env)
    elif transport == "http":
        entry = {"url": url}
        if headers:
            entry["headers"] = dict(headers)
    else:
        raise ValueError(f"unknown transport {transport!r} — expected 'stdio' or 'http'")

    servers[name] = entry
    _write_raw(mcp_config_path, raw)


def remove_local_mcp_server(mcp_config_path: Path, name: str) -> bool:
    """`.mcp.json` 에서 항목을 제거한다. 있었으면 True, 없었으면 False(no-op, idempotent)."""
    raw = _read_raw_for_write(mcp_config_path)
    servers = raw.get("mcpServers")
    if not isinstance(servers, dict) or name not in servers:
        return False
    del servers[name]
    _write_raw(mcp_config_path, raw)
    return True
