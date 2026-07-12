"""test_skill_export.py — 학습 루프 ↔ 마켓 다리(나가는 방향) 고정.

이 파일이 고정하는 계약 (2026-07-12, POST /skills/{id}/export):
  1. export 산출물: artifact 디렉토리(rice-artifact.yaml + SKILL.md) +
     재현 가능한 tar.gz + sha256 checksum. manifest는 CONTRACTS §2.1을 만족
     (parse_manifest 통과)하고, checksum은 archive 파일의 실제 해시다.
  2. **라운드트립**: export한 tar.gz + manifest를 다른 런타임의
     PluginLoader.install(type=skill)에 그대로 넣으면 받은 쪽의 독립 학습
     스킬로 설치된다 — 계보 없음(원작자 source/타임스탬프가 실려가지 않는다).
  3. 재현성: 같은 내용의 export는 항상 같은 checksum.
  4. 빌트인 스킬도 export 가능(읽기 사본 — 불변성과 무관).
  5. skill-authoring 빌트인이 카탈로그에 실리고 명시 언급으로 라우팅된다
     (방향 2 — 스킬 저작 지침의 정본이 plugin-dev에서 RICE 자체로 이동).
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from rice_runtime.core.config import LearningConfig
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.execution.plugin.loader import PluginLoader
from rice_runtime.execution.plugin.manifest import parse_manifest
from rice_runtime.execution.service import NoopSandbox
from rice_runtime.learning.service import LearningService
from rice_runtime.learning.skill.builtin import BuiltinSkillCatalog
from rice_runtime.learning.skill.store import SkillStore

_BODY = (
    "## Steps\n"
    "1. Gather last week's notes with search_files.\n"
    "2. Write the report with create_document.\n"
    "3. Verify the file with read_file before answering.\n"
)


def _fresh_paths() -> RicePaths:
    return RicePaths.from_env(tempfile.mkdtemp(prefix="rice-export-")).ensure()


def _service(paths: RicePaths) -> LearningService:
    return LearningService(Database(":memory:").connect(), LearningConfig(), paths)


def _exported(svc: LearningService, paths: RicePaths) -> dict:
    svc.create_skill(
        name="weekly-report-build",
        body=_BODY,
        description="Build the weekly report file",
        tags=("report", "weekly"),
    )
    return svc.export_skill("weekly-report-build")


# ── 1. 산출물 형태 + checksum ─────────────────────────────────────────────────

def test_export_produces_valid_artifact_and_checksum():
    paths = _fresh_paths()
    out = _exported(_service(paths), paths)

    artifact_dir = Path(out["artifact_dir"])
    archive = Path(out["archive_path"])
    assert (artifact_dir / "rice-artifact.yaml").exists()
    assert (artifact_dir / "SKILL.md").exists()
    assert archive.exists()
    # 워크스페이스(사용자 가시 영역) 아래에 만들어진다.
    assert str(artifact_dir).startswith(str(paths.workspace))

    # manifest는 CONTRACTS §2.1 스키마를 만족한다 (버전은 semver 승격).
    parsed = parse_manifest(out["manifest"])
    assert parsed.type == "skill" and parsed.name == "weekly-report-build"
    assert parsed.version == "1.0.0"

    # checksum = tar.gz 파일의 실제 sha256.
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert out["checksum"] == f"sha256-{actual}"

    # 계보 단절: 원작자 provenance(source/타임스탬프)가 실려가지 않는다.
    md = (artifact_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "source:" not in md.split("---")[1]
    assert "created_at:" not in md.split("---")[1]


def test_export_is_reproducible():
    paths = _fresh_paths()
    svc = _service(paths)
    first = _exported(svc, paths)
    second = svc.export_skill("weekly-report-build")
    assert first["checksum"] == second["checksum"]


# ── 2. 라운드트립: export → 다른 런타임에 install ─────────────────────────────

async def test_export_round_trips_through_market_install():
    src_paths = _fresh_paths()
    out = _exported(_service(src_paths), src_paths)

    dst_paths = _fresh_paths()
    loader = PluginLoader(
        db=Database(":memory:").connect(), sandbox=NoopSandbox(), paths=dst_paths
    )
    manifest = dict(out["manifest"])
    manifest["checksum"] = out["checksum"]  # 마켓 다운로드 응답과 같은 전달 계약
    await loader.install(path=out["archive_path"], manifest=manifest)

    installed = SkillStore(dst_paths).read("weekly-report-build")
    assert installed.body.strip() == _BODY.strip()
    assert installed.description == "Build the weekly report file"
    assert installed.state == "active"
    # 받은 쪽의 독립 스킬 — 이후 HITL 패치가 자유롭다(관계 단절의 실체).
    assert installed.version == 1


async def test_repository_files_round_trip_and_remain_editable():
    src_paths = _fresh_paths()
    src = _service(src_paths)
    src.create_skill(
        name="weekly-report-build",
        body=_BODY + "\nRead references/tone.md before writing.\n",
        description="Build the weekly report file",
    )
    updated = src.update_skill_file(
        "weekly-report-build",
        "references/tone.md",
        "# Tone\n\nKeep the summary concise and write in Korean.\n",
    )
    assert updated.version == 2
    files = {item["path"]: item["content"] for item in src.get_skill_files("weekly-report-build")}
    assert "SKILL.md" in files
    assert files["references/tone.md"].startswith("# Tone")

    out = src.export_skill("weekly-report-build")
    assert (Path(out["artifact_dir"]) / "references" / "tone.md").exists()

    dst_paths = _fresh_paths()
    loader = PluginLoader(
        db=Database(":memory:").connect(), sandbox=NoopSandbox(), paths=dst_paths
    )
    manifest = dict(out["manifest"])
    manifest["checksum"] = out["checksum"]
    await loader.install(path=out["archive_path"], manifest=manifest)

    installed = SkillStore(dst_paths)
    assert "write in Korean" in installed.read_resource(
        "weekly-report-build", "references/tone.md"
    )


def test_resource_edit_participates_in_version_rollback():
    paths = _fresh_paths()
    svc = _service(paths)
    svc.create_skill(
        name="weekly-report-build",
        body=_BODY,
        description="Build the weekly report file",
    )
    svc.update_skill_file("weekly-report-build", "scripts/run.sh", "echo first\n")
    assert svc.get_skill("weekly-report-build").version == 2
    assert any(
        item["path"] == "scripts/run.sh"
        for item in svc.get_skill_files("weekly-report-build")
    )

    new_version = svc.rollback("weekly-report-build", 1)
    assert new_version == 3
    assert [item["path"] for item in svc.get_skill_files("weekly-report-build")] == [
        "SKILL.md"
    ]


# ── 4. 빌트인 export ──────────────────────────────────────────────────────────

def test_builtin_skill_is_exportable():
    paths = _fresh_paths()
    out = _service(paths).export_skill("business-planning")
    assert Path(out["archive_path"]).exists()
    assert out["manifest"]["name"] == "business-planning"


# ── 5. skill-authoring 빌트인 (방향 2) ────────────────────────────────────────

def test_skill_authoring_builtin_exists_and_routes_on_mention():
    paths = _fresh_paths()
    assert BuiltinSkillCatalog(paths).is_builtin("skill-authoring")
    svc = _service(paths)
    pf = svc.prefetch("s1", "skill-authoring 스킬 보고 새 스킬 만들어줘")
    assert any(s.name == "skill-authoring" for s in pf.skills)  # 명시 언급 → 전문 주입
    # 평상시엔 인덱스로만 실린다 (전문 주입 아님 — 소음 방지).
    pf_plain = svc.prefetch("s2", "오늘 날씨 어때")
    assert all(s.name != "skill-authoring" for s in pf_plain.skills)
