"""Plugins + artifact install (CONTRACTS §1.3, §2.3) — MyPluginsScreen.

  GET    /plugins                 installed list
  PATCH  /plugins/{id}/enabled    enable / disable
  DELETE /plugins/{id}            remove
  POST   /artifacts/install       install a downloaded artifact (manifest + path)
  DELETE /artifacts/{id}          remove an installed artifact
"""

from __future__ import annotations

import tempfile
from typing import Any
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ...core.errors import NotFoundError, ValidationError
from ..composition_root import Container
from .deps import get_container

router = APIRouter(tags=["plugins"])


class EnabledIn(BaseModel):
    enabled: bool


class InstallIn(BaseModel):
    path: str | None = None
    # Browser-only dev cannot write a platform tarball to a local file path.
    # When url is provided, runtime downloads the artifact itself, restricted to
    # localhost platform URLs.
    url: str | None = None
    manifest: dict[str, Any]
    # platform GET /artifacts/{id}/download 의 X-Checksum 헤더 값을 앱이 여기로 전달한다.
    # manifest.checksum 보다 우선한다 (CONTRACTS §2.3 무결성 검증 경로).
    checksum: str | None = None


@router.get("/plugins")
async def list_plugins(c: Container = Depends(get_container)):
    return {"success": True, "data": [p.__dict__ for p in c.plugins.list_plugins()]}


@router.patch("/plugins/{plugin_id}/enabled")
async def set_enabled(plugin_id: str, body: EnabledIn, c: Container = Depends(get_container)):
    info = c.plugins.get_plugin(plugin_id)
    if info is None:
        raise NotFoundError(f"plugin {plugin_id} not found")
    # CONTRACTS §4.4 step 2: 활성화 시 요구 connector 가 모두 연결돼 있어야 한다.
    # 미연결이면 422 로 차단해 "조용한 도구 실패"(v1 버그#6)를 방지한다.
    if body.enabled and info.required_connectors:
        connected = c.connectors.connected_types()
        missing = [ct for ct in info.required_connectors if ct not in connected]
        if missing:
            raise ValidationError(
                f"플러그인 '{info.name}' 활성화에 필요한 커넥터가 연결되지 않았습니다: {', '.join(missing)}. "
                f"커넥터를 먼저 연결하세요 (CONTRACTS §4.4).",
            )
    c.plugins.set_enabled(plugin_id, body.enabled)
    return {"success": True}


@router.delete("/plugins/{plugin_id}")
async def delete_plugin(plugin_id: str, c: Container = Depends(get_container)):
    await c.plugins.remove(plugin_id)
    return {"success": True}


@router.post("/artifacts/install")
async def install_artifact(body: InstallIn, c: Container = Depends(get_container)):
    # 앱이 X-Checksum 헤더 값을 body.checksum 으로 넘기면 manifest 에 주입해 무결성
    # 검증을 강제한다 (CONTRACTS §2.3 — v1 의 'checksum 전달 경로 없음' 버그#4 교정).
    manifest = dict(body.manifest)
    path = body.path
    checksum = body.checksum
    if path is None and body.url:
        path, checksum = await _download_local_platform_artifact(body.url)
    if path is None:
        raise ValidationError("artifact install requires either path or url")
    if checksum:
        manifest["checksum"] = checksum
    info = await c.plugins.install(path=path, manifest=manifest)
    return {"success": True, "data": info.__dict__}


@router.delete("/artifacts/{artifact_id}")
async def remove_artifact(artifact_id: str, c: Container = Depends(get_container)):
    await c.plugins.remove(artifact_id)
    return {"success": True}


async def _download_local_platform_artifact(url: str) -> tuple[str, str | None]:
    """Download a platform artifact to a temp file for browser-only dev installs."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValidationError(f"unsupported artifact url scheme: {parsed.scheme}")
    host = (parsed.hostname or "").lower()
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise ValidationError("artifact url must point to localhost platform")
    if not parsed.path.endswith("/download") or "/artifacts/" not in parsed.path:
        raise ValidationError("artifact url must be a platform /artifacts/{id}/download URL")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        checksum = resp.headers.get("x-checksum")
        suffix = Path(parsed.path).parts[-2] if len(Path(parsed.path).parts) >= 2 else "artifact"
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in suffix)
        path = Path(tempfile.mkdtemp(prefix="rice-artifact-download-")) / f"{safe}.tar.gz"
        path.write_bytes(resp.content)
        return str(path), checksum
