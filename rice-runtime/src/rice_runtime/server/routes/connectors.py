"""Connectors (CONTRACTS §4) — ConnectorsScreen.

  GET    /connectors                        list (+ each connector's tool catalog + pending update)
  GET    /connectors/{id}                   detail (granted scopes)
  POST   /connectors                        begin OAuth (returns oauth_url)
  POST   /connectors/custom/discover        auto-discover OAuth metadata for an arbitrary MCP URL
  POST   /connectors/custom                 register a custom connector + begin OAuth
  GET    /connectors/local                  list .mcp.json servers + cached tool catalog
  POST   /connectors/local/refresh          re-spawn/probe every local server, refresh the cache
  POST   /connectors/local/test             test-connect a not-yet-registered local server (no side effect)
  POST   /connectors/local                  write a new server into .mcp.json + warm it
  DELETE /connectors/local/{name}           remove a server from .mcp.json
  POST   /connectors/{id}/tools/resync      force re-fetch + apply the remote MCP tool catalog
  POST   /connectors/{id}/tools/check       detect remote catalog changes (sets a pending update)
  POST   /connectors/{id}/tools/apply-update  adopt the pending catalog update
  PUT    /connectors/{id}/tools/permission  set a tool's call policy (allow/approval/block)
  DELETE /connectors/{id}                   disconnect
  GET    /connectors/{id}/status            connection status
  GET    /connectors/oauth/callback         OAuth redirect handler
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ...core.errors import NotFoundError
from ..composition_root import Container
from .deps import get_container

router = APIRouter(prefix="/connectors", tags=["connectors"])


class ConnectIn(BaseModel):
    connector_type: str  # registry.py supported_types() — "gmail" / "calendar" / "drive"


class ToolPermissionIn(BaseModel):
    tool_name: str
    permission: str  # 'allow' | 'approval' | 'block'


class CustomDiscoverIn(BaseModel):
    mcp_endpoint: str


class LocalMcpTestIn(BaseModel):
    transport: str  # "stdio" | "http"
    command: str = ""
    args: list[str] = []
    env: dict[str, str] = {}
    url: str = ""
    headers: dict[str, str] = {}


class LocalMcpRegisterIn(LocalMcpTestIn):
    name: str


class CustomConnectIn(BaseModel):
    display_name: str
    mcp_endpoint: str
    auth_url: str
    token_url: str
    registration_endpoint: str | None = None
    scopes: list[str] = []
    # 모달의 "직접 입력" 경로 — 있으면 동적 등록(RFC 7591)을 건너뛰고 그대로 쓴다.
    client_id: str | None = None
    client_secret: str | None = None


def _callback_page(title: str, message: str, ok: bool) -> HTMLResponse:
    """OAuth 콜백은 시스템 브라우저가 직접 연다 — 사람이 읽을 HTML 을 돌려준다.
    성공/실패에 따라 색만 다른 미니멀 페이지(자동 닫기 시도)."""
    color = "#198754" if ok else "#dc3545"
    icon = "✓" if ok else "✕"
    html = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>{title}</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font-family:-apple-system,system-ui,sans-serif;background:#f6f8fa;color:#1f2328;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:40px 48px;text-align:center;
box-shadow:0 1px 3px rgba(0,0,0,.06);max-width:420px}}
.badge{{width:56px;height:56px;border-radius:50%;background:{color};color:#fff;font-size:30px;
display:flex;align-items:center;justify-content:center;margin:0 auto 18px}}
h1{{font-size:18px;margin:0 0 8px}}p{{color:#57606a;font-size:14px;line-height:1.6;margin:0}}</style></head>
<body><div class="card"><div class="badge">{icon}</div><h1>{title}</h1><p>{message}</p></div>
<script>setTimeout(function(){{window.close()}},2500)</script></body></html>"""
    return HTMLResponse(content=html, status_code=200 if ok else 400)


def _tools_payload(c: Container, connector_id: str) -> list[dict]:
    """connector 의 도구 카탈로그(원격 MCP tools/list)를 UI 용으로 직렬화한다:
    [{name, requires_approval, permission}]. permission 은 저장값 또는 휴리스틱 기본값.
    구현체가 tools_for 를 지원하지 않으면(테스트/골격) 빈 목록."""
    tools_for = getattr(c.connectors, "tools_for", None)
    if tools_for is None:
        return []
    perm_fn = getattr(c.connectors, "permission_for", None)
    out: list[dict] = []
    for t in tools_for(connector_id):
        item = {"name": t.name, "requires_approval": t.requires_approval}
        if perm_fn is not None:
            item["permission"] = perm_fn(connector_id, t.name)
        out.append(item)
    return out


