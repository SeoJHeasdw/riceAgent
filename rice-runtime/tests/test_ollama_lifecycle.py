"""Ollama residency policy: turn lease, post-turn grace, switch, shutdown."""

from __future__ import annotations

import asyncio

import pytest

from rice_runtime import build_container
from rice_runtime.agent.providers.ollama_lifecycle import (
    OllamaLifecycleManager,
    OllamaModelKey,
)
from rice_runtime.agent.providers.registry import ProviderRegistry
from rice_runtime.core.config import Config, ConfigStore, ProviderConfig, RuntimeArgs, load_config
from rice_runtime.core.paths import RicePaths


@pytest.mark.asyncio
async def test_outer_turn_lease_prevents_unload_between_model_calls():
    unloaded: list[OllamaModelKey] = []

    async def unload(key: OllamaModelKey) -> None:
        unloaded.append(key)

    manager = OllamaLifecycleManager(unload=unload)
    key = OllamaModelKey.create("http://ollama.local/", "qwen")

    async with manager.lease(key, idle_seconds=0.01):
        # Planner/model round 1.
        async with manager.lease(key, idle_seconds=0.01, touches_model=True):
            pass
        # A tool or approval can take longer than the post-turn grace. The outer
        # logical-turn lease must keep the unload timer from even starting.
        await asyncio.sleep(0.025)
        assert unloaded == []
        # ReAct round 2 reuses the same residency.
        async with manager.lease(key, idle_seconds=0.01, touches_model=True):
            pass

    await asyncio.sleep(0.025)
    assert unloaded == [key]
    await manager.shutdown()
    assert unloaded == [key]  # successful idle unload is not repeated at shutdown


@pytest.mark.asyncio
async def test_long_active_wait_refreshes_finite_server_keep_alive():
    refreshed: list[tuple[OllamaModelKey, str | int | float]] = []
    unloaded: list[OllamaModelKey] = []

    async def refresh(key: OllamaModelKey, keep_alive: str | int | float) -> None:
        refreshed.append((key, keep_alive))

    async def unload(key: OllamaModelKey) -> None:
        unloaded.append(key)

    manager = OllamaLifecycleManager(unload=unload, refresh=refresh)
    key = OllamaModelKey.create(None, "qwen")
    # 1.1s gives a one-second heartbeat after the manager's safety floor. The
    # production default 5m emits its first heartbeat only after four idle minutes.
    async with manager.lease(key, idle_seconds=0, request_keep_alive="1.1s"):
        async with manager.lease(
            key,
            idle_seconds=0,
            touches_model=True,
            request_keep_alive="1.1s",
        ):
            pass
        await asyncio.sleep(1.05)
        assert refreshed == [(key, "1.1s")]
        assert unloaded == []

    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert unloaded == [key]


@pytest.mark.asyncio
async def test_post_turn_activity_cancels_grace_timer():
    unloaded: list[OllamaModelKey] = []

    async def unload(key: OllamaModelKey) -> None:
        unloaded.append(key)

    manager = OllamaLifecycleManager(unload=unload)
    key = OllamaModelKey.create(None, "qwen")

    async with manager.lease(key, idle_seconds=0.03, touches_model=True):
        pass
    await asyncio.sleep(0.01)
    # The title/review request arrives after main-turn done and cancels the first
    # timer. Its completion starts a fresh full grace period.
    async with manager.lease(key, idle_seconds=0.03, touches_model=True):
        await asyncio.sleep(0.025)
        assert unloaded == []
    await asyncio.sleep(0.04)
    assert unloaded == [key]


@pytest.mark.asyncio
async def test_model_switch_defers_old_unload_until_active_turn_releases():
    unloaded: list[OllamaModelKey] = []

    async def unload(key: OllamaModelKey) -> None:
        unloaded.append(key)

    manager = OllamaLifecycleManager(unload=unload)
    old = OllamaModelKey.create(None, "old")

    async with manager.lease(old, idle_seconds=60):
        async with manager.lease(old, idle_seconds=60, touches_model=True):
            pass
        await manager.retire(old)
        assert unloaded == []  # settings change cannot tear down in-flight work

    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert unloaded == [old]


