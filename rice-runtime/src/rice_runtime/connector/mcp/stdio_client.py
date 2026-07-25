"""로컬 MCP 서버 stdio 트랜스포트 클라이언트 (JSON-RPC 2.0, newline-delimited).

로컬 MCP 서버는 `.mcp.json` 의 command/args 로 **서브프로세스**로 실행되고, stdin/stdout
으로 줄 단위(개행 구분) JSON-RPC 메시지를 주고받는다(MCP stdio transport 규약).

전략: **연산당 spawn(spawn-per-op)** — list_tools/call_tool 각각에 대해 프로세스를 띄우고
initialize 핸드셰이크 후 요청 1건을 처리하고 종료한다. 장기 프로세스 관리(read loop,
좀비, 교착)를 피해 단순·견고하게 간다. 도구 정의는 idempotent 하므로 매 호출 재초기화도
안전하다. (효율이 필요해지면 이후 persistent 세션으로 승격.)

owner_id 로 노출 ToolSpec 에 소유자(local:<name>)를 붙여 dispatcher 라우팅에 쓴다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from ...core.types import ToolResult, ToolSpec, coerce_tool_keywords

log = logging.getLogger("rice.connector.mcp.stdio")

_MCP_PROTOCOL_VERSION = "2025-06-18"
_CLIENT_INFO = {"name": "rice-runtime", "version": "0.1.0"}
_STARTUP_TIMEOUT = 20.0  # 서버 부팅(npx 등)이 느릴 수 있어 넉넉히


class StdioMcpError(RuntimeError):
    """stdio MCP 서버와의 통신 실패."""


class StdioMcpClient:
    """로컬 stdio MCP 서버 클라이언트 (spawn-per-op)."""

    def __init__(
        self,
        command: str,
        args: tuple[str, ...] = (),
        env: dict[str, str] | None = None,
        *,
        owner_id: str = "",
    ) -> None:
        self._command = command
        self._args = list(args)
        self._env = env or {}
        self._owner_id = owner_id

    # ── public API ────────────────────────────────────────────────────────────
    async def list_tools(self) -> list[ToolSpec]:
        result = await self._run_op("tools/list", {})
        tools_raw = result.get("tools", []) if isinstance(result, dict) else []
        return [self._raw_to_spec(t) for t in tools_raw if isinstance(t, dict) and t.get("name")]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any], call_id: str) -> ToolResult:
        try:
            result = await self._run_op("tools/call", {"name": tool_name, "arguments": arguments or {}})
        except Exception as exc:  # noqa: BLE001 — 실패는 ToolResult 로 정규화
            return ToolResult(call_id, ok=False, error=str(exc))
        if isinstance(result, dict) and result.get("isError"):
            return ToolResult(call_id, ok=False, error=_stringify(result.get("content", result)))
        return ToolResult(call_id, ok=True, result=result)

    # ── internals ─────────────────────────────────────────────────────────────
    async def _run_op(self, method: str, params: dict[str, Any]) -> Any:
        """프로세스를 띄워 initialize → method 1건을 처리하고 result 를 반환한다."""
        proc = await asyncio.create_subprocess_exec(
            self._command,
            *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **self._env},
        )
        try:
            # initialize 핸드셰이크
            await self._request(proc, "initialize", {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": _CLIENT_INFO,
            }, req_id=1)
            await self._notify(proc, "notifications/initialized")
            # 실제 요청
            resp = await self._request(proc, method, params, req_id=2)
            error = resp.get("error")
            if error:
                raise StdioMcpError(_stringify(error))
            return resp.get("result", {})
        finally:
            await self._shutdown(proc)

    async def _send(self, proc: asyncio.subprocess.Process, msg: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        await proc.stdin.drain()

    async def _notify(self, proc: asyncio.subprocess.Process, method: str) -> None:
        await self._send(proc, {"jsonrpc": "2.0", "method": method, "params": {}})

    async def _request(
        self, proc: asyncio.subprocess.Process, method: str, params: dict, *, req_id: int
    ) -> dict[str, Any]:
        """요청을 보내고 같은 id 의 응답을 읽어 반환한다. 알림/로그 라인은 건너뛴다."""
        await self._send(proc, {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        assert proc.stdout is not None
        while True:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=_STARTUP_TIMEOUT)
            except asyncio.TimeoutError as exc:
                raise StdioMcpError(f"timeout waiting for {method} response") from exc
            if not line:  # EOF — 프로세스가 죽음
                stderr = b""
                if proc.stderr is not None:
                    try:
                        stderr = await asyncio.wait_for(proc.stderr.read(2048), timeout=1.0)
                    except asyncio.TimeoutError:
                        pass
                raise StdioMcpError(
                    f"stdio server exited before responding to {method}: {stderr.decode('utf-8', 'replace')[:500]}"
                )
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                continue  # 서버가 stdout 에 찍은 비-JSON 로그 — 무시
            if isinstance(msg, dict) and msg.get("id") == req_id:
                return msg
            # 다른 id/알림 → 계속 읽는다

    async def _shutdown(self, proc: asyncio.subprocess.Process) -> None:
        """stdin 을 닫고 프로세스를 정리한다(terminate → 필요 시 kill)."""
        try:
            if proc.stdin is not None and not proc.stdin.is_closing():
                proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

    def _raw_to_spec(self, raw: dict) -> ToolSpec:
        """MCP tools/list 항목 → ToolSpec (source=connector, 승인 필요 기본).

        `keywords`는 MCP 표준 밖의 선택적 확장(라우팅 전용 트리거 어휘) — 번들
        로컬 서버(connector/local/*)가 한국어 트리거를 싣는 통로다. 표준만 따르는
        서드파티 stdio 서버는 이 키가 없어 ()로 남는다(동작 변화 없음).
        """
        return ToolSpec(
            name=raw["name"],
            description=raw.get("description", ""),
            parameters=raw.get("inputSchema") or {"type": "object", "properties": {}},
            source="connector",
            owner_id=self._owner_id or None,
            requires_approval=True,
            keywords=coerce_tool_keywords(raw.get("keywords")),
        )


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)
