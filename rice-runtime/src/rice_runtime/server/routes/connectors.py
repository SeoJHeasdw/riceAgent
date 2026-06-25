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
from pydantic import BaseModel

from ...core.errors import NotFoundError
from ..composition_root import Container
from .deps import get_container

router = APIRouter(prefix="/connectors", tags=["connectors"])


class ConnectIn(BaseModel):
    connector_type: str  # "gmail" (prototype)


@router.get("")
async def list_connectors(c: Container = Depends(get_container)):
    return {"success": True, "data": [i.__dict__ for i in c.connectors.list_connectors()]}


@router.get("/oauth/callback")
async def oauth_callback(code: str, state: str, c: Container = Depends(get_container)):
    info = await c.connectors.complete_oauth(code=code, state=state)
    return {"success": True, "data": info.__dict__}


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
