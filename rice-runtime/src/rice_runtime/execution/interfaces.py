"""Execution module seams (#7 plugin loading, #8 sandbox).

Owns: installing/loading plugins (manifest → injected skills + required
connectors), and the **sandbox boundary** for any code that actually runs
(RUNTIME_DESIGN §5). Recall the trust model: market *skills* are declarative
(SKILL.md, no code → no sandbox); only plugin/connector *code* executes, in a
child process with a scrubbed env (§5, demo-trust).

Seams:
  IPluginLoader  — install/list/enable/remove plugins; surface plugin-provided
                   tools (source="plugin") + the skills a plugin injects.
  ISandbox       — the execution boundary. `run(...)` is the single choke point
                   where untrusted code runs; today = child process + env scrub,
                   future = container (ADR). Keeping it behind a Protocol means the
                   backend can be hardened without touching callers.

**FROZEN.** `plugin/loader.py`, `plugin/manifest.py`, `sandbox/runner.py` implement
these. The artifact manifest schema is owned by CONTRACTS §2.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..core.types import InjectedSkill, PluginAgent, ToolCall, ToolResult, ToolSpec


@dataclass(frozen=True)
class PluginInfo:
    id: str
    name: str
    version: str
    description: str
    enabled: bool
    required_connectors: tuple[str, ...] = ()  # connector_type list (CONTRACTS §2.1)
    skill_count: int = 0
    # 스킬(두 레이아웃) + 커맨드(":" 네임스페이스) 이름 — bundle.display_skill_names.
    skill_names: tuple[str, ...] = ()
    # agents/*.md 페르소나 이름 — delegate_subagent(agent=...) 로 위임 가능(2026-07-19).
    agent_names: tuple[str, ...] = ()
    # 설치 시 위험 스캔 결과 — "<skill_name>:<label>" (core/guard.py 안정 코드,
    # 로케일 문장은 프론트 learningWarnings.ts). 비어있으면 깨끗. 플래그가 있는
    # 플러그인은 enabled=False 로 설치되고 활성화는 사용자 판단(CONTRACTS §2.3).
    risk_flags: tuple[str, ...] = ()
    # ``builtin`` plugins are package assets and may only be enabled/disabled.
    source: str = "installed"
    author: str = ""


@dataclass(frozen=True)
class SandboxResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    result: Any = None
    error: str | None = None


@runtime_checkable
class ISandbox(Protocol):
    """The execution boundary (RUNTIME_DESIGN §5). The *only* place untrusted
    plugin/connector code is run."""

    async def run(
        self,
        *,
        entrypoint: str,
        payload: dict[str, Any],
        timeout_s: float = 30.0,
        sys_path: list[str] | None = None,
    ) -> SandboxResult:
        """Run `entrypoint` in an isolated child process (env-scrubbed) with the
        given payload, enforcing a timeout. Never executes in-process.

        `sys_path`: extra import roots prepended to the child's module search path
        (e.g. an installed plugin's dir) so `entrypoint`'s dotted module imports.
        Still env-scrubbed — only these paths are added, no parent secrets."""
        ...


@runtime_checkable
class IPluginLoader(Protocol):
    # ── tool/skill surface (orchestrator) ────────────────────────────────────
    def list_tools(self) -> list[ToolSpec]:
        """Tools from enabled plugins (source="plugin")."""
        ...

    def injected_skills(self) -> list[InjectedSkill]:
        """Active SKILL.md bundles + commands/*.md from enabled plugins. The
        orchestrator feeds these into learning.prefetch as `extra_skills` — they
        ride the same relevance pipeline (index → top-K full body → skill_view)
        as learned skills instead of being injected wholesale."""
        ...

    def injected_agents(self) -> list[PluginAgent]:
        """Specialist worker personas from enabled plugins' `agents/*.md`.
        Consumed by agent/subagents.py: `delegate_subagent(agent=<name>)` swaps
        the worker's system prompt for the persona body (2026-07-19)."""
        ...

    async def invoke(self, call: ToolCall, owner_id: str | None = None) -> ToolResult:
        """Run a plugin tool via the sandbox (after dispatcher's approval check).
        `owner_id` = the owning plugin id from the ToolSpec, so same-named tools
        across plugins never misroute; None → cross-plugin name scan."""
        ...

    # ── management (routes/plugins.py, routes/artifacts.py) ──────────────────
    def list_plugins(self) -> list[PluginInfo]: ...
    def get_plugin(self, plugin_id: str) -> PluginInfo | None: ...
    def set_enabled(self, plugin_id: str, enabled: bool) -> None: ...

    def reconcile_builtin_shadows(self) -> int:
        """부팅 1회 위생 훅(composition_root): 빌트인으로 승격된 플러그인의 과거
        market 설치본(DB 행+디렉토리)을 정리한다. 빌트인 카탈로그가 모든 조회를
        가리는데 remove()는 빌트인 id를 거부하므로, 이 훅 없이는 지울 방법이 없는
        유령 설치본이 영구히 남는다. 정리 건수를 반환."""
        ...

    def reconcile_installed_skill_sources(self) -> int:
        """부팅 시 구버전 market 설치 스킬의 ``source`` frontmatter를 보충한다."""
        ...

    async def install(self, *, path: str, manifest: dict[str, Any]) -> PluginInfo:
        """Verify checksum, place under `~/.rice/{skills|plugins}/`, load
        (CONTRACTS §2.3)."""
        ...

    async def remove(self, artifact_id: str) -> None: ...
