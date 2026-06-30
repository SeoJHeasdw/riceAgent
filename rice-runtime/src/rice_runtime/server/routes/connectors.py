"""Connectors (CONTRACTS §4) — ConnectorsScreen.

  GET    /connectors                    list
  GET    /connectors/{id}               detail (granted scopes)
  POST   /connectors                    begin OAuth (returns oauth_url)
  DELETE /connectors/{id}               disconnect
  GET    /connectors/{id}/status        connection status
  GET    /connectors/oauth/callback     OAuth redirect handler
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
    connector_type: str  # "gmail" (prototype)


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


@router.get("")
async def list_connectors(c: Container = Depends(get_container)):
    return {"success": True, "data": [i.__dict__ for i in c.connectors.list_connectors()]}


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
    return {"success": True, "data": info.__dict__}


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