def _connector_payload(c: Container, info) -> dict:
    """ConnectorInfo → wire dict + 도구 카탈로그(tools) + 대기 중 업데이트(update)."""
    pending = getattr(c.connectors, "pending_update", None)
    update = pending(info.id) if pending is not None else None
    return {**info.__dict__, "tools": _tools_payload(c, info.id), "update": update}


@router.get("")
async def list_connectors(c: Container = Depends(get_container)):
    return {"success": True, "data": [_connector_payload(c, i) for i in c.connectors.list_connectors()]}


@router.get("/local")
async def list_local_mcp(c: Container = Depends(get_container)):
    """`.mcp.json` 에 선언된 로컬 MCP 서버 + (캐시된) 도구 목록. local-MCP 탭 실데이터."""
    info_fn = getattr(c.connectors, "local_servers_info", None)
    data = info_fn() if info_fn is not None else []
    return {"success": True, "data": data}


@router.post("/local/refresh")
async def refresh_local_mcp(c: Container = Depends(get_container)):
    """로컬 MCP 서버를 다시 spawn 해 도구 카탈로그를 갱신한다."""
    warm = getattr(c.connectors, "warm_local", None)
    count = await warm() if warm is not None else 0
    info_fn = getattr(c.connectors, "local_servers_info", None)
    data = info_fn() if info_fn is not None else []
    return {"success": True, "data": {"count": count, "servers": data}}


@router.post("/local/test")
async def test_local_mcp(body: LocalMcpTestIn, c: Container = Depends(get_container)):
    """등록 전 연결 확인 — **부작용 없음**(.mcp.json 안 씀). "등록" 모달의 필수 게이트."""
    test_fn = getattr(c.connectors, "test_local_mcp_server", None)
    if test_fn is None:
        return {"success": True, "data": {"ok": False, "error": "not supported", "tool_count": 0, "tools": []}}
    result = await test_fn(
        transport=body.transport, command=body.command, args=tuple(body.args),
        env=body.env, url=body.url, headers=body.headers,
    )
    return {"success": True, "data": result}


@router.post("/local")
async def register_local_mcp(body: LocalMcpRegisterIn, c: Container = Depends(get_container)):
    """`.mcp.json` 에 서버를 실제로 추가하고 바로 warm 한다(다른 서버는 안 건드림)."""
    register = getattr(c.connectors, "register_local_mcp_server", None)
    if register is None:
        raise NotFoundError("local MCP registration is not supported by this connector service")
    info = await register(
        body.name, transport=body.transport, command=body.command, args=tuple(body.args),
        env=body.env, url=body.url, headers=body.headers,
    )
    return {"success": True, "data": info}


@router.delete("/local/{name}")
async def unregister_local_mcp(name: str, c: Container = Depends(get_container)):
    """`.mcp.json` 에서 서버를 제거한다."""
    unregister = getattr(c.connectors, "unregister_local_mcp_server", None)
    removed = unregister(name) if unregister is not None else False
    if not removed:
        raise NotFoundError(f"local mcp server {name!r} not found")
    return {"success": True}


@router.post("/custom/discover")
async def discover_custom(body: CustomDiscoverIn, c: Container = Depends(get_container)):
    """임의의 원격 MCP 서버 URL 하나로 OAuth 메타데이터를 발견한다(부작용 없음 — 미리보기).

    발견 못 한 필드는 null 로 온다 — 모달이 그 항목만 수동 입력을 받는다.
    """
    discover = getattr(c.connectors, "discover_custom_connector", None)
    if discover is None:
        return {"success": True, "data": {"ok": False}}
    cfg = await discover(body.mcp_endpoint)
    return {
        "success": True,
        "data": {
            "ok": cfg.ok,
            "auth_url": cfg.auth_url,
            "token_url": cfg.token_url,
            "registration_endpoint": cfg.registration_endpoint,
            "scopes": list(cfg.scopes),
            "issuer": cfg.issuer,
        },
    }


@router.post("/custom")
async def connect_custom(body: CustomConnectIn, c: Container = Depends(get_container)):
    """커스텀 커넥터 등록을 확정한다 — 필요하면 RFC 7591 동적 등록을 수행하고, 바로 OAuth 를
    시작한다(discover 미리보기와 달리 여기서부터 실제 부작용이 생긴다)."""
    add = getattr(c.connectors, "add_custom_connector", None)
    if add is None:
        raise NotFoundError("custom connectors are not supported by this connector service")
    start = await add(
        display_name=body.display_name,
        mcp_endpoint=body.mcp_endpoint,
        auth_url=body.auth_url,
        token_url=body.token_url,
        registration_endpoint=body.registration_endpoint,
        scopes=tuple(body.scopes),
        client_id=body.client_id,
        client_secret=body.client_secret,
    )
    return {"success": True, "data": {"oauth_url": start.oauth_url, "connector_id": start.connector_id}}