@pytest.mark.asyncio
async def test_concurrent_shutdown_is_idempotent_and_unloads_once():
    unloaded: list[OllamaModelKey] = []

    async def unload(key: OllamaModelKey) -> None:
        await asyncio.sleep(0)
        unloaded.append(key)

    manager = OllamaLifecycleManager(unload=unload)
    key = OllamaModelKey.create(None, "qwen")
    async with manager.lease(key, idle_seconds=60, touches_model=True):
        pass

    await asyncio.gather(manager.shutdown(), manager.shutdown())
    assert unloaded == [key]


@pytest.mark.asyncio
async def test_documented_unload_wire_uses_empty_generate_keep_alive_zero(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class _Response:
        def raise_for_status(self) -> None:
            pass

    class _Client:
        async def post(self, url, *, json):
            calls.append((url, json))
            return _Response()

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: _Client())
    manager = OllamaLifecycleManager()
    key = OllamaModelKey.create("http://ollama.local/", "qwen")

    async with manager.lease(key, idle_seconds=0, touches_model=True):
        pass
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert calls == [
        (
            "http://ollama.local/api/generate",
            {"model": "qwen", "keep_alive": 0, "stream": False},
        )
    ]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_container_async_shutdown_is_idempotent(tmp_path, monkeypatch):
    container = build_container(RuntimeArgs(), paths=RicePaths.from_env(tmp_path))
    calls = 0

    async def shutdown_providers() -> None:
        nonlocal calls
        await asyncio.sleep(0)
        calls += 1

    monkeypatch.setattr(container.providers, "shutdown", shutdown_providers)

    await asyncio.gather(container.shutdown_async(), container.shutdown_async())

    assert calls == 1


@pytest.mark.asyncio
async def test_registry_transition_retires_previous_ollama_key():
    unloaded: list[OllamaModelKey] = []

    async def unload(key: OllamaModelKey) -> None:
        unloaded.append(key)

    old_cfg = ProviderConfig(kind="ollama", model="old", idle_unload_seconds=60)
    new_cfg = ProviderConfig(kind="ollama", model="new", idle_unload_seconds=60)
    manager = OllamaLifecycleManager(unload=unload)
    store = ConfigStore(Config(provider=old_cfg))
    registry = ProviderRegistry(store, ollama_lifecycle=manager)
    registry.get("ollama")
    old_key = OllamaModelKey.create(old_cfg.base_url, old_cfg.model)
    async with manager.lease(old_key, idle_seconds=60, touches_model=True):
        pass

    store.update(Config(provider=new_cfg))
    await registry.provider_changed(old_cfg, new_cfg)

    assert unloaded == [old_key]
    assert registry.get("ollama")._cfg.model == "new"  # noqa: SLF001 - cache transition contract
    await registry.shutdown()


def test_legacy_30m_config_migrates_to_bounded_default(tmp_path):
    paths = RicePaths.from_env(tmp_path).ensure()
    paths.config.write_text("provider:\n  kind: ollama\n  model: qwen\n  keep_alive: 30m\n", encoding="utf-8")

    cfg = load_config(paths)

    assert cfg.provider.keep_alive == "5m"
    assert cfg.provider.idle_unload_seconds == 60.0


def test_explicit_30m_in_new_lifecycle_schema_is_preserved(tmp_path):
    paths = RicePaths.from_env(tmp_path).ensure()
    paths.config.write_text(
        "provider:\n  kind: ollama\n  model: qwen\n  keep_alive: 30m\n  idle_unload_seconds: 9\n",
        encoding="utf-8",
    )

    cfg = load_config(paths)

    assert cfg.provider.keep_alive == "30m"
    assert cfg.provider.idle_unload_seconds == 9
