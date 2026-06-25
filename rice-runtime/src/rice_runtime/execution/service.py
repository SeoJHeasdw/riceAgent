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
    ) -> SandboxResult:
        return SandboxResult(ok=False, error="sandbox not implemented — execution denied")


class PluginLoaderService(IPluginLoader):
    """Thin facade delegating to `PluginLoader` (plugin/loader.py).

    Keeps the public surface (`IPluginLoader`) stable while the internals can
    evolve. The `paths` dependency is injected by composition_root — tests pass
    a temp-dir RicePaths.
    """

    def __init__(self, db: Database, sandbox: ISandbox, paths: RicePaths) -> None:
        self._impl = PluginLoader(db=db, sandbox=sandbox, paths=paths)

    # ── tool / skill surface ─────────────────────────────────────────────────

    def list_tools(self):
        return self._impl.list_tools()

    def injected_skills(self) -> list[InjectedSkill]:
        return self._impl.injected_skills()

    async def invoke(self, call: ToolCall) -> ToolResult:
        return await self._impl.invoke(call)

    # ── management ────────────────────────────────────────────────────────────

    def list_plugins(self) -> list[PluginInfo]:
        return self._impl.list_plugins()

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        self._impl.set_enabled(plugin_id, enabled)

    async def install(self, *, path: str, manifest: dict[str, Any]) -> PluginInfo:
        return await self._impl.install(path=path, manifest=manifest)

    async def remove(self, artifact_id: str) -> None:
        await self._impl.remove(artifact_id)