@router.get("/oauth/callback")
async def oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    c: Container = Depends(get_container),
):
    """Google OAuth redirect handler (CONTRACTS §4.2 step 4). 브라우저가 직접 연다."""
    if error:
        return _callback_page("연결 취소됨", f"인증이 거부되었습니다 ({error}). 창을 닫으셔도 됩니다.", ok=False)
    if not code or not state:
        return _callback_page("연결 실패", "필수 파라미터(code/state)가 없습니다.", ok=False)
    try:
        info = await c.connectors.complete_oauth(code=code, state=state)
    except Exception as exc:  # noqa: BLE001
        return _callback_page("연결 실패", f"토큰 교환 중 오류: {exc}", ok=False)
    return _callback_page(
        f"{info.display_name} 연결 완료",
        "계정이 RICE 에 연결되었습니다. 이 창을 닫고 앱으로 돌아가세요.",
        ok=True,
    )


@router.get("/{connector_id}")
async def get_connector(connector_id: str, c: Container = Depends(get_container)):
    info = c.connectors.get_connector(connector_id)
    if info is None:
        raise NotFoundError(f"connector {connector_id} not found")
    return {"success": True, "data": _connector_payload(c, info)}


def _require_connector(c: Container, connector_id: str):
    info = c.connectors.get_connector(connector_id)
    if info is None:
        raise NotFoundError(f"connector {connector_id} not found")
    return info


def _require_connector_or_local(c: Container, connector_id: str) -> None:
    """원격(connectors 테이블) 또는 로컬(.mcp.json) 커넥터 어느 쪽으로든 존재를 확인한다.

    tool 권한처럼 두 종류 다 다루는 엔드포인트용 — 로컬 서버는 DB 행이 없어
    _require_connector 만으로는 항상 404 난다.
    """
    if c.connectors.get_connector(connector_id) is not None:
        return
    exists = getattr(c.connectors, "local_server_exists", None)
    if exists is not None and exists(connector_id):
        return
    raise NotFoundError(f"connector {connector_id} not found")


@router.post("/{connector_id}/tools/resync")
async def resync_tools(connector_id: str, c: Container = Depends(get_container)):
    """원격 MCP `tools/list` 를 다시 받아 카탈로그를 **강제 갱신**한다(즉시 적용).

    "재인증" 대신 도구 목록만 강제로 새로고침한다(재인증은 연결 해제→연결).
    """
    _require_connector(c, connector_id)
    sync_tools = getattr(c.connectors, "sync_tools", None)
    count = await sync_tools(connector_id) if sync_tools is not None else 0
    return {"success": True, "data": {"count": count, "tools": _tools_payload(c, connector_id)}}


@router.post("/{connector_id}/tools/check")
async def check_tool_updates(connector_id: str, c: Container = Depends(get_container)):
    """원격 카탈로그 변경을 감지한다(적용하지 않음). 변경 시 pending 업데이트로 표시.

    커넥터 화면 진입 시 온디맨드로 호출해 "업데이트 있음" 배지를 실데이터로 띄운다.
    """
    _require_connector(c, connector_id)
    check = getattr(c.connectors, "check_updates", None)
    result = await check(connector_id) if check is not None else {"changed": False}
    return {"success": True, "data": result}


@router.post("/{connector_id}/tools/apply-update")
async def apply_tool_update(connector_id: str, c: Container = Depends(get_container)):
    """대기 중(pending) 카탈로그 업데이트를 활성으로 적용한다."""
    _require_connector(c, connector_id)
    apply = getattr(c.connectors, "apply_update", None)
    count = await apply(connector_id) if apply is not None else 0
    return {"success": True, "data": {"count": count, "tools": _tools_payload(c, connector_id)}}


@router.put("/{connector_id}/tools/permission")
async def set_tool_permission(connector_id: str, body: ToolPermissionIn, c: Container = Depends(get_container)):
    """도구별 호출 정책(allow/approval/block)을 저장한다 — dispatcher 가 강제한다."""
    _require_connector_or_local(c, connector_id)
    setter = getattr(c.connectors, "set_tool_permission", None)
    if setter is not None:
        setter(connector_id, body.tool_name, body.permission)
    return {"success": True, "data": {"tool_name": body.tool_name, "permission": body.permission}}


@router.get("/{connector_id}/status")
async def status(connector_id: str, c: Container = Depends(get_container)):
    return {"success": True, "data": {"status": c.connectors.status(connector_id)}}


@router.post("")
async def connect(body: ConnectIn, c: Container = Depends(get_container)):
    start = await c.connectors.begin_connect(body.connector_type)
    return {"success": True, "data": {"oauth_url": start.oauth_url, "connector_id": start.connector_id}}


@router.delete("/{connector_id}")
async def delete(connector_id: str, c: Container = Depends(get_container)):
    await c.connectors.delete(connector_id)
    return {"success": True}
