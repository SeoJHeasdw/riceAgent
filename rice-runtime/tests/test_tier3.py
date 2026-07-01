"""test_tier3.py — Tier 3 고도화(Skill·Plugin·Connector) 회귀 테스트.

v1/v2 분석 리포트의 버그 교정과 신규 기능을 코드 레벨에서 고정한다. 네트워크·실제
키체인·실제 OAuth 는 건드리지 않는다(전부 파일/DB/순수 함수).
"""
from __future__ import annotations

import json
import tempfile

import pytest

from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database


def _fresh_db() -> Database:
    return Database(":memory:").connect()


def _fresh_paths() -> RicePaths:
    return RicePaths.from_env(tempfile.mkdtemp(prefix="rice-tier3-")).ensure()


# ── 버그#1: PermissionChecker 가 rice-artifact.json 을 읽는다 ──────────────────

class TestPermissionCheckerJson:
    def test_reads_json_manifest(self):
        from rice_runtime.connector.permission.checker import PermissionChecker

        paths = _fresh_paths()
        pdir = paths.plugins / "plugin:gmailer"
        pdir.mkdir(parents=True)
        (pdir / "rice-artifact.json").write_text(
            json.dumps({"requires": {"connectors": ["gmail"]}}), encoding="utf-8"
        )
        checker = PermissionChecker(paths.plugins)
        assert checker.check("plugin:gmailer", "gmail") is True
        assert checker.check("plugin:gmailer", "slack") is False

    def test_yaml_fallback_still_works(self):
        import yaml

        from rice_runtime.connector.permission.checker import PermissionChecker

        paths = _fresh_paths()
        pdir = paths.plugins / "plugin:y"
        pdir.mkdir(parents=True)
        (pdir / "rice-artifact.yaml").write_text(
            yaml.safe_dump({"requires": {"connectors": ["gmail"]}}), encoding="utf-8"
        )
        assert PermissionChecker(paths.plugins).check("plugin:y", "gmail") is True


# ── Gmail Connector: 자격증명 로딩 / redirect / gmail_send ─────────────────────

