"""Settings, models, memories (CONTRACTS §1.3) — SettingsScreen + personalization.

  GET /settings        provider/temperature/configured + registered_models (state.db)
  PUT /settings        update config + replace registered SaaS models (model+key+extra)
  GET /models          available provider kinds (echo/claude/ollama/openai/watsonx)
  GET /ollama/models   list models installed in the local Ollama daemon
  GET /memories        personalization visualization
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...core.config import Config, ContextConfig, ProviderConfig, UIConfig, save_config
from ..composition_root import Container
from .deps import get_container

router = APIRouter(tags=["settings"])


class SettingsIn(BaseModel):
    provider: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    ui: dict[str, Any] | None = None
    # api_key is handled out-of-band → OS keychain (never persisted in config.yaml)
    api_key: str | None = None
    # SaaS 등록 모델 목록 (모델 + API 키). None이면 변경 안 함, 리스트면 전체 교체.
    # 로컬 단일 사용자 앱이라 state.db(provider_models)에 보관한다.
    registered_models: list[dict[str, Any]] | None = None


def _load_registered_models(c: Container) -> list[dict[str, Any]]:
    rows = c.db.query_all(
        "SELECT model_id, provider, provider_label, api_key, extra FROM provider_models ORDER BY created_at"
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        extra = json.loads(r["extra"]) if r["extra"] else {}
        out.append(
            {
                "modelId": r["model_id"],
                "provider": r["provider"],
                "providerLabel": r["provider_label"],
                "apiKey": r["api_key"],
                # provider-specific (없으면 생략)
                **({"baseUrl": extra["base_url"]} if extra.get("base_url") else {}),
                **({"projectId": extra["project_id"]} if extra.get("project_id") else {}),
            }
        )
    return out


@router.get("/settings")
async def get_settings(c: Container = Depends(get_container)):
    data = c.config.to_dict()
    data["registered_models"] = _load_registered_models(c)
    return {"success": True, "data": data}


@router.put("/settings")
async def update_settings(body: SettingsIn, c: Container = Depends(get_container)):
    cfg = c.config
    provider = ProviderConfig(**{**cfg.provider.__dict__, **(body.provider or {})})
    context = ContextConfig(**{**cfg.context.__dict__, **(body.context or {})})
    ui = UIConfig(**{**cfg.ui.__dict__, **(body.ui or {})})
    new_cfg = Config(provider=provider, learning=cfg.learning, context=context, ui=ui)
    save_config(c.paths, new_cfg)
    c.config_store.update(new_cfg)  # live: provider/orchestrator observe this next turn (C-1)

    # 등록 모델 목록이 오면 전체 교체 (프론트가 항상 전체 목록을 보낸다)
    if body.registered_models is not None:
        c.db.execute("DELETE FROM provider_models")
        rows = []
        for m in body.registered_models:
            if not m.get("modelId"):
                continue
            extra = {
                k: v
                for k, v in (("base_url", m.get("baseUrl")), ("project_id", m.get("projectId")))
                if v
            }
            rows.append(
                (
                    m.get("modelId"),
                    m.get("provider"),
                    m.get("providerLabel"),
                    m.get("apiKey", ""),
                    json.dumps(extra) if extra else None,
                )
            )
        c.db.executemany(
            "INSERT INTO provider_models(model_id, provider, provider_label, api_key, extra) VALUES (?, ?, ?, ?, ?)",
            rows,
        )

    data = new_cfg.to_dict()
    data["registered_models"] = _load_registered_models(c)
    return {"success": True, "data": data}


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


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str, c: Container = Depends(get_container)):
    """메모리 삭제 (Self-Improvement v3). 메모리는 HITL 없이 즉시 반영되는 유일한
    학습 경로 — 잘못 들어간 항목이 매 턴 영구 주입되지 않도록 사후 제거 수단을
    제공한다. 격리 메모리를 여기로 지우면 곧 '거부'다. 없으면 NotFoundError → 404."""
    c.learning.delete_memory(memory_id)
    return {"success": True}


@router.post("/memories/{memory_id}/approve")
async def approve_memory(memory_id: str, c: Container = Depends(get_container)):
    """격리(quarantined) 메모리 승인. 외부 콘텐츠(도구 결과)에서 나온
    메모리는 격리 상태로 저장돼 주입되지 않는데, 사용자가 승인하면 active가 되어
    이후 prefetch가 주입한다. 없으면 NotFoundError → 404."""
    c.learning.approve_memory(memory_id)
    return {"success": True}


@router.get("/learning/events")
async def learning_events(limit: int = 100, c: Container = Depends(get_container)):
    """학습 감사 로그 (Self-Improvement v3) — 제안/승인/거부/타임아웃/메모리 기록의
    영속 이력. '루프가 실제로 무엇을 배우고 무엇이 기각됐는가'를 UI가 보여줄 수
    있게 하는 원천(편집가능·감사가능·롤백 원칙의 감사 축)."""
    return {"success": True, "data": c.learning.learning_events(min(max(limit, 1), 500))}
