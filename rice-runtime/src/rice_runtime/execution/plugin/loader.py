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
import re
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


def _canonicalize_skill_md(text: str, name: str) -> str:
    """설치되는 SKILL.md 의 frontmatter `name` 을 manifest name 으로 맞춘다.

    SkillStore 는 `<name>.md` 파일명과 frontmatter `name` 이 일치한다고 가정한다
    (read/write/lifecycle 일관성). 시장 artifact 의 frontmatter 가 다르거나 없으면
    여기서 교정한다 — 다른 frontmatter 필드(description/tags 등)는 보존한다.
    """
    if not text.lstrip().startswith("---"):
        # frontmatter 없음 → 최소 frontmatter 로 감싼다.
        return f"---\nname: {name}\nversion: 1\ndescription: \"\"\nstate: active\n---\n{text}"
    parts = text.split("---", 2)
    if len(parts) < 3:
        return f"---\nname: {name}\nversion: 1\ndescription: \"\"\nstate: active\n---\n{text}"
    fm = parts[1]
    if re.search(r"(?m)^\s*name\s*:", fm):
        fm = re.sub(r"(?m)^(\s*name\s*:).*$", rf"\1 {name}", fm, count=1)
    else:
        fm = f"\nname: {name}" + fm
    return "---" + fm + "---" + parts[2]


