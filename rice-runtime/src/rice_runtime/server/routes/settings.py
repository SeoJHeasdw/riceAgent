"""Settings, models, memories (CONTRACTS §1.3) — SettingsScreen + personalization.

  GET /settings        model/provider/temperature, language, theme, font
  PUT /settings        update (api_key → OS keychain, not stored in config)
  GET /models          available providers (echo/claude/ollama)
  GET /ollama/models   list models installed in the local Ollama daemon
  GET /memories        personalization visualization
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...core.config import Config, ProviderConfig, UIConfig, save_config
from ..composition_root import Container
from .deps import get_container

router = APIRouter(tags=["settings"])


class SettingsIn(BaseModel):
    provider: dict[str, Any] | None = None
    ui: dict[str, Any] | None = None
    # api_key is handled out-of-band → OS keychain (never persisted in config.yaml)
    api_key: str | None = None


@router.get("/settings")
async def get_settings(c: Container = Depends(get_container)):
    return {"success": True, "data": c.config.to_dict()}


@router.put("/settings")
async def update_settings(body: SettingsIn, c: Container = Depends(get_container)):
    cfg = c.config
    provider = ProviderConfig(**{**cfg.provider.__dict__, **(body.provider or {})})
    ui = UIConfig(**{**cfg.ui.__dict__, **(body.ui or {})})
    new_cfg = Config(provider=provider, learning=cfg.learning, ui=ui)
    save_config(c.paths, new_cfg)
    c.config_store.update(new_cfg)  # live: provider/orchestrator observe this next turn (C-1)
    # TODO(connector/credential): if body.api_key, store in OS keychain.
    return {"success": True, "data": new_cfg.to_dict()}


@router.get("/models")
async def get_models(c: Container = Depends(get_container)):
    return {"success": True, "data": {"providers": c.providers.available(), "active": c.resolve_provider().name}}


@router.get("/ollama/models")
async def get_ollama_models(c: Container = Depends(get_container)):
    """Proxy to Ollama /api/tags — returns the list of locally installed models.

    Uses the endpoint from the current provider config so the UI's endpoint field
    is the single source of truth (no duplicated URL state).
    """
    base_url = (c.config.provider.base_url or "http://localhost:11434").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail=f"Ollama에 연결할 수 없습니다: {base_url}")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail="Ollama 응답 오류")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    # Normalize to a flat list with only the fields the UI needs.
    models = [
        {
            "name": m["name"],
            "parameter_size": m.get("details", {}).get("parameter_size", ""),
            "quantization_level": m.get("details", {}).get("quantization_level", ""),
            "context_length": m.get("details", {}).get("context_length"),
            "size_bytes": m.get("size", 0),
            "capabilities": m.get("capabilities", []),
        }
        for m in data.get("models", [])
    ]
    return {"success": True, "data": models}


@router.get("/ollama/health")
async def ollama_model_health(c: Container = Depends(get_container)):
    """Check whether the currently configured Ollama model is installed.

    Calls POST /api/show with the saved model name — the lightest way to ask
    Ollama "does this model exist?" without pulling the full tag list.
    Returns { exists: bool, model: str } so the UI can warn the user when they
    deleted a model without updating their settings.
    Only meaningful when provider.kind == "ollama"; returns exists=true for
    other providers (nothing to check).
    """
    cfg = c.config.provider
    if cfg.kind != "ollama":
        return {"success": True, "data": {"exists": True, "model": cfg.model}}

    base_url = (cfg.base_url or "http://localhost:11434").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{base_url}/api/show", json={"name": cfg.model})
            exists = resp.status_code == 200
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail=f"Ollama에 연결할 수 없습니다: {base_url}")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"success": True, "data": {"exists": exists, "model": cfg.model}}


@router.get("/memories")
async def get_memories(c: Container = Depends(get_container)):
    return {"success": True, "data": [m.__dict__ for m in c.learning.list_memories()]}
