"""Plugins + artifact install (CONTRACTS §1.3, §2.3) — MyPluginsScreen.

  GET    /plugins                 installed list
  PATCH  /plugins/{id}/enabled    enable / disable
  DELETE /plugins/{id}            remove
  POST   /artifacts/install       install a downloaded artifact (manifest + path)
  DELETE /artifacts/{id}          remove an installed artifact
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..composition_root import Container
from .deps import get_container

router = APIRouter(tags=["plugins"])


class EnabledIn(BaseModel):
    enabled: bool


class InstallIn(BaseModel):
    path: str
    manifest: dict[str, Any]


@router.get("/plugins")
async def list_plugins(c: Container = Depends(get_container)):
    return {"success": True, "data": [p.__dict__ for p in c.plugins.list_plugins()]}


@router.patch("/plugins/{plugin_id}/enabled")
async def set_enabled(plugin_id: str, body: EnabledIn, c: Container = Depends(get_container)):
    c.plugins.set_enabled(plugin_id, body.enabled)
    return {"success": True}


@router.delete("/plugins/{plugin_id}")
async def delete_plugin(plugin_id: str, c: Container = Depends(get_container)):
    await c.plugins.remove(plugin_id)
    return {"success": True}


@router.post("/artifacts/install")
async def install_artifact(body: InstallIn, c: Container = Depends(get_container)):
    info = await c.plugins.install(path=body.path, manifest=body.manifest)
    return {"success": True, "data": info.__dict__}


@router.delete("/artifacts/{artifact_id}")
async def remove_artifact(artifact_id: str, c: Container = Depends(get_container)):
    await c.plugins.remove(artifact_id)
    return {"success": True}