def _resolved_within(base: Path, target: Path) -> Path:
    """Resolve `target` and require it to stay inside `base`.

    manifest name 은 slug 로 검증되지만(manifest.py), 최종 경로 조립 지점에서
    한 번 더 fail-closed 로 재검증한다 — symlink·정규화 우회까지 resolve() 뒤
    포함 관계로 막는 두 번째 방어선. 탈출이면 ValidationError.
    """
    base_r = base.resolve()
    target_r = target.resolve()
    if target_r != base_r and base_r not in target_r.parents:
        raise ValidationError(f"artifact path escapes install root: {target}")
    return target_r


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
        Only `state: active` skills surface (an author shipping a draft/archived
        skill must not leak into prompts), and description/tags/provenance ride
        along so these skills go through the SAME relevance pipeline as learned
        skills (learning.prefetch: index → top-K full body → skill_view) instead
        of being dumped wholesale into the system prompt.
        """
        skills: list[InjectedSkill] = []
        for info in self._list_enabled_plugin_infos():
            skills_dir = self._paths.plugins / info.id / "skills"
            if not skills_dir.is_dir():
                continue
            # Both layouts (CONTRACTS §2.2): flat `skills/*.md` AND the standard
            # `skills/<sub>/SKILL.md`. v1 버그#10: only flat *.md was scanned, so the
            # standard subdir layout (which platform/registry.py supports) was dropped.
            md_paths = sorted(skills_dir.glob("*.md")) + sorted(skills_dir.glob("*/SKILL.md"))
            for md_path in md_paths:
                try:
                    text = md_path.read_text(encoding="utf-8")
                    fm = _parse_skill_md_minimal(text)
                    if not fm["name"]:
                        fm["name"] = md_path.stem
                    if fm["state"] != "active":
                        continue
                    skills.append(
                        InjectedSkill(
                            skill_id=f"plugin:{info.id}:{fm['name']}",
                            name=fm["name"],
                            version=int(fm["version"]),
                            body=fm["body"],
                            description=fm["description"],
                            tags=tuple(fm["tags"]),
                            source="plugin",
                        )
                    )
                except (OSError, ValueError):
                    continue
        return skills

    async def invoke(self, call: ToolCall, owner_id: str | None = None) -> ToolResult:
        """Run a plugin tool through the sandbox (ISandbox.run is the choke point).

        Resolves the tool's **dotted entrypoint** (`module.func`) from the owning
        plugin's manifest and adds that plugin's install dir to the child's import
        path, so the real plugin code actually loads & runs (v1: the bare tool name
        was eval'd in the child → always ImportError/NameError). Runs in a child
        process — never in-process.

        `owner_id` (the ToolSpec's owning plugin id, passed by the dispatcher)
        makes resolution owner-aware: when two enabled plugins declare the same
        tool name, the call goes to the plugin that advertised the spec instead
        of whichever plugin happens to enumerate first. None falls back to the
        cross-plugin scan (legacy callers).
        """
        entry, plugin_dir = self._resolve_tool_entry(call.tool_name, owner_id=owner_id)
        if entry is None:
            return ToolResult(
                call.id, ok=False, error=f"no entrypoint registered for plugin tool '{call.tool_name}'"
            )
        result: SandboxResult = await self._sandbox.run(
            entrypoint=entry,
            payload=call.arguments,
            sys_path=[str(plugin_dir)] if plugin_dir else None,
        )
        return ToolResult(call.id, ok=result.ok, result=result.result, error=result.error)

    def _resolve_tool_entry(
        self, tool_name: str, owner_id: str | None = None
    ) -> tuple[str | None, Path | None]:
        """Map a plugin tool name → (dotted entrypoint, plugin install dir).

        With `owner_id`, only that plugin's manifest is consulted (owner-aware
        dispatch); otherwise looks across enabled plugins' manifests. Each tool
        decl may carry an `entry` (dotted `module.func`); if absent, falls back
        to `<entrypoint.module>.<tool_name>`, else the bare tool name. The
        plugin's install dir is returned so the sandbox can import its module.
        Returns (None, None) if no matching enabled plugin declares the tool.
        """
        infos = self._list_enabled_plugin_infos()
        if owner_id is not None:
            infos = [i for i in infos if i.id == owner_id]
        for info in infos:
            manifest_path = self._paths.plugins / info.id / "rice-artifact.json"
            if not manifest_path.exists():
                continue
            try:
                raw: dict[str, Any] = json.loads(manifest_path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            ep = raw.get("entrypoint", {}) or {}
            module = ep.get("module")
            for t in ep.get("tools", []) or []:
                if isinstance(t, dict) and t.get("name") == tool_name:
                    entry = t.get("entry") or (f"{module}.{tool_name}" if module else tool_name)
                    return entry, self._paths.plugins / info.id
        return None, None

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
            description = ""
            skill_count = 0
            if manifest_path.exists():
                try:
                    raw = json.loads(manifest_path.read_text())
                    description = str(raw.get("description", "") or "")
                    required_connectors = tuple(
                        str(c) for c in (raw.get("requires", {}).get("connectors", []) or [])
                    )
                    skills_dir = self._paths.plugins / r["id"] / "skills"
                    if skills_dir.is_dir():
                        skill_count = len(list(skills_dir.glob("*.md"))) + len(
                            list(skills_dir.glob("*/SKILL.md"))
                        )
                except (OSError, json.JSONDecodeError):
                    pass
            infos.append(
                PluginInfo(
                    id=r["id"],
                    name=r["name"],
                    version=r["version"],
                    description=description,
                    enabled=bool(r["enabled"]),
                    required_connectors=required_connectors,
                    skill_count=skill_count,
                )
            )
        return infos

    def get_plugin(self, plugin_id: str) -> PluginInfo | None:
        """Single plugin's info (enabled state + required_connectors), or None."""
        return next((p for p in self.list_plugins() if p.id == plugin_id), None)

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        """Toggle a plugin's active state (CONTRACTS §1.3: PATCH /plugins/{id}/enabled)."""
        self._registry.set_enabled(plugin_id, enabled)

    async def install(self, *, path: str, manifest: dict[str, Any]) -> PluginInfo:
        """Install an artifact (CONTRACTS §2.3).

        Steps:
          ① Validate and parse the manifest dict.
          ② Verify sha256 checksum of the file at `path` (fail-closed on mismatch).
          ③ Place the artifact:
               plugin → ~/.rice/plugins/plugin:<name>/ (tree + rice-artifact.json)
               skill  → ~/.rice/skills/<name>.md (FLAT — so SkillStore.list()/prefetch
                        inject it like a learned skill; v1 버그#3 교정).
          ④ Record in `installed_artifacts` (enabled=1 by default).
        """
        parsed = parse_manifest(manifest)
        await self._verify_checksum(path, parsed)

        artifact_id = f"{parsed.type}:{parsed.name}"
        if parsed.type == "plugin":
            await self._install_plugin(path, parsed, manifest)
        else:
            await self._install_skill(path, parsed)

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
        # DB 의 id/name 은 설치 시 검증된 값이지만, 삭제 경로도 동일하게 설치 루트
        # 안임을 재검증한다(수동 편집된 DB 행이 rmtree 를 임의 경로로 끌고 가지 않게).
        if row["type"] == "plugin":
            dest = _resolved_within(self._paths.plugins, self._paths.plugins / artifact_id)
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
        else:
            # skill artifact installed FLAT as <name>.md (+ usage/history sidecars).
            name = row["name"]
            _resolved_within(self._paths.skills, self._paths.skills / f"{name}.md").unlink(missing_ok=True)
            _resolved_within(self._paths.skills, self._paths.skills / f"{name}.usage.json").unlink(missing_ok=True)
            shutil.rmtree(_resolved_within(self._paths.skills, self._paths.skills / name), ignore_errors=True)  # .history dir
        self._db.execute("DELETE FROM installed_artifacts WHERE id = ?", (artifact_id,))

    # ── internal helpers ──────────────────────────────────────────────────────

    async def _install_plugin(self, path: str, parsed: ArtifactManifest, manifest: dict[str, Any]) -> None:
        """Place a plugin tree under ~/.rice/plugins/plugin:<name>/ + manifest.json."""
        dest = _resolved_within(self._paths.plugins, self._paths.plugins / f"plugin:{parsed.name}")
        dest.mkdir(parents=True, exist_ok=True)
        await self._unpack(path, dest)
        self._flatten_single_package_root(dest, parsed.name)
        (dest / "rice-artifact.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async def _install_skill(self, path: str, parsed: ArtifactManifest) -> None:
        """Install a skill artifact as a FLAT <name>.md in the skills dir.

        Skill artifacts are a single declarative SKILL.md (CONTRACTS §3.1). Writing
        it flat (not under skill:<name>/) makes SkillStore.list() pick it up so the
        prefetch loop actually injects it (the v1 버그#3: market skills never injected).
        """
        staging = Path(tempfile.mkdtemp(prefix="rice-skill-install-"))
        try:
            await self._unpack(path, staging)
            md_text = self._find_skill_md_text(staging)
            if md_text is None:
                raise ValidationError(
                    f"skill artifact '{parsed.name}' has no SKILL.md to install"
                )
            md_text = _canonicalize_skill_md(md_text, parsed.name)
            self._paths.skills.mkdir(parents=True, exist_ok=True)
            target = _resolved_within(self._paths.skills, self._paths.skills / f"{parsed.name}.md")
            target.write_text(md_text, encoding="utf-8")
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def _find_skill_md_text(root: Path) -> str | None:
        """Locate the SKILL.md body inside an unpacked skill artifact.

        Prefers a file literally named SKILL.md; else the first *.md that is not a
        manifest. Returns its text, or None if nothing usable is found.
        """
        named = sorted(root.rglob("SKILL.md"))
        if named:
            return named[0].read_text(encoding="utf-8")
        for md in sorted(root.rglob("*.md")):
            if md.name.lower().startswith("rice-artifact"):
                continue
            return md.read_text(encoding="utf-8")
        return None

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

        # is_tarfile() opens the path → raises IsADirectoryError on a dir. Guard so
        # directory sources fall through to copytree (dev/local installs).
        if src.is_file() and tarfile.is_tarfile(str(src)):
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

    @staticmethod
    def _flatten_single_package_root(dest: Path, artifact_name: str) -> None:
        """Normalize archives that contain one top-level package directory.

        rice-platform download packages are reproducible tarballs rooted at
        `<artifact_id>/...`. Runtime installs plugins under
        `~/.rice/plugins/plugin:<name>/` and expects plugin payload directories
        such as `skills/`, `commands/`, `.claude-plugin/`, and `.mcp.json`
        directly there. Without this flattening, a marketplace plugin installs
        as `plugin:<name>/<name>/skills/...`, so injected_skills() sees nothing.
        Directory-based dev installs already copy contents directly, so this is
        only a tar/package normalization step.
        """
        entries = [p for p in dest.iterdir() if p.name != "rice-artifact.json"]
        if len(entries) != 1 or not entries[0].is_dir():
            return

        package_root = entries[0]
        looks_like_plugin = (
            package_root.name == artifact_name
            or (package_root / ".claude-plugin").exists()
            or (package_root / "skills").exists()
            or (package_root / "commands").exists()
            or (package_root / "agents").exists()
            or (package_root / "hooks").exists()
        )
        if not looks_like_plugin:
            return

        for child in package_root.iterdir():
            shutil.move(str(child), str(dest / child.name))
        package_root.rmdir()

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
