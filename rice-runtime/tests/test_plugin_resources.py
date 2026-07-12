"""test_plugin_resources.py — 플러그인 스킬 번들 리소스 파이프라인 + 로더 위생 고정.

이 파일이 고정하는 계약 (2026-07-12 플러그인 고도화):
  1. 표준 레이아웃(`skills/<sub>/SKILL.md`)의 형제 파일(references/ 등)은
     InjectedSkill.resource_dir/resources 로 실린다 — flat 레이아웃은 빈 값.
  2. `read_skill_resource`(skill_resource 도구 백엔드): 스킬 디렉토리 안의
     텍스트 파일만 서빙한다 — traversal/바이너리/미등록 스킬은 거부.
  3. 전문 주입 블록과 skill_view 결과에 번들 파일 목록이 광고된다.
  4. injected_skills() 는 SKILL.md stat 시그니처로 캐싱된다 — 내용 변경 시 재파싱.
  5. 미니 frontmatter 파서가 block scalar(`description: |`)를 문자열로 합친다.
  6. reconcile_builtin_shadows: 빌트인으로 승격된 플러그인의 과거 설치본
     (DB 행+디렉토리 — list에서 안 보이고 remove도 거부돼 영구 고착)을 부팅 시 정리.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from rice_runtime.core.config import LearningConfig
from rice_runtime.core.errors import NotFoundError, ValidationError
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.execution.plugin.builtin import BuiltinPluginCatalog
from rice_runtime.execution.plugin.loader import PluginLoader, _parse_skill_md_minimal
from rice_runtime.execution.service import NoopSandbox
from rice_runtime.learning.service import LearningService


def _fresh_paths() -> RicePaths:
    return RicePaths.from_env(tempfile.mkdtemp(prefix="rice-plugin-res-")).ensure()


def _loader(paths: RicePaths, *, builtin: BuiltinPluginCatalog | None = None) -> PluginLoader:
    db = Database(":memory:").connect()
    return PluginLoader(db=db, sandbox=NoopSandbox(), paths=paths, builtin_plugins=builtin)


def _install_demo_plugin(paths: RicePaths, loader: PluginLoader, *, plugin_name: str = "demo") -> None:
    """DB 행 + 표준 레이아웃 스킬(deploy-guide: SKILL.md + references/ + scripts/)
    + flat 스킬(quick-note.md) 을 가진 플러그인을 디스크에 직접 구성한다."""
    plugin_id = f"plugin:{plugin_name}"
    root = paths.plugins / plugin_id
    (root / "skills" / "deploy-guide" / "references").mkdir(parents=True)
    (root / "skills" / "deploy-guide" / "scripts").mkdir(parents=True)
    (root / "rice-artifact.json").write_text(json.dumps({"name": plugin_name}), encoding="utf-8")
    (root / "skills" / "deploy-guide" / "SKILL.md").write_text(
        "---\nname: deploy-guide\ndescription: deployment checklist guide\n"
        "tags: [deploy, release]\n---\n# Steps\nSee references/checklist.md for the full list.\n",
        encoding="utf-8",
    )
    (root / "skills" / "deploy-guide" / "references" / "checklist.md").write_text(
        "1. build\n2. verify\n3. ship\n", encoding="utf-8"
    )
    (root / "skills" / "deploy-guide" / "scripts" / "release.sh").write_text(
        "#!/bin/sh\necho release\n", encoding="utf-8"
    )
    (root / "skills" / "quick-note.md").write_text(
        "---\nname: quick-note\ndescription: flat layout skill\n---\nJust a note.\n",
        encoding="utf-8",
    )
    loader._db.execute(
        "INSERT INTO installed_artifacts (id, type, name, version, source, enabled) "
        f"VALUES (?, 'plugin', '{plugin_name}', '1.0.0', 'market', 1)",
        (plugin_id,),
    )


# ── 1. 리소스 수집 ────────────────────────────────────────────────────────────

def test_nested_skill_carries_resources_and_flat_does_not():
    paths = _fresh_paths()
    loader = _loader(paths)
    _install_demo_plugin(paths, loader)
    skills = {s.name: s for s in loader.injected_skills()}

    nested = skills["deploy-guide"]
    assert nested.resource_dir.endswith(str(Path("skills") / "deploy-guide"))
    assert "references/checklist.md" in nested.resources
    assert "scripts/release.sh" in nested.resources
    assert "SKILL.md" not in nested.resources

    flat = skills["quick-note"]
    assert flat.resource_dir == "" and flat.resources == ()


# ── 2+3. skill_resource 백엔드 + 광고 ─────────────────────────────────────────

class TestReadSkillResource:
    def setup_method(self):
        self._paths = _fresh_paths()
        self._loader = _loader(self._paths)
        _install_demo_plugin(self._paths, self._loader)
        self._svc = LearningService(Database(":memory:").connect(), LearningConfig(), self._paths)
        # prefetch 가 extra 레지스트리를 세운다 (오케스트레이터와 동일 경로).
        self._pf = self._svc.prefetch(
            "s1", "배포 배포 체크리스트 deploy release 진행해줘",
            extra_skills=tuple(self._loader.injected_skills()),
        )

    def test_serves_bundled_text_file(self):
        out = self._svc.read_skill_resource("deploy-guide", "references/checklist.md")
        assert out["content"].startswith("1. build")
        assert out["skill"] == "deploy-guide"

    def test_rejects_traversal_and_unknown(self):
        with pytest.raises(ValidationError):
            self._svc.read_skill_resource("deploy-guide", "../../../etc/passwd")
        with pytest.raises(NotFoundError):
            self._svc.read_skill_resource("deploy-guide", "references/nope.md")
        with pytest.raises(NotFoundError):
            self._svc.read_skill_resource("quick-note", "anything.md")  # flat: 번들 없음

    def test_rejects_binary(self):
        binary = (
            Path(self._svc._extra_skills["deploy-guide"].resource_dir) / "references" / "img.png"
        )
        binary.write_bytes(b"\x89PNG\x00\x00binary")
        with pytest.raises(ValidationError):
            self._svc.read_skill_resource("deploy-guide", "references/img.png")

    def test_view_and_injection_advertise_resources(self):
        out = self._svc.view_skill_body("s1", "deploy-guide")
        assert "references/checklist.md" in out.get("resources", [])
        assert "skill_resource" in out.get("resources_note", "")
        # 전문 주입됐다면 시스템 프롬프트 블록에도 광고가 실린다.
        if any(s.name == "deploy-guide" for s in self._pf.skills):
            assert "read with skill_resource" in self._pf.system_prompt_additions


# ── 4. 캐시 ──────────────────────────────────────────────────────────────────

def test_injected_skills_cache_refreshes_on_content_change():
    paths = _fresh_paths()
    loader = _loader(paths)
    _install_demo_plugin(paths, loader)
    first = loader.injected_skills()
    again = loader.injected_skills()
    assert [s.body for s in again] == [s.body for s in first]  # 캐시 적중 (동일 결과)

    md = paths.plugins / "plugin:demo" / "skills" / "deploy-guide" / "SKILL.md"
    md.write_text(
        "---\nname: deploy-guide\ndescription: v2\n---\nUpdated body.\n", encoding="utf-8"
    )
    st = md.stat()
    os.utime(md, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))  # mtime 확실히 전진
    refreshed = {s.name: s for s in loader.injected_skills()}
    assert refreshed["deploy-guide"].body == "Updated body."


# ── 5. block scalar frontmatter ──────────────────────────────────────────────

def test_minimal_parser_handles_block_scalar_description():
    fm = _parse_skill_md_minimal(
        "---\nname: bs-skill\ndescription: |\n  line one\n  line two\nversion: 0.1.0\n---\nBody.\n"
    )
    assert fm["description"] == "line one line two"
    assert fm["name"] == "bs-skill"
    assert fm["body"] == "Body."


# ── 6. 빌트인 쉐도우 reconcile ────────────────────────────────────────────────

def _fake_builtin_catalog(paths: RicePaths, name: str) -> BuiltinPluginCatalog:
    asset_dir = Path(tempfile.mkdtemp(prefix="rice-builtin-assets-"))
    manifest_dir = asset_dir / name / ".claude-plugin"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")
    return BuiltinPluginCatalog(paths, asset_dir=asset_dir)


def test_reconcile_removes_shadowed_install_and_keeps_others():
    paths = _fresh_paths()
    catalog = _fake_builtin_catalog(paths, "promoted")
    loader = _loader(paths, builtin=catalog)
    # 승격 전 설치본 (이제 빌트인에 가려 UI에도 안 보이고 remove 도 거부된다).
    _install_demo_plugin(paths, loader, plugin_name="promoted")
    # 무관한 정상 설치본 — 건드리면 안 된다.
    _install_demo_plugin(paths, loader, plugin_name="innocent")

    assert loader.reconcile_builtin_shadows() == 1
    assert not (paths.plugins / "plugin:promoted").exists()
    assert (paths.plugins / "plugin:innocent").exists()
    rows = loader._db.query_all("SELECT id FROM installed_artifacts")
    assert {r["id"] for r in rows} == {"plugin:innocent"}
    # 재실행은 0 — 멱등.
    assert loader.reconcile_builtin_shadows() == 0
