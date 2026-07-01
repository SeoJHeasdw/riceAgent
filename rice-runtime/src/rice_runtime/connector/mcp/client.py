"""원격 MCP 서버 클라이언트 (Streamable HTTP transport, JSON-RPC 2.0).

이 클라이언트는 **어떤 도구도 하드코딩하지 않는다.** 연결된 connector 의 원격 MCP
서버에서 `tools/list` 로 도구 목록을 동적으로 받아 ToolSpec 으로 변환하고,
`tools/call` 로 실행을 위임한다. 도구가 늘어나도 코드 변경이 필요 없다.

전송 규약 (MCP Streamable HTTP):
    POST <endpoint>
    Content-Type: application/json
    Accept: application/json, text/event-stream
    Authorization: Bearer <access_token>

  - 서버는 응답을 `application/json` 또는 `text/event-stream`(SSE) 중 하나로 준다.
    양쪽 모두 파싱한다(doc 의 순수 `resp.json()` 은 SSE 를 깨뜨린다).
  - 세션형 서버는 `initialize` 핸드셰이크 후 `Mcp-Session-Id` 헤더를 요구한다.
    핸드셰이크는 best-effort 로 시도하고, 세션 id 가 오면 이후 요청에 붙인다.

인증은 connector 가 이미 보유한 OAuth Bearer 토큰을 그대로 재사용한다
(RUNTIME_DESIGN §9.3 — 자격증명은 keychain seam 뒤에 있고, service.py 가 주입).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ...core.types import ToolResult, ToolSpec

log = logging.getLogger("rice.connector.mcp")

# MCP 프로토콜 버전 (Streamable HTTP). 서버가 다른 버전을 협상하면 그대로 수용한다.
_MCP_PROTOCOL_VERSION = "2025-06-18"

_CLIENT_INFO = {"name": "rice-runtime", "version": "0.1.0"}


class RemoteMcpClient:
    """단일 원격 MCP 서버와 통신하는 클라이언트.

    connector 하나당 한 인스턴스를 쓴다. `set_owner()` 로 노출 도구의 owner_id
    (connector_id)를 지정하면 dispatcher 가 invoke 를 올바른 connector 로 라우팅한다.
    """

    def __init__(self, endpoint: str, *, extra_headers: dict[str, str] | None = None, transport=None) -> None:
        self._endpoint = endpoint
        self._owner_id: str = ""
        self._session_id: str | None = None
        self._initialized = False
        # 로컬 HTTP MCP 서버(.mcp.json 의 headers) 처럼 Bearer 토큰 외에 고정 헤더가
        # 필요한 경우용. 원격(OAuth) 커넥터는 None.
        self._extra_headers = extra_headers or {}
        # 테스트 전용 seam(discovery.py 와 같은 패턴) — httpx.MockTransport 주입.
        # 운영 코드는 넘기지 않는다(기본 None → 실제 네트워크).
        self._transport = transport

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def set_owner(self, connector_id: str) -> None:
        """노출 ToolSpec 에 붙일 owner_id(connector_id)를 설정한다.

        owner 가 바뀌면 세션 상태를 초기화한다(다른 계정 → 다른 세션).
        """
        if connector_id != self._owner_id:
            self._owner_id = connector_id
            self._session_id = None
            self._initialized = False

    # ── public API ────────────────────────────────────────────────────────────
    async def list_tools(self, access_token: str) -> list[ToolSpec]:
        """`tools/list` → ToolSpec 목록. 캐싱은 하지 않는다(호출자 책임)."""
        await self._ensure_initialized(access_token)
        resp = await self._rpc("tools/list", {}, access_token, rpc_id="tools-list")
        result = _rpc_result(resp)
        tools_raw: list[dict] = result.get("tools", []) if isinstance(result, dict) else []
        return [self._raw_to_spec(t) for t in tools_raw if isinstance(t, dict) and t.get("name")]

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any], access_token: str, call_id: str
    ) -> ToolResult:
        """`tools/call` → ToolResult. 도구 실패(error)도 예외 대신 ok=False 로 정규화."""
        await self._ensure_initialized(access_token)
        resp = await self._rpc(
            "tools/call",
            {"name": tool_name, "arguments": arguments or {}},
            access_token,
            rpc_id=f"call-{call_id}",
        )
        error = resp.get("error")
        if error:
            return ToolResult(call_id, ok=False, error=_stringify(error))
        result = resp.get("result", {})
        # MCP tools/call 규약: result.isError=True 면 도구 실행 자체가 실패한 것.
        if isinstance(result, dict) and result.get("isError"):
            return ToolResult(call_id, ok=False, error=_stringify(result.get("content", result)))
        return ToolResult(call_id, ok=True, result=result)

    # ── internals ─────────────────────────────────────────────────────────────
    async def _ensure_initialized(self, access_token: str) -> None:
        """`initialize` 핸드셰이크를 1회 수행한다(세션형 서버 대응).

        서버가 초기화를 요구하지 않아도(무상태) 무해하다. 실패는 치명적이지 않으므로
        경고만 남기고 계속 진행한다 — 무상태 서버는 tools/list 가 바로 동작한다.
        """
        if self._initialized:
            return
        self._initialized = True  # 재시도 폭주 방지: 성공/실패와 무관하게 1회만 시도
        try:
            resp = await self._rpc(
                "initialize",
                {
                    "protocolVersion": _MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": _CLIENT_INFO,
                },
                access_token,
                rpc_id="init",
                capture_session=True,
            )
            if resp.get("error"):
                log.debug("mcp initialize returned error (stateless server?): %s", resp["error"])
                return
            # 세션형 서버는 initialized 알림을 기대한다(응답 본문 없음 — 202).
            await self._notify("notifications/initialized", access_token)
        except Exception as exc:  # noqa: BLE001 — 무상태 서버면 정상 동작하므로 삼킨다
            log.debug("mcp initialize skipped: %s", exc)

    async def _rpc(
        self,
        method: str,
        params: dict[str, Any],
        access_token: str,
        *,
        rpc_id: str,
        capture_session: bool = False,
    ) -> dict[str, Any]:
        """JSON-RPC 요청 1건을 보내고 응답 dict({result|error})를 반환한다."""
        import httpx  # lazy import (connectors extra)

        payload = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}
        async with httpx.AsyncClient(transport=self._transport) as client:
            resp = await client.post(
                self._endpoint,
                json=payload,
                headers=self._headers(access_token),
                timeout=60.0,
            )
            resp.raise_for_status()
            if capture_session:
                sid = resp.headers.get("mcp-session-id")
                if sid:
                    self._session_id = sid
            return _parse_rpc_response(resp)

    async def _notify(self, method: str, access_token: str) -> None:
        """JSON-RPC 알림(id 없음)을 보낸다. 응답 본문을 기대하지 않는다."""
        import httpx

        payload = {"jsonrpc": "2.0", "method": method, "params": {}}
        async with httpx.AsyncClient(transport=self._transport) as client:
            resp = await client.post(
                self._endpoint,
                json=payload,
                headers=self._headers(access_token),
                timeout=30.0,
            )
            # 202 Accepted 가 정상. 다른 코드여도 알림이므로 치명적이지 않다.
            if resp.status_code >= 400:
                log.debug("mcp notify %s -> %s", method, resp.status_code)

    def _headers(self, access_token: str) -> dict[str, str]:
        headers = {
            **self._extra_headers,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": _MCP_PROTOCOL_VERSION,
        }
        if access_token:  # 로컬 무인증 HTTP MCP 서버는 access_token="" — 빈 Bearer 헤더를 안 보낸다.
            headers["Authorization"] = f"Bearer {access_token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _raw_to_spec(self, raw: dict) -> ToolSpec:
        """MCP `tools/list` 항목 → ToolSpec.

        connector 도구는 외부 머신에 접근하므로 항상 승인 대상이다
        (source="connector", requires_approval=True — RUNTIME_DESIGN §5).
        """
        return ToolSpec(
            name=raw["name"],
            description=raw.get("description", ""),
            parameters=raw.get("inputSchema") or {"type": "object", "properties": {}},
            source="connector",
            owner_id=self._owner_id or None,
            requires_approval=True,
        )


# ── module-level helpers ────────────────────────────────────────────────────
def _parse_rpc_response(resp: Any) -> dict[str, Any]:
    """application/json 또는 text/event-stream 응답에서 JSON-RPC 객체를 뽑는다."""
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        return _parse_sse(resp.text)
    if not resp.text.strip():
        return {}
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError):
        # 일부 서버는 content-type 을 잘못 붙이고 SSE 를 보낸다 — 폴백으로 SSE 파싱.
        return _parse_sse(resp.text)


def _parse_sse(body: str) -> dict[str, Any]:
    """SSE 프레임(`data: {...}`)에서 마지막 JSON 페이로드를 파싱한다."""
    last: dict[str, Any] = {}
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if not data or data == "[DONE]":
            continue
        try:
            parsed = json.loads(data)
            if isinstance(parsed, dict):
                last = parsed
        except json.JSONDecodeError:
            continue
    return last


def _rpc_result(resp: dict[str, Any]) -> Any:
    """JSON-RPC 응답에서 result 를 꺼낸다. error 면 예외로 승격."""
    if resp.get("error"):
        raise RuntimeError(_stringify(resp["error"]))
    return resp.get("result", {})


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)
