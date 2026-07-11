"""`~/.rice/` directory layout (RUNTIME_DESIGN §7).

The single source of truth for *where things live on disk*. Every module that
touches the filesystem goes through `RicePaths` so the layout can be relocated
(tests, eval cold/warm profiles) by changing one root.

    ~/.rice/
      state.db        # sessions/messages/memories (WAL) — core/storage
      skills/         # SKILL.md + .usage.json sidecars + <name>/.history/
      plugins/        # installed plugins
      connectors/     # connector connection meta (credentials live in OS keychain)
      config.yaml     # settings
      logs/
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


class WorkspaceLocation:
    """Mutable holder for the *live* workspace root (no-restart relocation).

    Builtin file tools read ``paths.workspace`` at call time, so pointing this
    holder at a new directory redirects every file/office/vision/terminal tool
    without an app restart. To keep the invariant "a single turn never spans two
    roots" (RUNTIME_DESIGN §10), the route only *queues* a change and the
    orchestrator ``promote()``s it at a turn boundary — mirroring how provider/
    model switches are read live from config each turn, not mid-turn.
    """

    def __init__(self, initial: Path) -> None:
        self._current = self._prepare(initial)
        self._pending: Path | None = None

    @staticmethod
    def _prepare(p: Path) -> Path:
        p = p.expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p.resolve()

    @property
    def current(self) -> Path:
        """The root tools write to right now (stable within a turn)."""
        return self._current

    @property
    def display(self) -> Path:
        """What the UI should show: the queued target if one is waiting, else
        the live root. The two differ only in the idle window between picking a
        folder and the next turn — no tool runs there, so nothing observes it."""
        return self._pending or self._current

    def queue(self, new_root: Path) -> None:
        """Record a root to switch to at the next turn boundary."""
        self._pending = new_root.expanduser().resolve()

    def promote(self) -> Path:
        """Apply a queued change (called at a turn boundary). Idempotent."""
        if self._pending is not None:
            self._current = self._prepare(self._pending)
            self._pending = None
        return self._current


@dataclass(frozen=True)
class RicePaths:
    root: Path
    # User-selected output root. Configuration and state still remain under
    # ``root``; only agent-created workspace files move to this directory.
    workspace_root: Path | None = None
    # Live relocation holder (composition root attaches one). When present it is
    # the authoritative source for ``workspace`` so a folder change takes effect
    # next turn without a restart. Excluded from equality so tests comparing
    # paths by (root, workspace_root) are unaffected.
    location: "WorkspaceLocation | None" = field(default=None, compare=False)

    @classmethod
    def from_env(cls, override: str | os.PathLike[str] | None = None) -> "RicePaths":
        """Resolve the data root. Precedence: explicit override → `$RICE_HOME`
        → `~/.rice`. Tests and the eval harness (cold/warm profiles) pass an
        override to get an isolated root."""
        if override is not None:
            root = Path(override)
        elif os.environ.get("RICE_HOME"):
            root = Path(os.environ["RICE_HOME"])
        else:
            root = Path.home() / ".rice"
        return cls(root=root.expanduser().resolve())

    # ── derived paths ────────────────────────────────────────────────────────
    @property
    def db(self) -> Path:
        return self.root / "state.db"

    @property
    def skills(self) -> Path:
        return self.root / "skills"

    @property
    def plugins(self) -> Path:
        return self.root / "plugins"

    @property
    def connectors(self) -> Path:
        return self.root / "connectors"

    @property
    def config(self) -> Path:
        return self.root / "config.yaml"

    @property
    def mcp_config(self) -> Path:
        """`~/.rice/.mcp.json` — 로컬 MCP 서버 정의(Claude 표준 포맷 `mcpServers`).
        사용자가 stdio/HTTP 로컬 MCP 서버를 선언하는 단일 소스(connector/local 참고)."""
        return self.root / ".mcp.json"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def workspace(self) -> Path:
        """Sandbox root for builtin file tools.

        Defaults to ``~/.rice/workspace/`` but may be relocated by the user's
        saved workspace setting. When a live ``location`` holder is attached it
        wins, so a mid-session folder change (promoted at a turn boundary) is
        reflected here without a restart. All agent read/write/search remains
        confined to this *single* root (RUNTIME_DESIGN §10 — least authority).
        """
        if self.location is not None:
            return self.location.current
        return (self.workspace_root or self.root / "workspace").expanduser().resolve()

    def skill_history(self, name: str) -> Path:
        """`~/.rice/skills/<name>/.history/` — prior SKILL.md versions for
        rollback/diff (LEARNING_LOOP §5.2.4)."""
        return self.skills / name / ".history"

    def ensure(self) -> "RicePaths":
        """Create the directory skeleton if missing. Idempotent."""
        for d in (self.root, self.skills, self.plugins, self.connectors, self.logs, self.workspace):
            d.mkdir(parents=True, exist_ok=True)
        return self
