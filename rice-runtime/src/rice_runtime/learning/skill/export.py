"""skill/export.py — 학습 스킬 → 유통 가능한 마켓 artifact 내보내기.

자기개선 루프(HITL)와 마켓을 잇는 다리의 '나가는 방향'이다: 대화에서 태어나
검증·사용 텔레메트리로 다듬어진 스킬을, rice-platform 카탈로그와 runtime
install이 그대로 소비하는 표준 artifact 형식으로 산출한다.

산출물 (dest_root 아래):
  <name>/rice-artifact.yaml     — manifest (CONTRACTS §2.1, type=skill)
  <name>/SKILL.md               — 스킬 전문 (frontmatter + body)
  <name>-v<version>.tar.gz      — 재현 가능한(reproducible) 설치 패키지

디렉토리는 rice-platform `artifacts/`에 복사하거나 git repo로 푸시하면 그대로
카탈로그에 실리는 형식이고(registry.py `_load_manifest`), tar.gz + checksum은
runtime `POST /artifacts/install`이 바로 받는 형식이다(loader.install).

재현 가능 패키징은 rice-platform registry.build_download_package와 같은 정규화
(정렬된 파일 + mtime/uid/gid 0 + gzip mtime=0)를 쓴다 — 내용이 같으면 체크섬이
항상 같아 무결성 앵커가 된다. checksum은 tar.gz **자신의** 해시이므로 manifest
파일 안에는 넣지 않는다(순환) — 반환값·응답으로만 전달한다(마켓 다운로드
응답과 같은 계약).

개인 유통 스킬과 설치자의 스킬은 **관계가 끊긴다**(제품 결정 2026-07-12):
설치 순간 받은 쪽의 학습 스킬이 되어 독립적으로 HITL 진화한다. 그래서 export에
계보(lineage) 메타데이터를 심지 않는다.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import shutil
import tarfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .store import SkillData, serialize_skill_md

__all__ = ["SkillExport", "export_skill"]


@dataclass(frozen=True)
class SkillExport:
    """export_skill의 결과 — 라우트가 그대로 JSON으로 내보낸다."""

    artifact_dir: str
    archive_path: str
    checksum: str  # sha256-<hex>, tar.gz 파일의 해시
    manifest: dict[str, Any]


def _manifest_for(skill: SkillData) -> dict[str, Any]:
    """CONTRACTS §2.1 manifest dict. version은 학습 스킬의 정수 리비전을
    semver 자리로 승격한다(v3 → "3.0.0") — 마켓 UI/설치 표기가 semver를 기대한다."""
    return {
        "schema_version": 1,
        "type": "skill",
        "name": skill.name,
        "version": f"{skill.version}.0.0",
        "description": skill.description or "",
        "tags": list(skill.tags or []),
    }


def _dump_manifest_yaml(manifest: dict[str, Any]) -> str:
    import yaml  # 지연 import (CONVENTIONS §2) — 정식 의존성이지만 관례를 따른다

    return yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)


def _reproducible_targz(root: Path, arc_root: str) -> bytes:
    """rice-platform registry.build_download_package와 동일한 정규화의 tar.gz."""

    def _norm(ti: tarfile.TarInfo) -> tarfile.TarInfo:
        ti.mtime = 0
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = ""
        return ti

    files = sorted(p for p in root.rglob("*") if p.is_file())
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tf:
            for p in files:
                arcname = f"{arc_root}/{p.relative_to(root).as_posix()}"
                tf.add(str(p), arcname=arcname, filter=_norm)
    return buf.getvalue()


def export_skill(
    skill: SkillData,
    dest_root: Path,
    *,
    resources: dict[str, str] | None = None,
) -> SkillExport:
    """`skill`을 dest_root 아래 artifact 디렉토리 + tar.gz로 내보낸다.

    내보내는 SKILL.md는 정규화된 사본이다: state=active(설치 즉시 주입 대상),
    source/타임스탬프 제거(작성자의 세션 provenance가 설치자에게 실려가면
    무의미한 계보가 남는다 — 관계 단절 원칙), enabled 초기화. 본문·설명·태그·
    버전은 그대로 간다."""
    clean = replace(
        skill,
        state="active",
        enabled=True,
        source=None,
        created_at=None,
        updated_at=None,
        absorbed_into=None,
    )
    artifact_dir = dest_root / skill.name
    shutil.rmtree(artifact_dir, ignore_errors=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    manifest = _manifest_for(skill)
    (artifact_dir / "rice-artifact.yaml").write_text(
        _dump_manifest_yaml(manifest), encoding="utf-8"
    )
    (artifact_dir / "SKILL.md").write_text(serialize_skill_md(clean), encoding="utf-8")
    for rel_path, content in sorted((resources or {}).items()):
        rel = Path(rel_path)
        if rel.is_absolute() or ".." in rel.parts or rel.name == "SKILL.md":
            continue
        target = artifact_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    data = _reproducible_targz(artifact_dir, skill.name)
    archive_path = dest_root / f"{skill.name}-v{skill.version}.tar.gz"
    archive_path.write_bytes(data)
    checksum = f"sha256-{hashlib.sha256(data).hexdigest()}"
    return SkillExport(
        artifact_dir=str(artifact_dir),
        archive_path=str(archive_path),
        checksum=checksum,
        manifest=manifest,
    )
