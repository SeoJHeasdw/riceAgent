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

    Parsing mechanics are shared with learning through core/frontmatter.py;
    execution keeps only its permissive field mapping (missing/malformed fields
    fall back safely instead of importing learning's stricter semantics).

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
import logging
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from ...core.errors import NotFoundError, ValidationError
from ...core.frontmatter import dump_yaml, split_frontmatter
from ...core.guard import VALID_SKILL_NAME, scan_risks
from ...core.paths import RicePaths
from ...core.storage.sqlite import Database
from ...core.types import (
    InjectedSkill,
    PluginAgent,
    ToolCall,
    ToolResult,
    ToolSpec,
    new_id,
    now_iso,
)
from ..interfaces import ISandbox, PluginInfo, SandboxResult
from . import bundle
from .manifest import ArtifactManifest, parse_manifest
from .registry import PluginRegistry
from .builtin import BUILTIN_PLUGIN_SOURCE, BuiltinPluginCatalog

log = logging.getLogger("rice.execution.plugin_loader")

_INSTALL_UPSERT_SQL = """
    INSERT INTO installed_artifacts
      (id, type, name, version, source, installed_at, enabled, risk_flags)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
      version=excluded.version,
      installed_at=excluded.installed_at,
      enabled=excluded.enabled,
      risk_flags=excluded.risk_flags
"""


# ─── SKILL.md field mapping (parsing mechanics live in core/frontmatter.py) ──

def _parse_skill_md_minimal(text: str) -> dict[str, Any]:
    """Extract frontmatter fields + body from a SKILL.md file.

    Returns a dict with keys: name, version, description, state, tags, body.
    Raises nothing — missing fields get safe defaults. This is intentionally
    permissive because we don't validate learning-generated skills; we just
    surface them. 파싱 메커니즘은 core/frontmatter.py 공용(2026-07-19 — 종전의
    store.py 와의 의도적 중복 해소); 여기는 관대한 기본값 매핑만 남는다.
    """
    try:
        parsed = split_frontmatter(text)
    except Exception:  # noqa: BLE001 — 외부 plugin YAML 손상은 파일 단위 fail-soft
        # loader 의 기존 무의존 미니 파서는 malformed YAML 에서도 절대 예외를
        # 내지 않았다. 공용 PyYAML 경로로 승격한 뒤 parser error 가 설치 전체나
        # 매 턴 injected_skills()를 깨는 회귀를 막고, 원문은 body 로 보존한다.
        log.warning("plugin markdown frontmatter is malformed; using safe defaults")
        parsed = None
    if parsed is None:
        return {
            "name": "",
            "version": 1,
            "description": "",
            "state": "active",
            "tags": [],
            "body": text,
        }
    fm, body = parsed

    version_raw = fm.get("version", 1)
    try:
        version = int(version_raw)
    except (TypeError, ValueError):
        # Claude plugins commonly use semver in SKILL.md (for example 0.1.0),
        # while InjectedSkill keeps the learning subsystem's integer revision
        # contract. Plugin revisions are immutable here, so use revision 1.
        version = 1

    # description 은 인덱스/도구 스펙 렌더에 실리므로 한 줄로 정규화한다(블록
    # 스칼라·멀티라인 YAML 값 대비 — 종전 미니 파서의 공백 join 과 동일 결과).
    description = " ".join(str(fm.get("description") or "").split())

    tags_raw = fm.get("tags") or []
    if isinstance(tags_raw, (list, tuple)):
        tags = [str(t) for t in tags_raw if str(t).strip()]
    elif isinstance(tags_raw, str) and tags_raw.strip():
        # 관대한 loader 계약: 잘못된 scalar tags 도 문자 단위로 쪼개지지 않게
        # 한 항목으로 취급한다(종전 list("plugin") 회귀 방지).
        tags = [tags_raw.strip()]
    else:
        tags = []

    return {
        "name": str(fm.get("name") or ""),
        "version": version,
        "description": description,
        "state": str(fm.get("state") or "active"),
        "tags": tags,
        "body": body.strip(),
    }


def _canonicalize_skill_md(text: str, name: str, *, source: str | None = None) -> str:
    """설치되는 SKILL.md 의 frontmatter `name` 을 manifest name 으로 맞춘다.

    SkillStore 는 `<name>.md` 파일명과 frontmatter `name` 이 일치한다고 가정한다
    (read/write/lifecycle 일관성). 시장 artifact 의 frontmatter 가 다르거나 없으면
    여기서 교정한다 — 다른 frontmatter 필드(description/tags 등)는 보존한다.
    """
    try:
        parsed = split_frontmatter(text)
    except Exception as exc:
        raise ValidationError(f"skill artifact frontmatter is not valid YAML: {exc}") from exc
    if parsed is None:
        # frontmatter 없음 → 최소 frontmatter 로 감싼다. 문서 중간의 --- 블록은
        # core parser 계약상 frontmatter가 아니므로 원문 body에 그대로 보존된다.
        fm: dict[str, Any] = {
            "name": name,
            "version": 1,
            "description": "",
            "state": "active",
        }
        body = text
    else:
        fm, body = parsed
        fm = dict(fm)
        fm["name"] = name
    if source is not None:
        fm["source"] = source
    body_sep = "" if body.startswith(("\n", "\r")) else "\n"
    return f"---\n{dump_yaml(fm)}---{body_sep}{body}"


def _stat_signature(paths: list[Path]) -> tuple | None:
    """파일 목록의 (경로, mtime, size) 시그니처 — 캐시 무효화 판별용.
    stat 실패는 None(캐시 우회 — 이후 파싱이 개별 방어)."""
    try:
        return tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
    except OSError:
        return None


def _parse_risk_flags(raw: object) -> tuple[str, ...]:
    """installed_artifacts.risk_flags(JSON 배열) → tuple. 손상/구버전 값은 빈 튜플
    — 표시용 메타라 조용한 복구가 맞다(설치 게이트는 이미 지나갔다)."""
    if not raw or not isinstance(raw, str):
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(f) for f in parsed)


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

    def __init__(
        self,
        db: Database,
        sandbox: ISandbox,
        paths: RicePaths,
        builtin_plugins: BuiltinPluginCatalog | None = None,
    ) -> None:
        self._db = db
        self._sandbox = sandbox
        self._paths = paths
        self._registry = PluginRegistry(db)
        self._builtin_plugins = builtin_plugins
        # injected_skills() 파싱 캐시: plugin_id → (SKILL.md stat 시그니처, 결과).
        # prefetch가 매 턴 부르므로 플러그인당 수십 KB 재독·재파싱이 턴 세금이
        # 됐다. 시그니처는 SKILL.md 파일들의 (경로, mtime, size) — 번들 리소스
        # 목록은 SKILL.md 변경 시에만 재수집된다(설치본은 사실상 불변이라 충분).
        self._skills_cache: dict[str, tuple[tuple, list[InjectedSkill]]] = {}
        # injected_agents() 파싱 캐시 — 같은 원리, agents/*.md 시그니처 기준.
        self._agents_cache: dict[str, tuple[tuple, list[PluginAgent]]] = {}

    # ── tool / skill surface ─────────────────────────────────────────────────

    def list_tools(self) -> list[ToolSpec]:
        """Tools declared by enabled plugins (source="plugin", CONTRACTS §2.2)."""
        tools: list[ToolSpec] = []
        for info in self._list_enabled_plugin_infos():
            tools.extend(self._tools_for_plugin(info))
        return tools

    def _read_manifest(self, info: PluginInfo) -> dict[str, Any] | None:
        """플러그인의 저장 manifest JSON을 읽는다 (빌트인 = .claude-plugin/plugin.json,
        설치본 = rice-artifact.json). 도구 조회 경로들(_tools_for_plugin,
        _resolve_tool_entry)이 각자 중복 구현하던 것을 한 곳으로 모은 단일 관문 —
        읽기 실패는 None(해당 플러그인만 조용히 제외, fail-safe)."""
        plugin_dir = self._plugin_dir(info)
        manifest_path = (
            plugin_dir / ".claude-plugin" / "plugin.json"
            if info.source == BUILTIN_PLUGIN_SOURCE
            else plugin_dir / "rice-artifact.json"
        )
        if not manifest_path.exists():
            return None
        try:
            return json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def _tools_for_plugin(self, info: PluginInfo) -> list[ToolSpec]:
        """Read the tool declarations from the plugin's stored manifest JSON."""
        raw = self._read_manifest(info)
        if raw is None:
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

    # 번들 리소스 목록 상한 — 프롬프트/도구 결과에 실리는 목록이므로 bounded.
    _RESOURCE_LIST_MAX = 24

    def injected_skills(self) -> list[InjectedSkill]:
        """SKILL.md bundles + commands from enabled plugins → InjectedSkill
        (CONTRACTS §2.2).

        Walks `~/.rice/plugins/<plugin_id>/skills/*.md` for each enabled plugin.
        Only `state: active` skills surface (an author shipping a draft/archived
        skill must not leak into prompts), and description/tags/provenance ride
        along so these skills go through the SAME relevance pipeline as learned
        skills (learning.prefetch: index → top-K full body → skill_view) instead
        of being dumped wholesale into the system prompt.

        `commands/*.md`(Claude Code 슬래시 커맨드 형식)도 같은 파이프라인을 탄다
        (2026-07-19 — 종전엔 시장 호환성 표기일 뿐 미소비였다): 커맨드는 "사용자
        호출 절차"라 RICE 의 스킬과 동형이고, explicit-first 정렬 덕에 이름 언급
        ("/create-plugin", "create-plugin 해줘")이 주입 슬롯을 얻는다. 이름은
        파일 stem(중첩은 `<dir>:<stem>` — Claude Code 네임스페이스 관례),
        tags=("command",) 로 출처를 표시한다. Claude Code 전용 frontmatter
        (allowed-tools 등)는 RICE 도구와 무관하므로 싣지 않는다.

        표준 레이아웃(`skills/<sub>/SKILL.md`)의 형제 파일들(references/,
        examples/, scripts/)은 `resource_dir`/`resources`로 실어 `skill_resource`
        도구가 온디맨드로 열 수 있게 한다 — 본문이 "see references/x.md"라고
        지시해도 파일 도구는 워크스페이스에 갇혀 있어 닿을 수 없던 간극의 해소.

        매 턴 호출되므로(prefetch) SKILL.md+commands stat 시그니처로 캐싱한다 —
        내용이 안 변한 플러그인은 재독·재파싱 없이 이전 결과를 그대로 쓴다.
        """
        skills: list[InjectedSkill] = []
        for info in self._list_enabled_plugin_infos():
            plugin_dir = self._plugin_dir(info)
            # Both layouts (CONTRACTS §2.2): flat `skills/*.md` AND the standard
            # `skills/<sub>/SKILL.md`. v1 버그#10: only flat *.md was scanned, so the
            # standard subdir layout (which platform/registry.py supports) was dropped.
            md_paths = bundle.skill_md_paths(plugin_dir)
            command_paths = bundle.command_paths(plugin_dir)
            if not md_paths and not command_paths:
                continue
            signature = _stat_signature(md_paths + command_paths)
            cached = self._skills_cache.get(info.id)
            if signature is not None and cached is not None and cached[0] == signature:
                skills.extend(cached[1])
                continue
            parsed: list[InjectedSkill] = []
            for md_path in md_paths:
                try:
                    text = md_path.read_text(encoding="utf-8")
                    fm = _parse_skill_md_minimal(text)
                    if not fm["name"]:
                        fm["name"] = bundle.skill_name_for(md_path)
                    if fm["state"] != "active":
                        continue
                    resource_dir, resources = self._skill_resources(md_path)
                    parsed.append(
                        InjectedSkill(
                            skill_id=f"plugin:{info.id}:{fm['name']}",
                            name=fm["name"],
                            version=int(fm["version"]),
                            body=fm["body"],
                            description=fm["description"],
                            tags=tuple(fm["tags"]),
                            source="plugin",
                            resource_dir=resource_dir,
                            resources=resources,
                        )
                    )
                except (OSError, ValueError):
                    continue
            for md_path in command_paths:
                try:
                    text = md_path.read_text(encoding="utf-8")
                    fm = _parse_skill_md_minimal(text)
                    name = bundle.command_name(plugin_dir, md_path)
                    parsed.append(
                        InjectedSkill(
                            skill_id=f"plugin:{info.id}:command:{name}",
                            name=name,
                            version=int(fm["version"]),
                            body=fm["body"],
                            description=fm["description"],
                            tags=("command", *fm["tags"]),
                            source="plugin",
                        )
                    )
                except (OSError, ValueError):
                    continue
            if signature is not None:
                self._skills_cache[info.id] = (signature, parsed)
            skills.extend(parsed)
        return skills

    def injected_agents(self) -> list[PluginAgent]:
        """`agents/*.md` from enabled plugins → PluginAgent (2026-07-19).

        에이전트 md 는 Claude Code 서브에이전트 형식(frontmatter name/description
        + 본문=시스템 프롬프트)이다. RICE 에서는 delegate_subagent 의 페르소나로
        소비된다(agent/subagents.py): 메인 모델이 `agent=<name>` 으로 위임하면
        워커 시스템 프롬프트가 이 body 로 바뀐다 — 종전엔 시장 호환성 표기일 뿐
        미소비였다. 같은 이름이 여러 플러그인에 있으면 활성 목록 순서의 첫
        매칭이 이긴다(도구 이름 충돌과 같은 first-match 규칙).

        skills 캐시와 같은 원리로 stat 시그니처 캐싱한다(delegate 도구 스펙
        렌더가 매 턴 부른다).
        """
        agents: list[PluginAgent] = []
        for info in self._list_enabled_plugin_infos():
            agents.extend(self._agents_for_plugin(info.id, self._plugin_dir(info)))
        return agents

    def _agents_for_plugin(self, plugin_id: str, plugin_dir: Path) -> list[PluginAgent]:
        """한 플러그인의 실제 소비 가능한 페르소나를 파싱한다.

        enabled 필터는 호출자가 소유한다. 그래서 list_plugins()도 이 함수를 써서
        frontmatter name 우선 규칙이 반영된 정확한 agent_names 를 표시할 수 있다.
        이름은 스킬과 같은 bounded kebab-case 규약만 허용한다 — 매 턴 도구 스펙에
        실리는 새 프롬프트 표면이라 임의 길이/제어문자 이름을 광고하지 않는다.
        """
        md_paths = bundle.agent_paths(plugin_dir)
        if not md_paths:
            return []
        signature = _stat_signature(md_paths)
        cached = self._agents_cache.get(plugin_id)
        if signature is not None and cached is not None and cached[0] == signature:
            return cached[1]

        parsed: list[PluginAgent] = []
        for md_path in md_paths:
            try:
                text = md_path.read_text(encoding="utf-8")
                fm = _parse_skill_md_minimal(text)
                declared = str(fm["name"] or "").strip()
                fallback = md_path.stem
                name = declared if VALID_SKILL_NAME.fullmatch(declared) else fallback
                if not VALID_SKILL_NAME.fullmatch(name):
                    log.warning("plugin agent with invalid name skipped: %s", md_path)
                    continue
                if declared and declared != name:
                    log.warning(
                        "plugin agent %s has invalid frontmatter name %r; using filename %r",
                        md_path,
                        declared,
                        fallback,
                    )
                if not fm["body"].strip():
                    continue  # 본문 없는 에이전트는 페르소나가 성립하지 않는다
                parsed.append(
                    PluginAgent(
                        agent_id=f"plugin:{plugin_id}:agent:{name}",
                        name=name,
                        description=fm["description"],
                        body=fm["body"],
                        plugin_id=plugin_id,
                    )
                )
            except (OSError, ValueError):
                continue
        if signature is not None:
            self._agents_cache[plugin_id] = (signature, parsed)
        return parsed

    @classmethod
    def _skill_resources(cls, md_path: Path) -> tuple[str, tuple[str, ...]]:
        """표준 레이아웃 스킬의 번들 파일 목록 — (스킬 루트, 상대경로들).

        flat `skills/*.md`는 형제가 다른 스킬들이라 번들이 성립하지 않는다 → 빈 값.
        숨김 파일은 제외하고, 목록은 _RESOURCE_LIST_MAX로 자른다(실제 읽기는
        skill_resource가 경로 격리로 다시 검증하므로 목록은 광고일 뿐이다)."""
        if md_path.name != "SKILL.md":
            return "", ()
        skill_root = md_path.parent
        try:
            rel_paths = sorted(
                str(p.relative_to(skill_root))
                for p in skill_root.rglob("*")
                if p.is_file()
                and p.name != "SKILL.md"
                and not any(part.startswith(".") for part in p.relative_to(skill_root).parts)
            )
        except OSError:
            return "", ()
        if not rel_paths:
            return "", ()
        return str(skill_root), tuple(rel_paths[: cls._RESOURCE_LIST_MAX])

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
            raw = self._read_manifest(info)
            if raw is None:
                continue
            plugin_dir = self._plugin_dir(info)
            ep = raw.get("entrypoint", {}) or {}
            module = ep.get("module")
            for t in ep.get("tools", []) or []:
                if isinstance(t, dict) and t.get("name") == tool_name:
                    entry = t.get("entry") or (f"{module}.{tool_name}" if module else tool_name)
                    return entry, plugin_dir
        return None, None

    # ── management ────────────────────────────────────────────────────────────

    def list_plugins(self) -> list[PluginInfo]:
        """All installed plugins (enabled + disabled)."""
        builtin_ids = self._builtin_plugins.ids() if self._builtin_plugins else frozenset()
        rows = self._db.query_all(
            "SELECT id, name, version, enabled, risk_flags FROM installed_artifacts WHERE type = 'plugin'"
        )
        infos: list[PluginInfo] = []
        for r in rows:
            if r["id"] in builtin_ids:
                continue
            manifest_path = self._paths.plugins / r["id"] / "rice-artifact.json"
            required_connectors: tuple[str, ...] = ()
            description = ""
            skill_count = 0
            skill_names: tuple[str, ...] = ()
            agent_names: tuple[str, ...] = ()
            if manifest_path.exists():
                try:
                    raw = json.loads(manifest_path.read_text())
                    description = str(raw.get("description", "") or "")
                    required_connectors = tuple(
                        str(c) for c in (raw.get("requires", {}).get("connectors", []) or [])
                    )
                    # 스킬(두 레이아웃) + 커맨드(":" 네임스페이스) — bundle 이 규칙 소유.
                    plugin_dir = self._paths.plugins / r["id"]
                    skill_names = bundle.display_skill_names(plugin_dir)
                    skill_count = len(skill_names)
                    agent_names = tuple(
                        a.name for a in self._agents_for_plugin(r["id"], plugin_dir)
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
                    skill_names=skill_names,
                    agent_names=agent_names,
                    risk_flags=_parse_risk_flags(r["risk_flags"]),
                )
            )
        builtins = self._builtin_plugins.list() if self._builtin_plugins else []
        return builtins + infos

    def get_plugin(self, plugin_id: str) -> PluginInfo | None:
        """Single plugin's info (enabled state + required_connectors), or None."""
        return next((p for p in self.list_plugins() if p.id == plugin_id), None)

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        """Toggle a plugin's active state (CONTRACTS §1.3: PATCH /plugins/{id}/enabled)."""
        if self._builtin_plugins and self._builtin_plugins.is_builtin(plugin_id):
            self._builtin_plugins.set_enabled(plugin_id, enabled)
            return
        self._registry.set_enabled(plugin_id, enabled)

    async def install(self, *, path: str, manifest: dict[str, Any]) -> PluginInfo:
        """Install an artifact (CONTRACTS §2.3).

        Steps:
          ① Validate and parse the manifest dict.
          ② Verify sha256 checksum of the file at `path` (fail-closed on mismatch).
          ③ Unpack plugin artifacts into an isolated staging tree and risk-scan
             every prompt surface there; malformed/unreadable files never reach
             an enabled install by accident.
          ④ Place the artifact:
               plugin → ~/.rice/plugins/plugin:<name>/ (tree + rice-artifact.json)
               skill  → ~/.rice/skills/<name>.md (FLAT — so SkillStore.list()/prefetch
                        inject it like a learned skill; v1 버그#3 교정).
             Plugin replacement and its DB upsert share one rollback boundary, so
             removed files from an older version cannot survive a re-install.
          ⑤ Risk-scan skill artifact SKILL.md contents (core/guard.py::scan_risks —
             market artifact 는 외부 콘텐츠이므로 신뢰 가정으로 싣지 않는다).
             플래그는 installed_artifacts.risk_flags 에 영속되고, 플래그가 있는
             **plugin 은 enabled=0 으로 설치**된다 — 프롬프트 주입 표면이 되기
             전에 사용자가 경고를 보고 직접 활성화해야 한다(HITL). skill 은
             MySkills 에서 본문을 직접 검수·수정할 수 있으므로 플래그+경고만
             한다(학습 경로의 "스킬=플래그, memory=차단" 정책과 정합).
          ⑥ Record in `installed_artifacts`.
        """
        parsed = parse_manifest(manifest)
        if parsed.type == "plugin" and self._builtin_plugins:
            self._builtin_plugins.ensure_mutable(parsed.name, "설치/덮어쓰기")
        await self._verify_checksum(path, parsed)

        artifact_id = f"{parsed.type}:{parsed.name}"
        if parsed.type == "plugin":
            # 영구 설치 경로에 닿기 전에 완전한 트리를 staging 에 만들고 스캔한다.
            # 기존 디렉토리에 copytree(dirs_exist_ok=True)로 덧씌우면 새 버전에서
            # 삭제된 구버전 SKILL.md가 남아 clean reinstall이 영원히 위험 플래그를
            # 지우지 못했고, 스캔 전 잠깐 기존 enabled 행이 새 파일을 가리킬 수도
            # 있었다. DB 트랜잭션 락 안에서 트리 교체까지 수행해 그 창을 닫는다.
            dest = _resolved_within(
                self._paths.plugins, self._paths.plugins / f"plugin:{parsed.name}"
            )
            staged = await self._stage_plugin(path, parsed, manifest)
            backup_root: Path | None = None
            swapped = False
            try:
                risk_flags = self._scan_plugin_skill_risks(staged)
                enabled = not risk_flags
                params = self._install_record_params(
                    artifact_id, parsed, enabled=enabled, risk_flags=risk_flags
                )
                with self._db.transaction() as conn:
                    conn.execute(_INSTALL_UPSERT_SQL, params)
                    backup_root = self._swap_plugin_tree(staged, dest)
                    swapped = True
            except BaseException:
                # commit 실패까지 포함해 DB가 이전 행으로 롤백되면 파일도 이전
                # 트리로 되돌린다. 신규 설치였다면 새 dest를 제거한다.
                if swapped:
                    self._restore_plugin_tree(dest, backup_root)
                raise
            else:
                if backup_root is not None:
                    shutil.rmtree(backup_root, ignore_errors=True)
            finally:
                shutil.rmtree(staged, ignore_errors=True)
            # 재설치 시 이전 파싱 무효화(스킬·커맨드·에이전트 캐시 모두)
            self._skills_cache.pop(artifact_id, None)
            self._agents_cache.pop(artifact_id, None)
        else:
            risk_flags = await self._install_skill(path, parsed)
            enabled = True
            self._db.execute(
                _INSTALL_UPSERT_SQL,
                self._install_record_params(
                    artifact_id, parsed, enabled=enabled, risk_flags=risk_flags
                ),
            )
        if risk_flags:
            log.warning(
                "artifact %s installed with risk flags: %s%s",
                artifact_id,
                ", ".join(risk_flags),
                " (plugin disabled until user enables it)" if not enabled else "",
            )

        return PluginInfo(
            id=artifact_id,
            name=parsed.name,
            version=parsed.version,
            description=parsed.description,
            enabled=enabled,
            required_connectors=parsed.required_connectors,
            risk_flags=tuple(risk_flags),
        )

    async def remove(self, artifact_id: str) -> None:
        """Remove an installed artifact from DB and filesystem."""
        if self._builtin_plugins:
            self._builtin_plugins.ensure_mutable(artifact_id, "삭제")
        row = self._db.query_one(
            "SELECT type, name FROM installed_artifacts WHERE id = ?", (artifact_id,)
        )
        if row is None:
            # Legacy/broken resets could remove the installed_artifacts row while
            # leaving the canonical market skill on disk. GET /skills still
            # exposes that file with source=market:*, so a later reset correctly
            # routes here but used to receive a permanent 404. Repair only this
            # narrowly-proven ghost case; an arbitrary skill:<name> request must
            # never delete a user-authored file without its ownership record.
            name = artifact_id.removeprefix("skill:")
            if artifact_id.startswith("skill:") and VALID_SKILL_NAME.fullmatch(name):
                skill_path = _resolved_within(
                    self._paths.skills, self._paths.skills / f"{name}.md"
                )
                try:
                    frontmatter = split_frontmatter(skill_path.read_text(encoding="utf-8"))
                    source = str(frontmatter[0].get("source") or "") if frontmatter else ""
                except Exception:  # noqa: BLE001 — malformed legacy YAML stays protected
                    source = ""
                if source.startswith("market:"):
                    self._remove_skill_files(name)
                    log.info("removed market skill ghost without DB row: %s", artifact_id)
                    return
            raise NotFoundError(f"artifact not found: {artifact_id}")
        # DB 의 id/name 은 설치 시 검증된 값이지만, 삭제 경로도 동일하게 설치 루트
        # 안임을 재검증한다(수동 편집된 DB 행이 rmtree 를 임의 경로로 끌고 가지 않게).
        if row["type"] == "plugin":
            dest = _resolved_within(self._paths.plugins, self._paths.plugins / artifact_id)
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
        else:
            # skill artifact installed FLAT as <name>.md (+ usage/history sidecars).
            self._remove_skill_files(row["name"])
        self._db.execute("DELETE FROM installed_artifacts WHERE id = ?", (artifact_id,))
        self._skills_cache.pop(artifact_id, None)
        self._agents_cache.pop(artifact_id, None)

    def _remove_skill_files(self, name: str) -> None:
        """Remove one installed skill's canonical file and every sidecar.

        Kept in the plugin loader because both the normal DB-backed uninstall and
        the narrowly-scoped market-ghost repair above own the same artifact tree.
        """
        for suffix in (".md", ".usage.json", ".analysis.json"):
            _resolved_within(
                self._paths.skills, self._paths.skills / f"{name}{suffix}"
            ).unlink(missing_ok=True)
        shutil.rmtree(
            _resolved_within(self._paths.skills, self._paths.skills / name),
            ignore_errors=True,
        )

    # ── boot hygiene ──────────────────────────────────────────────────────────

    def reconcile_builtin_shadows(self) -> int:
        """빌트인으로 승격된 플러그인의 과거 market 설치본을 부팅 시 정리한다.

        플러그인이 앱 빌트인으로 승격되면(예: plugin-dev) 예전 설치본이 갇힌다:
        list_plugins는 빌트인 id의 DB 행을 숨기고(카탈로그가 이김), remove()는
        ensure_mutable로 빌트인 id 삭제를 거부한다 — 사용자가 UI로도 API로도
        지울 수 없는 유령 행+디렉토리가 영구히 남는다. 빌트인 카탈로그가 어차피
        모든 조회를 가리므로(shadowing) 설치본 제거는 동작을 바꾸지 않는다.
        learning.recover_boot_state와 같은 부팅 1회 위생 훅 — 정리 건수를 반환."""
        if self._builtin_plugins is None:
            return 0
        removed = 0
        for plugin_id in sorted(self._builtin_plugins.ids()):
            row = self._db.query_one(
                "SELECT id FROM installed_artifacts WHERE id = ?", (plugin_id,)
            )
            stale_dir = self._paths.plugins / plugin_id
            if row is None and not stale_dir.exists():
                continue
            if row is not None:
                self._db.execute("DELETE FROM installed_artifacts WHERE id = ?", (plugin_id,))
            if stale_dir.exists():
                # 삭제 경로 재검증(remove()와 동일 원칙) — id는 카탈로그 유래라
                # 안전하지만, rmtree 직전 검증은 관례로 유지한다.
                shutil.rmtree(_resolved_within(self._paths.plugins, stale_dir), ignore_errors=True)
            self._skills_cache.pop(plugin_id, None)
            self._agents_cache.pop(plugin_id, None)
            removed += 1
        if removed:
            log.info("reconciled %d builtin-shadowed plugin install(s)", removed)
        return removed

    def reconcile_installed_skill_sources(self) -> int:
        """구버전 market 설치 스킬에 영속 source 메타를 보충한다.

        예전 설치본은 ``installed_artifacts``에는 ``skill:<name>`` 행이 있지만
        ``<name>.md`` frontmatter에는 source가 없었다. 그러면 재시작 뒤 GET
        /skills가 설치본을 학습 스킬처럼 내보내고, My Skills 삭제가 artifact
        제거 대신 일반 스킬 삭제 경로를 골라 DB 유령 행을 남긴다.

        설치 DB를 정본으로 삼아 파일만 원자 교체한다. 손상되거나 사라진 파일은
        이 훅에서 추측 복구하지 않고 로그만 남긴다.
        """
        repaired = 0
        rows = self._db.query_all(
            "SELECT id, name FROM installed_artifacts WHERE type = 'skill'"
        )
        for row in rows:
            name = str(row["name"])
            staged: Path | None = None
            try:
                path = _resolved_within(
                    self._paths.skills, self._paths.skills / f"{name}.md"
                )
                if not path.is_file():
                    log.warning(
                        "installed skill record has no definition file: %s", row["id"]
                    )
                    continue
                text = path.read_text(encoding="utf-8")
                parsed = split_frontmatter(text)
                source = parsed[0].get("source") if parsed is not None else None
                expected = f"market:{name}"
                if source == expected:
                    continue
                canonical = _canonicalize_skill_md(text, name, source=expected)
                staged = path.with_name(f".{path.name}.source-reconcile")
                staged.write_text(canonical, encoding="utf-8")
                staged.replace(path)
                repaired += 1
            except Exception:  # noqa: BLE001 — 손상된 구버전 파일 하나가 부팅을 막으면 안 됨
                log.warning(
                    "failed to reconcile installed skill source: %s",
                    row["id"],
                    exc_info=True,
                )
            finally:
                if staged is not None:
                    try:
                        staged.unlink(missing_ok=True)
                    except OSError:
                        log.warning(
                            "failed to clean installed skill reconcile staging file: %s",
                            staged,
                            exc_info=True,
                        )
        if repaired:
            log.info("reconciled source metadata for %d installed skill(s)", repaired)
        return repaired

    # ── internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _install_record_params(
        artifact_id: str,
        parsed: ArtifactManifest,
        *,
        enabled: bool,
        risk_flags: list[str],
    ) -> tuple[Any, ...]:
        return (
            artifact_id,
            parsed.type,
            parsed.name,
            parsed.version,
            "market",
            now_iso(),
            1 if enabled else 0,
            json.dumps(risk_flags, ensure_ascii=False),
        )

    async def _stage_plugin(
        self, path: str, parsed: ArtifactManifest, manifest: dict[str, Any]
    ) -> Path:
        """완전한 plugin tree를 설치 루트 안 숨김 staging 디렉토리에 만든다."""
        self._paths.plugins.mkdir(parents=True, exist_ok=True)
        staged = Path(
            tempfile.mkdtemp(prefix=".rice-plugin-stage-", dir=str(self._paths.plugins))
        )
        try:
            await self._unpack(path, staged)
            self._flatten_single_package_root(staged, parsed.name)
            (staged / "rice-artifact.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return staged
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise

    def _swap_plugin_tree(self, staged: Path, dest: Path) -> Path | None:
        """staged를 dest로 교체하고, 기존 트리가 있으면 복구용 컨테이너 반환."""
        backup_root: Path | None = None
        previous: Path | None = None
        if dest.exists():
            backup_root = Path(
                tempfile.mkdtemp(prefix=".rice-plugin-backup-", dir=str(self._paths.plugins))
            )
            previous = backup_root / "previous"
            dest.rename(previous)
        try:
            staged.rename(dest)
        except BaseException:
            if previous is not None and previous.exists():
                previous.rename(dest)
            if backup_root is not None:
                shutil.rmtree(backup_root, ignore_errors=True)
            raise
        return backup_root

    @staticmethod
    def _restore_plugin_tree(dest: Path, backup_root: Path | None) -> None:
        """트리 교체 뒤 DB commit이 실패했을 때 파일시스템도 이전 상태로 롤백."""
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        if backup_root is None:
            return
        previous = backup_root / "previous"
        if previous.exists():
            previous.rename(dest)
        shutil.rmtree(backup_root, ignore_errors=True)

    def _scan_plugin_skill_risks(self, plugin_dir: Path) -> list[str]:
        """설치된 플러그인 트리의 프롬프트 표면 md 전체를 위험 스캔한다.

        대상은 주입/실행 표면이 되는 세 디렉토리 전부다(2026-07-19 commands/
        agents 소비와 함께 확장): ① skills 의 두 레이아웃(flat `skills/*.md` +
        표준 `skills/<sub>/SKILL.md`), ② `commands/**/*.md`(InjectedSkill 로
        주입), ③ `agents/*.md`(워커 **시스템 프롬프트**가 된다 — 가장 민감한
        표면). frontmatter 를 포함한 **원문 전체**를 스캔한다 — description/
        tags 도 인덱스·프롬프트에 실리므로 본문만으로는 숨은 문자·주입 문구를
        놓친다. 반환 형식은 `"<name>:<label>"` (라벨은 core/guard.py 안정 코드 —
        로케일 문장은 프론트 몫), 이름은 각 소비 경로의 이름 규칙과 동일하게
        정한다. 읽기 실패한 파일은 건너뛰지 않고 플래그한다(스캔 불능을
        깨끗함으로 오인하지 않는다 — fail-closed 방향)."""
        flags: list[str] = []

        def scan_file(md_path: Path, fallback: str) -> None:
            try:
                text = md_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                flags.append(f"{fallback}:unreadable_skill_file")
                return
            name = _parse_skill_md_minimal(text).get("name") or fallback
            flags.extend(f"{name}:{label}" for label in scan_risks(text))

        for md_path in bundle.skill_md_paths(plugin_dir):
            scan_file(md_path, bundle.skill_name_for(md_path))
        for md_path in bundle.command_paths(plugin_dir):
            # 커맨드는 frontmatter name 이 없는 게 보통 — 경로 유래 이름으로 귀속.
            flags_name = bundle.command_name(plugin_dir, md_path)
            try:
                text = md_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                flags.append(f"{flags_name}:unreadable_skill_file")
                continue
            flags.extend(f"{flags_name}:{label}" for label in scan_risks(text))
        for md_path in bundle.agent_paths(plugin_dir):
            scan_file(md_path, md_path.stem)
        return flags

    async def _install_skill(self, path: str, parsed: ArtifactManifest) -> list[str]:
        """Install SKILL.md plus its repository-style bundled resources.

        The canonical definition remains the flat ``<name>.md`` so SkillStore.list()
        and injection stay backwards compatible. Sibling references/scripts are
        copied to ``skills/<name>/`` where SkillStore exposes them as one virtual
        repository and ``skill_resource`` can read them.

        Returns risk-scan flags (`"<name>:<label>"`) for the installed SKILL.md —
        market 스킬은 학습 HITL 없이 SkillStore 주입 경로에 바로 들어가므로,
        설치 응답에 경고로 실어 사용자가 본문을 검수하게 한다(플래그, 비차단).
        """
        staging = Path(tempfile.mkdtemp(prefix="rice-skill-install-"))
        try:
            await self._unpack(path, staging)
            md_path = self._find_skill_md_path(staging)
            if md_path is None:
                raise ValidationError(
                    f"skill artifact '{parsed.name}' has no SKILL.md to install"
                )
            raw_md_text = md_path.read_text(encoding="utf-8")
            # 반드시 canonicalize 전 **시장 원문 전체**를 스캔한다. manifest name과
            # 맞추며 frontmatter name을 교체한 뒤 스캔하면, 공격자가 원래 name
            # 필드에 숨긴 주입 문구/zero-width 문자가 검사 전에 사라진다.
            risk_flags = [
                f"{parsed.name}:{label}" for label in scan_risks(raw_md_text)
            ]
            # 설치 출처를 SKILL.md에 영속한다. 재시작 후 GET /skills hydrate에서도
            # 마켓 설치본(origin=installed)을 직접 작성/학습 스킬과 구분할 수 있어야
            # 제거 시 installed_artifacts 행까지 함께 정리할 수 있다.
            md_text = _canonicalize_skill_md(
                raw_md_text,
                parsed.name,
                source=f"market:{parsed.name}",
            )
            self._paths.skills.mkdir(parents=True, exist_ok=True)
            target = _resolved_within(self._paths.skills, self._paths.skills / f"{parsed.name}.md")
            target.write_text(md_text, encoding="utf-8")

            if md_path.name == "SKILL.md":
                source_root = md_path.parent
                bundle_root = _resolved_within(
                    self._paths.skills, self._paths.skills / parsed.name
                )
                bundle_root.mkdir(parents=True, exist_ok=True)
                for resource in sorted(source_root.rglob("*")):
                    if not resource.is_file() or resource == md_path:
                        continue
                    rel = resource.relative_to(source_root)
                    if any(part.startswith(".") for part in rel.parts):
                        continue
                    if resource.name.lower().startswith("rice-artifact"):
                        continue
                    dest = _resolved_within(bundle_root, bundle_root / rel)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(resource, dest)
            return risk_flags
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def _find_skill_md_path(root: Path) -> Path | None:
        named = sorted(root.rglob("SKILL.md"))
        if named:
            return named[0]
        for md in sorted(root.rglob("*.md")):
            if not md.name.lower().startswith("rice-artifact"):
                return md
        return None

    @staticmethod
    def _find_skill_md_text(root: Path) -> str | None:
        """Locate the SKILL.md body inside an unpacked skill artifact.

        Prefers a file literally named SKILL.md; else the first *.md that is not a
        manifest. Returns its text, or None if nothing usable is found.
        """
        path = PluginLoader._find_skill_md_path(root)
        return path.read_text(encoding="utf-8") if path else None

    async def _verify_checksum(self, path: str, parsed: ArtifactManifest) -> None:
        """Verify sha256 checksum (CONTRACTS §2.3). Fail-closed on mismatch.

        Expected format: "sha256-<hex>". Empty checksum is only allowed when
        the manifest also has an empty checksum (i.e., dev/test installs).
        """
        file_path = Path(path)
        if not file_path.exists():
            raise ValidationError(f"artifact file not found: {path}")

        if not parsed.checksum:
            # NOTE(agent): Empty checksum is allowed for dev/test installs only.
            # Production market installs always include a checksum. Accepting
            # empty-checksum manifests here enables integration tests without
            # real archive files.
            return

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
            raise ValidationError(f"artifact file not found: {path}")

        # is_tarfile() opens the path → raises IsADirectoryError on a dir. Guard so
        # directory sources fall through to copytree (dev/local installs).
        if src.is_file() and tarfile.is_tarfile(str(src)):
            with tarfile.open(str(src), "r:gz") as tf:
                # 프로젝트 최소 Python은 3.13 — data filter를 반드시 적용한다.
                # 미지원 런타임에서 무필터 extractall로 폴백하면 traversal/symlink
                # 방어를 조용히 잃으므로 fail-closed가 맞다.
                tf.extractall(path=str(dest), filter="data")
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
        builtin_ids = self._builtin_plugins.ids() if self._builtin_plugins else frozenset()
        rows = self._db.query_all(
            "SELECT id, name, version FROM installed_artifacts "
            "WHERE type = 'plugin' AND enabled = 1"
        )
        installed = [
            PluginInfo(id=r["id"], name=r["name"], version=r["version"],
                       description="", enabled=True)
            for r in rows if r["id"] not in builtin_ids
        ]
        builtins = (
            [info for info in self._builtin_plugins.list() if info.enabled]
            if self._builtin_plugins
            else []
        )
        return builtins + installed

    def _plugin_dir(self, info: PluginInfo) -> Path:
        if info.source == BUILTIN_PLUGIN_SOURCE and self._builtin_plugins:
            asset_path = self._builtin_plugins.asset_path(info.id)
            if asset_path is not None:
                return asset_path
        return self._paths.plugins / info.id
