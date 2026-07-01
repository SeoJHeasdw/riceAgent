"""Connectors (CONTRACTS §4) — ConnectorsScreen.

  GET    /connectors                        list (+ each connector's tool catalog + pending update)
  GET    /connectors/{id}                   detail (granted scopes)
  POST   /connectors                        begin OAuth (returns oauth_url)
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
    _require_connector(c, connector_id)
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