class TestGmailConnector:
    def test_load_client_config_from_json_file(self, monkeypatch):
        from rice_runtime.connector.oauth.flow import load_gmail_client_config

        monkeypatch.delenv("RICE_GMAIL_CLIENT_ID", raising=False)
        monkeypatch.delenv("RICE_GMAIL_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("RICE_GMAIL_CLIENT_SECRET_FILE", raising=False)
        paths = _fresh_paths()
        (paths.connectors / "gmail_client_secret.json").write_text(
            json.dumps({"installed": {"client_id": "cid.apps", "client_secret": "sec"}}),
            encoding="utf-8",
        )
        cfg = load_gmail_client_config(paths.connectors)
        assert cfg is not None and cfg.client_id == "cid.apps" and cfg.client_secret == "sec"

    def test_env_takes_precedence_over_json(self, monkeypatch):
        from rice_runtime.connector.oauth.flow import load_gmail_client_config

        monkeypatch.setenv("RICE_GMAIL_CLIENT_ID", "env-id")
        monkeypatch.setenv("RICE_GMAIL_CLIENT_SECRET", "env-sec")
        paths = _fresh_paths()
        (paths.connectors / "gmail_client_secret.json").write_text(
            json.dumps({"installed": {"client_id": "json-id", "client_secret": "json-sec"}}),
            encoding="utf-8",
        )
        cfg = load_gmail_client_config(paths.connectors)
        assert cfg.client_id == "env-id"

    def test_default_redirect_uri_uses_port(self):
        from rice_runtime.connector.oauth.flow import default_redirect_uri

        assert default_redirect_uri(8080) == "http://localhost:8080/connectors/oauth/callback"
        assert default_redirect_uri(None).startswith("http://localhost:")

    def test_build_auth_url_uses_loaded_creds(self):
        from rice_runtime.connector.oauth.flow import GmailOAuthFlow

        flow = GmailOAuthFlow(client_id="abc", client_secret="xyz", redirect_uri="http://localhost:9/cb")
        url, state = flow.build_auth_url()
        assert "client_id=abc" in url and "redirect_uri=http" in url and state

    @pytest.mark.asyncio
    async def test_remote_mcp_tools_persisted_at_connect_and_read_locally(self):
        """도구는 하드코딩이 아니라 원격 MCP tools/list 로 연결 시점에 DB 에 저장된다.

        sync_tools() 가 카탈로그를 저장하면 list_tools() 가 네트워크 없이 DB 에서 읽는다.
        """
        from unittest.mock import AsyncMock, patch

        from rice_runtime.connector.service import ConnectorService
        from rice_runtime.core.types import ToolSpec

        db = _fresh_db()
        db.execute(
            "INSERT INTO connectors(id, connector_type, display_name, status) "
            "VALUES ('c1','gmail','Gmail','connected')"
        )
        svc = ConnectorService(db, _fresh_paths())
        # 카탈로그 저장 전에는 비어 있다.
        assert svc.list_tools() == []

        remote = [
            ToolSpec(name="search_threads", description="", parameters={}, source="connector",
                     owner_id="c1", requires_approval=True),
            ToolSpec(name="create_draft", description="", parameters={}, source="connector",
                     owner_id="c1", requires_approval=True),
        ]
        with patch.object(svc, "_fetch_remote_tools", AsyncMock(return_value=remote)):
            await svc.sync_tools("c1")
        names = {t.name for t in svc.list_tools()}
        assert {"search_threads", "create_draft"} <= names

    def test_connected_types(self):
        from rice_runtime.connector.service import ConnectorService

        db = _fresh_db()
        svc = ConnectorService(db, _fresh_paths())
        assert svc.connected_types() == set()
        db.execute(
            "INSERT INTO connectors(id, connector_type, display_name, status) "
            "VALUES ('c1','gmail','Gmail','connected')"
        )
        assert svc.connected_types() == {"gmail"}


# ── Calendar/Drive: 같은 Google Cloud OAuth client 를 공유하는 신규 커넥터 타입 ──

class TestGoogleFamilyConnectors:
    def test_registry_includes_calendar_and_drive(self):
        from rice_runtime.connector.registry import (
            CALENDAR_DESCRIPTOR,
            DRIVE_DESCRIPTOR,
            GMAIL_DESCRIPTOR,
            get_descriptor,
            supported_types,
        )

        assert {"gmail", "calendar", "drive"} <= supported_types()
        assert get_descriptor("calendar") is CALENDAR_DESCRIPTOR
        assert get_descriptor("drive") is DRIVE_DESCRIPTOR
        # 셋 다 같은 Google Identity 엔드포인트를 쓴다 (OAuth client 하나 공유).
        assert CALENDAR_DESCRIPTOR.auth_url == GMAIL_DESCRIPTOR.auth_url == DRIVE_DESCRIPTOR.auth_url

    def test_calendar_and_drive_mcp_endpoints_distinct(self):
        from rice_runtime.connector.registry import CALENDAR_DESCRIPTOR, DRIVE_DESCRIPTOR, GMAIL_DESCRIPTOR

        endpoints = {GMAIL_DESCRIPTOR.mcp_endpoint, CALENDAR_DESCRIPTOR.mcp_endpoint, DRIVE_DESCRIPTOR.mcp_endpoint}
        assert len(endpoints) == 3  # 세 타입이 서로 다른 원격 MCP 서버를 가리킨다

    def test_client_config_falls_back_to_gmail_json_when_no_dedicated_file(self, monkeypatch):
        """calendar/drive 전용 client json 이 없으면 이미 설치된 gmail_client_secret.json 을 쓴다."""
        from rice_runtime.connector.oauth.flow import load_client_config
        from rice_runtime.connector.registry import CALENDAR_DESCRIPTOR, DRIVE_DESCRIPTOR

        monkeypatch.delenv("RICE_CALENDAR_CLIENT_ID", raising=False)
        monkeypatch.delenv("RICE_CALENDAR_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("RICE_DRIVE_CLIENT_ID", raising=False)
        monkeypatch.delenv("RICE_DRIVE_CLIENT_SECRET", raising=False)
        paths = _fresh_paths()
        (paths.connectors / "gmail_client_secret.json").write_text(
            json.dumps({"installed": {"client_id": "shared-cid", "client_secret": "shared-sec"}}),
            encoding="utf-8",
        )

        cal_cfg = load_client_config(CALENDAR_DESCRIPTOR, paths.connectors)
        drv_cfg = load_client_config(DRIVE_DESCRIPTOR, paths.connectors)

        assert cal_cfg is not None and cal_cfg.client_id == "shared-cid"
        assert drv_cfg is not None and drv_cfg.client_id == "shared-cid"

    def test_dedicated_client_json_takes_precedence_over_fallback(self, monkeypatch):
        """calendar_client_secret.json 이 있으면 gmail 공유 파일보다 그것을 우선한다."""
        from rice_runtime.connector.oauth.flow import load_client_config
        from rice_runtime.connector.registry import CALENDAR_DESCRIPTOR

        monkeypatch.delenv("RICE_CALENDAR_CLIENT_ID", raising=False)
        monkeypatch.delenv("RICE_CALENDAR_CLIENT_SECRET", raising=False)
        paths = _fresh_paths()
        (paths.connectors / "gmail_client_secret.json").write_text(
            json.dumps({"installed": {"client_id": "shared-cid", "client_secret": "shared-sec"}}),
            encoding="utf-8",
        )
        (paths.connectors / "calendar_client_secret.json").write_text(
            json.dumps({"installed": {"client_id": "dedicated-cid", "client_secret": "dedicated-sec"}}),
            encoding="utf-8",
        )

        cfg = load_client_config(CALENDAR_DESCRIPTOR, paths.connectors)
        assert cfg is not None and cfg.client_id == "dedicated-cid"

    def test_begin_connect_calendar_and_drive_create_pending_records(self):
        """manager 가 gmail 과 똑같은 제네릭 경로로 calendar/drive 도 처리한다."""
        from rice_runtime.connector.service import ConnectorService

        db = _fresh_db()
        paths = _fresh_paths()
        (paths.connectors / "gmail_client_secret.json").write_text(
            json.dumps({"installed": {"client_id": "shared-cid", "client_secret": "shared-sec"}}),
            encoding="utf-8",
        )
        svc = ConnectorService(db, paths)

        for connector_type, display_name in (("calendar", "Google Calendar"), ("drive", "Google Drive")):
            result = svc._manager.begin_connect(connector_type)
            assert result.state and result.connector_id
            assert "accounts.google.com" in result.oauth_url
            assert f"state={result.state}" in result.oauth_url
            row = db.query_one("SELECT * FROM connectors WHERE id=?", (result.connector_id,))
            assert row is not None
            assert row["connector_type"] == connector_type
            assert row["display_name"] == display_name
            assert row["status"] == "disconnected"


# ── 버그#5: lifecycle 이 last_used_at 을 우선한다 ─────────────────────────────

class TestLifecycleLastUsed:
    def test_recent_use_keeps_active(self):
        from rice_runtime.core.types import now_iso
        from rice_runtime.learning.skill.lifecycle import compute_transition
        from rice_runtime.learning.skill.store import SkillData

        # updated_at 은 오래됨(10일 전)이지만 방금 사용됨 → stale 전이 없어야 함.
        old = "2000-01-01T00:00:00Z"
        skill = SkillData("s", 1, "", "active", "body", [], None, old, old)
        assert compute_transition(skill, 3, 7, last_used_at=now_iso()) is None

    def test_stale_when_never_used_and_old(self):
        from rice_runtime.learning.skill.lifecycle import compute_transition
        from rice_runtime.learning.skill.store import SkillData

        old = "2000-01-01T00:00:00Z"
        skill = SkillData("s", 1, "", "active", "body", [], None, old, old)
        assert compute_transition(skill, 3, 7, last_used_at=None) == "stale"


# ── 버그#8: 다국어 학습 신호 ─────────────────────────────────────────────────

class TestSignals:
    def test_korean_correction_and_failure(self):
        from rice_runtime.learning.signals import has_correction_signal, has_failure_signal

        assert has_correction_signal("아니 그게 아니라 이렇게 해야 해")
        assert has_correction_signal("그 방법은 틀렸어요")
        assert has_failure_signal("그렇게 하니까 안 돼요")
        assert has_failure_signal("오류가 발생했습니다")

    def test_korean_skill_intent_and_name(self):
        from rice_runtime.learning.signals import extract_skill_name, has_skill_creation_intent

        assert has_skill_creation_intent("이건 재사용 가능한 절차예요")
        assert extract_skill_name("이걸 스킬: deploy-web 으로 저장해줘") == "deploy-web"
        assert extract_skill_name("save this as a skill: my-flow") == "my-flow"
        assert extract_skill_name("그냥 아무 말") is None

    def test_patch_trigger_scans_user_text(self):
        from rice_runtime.learning.skill.patch import detect_patch_trigger

        # 어시스턴트 최종 메시지엔 신호 없음, 사용자 메시지(교정)에 있음 → T1a
        assert detect_patch_trigger("알겠습니다", 1, ("sk",), user_text="아니 그건 틀렸어") == "T1a"
        # 주입 스킬 없으면 트리거 없음
        assert detect_patch_trigger("틀렸어", 1, (), user_text="틀렸어") is None


# ── Skill CRUD ────────────────────────────────────────────────────────────────

class TestSkillCrud:
    def _svc(self):
        from rice_runtime.core.config import LearningConfig
        from rice_runtime.learning.service import LearningService

        return LearningService(_fresh_db(), LearningConfig(), _fresh_paths())

    def test_create_read_update_delete(self):
        from rice_runtime.core.errors import NotFoundError, ValidationError

        svc = self._svc()
        detail = svc.create_skill(name="my-skill", body="step 1\nstep 2", description="d", tags=("a",))
        assert detail.name == "my-skill" and detail.version == 1 and detail.source == "user"

        # 중복 생성 거부
        with pytest.raises(ValidationError):
            svc.create_skill(name="my-skill", body="x")
        # 잘못된 이름 거부
        with pytest.raises(ValidationError):
            svc.create_skill(name="Bad Name", body="x")

        # 본문 변경 → version+1 + history 보관
        updated = svc.update_skill("my-skill", body="step 1\nstep 2\nstep 3")
        assert updated.version == 2
        assert any(v.version == 1 for v in svc.history("my-skill"))

        # 삭제 후 조회 불가
        svc.delete_skill("my-skill")
        assert svc.get_skill("my-skill") is None
        with pytest.raises(NotFoundError):
            svc.delete_skill("my-skill")


# ── Plugin loader: version float, 하위dir SKILL.md, skill artifact 평면 설치 ──

class TestPluginLoader:
    def _loader(self, paths):
        from rice_runtime.execution.plugin.loader import PluginLoader
        from rice_runtime.execution.service import NoopSandbox

        return PluginLoader(db=_fresh_db(), sandbox=NoopSandbox(), paths=paths)

    def test_manifest_version_float_coerced(self):
        from rice_runtime.execution.plugin.manifest import parse_manifest

        m = parse_manifest({"schema_version": 1, "type": "plugin", "name": "p", "version": 1.0})
        assert m.version == "1.0"
        m2 = parse_manifest({"schema_version": 1, "type": "plugin", "name": "p", "version": 1})
        assert m2.version == "1"

    def test_injected_skills_subdir_layout(self):
        paths = _fresh_paths()
        loader = self._loader(paths)
        pid = "plugin:bundle"
        sub = paths.plugins / pid / "skills" / "deploy"
        sub.mkdir(parents=True)
        (sub / "SKILL.md").write_text(
            "---\nname: deploy-thing\nversion: 1\ndescription: d\nstate: active\n---\nbody",
            encoding="utf-8",
        )
        (paths.plugins / pid / "rice-artifact.json").write_text(
            json.dumps({"schema_version": 1, "type": "plugin", "name": "bundle", "version": "1"}),
            encoding="utf-8",
        )
        loader._db.execute(
            "INSERT INTO installed_artifacts(id,type,name,version,source,enabled) "
            "VALUES (?,?,?,?,?,1)",
            (pid, "plugin", "bundle", "1", "market"),
        )
        names = {s.name for s in loader.injected_skills()}
        assert "deploy-thing" in names

    @pytest.mark.asyncio
    async def test_skill_artifact_installed_flat_and_injectable(self):
        from rice_runtime.learning.skill.store import SkillStore

        paths = _fresh_paths()
        loader = self._loader(paths)
        # 디렉토리 소스 skill artifact (rice-artifact.yaml + SKILL.md).
        src = paths.root / "artifact_src"
        src.mkdir(parents=True)
        (src / "SKILL.md").write_text(
            "---\nname: from-market\nversion: 1\ndescription: d\nstate: active\n---\nmarket body",
            encoding="utf-8",
        )
        manifest = {"schema_version": 1, "type": "skill", "name": "from-market", "version": "1"}
        await loader.install(path=str(src), manifest=manifest)
        # 평면 <name>.md 로 설치되어 SkillStore.list() 가 집어든다(버그#3 교정).
        flat = paths.skills / "from-market.md"
        assert flat.exists()
        names = {s.name for s in SkillStore(paths).list()}
        assert "from-market" in names


# ── 버그#9: approval.resolve 가 bool 을 반환 + get() ──────────────────────────

class TestApprovalResolve:
    @pytest.mark.asyncio
    async def test_resolve_returns_bool_and_get(self):
        import asyncio

        from rice_runtime.core.types import ApprovalRequest, ApprovalResponse
        from rice_runtime.server.approval import ApprovalRegistry

        gate = ApprovalRegistry()
        # unknown id → False
        assert gate.resolve("nope", ApprovalResponse("nope", "approve")) is False
        assert gate.get("nope") is None

        req = ApprovalRequest(kind="skill_patch", payload={"skill_id": "s1"})

        async def answer():
            await asyncio.sleep(0.01)
            assert gate.get(req.approval_id) is not None  # parked & inspectable
            assert gate.resolve(req.approval_id, ApprovalResponse(req.approval_id, "approve")) is True

        resp, _ = await asyncio.gather(gate.request("sess", req, timeout_s=5), answer())
        assert resp.decision == "approve"
