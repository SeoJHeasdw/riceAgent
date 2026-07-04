"""test_builtin_skills.py — 빌트인 스킬 계층 (skill/builtin.py + service 통합) 검증.

수용 기준:
  1. 카탈로그: 자산 로드(business-planning, source=builtin 강제) + 상태 영속화
     (active↔archived, 그 외 거부) + 손상 상태 파일 관용.
  2. list_skills/get_skill: 빌트인 포함, 동명 사용자 파일은 숨김(예약 이름).
  3. prefetch: active 빌트인 주입, archived(비활성)면 제외.
  4. 보호: update/delete/rollback/동명 create/stale 전이/stage_proposal/
     apply_patch 전부 거부 — 활성/비활성 전환만 허용.
  5. 라우트: PATCH state 200, PUT/DELETE 422 (ValidationError 매핑, 빌트인).

격리: tempfile.mkdtemp() 임시 루트 (CONVENTIONS §6).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from rice_runtime.core.config import LearningConfig
from rice_runtime.core.errors import ValidationError
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.learning.service import LearningService
from rice_runtime.learning.skill.builtin import BUILTIN_SOURCE, BuiltinSkillCatalog

BUILTIN_NAME = "business-planning"


def _fresh_paths() -> RicePaths:
    return RicePaths.from_env(tempfile.mkdtemp(prefix="rice-builtin-test-")).ensure()


def _service(paths: RicePaths) -> LearningService:
    return LearningService(Database(":memory:").connect(), LearningConfig(), paths)


# ── 1. catalog ────────────────────────────────────────────────────────────────

def test_catalog_loads_assets_and_forces_source():
    catalog = BuiltinSkillCatalog(_fresh_paths())
    assert catalog.is_builtin(BUILTIN_NAME)
    skill = catalog.get(BUILTIN_NAME)
    assert skill is not None
    assert skill.source == BUILTIN_SOURCE
    assert skill.state == "active"  # 기본 활성
    assert "create_workbook" in skill.body  # 빌트인 도구를 실제로 참조하는 절차 문서


def test_catalog_state_persists_and_rejects_stale():
    paths = _fresh_paths()
    catalog = BuiltinSkillCatalog(paths)
    catalog.set_state(BUILTIN_NAME, "archived")
    # 새 인스턴스로 다시 읽어도 상태 유지 (파일 영속)
    assert BuiltinSkillCatalog(paths).get(BUILTIN_NAME).state == "archived"
    catalog.set_state(BUILTIN_NAME, "active")
    assert BuiltinSkillCatalog(paths).get(BUILTIN_NAME).state == "active"

    with pytest.raises(ValidationError):
        catalog.set_state(BUILTIN_NAME, "stale")
    with pytest.raises(ValidationError):
        catalog.set_state("no-such-skill", "active")


def test_catalog_tolerates_corrupt_state_file():
    paths = _fresh_paths()
    (paths.skills / ".builtin-states.json").write_text("{not json", encoding="utf-8")
    assert BuiltinSkillCatalog(paths).get(BUILTIN_NAME).state == "active"


def test_catalog_copies_are_isolated():
    catalog = BuiltinSkillCatalog(_fresh_paths())
    catalog.get(BUILTIN_NAME).body = "tampered"
    assert catalog.get(BUILTIN_NAME).body != "tampered"


# ── 2. service: 목록/조회/주입 ────────────────────────────────────────────────

def test_list_and_get_include_builtin():
    svc = _service(_fresh_paths())
    ids = {s.id: s for s in svc.list_skills()}
    assert BUILTIN_NAME in ids
    assert ids[BUILTIN_NAME].source == BUILTIN_SOURCE
    detail = svc.get_skill(BUILTIN_NAME)
    assert detail is not None and detail.body


def test_builtin_name_shadows_stale_user_file():
    """과거 버전에서 동명 사용자 파일이 남았더라도 목록에 중복 id가 생기지 않는다."""
    paths = _fresh_paths()
    (paths.skills / f"{BUILTIN_NAME}.md").write_text(
        f"---\nname: {BUILTIN_NAME}\nversion: 9\ndescription: shadow\nstate: active\n---\nshadow body\n",
        encoding="utf-8",
    )
    svc = _service(paths)
    matches = [s for s in svc.list_skills() if s.id == BUILTIN_NAME]
    assert len(matches) == 1
    assert matches[0].source == BUILTIN_SOURCE  # 빌트인이 이긴다


def test_prefetch_injects_active_builtin_and_skips_disabled():
    paths = _fresh_paths()
    svc = _service(paths)
    injected = [s.name for s in svc.prefetch("sess", "make a pitch deck").skills]
    assert BUILTIN_NAME in injected

    svc.set_state(BUILTIN_NAME, "archived")  # 비활성
    injected = [s.name for s in svc.prefetch("sess", "make a pitch deck").skills]
    assert BUILTIN_NAME not in injected


# ── 3. 보호 규칙 ─────────────────────────────────────────────────────────────

def test_builtin_write_paths_are_all_blocked():
    svc = _service(_fresh_paths())
    with pytest.raises(ValidationError):
        svc.update_skill(BUILTIN_NAME, body="hack")
    with pytest.raises(ValidationError):
        svc.delete_skill(BUILTIN_NAME)
    with pytest.raises(ValidationError):
        svc.rollback(BUILTIN_NAME, 1)
    with pytest.raises(ValidationError):
        svc.create_skill(name=BUILTIN_NAME, body="shadow")
    with pytest.raises(ValidationError):
        svc.set_state(BUILTIN_NAME, "stale")
    with pytest.raises(ValidationError):
        svc.apply_patch(BUILTIN_NAME, approved=True, proposed_body="hack")

    # 미드턴 발의는 예외 대신 드롭 메시지 (모델 피드백 경로)
    result = svc.stage_proposal("sess", name=BUILTIN_NAME, body="x")
    assert result["status"] == "dropped"

    # 히스토리는 없음(에러가 아니라 빈 목록), 아카이브 제안은 no-op
    assert svc.history(BUILTIN_NAME) == []
    assert svc.decide_archive(BUILTIN_NAME, approved=True) is False


def test_builtin_enable_disable_is_the_only_transition():
    svc = _service(_fresh_paths())
    svc.set_state(BUILTIN_NAME, "archived")
    assert svc.get_skill(BUILTIN_NAME).state == "archived"
    svc.set_state(BUILTIN_NAME, "active")
    assert svc.get_skill(BUILTIN_NAME).state == "active"


# ── 4. 라우트 (FastAPI 계층에서 400으로 표면화) ───────────────────────────────

def test_routes_reject_builtin_mutations():
    from fastapi.testclient import TestClient
    from rice_runtime.core.config import RuntimeArgs
    from rice_runtime.server.app import create_app
    from rice_runtime.server.composition_root import build_container

    container = build_container(RuntimeArgs(token=""), paths=_fresh_paths())
    client = TestClient(create_app(container))
    host = {"host": "localhost"}  # 미들웨어의 localhost 화이트리스트 통과용
    try:
        # 조회/상태 전환은 허용
        listed = client.get("/skills", headers=host).json()["data"]
        assert any(s["id"] == BUILTIN_NAME for s in listed)
        ok = client.patch(f"/skills/{BUILTIN_NAME}/state", json={"state": "archived"}, headers=host)
        assert ok.status_code == 200

        # 편집/삭제/stale은 ValidationError → 422 (core/errors.py 매핑)
        assert client.put(f"/skills/{BUILTIN_NAME}", json={"body": "hack"}, headers=host).status_code == 422
        assert client.delete(f"/skills/{BUILTIN_NAME}", headers=host).status_code == 422
        assert client.patch(f"/skills/{BUILTIN_NAME}/state", json={"state": "stale"}, headers=host).status_code == 422
    finally:
        container.shutdown()
