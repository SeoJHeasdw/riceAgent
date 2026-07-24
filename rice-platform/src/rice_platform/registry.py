"""Artifact registry — reads artifact packages from the local filesystem.

구조 (RICE_CONTRACTS.md §2.2):
    artifacts/
    └── <name>/                      # artifact 루트 (이름 = artifact name)
        ├── rice-artifact.yaml       # manifest (CONTRACTS §2.1)
        ├── SKILL.md                 # skill 타입: 진입점
        └── ... (기타 파일들)        # subagents/, scripts/, etc.

MVP: PostgreSQL 대신 로컬 디렉토리를 registry로 사용.
     artifacts/ 아래 각 서브디렉토리를 하나의 artifact로 인식한다.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_ARTIFACTS_DIR = Path(__file__).parent.parent.parent / "artifacts"
_REPO_ROOT = _DEFAULT_ARTIFACTS_DIR.parent.parent  # <repo>/
RICE_APP_MANAGED_BY = "rice-app"
_PUBLISH_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _artifacts_dir() -> Path:
    env = os.environ.get("RICE_ARTIFACTS_DIR")
    return Path(env) if env else _DEFAULT_ARTIFACTS_DIR


def _load_manifest(artifact_dir: Path) -> dict[str, Any] | None:
    """artifact manifest 읽기.

    1순위: rice-artifact.yaml (rice 네이티브 artifact)
    2순위: .claude-plugin/plugin.json (실제 Claude Code 플러그인 — skill 묶음이 아니라
           agents·hooks·skills·MCP 커넥터를 포함하는 패키지)
    없으면 None.
    """
    yaml_path = artifact_dir / "rice-artifact.yaml"
    if yaml_path.exists():
        try:
            with yaml_path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data if isinstance(data, dict) else None
        except Exception:
            return None
    if (artifact_dir / ".claude-plugin" / "plugin.json").exists():
        return _claude_plugin_manifest(artifact_dir)
    return None


# ── Claude Code 플러그인 마켓플레이스 인제스트 ────────────────────────────────
# 실제 Anthropic 플러그인(claude-for-legal 등)은 rice-artifact.yaml 대신
# .claude-plugin/{marketplace.json, plugin.json} + .mcp.json 형식을 쓴다.
# registry가 이 형식을 직접 읽어 카탈로그에 노출한다(파일 복사·변환 없이 in-place).


def _marketplace_dirs() -> list[Path]:
    """Claude 플러그인 마켓플레이스 디렉토리들 (.claude-plugin/marketplace.json 보유).

    RICE_PLUGIN_MARKETPLACES(os.pathsep 구분) 우선, 없으면 레포 루트의 claude-for-legal 자동 감지.
    """
    env = os.environ.get("RICE_PLUGIN_MARKETPLACES")
    if env:
        return [Path(p) for p in env.split(os.pathsep) if p.strip()]
    default = _REPO_ROOT / "claude-for-legal"
    if (default / ".claude-plugin" / "marketplace.json").exists():
        return [default]
    return []


def _iter_claude_plugins() -> Iterator[tuple[str, Path, dict[str, Any]]]:
    """마켓플레이스의 각 플러그인 → (id, plugin_dir, marketplace_entry)."""
    for mkt in _marketplace_dirs():
        manifest_path = mkt / ".claude-plugin" / "marketplace.json"
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for entry in data.get("plugins", []):
            name = entry.get("name", "")
            source = entry.get("source", "")
            # Rice only ingests local marketplace entries here. Upstream Claude
            # marketplaces may also include remote source objects; those need a
            # separate fetch/verification step and must not break local catalog
            # loading.
            if not name or not isinstance(source, str):
                continue
            plugin_dir = (mkt / source).resolve()
            if plugin_dir.is_dir():
                yield name, plugin_dir, entry


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _connector_requirement_key(name: str) -> str:
    """Display connector names from `.mcp.json` → runtime connector_type where known."""
    s = name.strip().lower()
    aliases = {
        "google drive": "drive",
        "drive": "drive",
        "google calendar": "calendar",
        "calendar": "calendar",
        "gmail": "gmail",
        "google gmail": "gmail",
    }
    return aliases.get(s, name)


def _claude_plugin_manifest(plugin_dir: Path, entry: dict[str, Any] | None = None) -> dict[str, Any]:
    """.claude-plugin/plugin.json + .mcp.json (+ marketplace entry) → rice manifest dict.

    - connectors: .mcp.json 의 mcpServers 키 (예: Slack, Google Drive) — plugin이 요구하는 외부 연결
    - tags: .mcp.json 의 recommendedCategories
    - display_name/description: marketplace entry 우선(거기에만 displayName이 있음), 없으면 plugin.json
    """
    entry = entry or {}
    pj = _read_json(plugin_dir / ".claude-plugin" / "plugin.json")

    mcp = _read_json(plugin_dir / ".mcp.json")
    connectors = list((mcp.get("mcpServers") or {}).keys())
    tags = list(mcp.get("recommendedCategories") or [])

    author = entry.get("author") or pj.get("author") or {}
    if not isinstance(author, dict):
        author = {"name": str(author)}

    requires: dict[str, Any] = {}
    if connectors:
        requires["connectors"] = [_connector_requirement_key(c) for c in connectors]

    return {
        "schema_version": 1,
        "type": "plugin",
        "name": entry.get("name") or pj.get("name", plugin_dir.name),
        "version": pj.get("version", "1.0.0"),
        "display_name": entry.get("displayName") or pj.get("name", plugin_dir.name),
        "description": (entry.get("description") or pj.get("description", "")).strip(),
        "category": "",
        "tags": tags,
        "author": author,
        "requires": requires,
        "license": "",
    }


def _catalog_id(name: str, manifest: dict[str, Any]) -> str:
    if manifest.get("type") == "plugin":
        plugin_name = str(manifest.get("name") or name)
        return plugin_name if plugin_name.startswith("plugin:") else f"plugin:{plugin_name}"
    return name


def _iter_artifacts() -> list[tuple[str, Path, dict[str, Any]]]:
    """카탈로그 대상 (artifact_dir, manifest) 전체 — rice artifacts + Claude 플러그인.

    Skill과 plugin은 같은 upstream 이름을 가질 수 있으므로 catalog id를 타입별로
    분리한다. skill은 `<name>`, plugin은 `plugin:<name>`를 쓴다. 이렇게 해야
    `/artifacts/{id}` 상세/다운로드가 같은 이름의 skill과 plugin을 혼동하지 않는다.
    """
    out: list[tuple[str, Path, dict[str, Any]]] = []
    seen: set[str] = set()

    root = _artifacts_dir()
    if root.exists():
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            manifest = _load_manifest(d)
            if manifest is not None:
                artifact_id = _catalog_id(d.name, manifest)
                out.append((artifact_id, d, manifest))
                seen.add(artifact_id)

    for name, pdir, entry in _iter_claude_plugins():
        manifest = _claude_plugin_manifest(pdir, entry)
        artifact_id = _catalog_id(name, manifest)
        if artifact_id in seen:
            continue
        out.append((artifact_id, pdir, manifest))
        seen.add(artifact_id)

    return out


def _resolve_artifact_dir(artifact_id: str) -> Path | None:
    """artifact id → 실제 디렉토리 (rice artifacts 또는 Claude 플러그인). 파일/다운로드 공용."""
    for catalog_id, d, manifest in _iter_artifacts():
        if catalog_id == artifact_id:
            return d
        # Backward-compatible lookup for legacy plugin URLs that did not use
        # `plugin:`. If a same-named skill exists, the skill wins because it was
        # yielded earlier from `artifacts/`.
        if manifest.get("type") == "plugin" and d.name == artifact_id:
            return d
    return None


def publish_artifact(manifest: dict[str, Any], files: list[dict[str, str]]) -> dict[str, str]:
    """앱이 게시한 artifact를 staging 후 원자적으로 교체한다.

    기본 제공/외부 marketplace artifact를 같은 이름으로 덮어쓰지 않도록 기존
    artifact도 ``managed_by: rice-app`` 인 경우에만 교체한다. 파일은 모두 artifact
    루트 안의 상대경로여야 한다.
    """
    name = str(manifest.get("name", "")).strip()
    if not _PUBLISH_NAME_RE.fullmatch(name):
        raise ValueError(
            "manifest.name must be a safe slug "
            "([A-Za-z0-9][A-Za-z0-9._-]*, max 64 chars)"
        )
    if manifest.get("managed_by") != RICE_APP_MANAGED_BY:
        raise PermissionError("published artifacts must declare managed_by: rice-app")

    root = _artifacts_dir()
    root.mkdir(parents=True, exist_ok=True)
    artifact_dir = root / name
    if artifact_dir.exists():
        existing = _load_manifest(artifact_dir)
        if not existing or existing.get("managed_by") != RICE_APP_MANAGED_BY:
            raise PermissionError(f"artifact is not managed by rice-app: {name}")

    stage_parent = Path(tempfile.mkdtemp(prefix=".rice-publish-", dir=str(root)))
    staged = stage_parent / name
    staged.mkdir()
    backup_parent: Path | None = None
    try:
        with (staged / "rice-artifact.yaml").open("w", encoding="utf-8") as f:
            yaml.dump(manifest, f, allow_unicode=True, default_flow_style=False)

        resolved_stage = staged.resolve()
        for file_entry in files:
            rel_path = str(file_entry.get("path", "")).lstrip("/")
            if not rel_path:
                continue
            target = (staged / rel_path).resolve()
            if resolved_stage not in target.parents and target != resolved_stage:
                raise ValueError(f"artifact file escapes artifact root: {rel_path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(file_entry.get("content", "")), encoding="utf-8")

        if artifact_dir.exists():
            backup_parent = Path(tempfile.mkdtemp(prefix=".rice-publish-backup-", dir=str(root)))
            artifact_dir.rename(backup_parent / name)
        try:
            staged.rename(artifact_dir)
        except BaseException:
            backup = backup_parent / name if backup_parent else None
            if backup is not None and backup.exists() and not artifact_dir.exists():
                backup.rename(artifact_dir)
            raise
    finally:
        shutil.rmtree(stage_parent, ignore_errors=True)
        # 교체/롤백 어느 쪽도 target을 복구하지 못한 예외적 경우에는 backup을
        # 남겨 수동 복구 가능성을 보존한다.
        if (
            backup_parent is not None
            and (artifact_dir.exists() or not (backup_parent / name).exists())
        ):
            shutil.rmtree(backup_parent, ignore_errors=True)

    return {"id": name, "name": name, "version": str(manifest.get("version", "1.0.0"))}


def delete_managed_artifact(artifact_id: str) -> bool:
    """rice-app이 게시한 로컬 artifact만 삭제한다.

    외부 plugin marketplace와 기본 제공 artifact는 이 경로로 삭제할 수 없다.
    """
    if not _PUBLISH_NAME_RE.fullmatch(artifact_id):
        raise ValueError("invalid artifact id")
    artifact_dir = _artifacts_dir() / artifact_id
    if not artifact_dir.exists():
        return False
    manifest = _load_manifest(artifact_dir)
    if not manifest or manifest.get("managed_by") != RICE_APP_MANAGED_BY:
        raise PermissionError(f"artifact is not managed by rice-app: {artifact_id}")
    shutil.rmtree(artifact_dir)
    return True


def list_artifacts(
    *,
    artifact_type: str | None = None,
    q: str | None = None,
    category: str | None = None,
    tags: list[str] | None = None,
    sort: str = "popular",
) -> list[dict[str, Any]]:
    """카탈로그 목록 반환 (CONTRACTS §3 GET /catalog).

    artifact_type: 'skill' | 'plugin' | None(전체)
    q:             name/display_name/description 검색
    category:      manifest category 필터
    tags:          manifest tags 필터 (AND)
    sort:          popular | recent | name
    """
    results: list[dict[str, Any]] = []
    for artifact_id, artifact_dir, manifest in _iter_artifacts():
        # type 필터
        if artifact_type and manifest.get("type") != artifact_type:
            continue

        # category 필터
        if category and manifest.get("category") != category:
            continue

        # tags 필터 (요청 tag 전부 포함해야 함)
        if tags:
            manifest_tags = manifest.get("tags") or []
            if not all(t in manifest_tags for t in tags):
                continue

        # 검색어 필터
        if q:
            ql = q.lower()
            searchable = " ".join(
                str(manifest.get(f, ""))
                for f in ("name", "display_name", "description")
            ).lower()
            if ql not in searchable:
                continue

        entry = _to_catalog_entry(manifest, artifact_dir, artifact_id)
        results.append(entry)

    # 정렬
    if sort == "name":
        results.sort(key=lambda e: e["name"])
    elif sort == "recent":
        results.sort(key=lambda e: e.get("published_at", ""), reverse=True)
    else:  # popular — download_count 기준 (MVP: 고정값)
        results.sort(key=lambda e: e.get("download_count", 0), reverse=True)

    return results


def get_artifact(artifact_id: str) -> dict[str, Any] | None:
    """단일 artifact 상세 조회 (GET /artifacts/{id})."""
    for catalog_id, artifact_dir, manifest in _iter_artifacts():
        if catalog_id == artifact_id:
            return _to_catalog_entry(manifest, artifact_dir, catalog_id)
        if manifest.get("type") == "plugin" and artifact_dir.name == artifact_id:
            return _to_catalog_entry(manifest, artifact_dir, catalog_id)
    return None


def build_download_package(artifact_id: str) -> tuple[bytes, str] | None:
    """artifact를 tar.gz로 패키징해 반환 (바이트, sha256 checksum).

    MVP: 요청마다 온-더-플라이로 패키징한다.
         production에서는 미리 만들어 캐싱해야 한다.
    Returns (archive_bytes, checksum_hex) 또는 None (artifact 없음).
    """
    artifact_dir = _resolve_artifact_dir(artifact_id)
    if artifact_dir is None:
        return None

    # 결정적(reproducible) tar.gz: 정렬된 파일 + 고정 mtime/uid/gid + gzip mtime=0.
    # → 내용이 같으면 항상 같은 체크섬(요청마다 흔들리지 않음 = 캐시 키 + 무결성 앵커).
    def _norm(ti: tarfile.TarInfo) -> tarfile.TarInfo:
        ti.mtime = 0
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = ""
        return ti

    files = sorted(p for p in artifact_dir.rglob("*") if p.is_file())
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tf:
            for p in files:
                arcname = f"{artifact_id}/{p.relative_to(artifact_dir).as_posix()}"
                tf.add(str(p), arcname=arcname, filter=_norm)
    data = buf.getvalue()
    checksum = hashlib.sha256(data).hexdigest()
    return data, checksum


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _skill_name_from_path(rel_path: str) -> str:
    p = Path(rel_path)
    if p.name.lower() == "skill.md" and p.parent.name:
        return p.parent.name
    return p.stem


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        data = yaml.safe_load(parts[1]) or {}
        return data if isinstance(data, dict) else {}, parts[2]
    except yaml.YAMLError:
        return {}, parts[2]


def _prompt_examples(text: str) -> list[str]:
    """Extract human prompt examples from explicit example/try/question sections."""
    examples: list[str] = []
    capture = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        heading = re.match(r"^#{1,6}\s+(.+)$", line)
        if heading:
            title = heading.group(1).lower()
            capture = any(token in title for token in ("example", "try", "prompt", "question", "질문", "예시"))
            continue
        if not capture or not line:
            continue
        item = re.match(r"^(?:[-*]|\d+[.)])\s+(.+)$", line)
        if not item:
            continue
        candidate = item.group(1).strip().strip('"').strip("'")
        if 8 <= len(candidate) <= 160:
            examples.append(candidate)
    return examples[:3]


def _plugin_skill_meta(artifact_dir: Path, rel_path: str) -> tuple[dict[str, str], list[str]]:
    default_name = _skill_name_from_path(rel_path)
    path = artifact_dir / rel_path
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"name": default_name, "command": default_name, "description": ""}, []
    fm, _ = _frontmatter(text)
    name = str(fm.get("name") or default_name).strip() or default_name
    description = str(fm.get("description") or "").strip()
    return {"name": name, "command": name, "description": description}, _prompt_examples(text)


def _dedupe(items: list[str], limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        out.append(item.strip())
        seen.add(key)
        if len(out) >= limit:
            break
    return out

def _to_catalog_entry(manifest: dict[str, Any], artifact_dir: Path, artifact_id: str) -> dict[str, Any]:
    """manifest dict → catalog API response dict.

    Anthropic skills 구조 반영 (spec §Anatomy of a Skill):
    - description: LLM의 스킬 트리거 결정에 쓰이는 1차 메커니즘 → 그대로 노출
    - license: manifest 또는 LICENSE.txt에서 추출
    - bundled_resources: scripts/, references/, assets/ 등 서브디렉토리 존재 여부
    - skill_files: SKILL.md 파일 목록 (skill 타입)
    """
    author = manifest.get("author") or {}

    # license: manifest 필드 우선, 없으면 LICENSE.txt 존재 여부로 판단
    license_val = manifest.get("license", "")
    if not license_val and (artifact_dir / "LICENSE.txt").exists():
        license_val = "See LICENSE.txt"

    # bundled resources 존재 여부.
    #  - skill 계열: scripts/, references/, assets/, templates/
    #  - plugin 계열(실제 Claude 플러그인): agents/, hooks/, commands/, skills/
    resource_dirs = ["scripts", "references", "assets", "templates", "agents", "hooks", "commands", "skills"]
    bundled_resources = [d for d in resource_dirs if (artifact_dir / d).is_dir()]

    # skill_files: skill 타입은 SKILL.md, plugin 타입은 skills/ 묶음.
    # plugin 레이아웃은 표준 skills/<name>/SKILL.md (CONTRACTS §2.2, Anthropic skills 구조)이며,
    # 평면 skills/*.md 도 느슨하게 허용한다.
    skill_files: list[str] = []
    if (artifact_dir / "SKILL.md").exists():
        skill_files.append("SKILL.md")
    skills_subdir = artifact_dir / "skills"
    if skills_subdir.is_dir():
        skill_files.extend(f"skills/{p.name}" for p in sorted(skills_subdir.glob("*.md")))
        skill_files.extend(
            f"skills/{sub.name}/SKILL.md"
            for sub in sorted(skills_subdir.iterdir())
            if sub.is_dir() and (sub / "SKILL.md").exists()
        )

    plugin_skills: list[dict[str, str]] = []
    examples: list[str] = []
    if manifest.get("type") == "plugin":
        for rel_path in skill_files:
            meta, skill_examples = _plugin_skill_meta(artifact_dir, rel_path)
            plugin_skills.append(meta)
            examples.extend(skill_examples)
        manifest_examples = manifest.get("examples") or manifest.get("use_cases") or []
        if isinstance(manifest_examples, list):
            examples.extend(str(e) for e in manifest_examples)

    # connectors(상세): plugin의 .mcp.json 에서 title·description까지 추출(프론트 커넥터 섹션용).
    # .mcp.json 이 없으면 requires.connectors 이름만으로 폴백.
    connectors: list[dict[str, str]] = []
    mcp_path = artifact_dir / ".mcp.json"
    if mcp_path.exists():
        servers = _read_json(mcp_path).get("mcpServers") or {}
        connectors = [
            {"name": k, "title": (v or {}).get("title", k), "description": (v or {}).get("description", "")}
            for k, v in servers.items()
        ]
    if not connectors:
        req_conns = (manifest.get("requires") or {}).get("connectors") or []
        connectors = [{"name": c, "title": c, "description": ""} for c in req_conns]

    return {
        "id": artifact_id,
        "type": manifest.get("type", "skill"),
        "name": manifest.get("name", artifact_dir.name),
        "display_name": manifest.get("display_name") or manifest.get("name", ""),
        # description은 LLM 트리거 메커니즘 — 트리거 조건을 포함해 완전하게 노출
        "description": manifest.get("description", ""),
        "version": manifest.get("version", "1.0.0"),
        "category": manifest.get("category", ""),
        "tags": manifest.get("tags") or [],
        "license": license_val,
        "author_name": author.get("name", "") if isinstance(author, dict) else "",
        "author_team": author.get("team", "") if isinstance(author, dict) else "",
        "download_count": manifest.get("download_count", 0),
        "checksum": "",  # 다운로드 시점에 계산
        "requires": manifest.get("requires") or {},
        "published_at": manifest.get("published_at", ""),
        # 로컬 앱이 게시·삭제를 관리하는 artifact인지 식별한다.
        "managed_by": manifest.get("managed_by", ""),
        # Anthropic 구조 반영: 스킬에 번들된 리소스 존재 여부
        "bundled_resources": bundled_resources,
        "skill_files": skill_files,
        "has_bundled_resources": len(bundled_resources) > 0,
        # plugin이 요구하는 외부 연결(.mcp.json) — 이름·제목·설명
        "connectors": connectors,
        # plugin 상세 UI용: SKILL.md frontmatter에서 추출한 명령/설명 + 예시 질문.
        "plugin_skills": plugin_skills,
        "examples": _dedupe(examples, 6),
    }
