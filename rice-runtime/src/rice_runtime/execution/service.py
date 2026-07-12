"""PluginLoaderService + ChildProcessSandbox — real execution seam implementations.

This module wires `PluginLoader` (plugin/*.py) and `ChildProcessSandbox`
(sandbox/runner.py) into the `IPluginLoader` / `ISandbox` Protocols that the
composition_root and the orchestrator see.

The old `NoopSandbox` is kept here as an escape-hatch — composition_root no
longer uses it, but tests can still import it when they need a safe no-op.
"""

from __future__ import annotations

from typing import Any

from ..core.paths import RicePaths
from ..core.storage.sqlite import Database
from ..core.types import InjectedSkill, ToolCall, ToolResult
from .interfaces import IPluginLoader, ISandbox, PluginInfo, SandboxResult
from .plugin.loader import PluginLoader
from .plugin.builtin import BuiltinPluginCatalog
from .sandbox.runner import ChildProcessSandbox


# Re-export ChildProcessSandbox so composition_root can import from one place.
__all__ = [
    "ChildProcessSandbox",
    "NoopSandbox",
    "PluginLoaderService",
]


class NoopSandbox(ISandbox):
    """Fail-closed no-op sandbox (kept for tests and legacy wiring).

    Returns an error on every call — never executes anything. Useful for test
    scenarios that don't care about sandbox output and for the walking skeleton
    fallback.
    """

    async def run(
        self,
        *,
        entrypoint: str,
        payload: dict[str, Any],
        timeout_s: float = 30.0,
        sys_path: list[str] | None = None,
    ) -> SandboxResult:
        return SandboxResult(ok=False, error="sandbox not implemented — execution denied")


class PluginLoaderService(IPluginLoader):
    """Thin facade delegating to `PluginLoader` (plugin/loader.py).

    Keeps the public surface (`IPluginLoader`) stable while the internals can
    evolve. The `paths` dependency is injected by composition_root — tests pass
    a temp-dir RicePaths.
    """

    def __init__(
        self,
        db: Database,
        sandbox: ISandbox,
        paths: RicePaths,
        builtin_plugins: BuiltinPluginCatalog | None = None,
    ) -> None:
        self._impl = PluginLoader(
            db=db, sandbox=sandbox, paths=paths, builtin_plugins=builtin_plugins
        )

    # ── tool / skill surface ─────────────────────────────────────────────────

    def list_tools(self):
        return self._impl.list_tools()

    def injected_skills(self) -> list[InjectedSkill]:
        return self._impl.injected_skills()

    async def invoke(self, call: ToolCall, owner_id: str | None = None) -> ToolResult:
        # IPluginLoader 계약과 동일하게 owner_id 를 그대로 전달한다 — 이 facade 가
        # 인자를 떨어뜨리면 dispatcher 의 실제 플러그인 호출 전부가 TypeError 로
        # 죽는다(적대 리뷰 P1#1).
        return await self._impl.invoke(call, owner_id=owner_id)

    # ── management ────────────────────────────────────────────────────────────

    def list_plugins(self) -> list[PluginInfo]:
        return self._impl.list_plugins()

    def get_plugin(self, plugin_id: str) -> PluginInfo | None:
        return self._impl.get_plugin(plugin_id)

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        self._impl.set_enabled(plugin_id, enabled)

    def reconcile_builtin_shadows(self) -> int:
        return self._impl.reconcile_builtin_shadows()

    async def install(self, *, path: str, manifest: dict[str, Any]) -> PluginInfo:
        return await self._impl.install(path=path, manifest=manifest)

    async def remove(self, artifact_id: str) -> None:
        await self._impl.remove(artifact_id)
