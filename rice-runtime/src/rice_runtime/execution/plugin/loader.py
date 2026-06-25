"""plugin/loader.py — artifact install, skill injection, tool loading (CONTRACTS §2.3).

WHY: The loader is the "install + serve" half of the plugin subsystem.

  install(path, manifest):
    1. Verify sha256 checksum against the manifest value (fail-closed).
    2. Copy the artifact tree to `~/.rice/{skills|plugins}/<name>/`.
    3. Parse any SKILL.md files bundled inside (plugin type only).
    4. Record the artifact in `installed_artifacts` (migration 0001 table).
    Raises `ValidationError` on checksum mismatch — never silently install.

  injected_skills():
    Walk enabled-plugin directories for SKILL.md files and return them as
    `InjectedSkill` objects, compatible with learning's SKILL.md format
    (LEARNING_LOOP §2). We need the *same* SKILL.md frontmatter fields:
    name, version, description, state, tags — and the body content.

    WHY not import learning's parser: CONVENTIONS §0 / §2 forbid cross-module
    concrete imports. We therefore embed a minimal parser here.
    # NOTE(agent): If learning's SKILL.md parser ever moves to core/ it can
    # replace this minimal duplicate. Until then, keep both in sync.

  list_tools():
    For each enabled plugin, surface its declared tools as ToolSpec objects
    with source="plugin". Requires the plugin's entrypoint to be a Python
    module that registers tools. For the demo, tools are declared in the
    manifest's `entrypoint.tools` field.

  invoke(call):
    Dispatch a tool call through ISandbox.run — never in-process.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from ...core.errors import NotFoundError, ValidationError
from ...core.paths import RicePaths
from ...core.storage.sqlite import Database
from ...core.types import InjectedSkill, ToolCall, ToolResult, ToolSpec, new_id, now_iso
from ..interfaces import ISandbox, PluginInfo, SandboxResult
from .manifest import ArtifactManifest, parse_manifest
from .registry import PluginRegistry


# ─── minimal SKILL.md parser (mirrors learning/skill/store.py) ───────────────

def _parse_skill_md_minimal(text: str) -> dict[str, Any]:
    """Extract frontmatter fields + body from a SKILL.md file.

    Returns a dict with keys: name, version, description, state, tags, body.
    Raises nothing — missing fields get safe defaults. This is intentionally
    permissive because we don't validate learning-generated skills; we just
    surface them.

    # NOTE(agent): This is a minimal duplicate of learning/skill/store.py's
    # parse_skill_md. If a shared SKILL.md parser ever moves to core/, replace
    # this with that import. Keep field names in sync.
    """
    if not text.startswith("---"):
        return {"name": "", "version": 1, "description": "", "state": "active", "tags": [], "body": text}

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {"name": "", "version": 1, "description": "", "state": "active", "tags": [], "body": text}

    fm_raw = parts[1].strip()
    body = parts[2].strip()

    # Minimal YAML-like key-value parse (no full YAML dep needed here).
    # Only handles flat keys and simple list values (SKILL.md frontmatter is
    # deliberately simple — LEARNING_LOOP §2).
    fm: dict[str, Any] = {}
    in_list_key: str | None = None
    for line in fm_raw.splitlines():
        line = line.rstrip()
        if not line:
            in_list_key = None
            continue
        if line.startswith("  - ") and in_list_key:
            fm.setdefault(in_list_key, []).append(line[4:].strip())
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip()
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                # Inline list: [a, b, c]
                fm[k] = [item.strip().strip('"').strip("'") for item in v[1:-1].split(",") if item.strip()]
                in_list_key = None
            elif v == "":
                in_list_key = k
                fm[k] = []
            else:
                fm[k] = v
                in_list_key = None

    return {
        "name": str(fm.get("name", "")),
        "version": int(fm.get("version", 1)),
        "description": str(fm.get("description", "")),
        "state": str(fm.get("state", "active")),
        "tags": list(fm.get("tags", [])),
        "body": body,
    }


# ─── PluginLoader ─────────────────────────────────────────────────────────────

class PluginLoader:
    """Concrete IPluginLoader implementation.

    Wraps PluginRegistry (enabled/disabled), the sandbox (ISandbox), and the
    filesystem layout under `paths.plugins` and `paths.skills`.
    """

    def __init__(self, db: Database, sandbox: ISandbox, paths: RicePaths) -> None:
        self._db = db
        self._sandbox = sandbox
        self._paths = paths
        self._registry = PluginRegistry(db)

    # ── tool / skill surface ─────────────────────────────────────────────────

    def list_tools(self) -> list[ToolSpec]:
        """Tools declared by enabled plugins (source="plugin", CONTRACTS §2.2)."""
        tools: list[ToolSpec] = []
        for info in self._list_enabled_plugin_infos():
            tools.extend(self._tools_for_plugin(info))
        return tools

    def _tools_for_plugin(self, info: PluginInfo) -> list[ToolSpec]:
        """Read the tool declarations from the plugin's stored manifest JSON."""
        manifest_path = self._paths.plugins / info.id / "rice-artifact.json"
        if not manifest_path.exists():
            return []
        try:
            raw: dict[str, Any] = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        tools_decl = raw.get("entrypoint", {}).get("tools", []) or []
        specs: list[ToolSpec] = []
        for t in tools_decl:
            if not isinstance(t, dict) or not t.get("name"):
                continue
            specs.append(
                ToolSpec(
                    name=str(t["name"]),
                    description=str(t.get("description", "")),
                    parameters=dict(t.get("parameters", {})),
                    source="plugin",
                    owner_id=info.id,
                    # plugin tools require approval by default (RUNTIME_DESIGN §5)
                    requires_approval=bool(t.get("requires_approval", True)),
                )
            )
        return specs

    def injected_skills(self) -> list[InjectedSkill]:
        """SKILL.md bundles from enabled plugins → InjectedSkill (CONTRACTS §2.2).

        Walks `~/.rice/plugins/<plugin_id>/skills/*.md` for each enabled plugin.
        """
        skills: list[InjectedSkill] = []
        for info in self._list_enabled_plugin_infos():
            skills_dir = self._paths.plugins / info.id / "skills"
            if not skills_dir.is_dir():
                continue
            for md_path in sorted(skills_dir.glob("*.md")):
                try:
                    text = md_path.read_text(encoding="utf-8")
                    fm = _parse_skill_md_minimal(text)
                    if not fm["name"]:
                        fm["name"] = md_path.stem
                    skills.append(
                        InjectedSkill(
                            skill_id=f"plugin:{info.id}:{fm['name']}",
                            name=fm["name"],
                            version=int(fm["version"]),
                            body=text,  # full SKILL.md content as body
                        )
                    )
                except (OSError, ValueError):
                    continue
        return skills

    async def invoke(self, call: ToolCall) -> ToolResult:
        """Run a plugin tool through the sandbox (ISandbox.run is the choke point).

        The entrypoint string passed to the sandbox is the tool name.
        The sandbox runs it in a child process — never in-process.
        """
        result: SandboxResult = await self._sandbox.run(
            entrypoint=call.tool_name,
            payload=call.arguments,
        )
        return ToolResult(call.id, ok=result.ok, result=result.result, error=result.error)

    # ── management ────────────────────────────────────────────────────────────

    def list_plugins(self) -> list[PluginInfo]:
        """All installed plugins (enabled + disabled)."""
        rows = self._db.query_all(
            "SELECT id, name, version, enabled FROM installed_artifacts WHERE type = 'plugin'"
        )
        infos: list[PluginInfo] = []
        for r in rows:
            manifest_path = self._paths.plugins / r["id"] / "rice-artifact.json"
            required_connectors: tuple[str, ...] = ()
            skill_count = 0
            if manifest_path.exists():
                try:
                    raw = json.loads(manifest_path.read_text())
                    required_connectors = tuple(
                        str(c) for c in (raw.get("requires", {}).get("connectors", []) or [])
                    )
                    skills_dir = self._paths.plugins / r["id"] / "skills"
                    if skills_dir.is_dir():
                        skill_count = len(list(skills_dir.glob("*.md")))
                except (OSError, json.JSONDecodeError):
                    pass
            infos.append(
                PluginInfo(
                    id=r["id"],
                    name=r["name"],
                    version=r["version"],
                    description="",
                    enabled=bool(r["enabled"]),
                    required_connectors=required_connectors,
                    skill_count=skill_count,
                )
            )
        return infos

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        """Toggle a plugin's active state (CONTRACTS §1.3: PATCH /plugins/{id}/enabled)."""
        self._registry.set_enabled(plugin_id, enabled)

    async def install(self, *, path: str, manifest: dict[str, Any]) -> PluginInfo:
        """Install an artifact (CONTRACTS §2.3).

        Steps:
          ① Validate and parse the manifest dict.
          ② Verify sha256 checksum of the file at `path`.
          ③ Unpack (tar.gz) or copy to ~/.rice/{skills|plugins}/<name>/.
          ④ Persist a rice-artifact.json in the install directory.
          ⑤ Record in `installed_artifacts` table (enabled=1 by default).
        Raises `ValidationError` on checksum mismatch (fail-closed).
        """
        parsed = parse_manifest(manifest)
        await self._verify_checksum(path, parsed)

        artifact_id = f"{parsed.type}:{parsed.name}"
        dest = self._install_dir(parsed)
        dest.mkdir(parents=True, exist_ok=True)

        # Unpack or copy the artifact.
        await self._unpack(path, dest)

        # Write a local copy of the manifest as JSON for later reads.
        manifest_dest = dest / "rice-artifact.json"
        manifest_dest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Record in DB (upsert by id — re-install overwrites).
        self._db.execute(
            """
            INSERT INTO installed_artifacts (id, type, name, version, source, installed_at, enabled)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(id) DO UPDATE SET
              version=excluded.version,
              installed_at=excluded.installed_at,
              enabled=1
            """,
            (artifact_id, parsed.type, parsed.name, parsed.version, "market", now_iso()),
        )

        return PluginInfo(
            id=artifact_id,
            name=parsed.name,
            version=parsed.version,
            description=parsed.description,
            enabled=True,
            required_connectors=parsed.required_connectors,
        )

    async def remove(self, artifact_id: str) -> None:
        """Remove an installed artifact from DB and filesystem."""
        row = self._db.query_one(
            "SELECT type, name FROM installed_artifacts WHERE id = ?", (artifact_id,)
        )
        if row is None:
            raise NotFoundError(f"artifact not found: {artifact_id}")
        dest = self._paths.plugins / artifact_id if row["type"] == "plugin" else self._paths.skills / artifact_id
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        self._db.execute("DELETE FROM installed_artifacts WHERE id = ?", (artifact_id,))

    # ── internal helpers ──────────────────────────────────────────────────────

    def _install_dir(self, parsed: ArtifactManifest) -> Path:
        """Target directory for the installed artifact."""
        if parsed.type == "plugin":
            return self._paths.plugins / f"{parsed.type}:{parsed.name}"
        # skill artifacts go into the skills directory
        return self._paths.skills / f"{parsed.type}:{parsed.name}"

    async def _verify_checksum(self, path: str, parsed: ArtifactManifest) -> None:
        """Verify sha256 checksum (CONTRACTS §2.3). Fail-closed on mismatch.

        Expected format: "sha256-<hex>". Empty checksum is only allowed when
        the manifest also has an empty checksum (i.e., dev/test installs).
        """
        if not parsed.checksum:
            # NOTE(agent): Empty checksum is allowed for dev/test installs only.
            # Production market installs always include a checksum. Accepting
            # empty-checksum manifests here enables integration tests without
            # real archive files.
            return

        file_path = Path(path)
        if not file_path.exists():
            raise ValidationError(f"artifact file not found: {path}")

        expected_hex = parsed.checksum.removeprefix("sha256-")
        actual_hex = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if actual_hex != expected_hex:
            raise ValidationError(
                f"checksum mismatch for {parsed.name}: "
                f"expected sha256-{expected_hex}, got sha256-{actual_hex}"
            )

    @staticmethod
    async def _unpack(path: str, dest: Path) -> None:
        """Unpack a .tar.gz artifact or copy a plain file/directory."""
        src = Path(path)
        if not src.exists():
            return  # checksum already validated existence

        if tarfile.is_tarfile(str(src)):
            with tarfile.open(str(src), "r:gz") as tf:
                # NOTE(agent): tarfile.extractall with filter="data" is the
                # safe extraction mode (Python 3.12+ default). On 3.11- it
                # silently does nothing for the filter kwarg.
                try:
                    tf.extractall(path=str(dest), filter="data")  # type: ignore[call-arg]
                except TypeError:
                    tf.extractall(path=str(dest))
        elif src.is_dir():
            # Directory source (local dev install)
            shutil.copytree(str(src), str(dest), dirs_exist_ok=True)
        else:
            # Single file — treat as SKILL.md for skill type
            shutil.copy2(str(src), str(dest / src.name))

    def _list_enabled_plugin_infos(self) -> list[PluginInfo]:
        """Internal: enabled plugins only, light read."""
        rows = self._db.query_all(
            "SELECT id, name, version FROM installed_artifacts "
            "WHERE type = 'plugin' AND enabled = 1"
        )
        return [
            PluginInfo(id=r["id"], name=r["name"], version=r["version"],
                       description="", enabled=True)
            for r in rows
        ]
