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

import hashlib
import io
import os
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_ARTIFACTS_DIR = Path(__file__).parent.parent.parent / "artifacts"


def _artifacts_dir() -> Path:
    env = os.environ.get("RICE_ARTIFACTS_DIR")
    return Path(env) if env else _DEFAULT_ARTIFACTS_DIR


def _load_manifest(artifact_dir: Path) -> dict[str, Any] | None:
    """rice-artifact.yaml 읽기. 없으면 None."""
    yaml_path = artifact_dir / "rice-artifact.yaml"
    if not yaml_path.exists():
        return None
    try:
        with yaml_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


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
    root = _artifacts_dir()
    if not root.exists():
        return []

    results: list[dict[str, Any]] = []
    for artifact_dir in sorted(root.iterdir()):
        if not artifact_dir.is_dir():
            continue
        manifest = _load_manifest(artifact_dir)
        if manifest is None:
            continue

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

        entry = _to_catalog_entry(manifest, artifact_dir)
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
    root = _artifacts_dir()
    artifact_dir = root / artifact_id
    if not artifact_dir.is_dir():
        return None
    manifest = _load_manifest(artifact_dir)
    if manifest is None:
        return None
    return _to_catalog_entry(manifest, artifact_dir)


def build_download_package(artifact_id: str) -> tuple[bytes, str] | None:
    """artifact를 tar.gz로 패키징해 반환 (바이트, sha256 checksum).

    MVP: 요청마다 온-더-플라이로 패키징한다.
         production에서는 미리 만들어 캐싱해야 한다.
    Returns (archive_bytes, checksum_hex) 또는 None (artifact 없음).
    """
    root = _artifacts_dir()
    artifact_dir = root / artifact_id
    if not artifact_dir.is_dir():
        return None

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        tf.add(str(artifact_dir), arcname=artifact_id)
    data = buf.getvalue()
    checksum = hashlib.sha256(data).hexdigest()
    return data, checksum


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _to_catalog_entry(manifest: dict[str, Any], artifact_dir: Path) -> dict[str, Any]:
    """manifest dict → catalog API response dict."""
    author = manifest.get("author") or {}
    return {
        "id": artifact_dir.name,
        "type": manifest.get("type", "skill"),
        "name": manifest.get("name", artifact_dir.name),
        "display_name": manifest.get("display_name") or manifest.get("name", ""),
        "description": manifest.get("description", ""),
        "version": manifest.get("version", "1.0.0"),
        "category": manifest.get("category", ""),
        "tags": manifest.get("tags") or [],
        "author_name": author.get("name", "") if isinstance(author, dict) else "",
        "author_team": author.get("team", "") if isinstance(author, dict) else "",
        "download_count": manifest.get("download_count", 0),
        "checksum": "",  # 다운로드 시점에 계산
        "requires": manifest.get("requires") or {},
        "published_at": manifest.get("published_at", ""),
    }
