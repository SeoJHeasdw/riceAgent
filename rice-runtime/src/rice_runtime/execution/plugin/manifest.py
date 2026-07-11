"""plugin/manifest.py — rice-artifact.yaml parsing and validation.

WHY: The artifact manifest schema is the single source of truth for what a
valid plugin package looks like (CONTRACTS §2.1). Centralising parsing here
means every other module (loader, registry, routes) works with a validated
`ArtifactManifest` dataclass, not raw dicts.

Schema (CONTRACTS §2.1 — abridged, do not extend without updating CONTRACTS):
  schema_version: 1
  type: skill | plugin
  name: <str>
  version: <semver str>
  display_name: <str>           optional
  description: <str>            optional
  category: <str>               optional, for market filtering
  tags: [<str>, ...]            optional
  author: {name, team}          optional
  entrypoint: ...               type-dependent (skill → SKILL.md path; plugin → python module)
  requires:
    runtime: ">=0.1"            optional
    connectors: ["gmail", ...]  connector_type list (RUNTIME_DESIGN §9.4)
  checksum: sha256-<hex>        REQUIRED for install verification (CONTRACTS §2.3)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ...core.errors import ValidationError

# Supported manifest schema versions.
_SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})

# Valid artifact types (CONTRACTS §2.1).
_VALID_TYPES: frozenset[str] = frozenset({"skill", "plugin"})

# Artifact name = strict slug (적대 리뷰 P0#2). name 은 그대로 설치 파일 경로가
# 되므로(`~/.rice/skills/<name>.md`, `~/.rice/plugins/plugin:<name>/`) 경로
# 구분자·상위 참조가 섞이면 설치 루트를 탈출한다. 첫 글자는 영숫자(선행 `.`/`-`
# 금지 → `..` 불가), 이후는 영숫자와 `._-` 만, 길이 상한 64.
_NAME_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class ArtifactManifest:
    """A validated, typed representation of a rice-artifact.yaml manifest.

    Fields map directly to CONTRACTS §2.1. Only `name`, `version`, `type`,
    and `checksum` are required — everything else is optional with sensible
    defaults for demo purposes.
    """

    schema_version: int
    type: str  # "skill" | "plugin"
    name: str
    version: str
    display_name: str = ""
    description: str = ""
    category: str = ""
    tags: tuple[str, ...] = ()
    author_name: str = ""
    author_team: str = ""
    # entrypoint is type-dependent; stored as an opaque dict for now.
    entrypoint: dict[str, Any] = field(default_factory=dict)
    # requires.connectors — connector_type list (CONTRACTS §2.1, RUNTIME_DESIGN §9.4)
    required_connectors: tuple[str, ...] = ()
    required_runtime: str = ""
    checksum: str = ""


def parse_manifest(raw: dict[str, Any]) -> ArtifactManifest:
    """Parse and validate a raw manifest dict (from YAML/JSON).

    Raises `ValidationError` on any schema violation.
    """
    # ── schema_version ──────────────────────────────────────────────────────
    schema_version = raw.get("schema_version")
    if schema_version is None:
        raise ValidationError("manifest missing required field: schema_version")
    if schema_version not in _SUPPORTED_SCHEMA_VERSIONS:
        raise ValidationError(
            f"unsupported schema_version: {schema_version!r} "
            f"(supported: {sorted(_SUPPORTED_SCHEMA_VERSIONS)})"
        )

    # ── type ─────────────────────────────────────────────────────────────────
    artifact_type = raw.get("type")
    if artifact_type not in _VALID_TYPES:
        raise ValidationError(
            f"manifest 'type' must be one of {sorted(_VALID_TYPES)}, got {artifact_type!r}"
        )

    # ── name ─────────────────────────────────────────────────────────────────
    name = raw.get("name")
    if not name or not isinstance(name, str):
        raise ValidationError("manifest missing required field: name")
    if not _NAME_SLUG_RE.fullmatch(name):
        raise ValidationError(
            f"manifest 'name' must be a slug ([A-Za-z0-9][A-Za-z0-9._-]*, max 64 chars), got {name!r}"
        )

    # ── version ──────────────────────────────────────────────────────────────
    # YAML 에서 `version: 1.0` 은 float, `version: 1` 은 int 로 파싱된다. semver 문자열을
    # 요구하되, 숫자로 와도 거부하지 않고 문자열로 정규화한다(v1 버그#13: float→
    # ValidationError 로 설치 실패하던 문제). 빈 값/None 만 거부한다.
    version_raw = raw.get("version")
    if version_raw is None or (isinstance(version_raw, str) and not version_raw.strip()):
        raise ValidationError("manifest missing required field: version")
    version = str(version_raw).strip()

    # ── checksum (required for install, optional at parse time) ──────────────
    # NOTE(agent): checksum is validated at *install* time (loader.py §checksum
    # verify). At parse time we just capture the value — an empty checksum is
    # allowed so manifests can be constructed in tests without a real archive.
    checksum: str = raw.get("checksum", "") or ""

    # ── optional fields ───────────────────────────────────────────────────────
    requires: dict[str, Any] = raw.get("requires", {}) or {}
    connectors_raw = requires.get("connectors", []) or []
    required_connectors = tuple(str(c) for c in connectors_raw)

    tags_raw = raw.get("tags", []) or []
    tags = tuple(str(t) for t in tags_raw)

    author: dict[str, Any] = raw.get("author", {}) or {}

    return ArtifactManifest(
        schema_version=int(schema_version),
        type=str(artifact_type),
        name=str(name),
        version=str(version),
        display_name=str(raw.get("display_name", "") or ""),
        description=str(raw.get("description", "") or ""),
        category=str(raw.get("category", "") or ""),
        tags=tags,
        author_name=str(author.get("name", "") or ""),
        author_team=str(author.get("team", "") or ""),
        entrypoint=dict(raw.get("entrypoint", {}) or {}),
        required_connectors=required_connectors,
        required_runtime=str(requires.get("runtime", "") or ""),
        checksum=checksum,
    )
