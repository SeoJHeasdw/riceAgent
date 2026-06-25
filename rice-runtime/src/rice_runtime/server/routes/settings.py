"""Settings, models, memories (CONTRACTS §1.3) — SettingsScreen + personalization.

  GET /settings   model/provider/temperature, language, theme, font
  PUT /settings   update (api_key → OS keychain, not stored in config)
  GET /models     available models (per provider)
  GET /memories   personalization visualization
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
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


@router.get("/memories")
async def get_memories(c: Container = Depends(get_container)):
    return {"success": True, "data": [m.__dict__ for m in c.learning.list_memories()]}
