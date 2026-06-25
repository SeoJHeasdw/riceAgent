"""PluginLoader + NoopSandbox — skeleton impls of the execution seams.

⚠️ STUB. Real plugin loading (`plugin/loader.py`, `plugin/manifest.py`) and the
child-process sandbox (`sandbox/runner.py`, `ipc.py`, `policy.py`) replace these
behind the same Protocols (RUNTIME_DESIGN §5, CONTRACTS §2).

Crucial safety note kept honest by the stub: `NoopSandbox.run` refuses to execute
anything. The sandbox is the *only* place untrusted code may run, so until it is
built, running is denied — never silently in-process.
"""

from __future__ import annotations

from typing import Any

from ..core.storage.sqlite import Database
from ..core.types import InjectedSkill, ToolCall, ToolResult
from .interfaces import IPluginLoader, ISandbox, PluginInfo, SandboxResult


class NoopSandbox(ISandbox):
    async def run(self, *, entrypoint: str, payload: dict[str, Any], timeout_s: float = 30.0) -> SandboxResult:
        # Fail-closed: refuse to execute until the real child-process sandbox exists.
        return SandboxResult(ok=False, error="sandbox not implemented — execution denied")


class PluginLoader(IPluginLoader):
    def __init__(self, db: Database, sandbox: ISandbox) -> None:
        self._db = db
        self._sandbox = sandbox

    # ── tool/skill surface ──────────────────────────────────────────────────
    def list_tools(self):
        return []  # TODO(execution/plugin): tools from enabled plugins

    def injected_skills(self) -> list[InjectedSkill]:
        return []  # TODO(execution/plugin): skills bundled by enabled plugins

    async def invoke(self, call: ToolCall) -> ToolResult:
        result = await self._sandbox.run(entrypoint=call.tool_name, payload=call.arguments)
        return ToolResult(call.id, ok=result.ok, result=result.result, error=result.error)

    # ── management ────────────────────────────────────────────────────────────
    def list_plugins(self) -> list[PluginInfo]:
        rows = self._db.query_all(
            "SELECT id, name, version FROM installed_artifacts WHERE type = 'plugin'"
        )
        return [
            PluginInfo(id=r["id"], name=r["name"], version=r["version"], description="", enabled=True)
            for r in rows
        ]

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        return None  # TODO(execution/plugin/registry)

    async def install(self, *, path: str, manifest: dict[str, Any]) -> PluginInfo:
        # TODO(execution/plugin/loader): verify checksum, place under ~/.rice/, load.
        raise NotImplementedError("artifact install not implemented yet")

    async def remove(self, artifact_id: str) -> None:
        self._db.execute("DELETE FROM installed_artifacts WHERE id = ?", (artifact_id,))
