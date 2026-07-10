"""Settings, models, memories (CONTRACTS §1.3) — SettingsScreen + personalization.

  GET /settings        provider/temperature/configured + registered_models (state.db)
  PUT /settings        update config + replace registered SaaS models (model+key+extra)
  GET /models          available provider kinds (echo/claude/ollama/openai/watsonx)
  GET /ollama/models   list models installed in the local Ollama daemon
  GET /memories        personalization visualization
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...core.config import AgentConfig, Config, ContextConfig, ProviderConfig, UIConfig, save_config
from ..composition_root import Container
from .deps import get_container

router = APIRouter(tags=["settings"])
log = logging.getLogger("rice.routes.settings")


class SettingsIn(BaseModel):
    provider: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    agent: dict[str, Any] | None = None  # plan-and-execute × ReAct 노브 (AgentConfig)
    ui: dict[str, Any] | None = None
    # api_key is handled out-of-band → OS keychain (never persisted in config.yaml)
    api_key: str | None = None
    # SaaS 등록 모델 목록 (모델 + API 키). None이면 변경 안 함, 리스트면 전체 교체.
    # 로컬 단일 사용자 앱이라 state.db(provider_models)에 보관한다.
    registered_models: list[dict[str, Any]] | None = None


# API 응답에 평문 키를 싣지 않기 위한 마스크. GET 은 항상 마스크만 반환하고,
# PUT 이 마스크 형태의 값을 되돌려 보내면 "변경 없음"으로 해석해 저장된 키를
# 보존한다 — 프론트가 전체 목록을 라운드트립하는 기존 계약을 깨지 않는 방식.
# (DB 평문 저장 자체는 0004 마이그레이션에 문서화된 로컬 프로토타입 결정 —
# 키체인 이관은 별도 트랙. 여기서는 API 표면 노출만 닫는다.)
_KEY_MASK_PREFIX = "••••"


def _mask_key(key: str) -> str:
    if not key:
        return ""
    suffix = key[-4:] if len(key) > 8 else ""
    return f"{_KEY_MASK_PREFIX}{suffix}"


def _is_masked_key(value: str) -> bool:
    return value.startswith(_KEY_MASK_PREFIX)


def _load_registered_models(c: Container) -> list[dict[str, Any]]:
    """등록 모델 목록 (UI 용). apiKey 는 마스크 + hasKey 로만 노출한다."""
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
                "apiKey": _mask_key(r["api_key"] or ""),
                "hasKey": bool(r["api_key"]),
                # provider-specific (없으면 생략)
                **({"baseUrl": extra["base_url"]} if extra.get("base_url") else {}),
                **({"projectId": extra["project_id"]} if extra.get("project_id") else {}),
            }
        )
    return out


def _stored_keys(c: Container) -> dict[str, str]:
    """PUT 의 마스크값 보존용 — model_id → 저장된 평문 키 (교체 전에 읽는다)."""
    rows = c.db.query_all("SELECT model_id, api_key FROM provider_models")
    return {r["model_id"]: r["api_key"] or "" for r in rows}


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
    agent = AgentConfig(**{**cfg.agent.__dict__, **(body.agent or {})})
    ui = UIConfig(**{**cfg.ui.__dict__, **(body.ui or {})})
    new_cfg = Config(provider=provider, learning=cfg.learning, context=context, agent=agent, ui=ui)

    # 영속화 순서: ① DB 모델 교체(단일 트랜잭션) → ② config.yaml(원자 저장) →
    # ③ live config. 이전엔 live 반영·파일 저장·DELETE·INSERT 가 전부 독립 커밋이라
    # 중간 실패 시 "설정은 새 모델, credential 행은 없음 → echo 폴백" 반쪽 상태가
    # 났다. 이제 어느 단계에서 죽어도 live config 는 갱신되지 않고 5xx 로 드러난다.
    if body.registered_models is not None:
        stored = _stored_keys(c)  # 마스크 보존 해석은 교체 전 스냅샷 기준
        rows = []
        for m in body.registered_models:
            if not m.get("modelId"):
                continue
            api_key = m.get("apiKey", "") or ""
            if _is_masked_key(api_key):
                # GET 이 준 마스크가 그대로 돌아왔다 = 변경 의사 없음 → 기존 키 유지.
                api_key = stored.get(m.get("modelId"), "")
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
                    api_key,
                    json.dumps(extra) if extra else None,
                )
            )
        with c.db.transaction() as conn:
            conn.execute("DELETE FROM provider_models")
            conn.executemany(
                "INSERT INTO provider_models(model_id, provider, provider_label, api_key, extra) VALUES (?, ?, ?, ?, ?)",
                rows,
            )

    save_config(c.paths, new_cfg)
    c.config_store.update(new_cfg)  # live: 모든 영속화가 성공한 뒤에만 (C-1)
    provider_changed = getattr(c.providers, "provider_changed", None)
    if provider_changed is not None:
        try:
            # Retire an idle old Ollama model immediately. If its previous turn
            # is still active, the lifecycle defers that unload until its lease
            # exits, so a concurrent settings change cannot tear down inference.
            await provider_changed(cfg.provider, new_cfg.provider)
        except Exception:  # noqa: BLE001 — settings persistence already succeeded
            log.warning("provider lifecycle transition failed", exc_info=True)

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
