"""Registry + file-endpoint regression tests.

서버를 띄우지 않고 registry 함수 + app 핸들러를 직접 호출한다(httpx 불필요).
실제 Claude 플러그인(claude-for-legal) 인제스트 테스트는 마켓플레이스가 없으면 skip.

이 테스트의 존재 이유: "실제 동작한다"던 보고가 실제로는 깨져 있던 사고를 CI가 잡게 하기 위함.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException

from rice_platform import registry
from rice_platform.app import artifact_file

HAS_PLUGINS = bool(registry._marketplace_dirs())
needs_plugins = pytest.mark.skipif(
    not HAS_PLUGINS, reason="claude-for-legal 마켓플레이스가 없어 plugin 인제스트 테스트 skip"
)


def _file(artifact_id: str, path: str):
    return asyncio.run(artifact_file(artifact_id, path))


# ── rice 네이티브 skill artifact ─────────────────────────────────────────────

def test_catalog_lists_rice_skills():
    ids = {e["id"] for e in registry.list_artifacts(artifact_type="skill")}
    assert {"caveman", "code-review", "data-analysis", "git-workflow"} <= ids


def test_catalog_derived_fields():
    art = registry.get_artifact("algorithmic-art")
    assert art is not None
    assert art["skill_files"] == ["SKILL.md"]
    assert "templates" in art["bundled_resources"]
    assert art["has_bundled_resources"] is True
    assert art["license"]  # manifest 또는 LICENSE.txt에서 도출


def test_file_endpoint_reads_skill_md():
    res = _file("caveman", "SKILL.md")
    assert res["success"]
    assert res["data"]["language"] == "markdown"
    assert res["data"]["content"]


def test_file_endpoint_nested_html():
    res = _file("algorithmic-art", "templates/viewer.html")
    assert res["success"]
    assert res["data"]["language"] == "html"


def test_sandbox_rejects_traversal():
    with pytest.raises(HTTPException) as ei:
        _file("caveman", "../code-review/SKILL.md")
    assert ei.value.status_code == 403


def test_missing_file_404():
    with pytest.raises(HTTPException) as ei:
        _file("caveman", "nope.md")
    assert ei.value.status_code == 404


def test_download_package_builds():
    pkg = registry.build_download_package("caveman")
    assert pkg is not None
    data, checksum = pkg
    assert data and len(checksum) == 64


def test_download_checksum_is_deterministic():
    # 같은 내용이면 요청을 반복해도 동일한 체크섬이어야 한다 (무결성 앵커 + 캐시 키).
    a = registry.build_download_package("caveman")
    b = registry.build_download_package("caveman")
    assert a is not None and b is not None
    assert a[1] == b[1]


def test_unknown_artifact_returns_none():
    assert registry.get_artifact("does-not-exist") is None
    assert registry.build_download_package("does-not-exist") is None


def test_marketplace_skips_remote_source_objects(monkeypatch, tmp_path):
    """Upstream Claude marketplaces mix local string sources and remote source objects."""
    local = tmp_path / "plugins" / "local-plugin"
    (local / ".claude-plugin").mkdir(parents=True)
    (local / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "local-plugin", "version": "1.0.0", "description": "local"}),
        encoding="utf-8",
    )
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "remote-plugin",
                        "source": {"source": "url", "url": "https://example.invalid/plugin.git"},
                    },
                    {"name": "local-plugin", "source": "./plugins/local-plugin"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RICE_PLUGIN_MARKETPLACES", str(tmp_path))

    ids = {p["id"] for p in registry.list_artifacts(artifact_type="plugin")}
    assert "plugin:local-plugin" in ids
    assert "plugin:remote-plugin" not in ids


def test_skill_and_plugin_same_name_can_coexist(monkeypatch, tmp_path):
    artifacts = tmp_path / "artifacts"
    skill = artifacts / "playwright"
    skill.mkdir(parents=True)
    (skill / "rice-artifact.yaml").write_text(
        "\n".join(
            [
                "schema_version: 1",
                "type: skill",
                "name: playwright",
                "version: 1.0.0",
                "display_name: Playwright Skill",
                "description: Skill version.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (skill / "SKILL.md").write_text("# Playwright Skill\n", encoding="utf-8")

    plugin = tmp_path / "plugins" / "playwright"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "playwright", "version": "1.0.0", "description": "Plugin version."}),
        encoding="utf-8",
    )
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"plugins": [{"name": "playwright", "source": "./plugins/playwright"}]}),
        encoding="utf-8",
    )

    monkeypatch.setenv("RICE_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setenv("RICE_PLUGIN_MARKETPLACES", str(tmp_path))

    skill_ids = {p["id"] for p in registry.list_artifacts(artifact_type="skill")}
    plugin_ids = {p["id"] for p in registry.list_artifacts(artifact_type="plugin")}
    assert "playwright" in skill_ids
    assert "plugin:playwright" in plugin_ids

    skill_detail = registry.get_artifact("playwright")
    plugin_detail = registry.get_artifact("plugin:playwright")
    assert skill_detail is not None and skill_detail["type"] == "skill"
    assert plugin_detail is not None and plugin_detail["type"] == "plugin"

    assert registry.build_download_package("playwright") is not None
    assert registry.build_download_package("plugin:playwright") is not None


# ── 실제 Claude 플러그인 인제스트 (claude-for-legal) ─────────────────────────

@needs_plugins
def test_claude_plugins_ingested():
    plugins = registry.list_artifacts(artifact_type="plugin")
    ids = {p["id"] for p in plugins}
    # 마켓플레이스의 핵심 플러그인들이 카탈로그에 떠야 함
    assert {"plugin:employment-legal", "plugin:privacy-legal", "plugin:corporate-legal"} <= ids


@needs_plugins
def test_employment_legal_detail():
    emp = registry.get_artifact("plugin:employment-legal")
    assert emp is not None
    assert emp["type"] == "plugin"
    # 중첩 레이아웃 skills/<name>/SKILL.md
    assert len(emp["skill_files"]) >= 15
    assert all(f.startswith("skills/") and f.endswith("/SKILL.md") for f in emp["skill_files"])
    # plugin 리소스
    assert "agents" in emp["bundled_resources"]
    assert "hooks" in emp["bundled_resources"]
    # .mcp.json → requires.connectors (이름)
    connectors = emp["requires"].get("connectors") or []
    assert "Slack" in connectors and "Google Drive" in connectors
    # connectors(상세): .mcp.json 의 description까지 노출
    detail = {c["name"]: c for c in emp["connectors"]}
    assert "Slack" in detail and detail["Slack"]["description"]


@needs_plugins
def test_plugin_files_readable():
    emp = registry.get_artifact("plugin:employment-legal")
    assert emp is not None
    first = emp["skill_files"][0]
    res = _file("plugin:employment-legal", first)
    assert res["success"] and res["data"]["content"]
    # 번들 리소스(에이전트) 파일도 읽힘
    agent = _file("plugin:employment-legal", "agents/leave-tracker.md")
    assert agent["success"] and agent["data"]["content"]


@needs_plugins
def test_plugin_download_builds():
    pkg = registry.build_download_package("plugin:employment-legal")
    assert pkg is not None
    data, checksum = pkg
    assert len(data) > 0 and len(checksum) == 64
