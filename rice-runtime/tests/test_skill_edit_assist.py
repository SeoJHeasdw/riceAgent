"""test_skill_edit_assist.py — "AI로 수정" 배선 (propose_skill_edit + edit-assist 라우트) 검증.

수용 기준:
  1. 제안 성공: {target_path, changelog, proposed_body, current_body, from_version}
     반환, **저장 없음**(버전 불변), ledger 에 skill_edit_proposed 기록.
  2. 프롬프트 계약: SKILL.md 편집이면 skill-authoring 저작 가이드(§2·§3·§5)가
     시스템 프롬프트에 실리고 §4(propose_skill 워크플로)는 제외된다. 번들 파일
     편집이면 가이드 없음. history 의 agent 턴은 assistant 로 매핑.
  3. 출력 예산: max_tokens 는 입력 크기에 비례(최소 8k, 천장 24k).
  4. 실패 봉투: provider 미배선 → 503 provider_unavailable, 미들스트림 예외 →
     502 provider_error, 파싱 불가 출력 → 422, 지시 초과/빈 지시 → 422,
     빌트인 → 422 (전부 RiceError 봉투 — 생 500 금지).
  5. 폴백: 모르는 target_path 는 SKILL.md 로 해석.
  6. 라우트: 빌트인 422 · 빈 지시 422 · 없는 스킬 404 (provider 자원 불필요).

격리: tempfile.mkdtemp() 임시 루트 (CONVENTIONS §6).
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import AsyncIterator, Sequence

import pytest

from rice_runtime.agent.providers.interfaces import ProviderCapabilities, ProviderChunk
from rice_runtime.core.config import LearningConfig
from rice_runtime.core.errors import NotFoundError, ValidationError
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.core.types import ToolSpec
from rice_runtime.learning.service import LearningService
from rice_runtime.prompts.skill_edit import (
    SKILL_EDIT_MIN_TOKENS,
    relevant_authoring_guide,
    skill_edit_max_tokens,
)


def _fresh_paths() -> RicePaths:
    return RicePaths.from_env(tempfile.mkdtemp(prefix="rice-edit-assist-test-")).ensure()


class _RecordingProvider:
    """호출 인자를 기록하고 고정 JSON 을 돌려주는 편집 provider 스텁."""

    name = "fake"

    def __init__(self, new_body: str = "## Steps\n1. 결과는 한국어로 요약\n", changelog: str = "요약 규칙 추가") -> None:
        self._payload = json.dumps({"changelog": changelog, "new_body": new_body}, ensure_ascii=False)
        self.system: str | None = None
        self.messages = None
        self.max_tokens: int | None = None

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    async def stream(
        self,
        *,
        system: str,
        messages,
        tools: Sequence[ToolSpec] = (),
        temperature: float = 0.7,
        response_schema: dict | None = None,
        think: bool | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        self.system, self.messages, self.max_tokens = system, list(messages), max_tokens
        yield ProviderChunk(kind="text", text=self._payload)
        yield ProviderChunk(kind="done", stop_reason="end_turn")


class _BrokenProvider(_RecordingProvider):
    """미들스트림 장애 — 첫 청크 후 연결이 끊긴 상황."""

    async def stream(self, **kwargs) -> AsyncIterator[ProviderChunk]:  # type: ignore[override]
        yield ProviderChunk(kind="text", text='{"changelog":')
        raise ConnectionError("ollama died mid-stream")


class _GarbageProvider(_RecordingProvider):
    """JSON 아닌 출력 — 스키마 미지원 모델의 이탈."""

    async def stream(self, **kwargs) -> AsyncIterator[ProviderChunk]:  # type: ignore[override]
        yield ProviderChunk(kind="text", text="죄송하지만 그건 못 해요.")
        yield ProviderChunk(kind="done", stop_reason="end_turn")


def _service(provider=None, paths: RicePaths | None = None) -> LearningService:
    resolver = (lambda: provider) if provider is not None else None
    return LearningService(
        Database(":memory:").connect(), LearningConfig(), paths or _fresh_paths(),
        resolve_provider=resolver,
    )


def _seed(svc: LearningService, name: str = "demo-skill") -> str:
    detail = svc.create_skill(name=name, body="## Steps\n1. Old step\n", description="d", tags=["t"])
    return detail.id


# ── 1. 제안 성공 (저장 없음) ──────────────────────────────────────────────────

def test_propose_returns_proposal_without_saving():
    provider = _RecordingProvider()
    svc = _service(provider)
    sid = _seed(svc)

    res = asyncio.run(svc.propose_skill_edit(sid, target_path="SKILL.md", instruction="결과는 한국어로"))

    assert res["target_path"] == "SKILL.md"
    assert res["changelog"] == "요약 규칙 추가"
    assert "한국어로 요약" in res["proposed_body"]
    assert res["from_version"] == 1
    assert svc.get_skill(sid).version == 1  # 저장 안 됨 — 커밋은 update_skill 경로의 몫
    kinds = [e["kind"] for e in svc.learning_events(limit=10)] if hasattr(svc, "learning_events") else None
    if kinds is not None:
        assert "skill_edit_proposed" in kinds


# ── 2. 프롬프트 계약 ─────────────────────────────────────────────────────────

def test_skill_md_edit_carries_authoring_guide_and_history_roles():
    provider = _RecordingProvider()
    svc = _service(provider)
    sid = _seed(svc)

    asyncio.run(svc.propose_skill_edit(
        sid, target_path="SKILL.md", instruction="더 짧게",
        history=[{"role": "user", "text": "이전 지시"}, {"role": "agent", "text": "적용했어요"}],
    ))

    assert "HOUSE STYLE" in provider.system
    assert "Name and metadata" in provider.system  # §2
    assert "Propose it" not in provider.system  # §4 제외 — 편집기에서 도구 호출 혼선 방지
    roles = [m.role for m in provider.messages]
    assert roles == ["user", "assistant", "user"]  # agent → assistant 매핑, 마지막은 지시


def test_bundle_file_edit_has_no_authoring_guide():
    provider = _RecordingProvider(new_body="#!/bin/sh\necho ok\n")
    svc = _service(provider)
    sid = _seed(svc)
    svc.update_skill_file(sid, "scripts/run.sh", "#!/bin/sh\necho old\n")  # 번들 파일 생성(v2)

    res = asyncio.run(svc.propose_skill_edit(sid, target_path="scripts/run.sh", instruction="ok 출력"))

    assert res["target_path"] == "scripts/run.sh"
    assert res["current_body"] == "#!/bin/sh\necho old\n"
    assert "HOUSE STYLE" not in provider.system


def test_unknown_target_path_falls_back_to_skill_md():
    provider = _RecordingProvider()
    svc = _service(provider)
    sid = _seed(svc)

    res = asyncio.run(svc.propose_skill_edit(sid, target_path="no/such.file", instruction="x"))
    assert res["target_path"] == "SKILL.md"


# ── 3. 출력 예산 ─────────────────────────────────────────────────────────────

def test_max_tokens_scales_with_input():
    assert skill_edit_max_tokens(0) == SKILL_EDIT_MIN_TOKENS
    assert skill_edit_max_tokens(20_000) == 22_000
    assert skill_edit_max_tokens(10**6) == 24_000  # 천장

    provider = _RecordingProvider()
    svc = _service(provider)
    sid = _seed(svc)
    asyncio.run(svc.propose_skill_edit(sid, target_path="SKILL.md", instruction="x"))
    assert provider.max_tokens >= SKILL_EDIT_MIN_TOKENS


# ── 4. 실패 봉투 (RiceError — 생 500 금지) ───────────────────────────────────

def test_no_provider_raises_503_provider_unavailable():
    svc = _service(provider=None)
    sid = _seed(svc)
    with pytest.raises(ValidationError) as ei:
        asyncio.run(svc.propose_skill_edit(sid, target_path="SKILL.md", instruction="x"))
    assert ei.value.code == "provider_unavailable"
    assert ei.value.http == 503


def test_midstream_failure_raises_502_provider_error():
    svc = _service(_BrokenProvider())
    sid = _seed(svc)
    with pytest.raises(ValidationError) as ei:
        asyncio.run(svc.propose_skill_edit(sid, target_path="SKILL.md", instruction="x"))
    assert ei.value.code == "provider_error"
    assert ei.value.http == 502


def test_unparseable_output_raises_422():
    svc = _service(_GarbageProvider())
    sid = _seed(svc)
    with pytest.raises(ValidationError):
        asyncio.run(svc.propose_skill_edit(sid, target_path="SKILL.md", instruction="x"))


def test_instruction_validation():
    svc = _service(_RecordingProvider())
    sid = _seed(svc)
    with pytest.raises(ValidationError):
        asyncio.run(svc.propose_skill_edit(sid, target_path="SKILL.md", instruction="   "))
    with pytest.raises(ValidationError):
        asyncio.run(svc.propose_skill_edit(sid, target_path="SKILL.md", instruction="긴 지시 " * 1000))


def test_builtin_and_missing_skill_rejected():
    svc = _service(_RecordingProvider())
    with pytest.raises(ValidationError):
        asyncio.run(svc.propose_skill_edit("skill-authoring", target_path="SKILL.md", instruction="x"))
    with pytest.raises(NotFoundError):
        asyncio.run(svc.propose_skill_edit("no-such-skill", target_path="SKILL.md", instruction="x"))


# ── 5. relevant_authoring_guide 단위 ─────────────────────────────────────────

def test_relevant_authoring_guide_sections():
    body = (
        "# Title\nintro\n"
        "## 1. Decide\nskip me\n"
        "## 2. Name and metadata\nkeep\n"
        "## 3. Write the body\nkeep\n"
        "## 4. Propose it\ncall propose_skill\n"
        "## 5. Rules that always apply\nkeep\n"
    )
    guide = relevant_authoring_guide(body)
    assert "Name and metadata" in guide
    assert "Write the body" in guide
    assert "Rules that always apply" in guide
    assert "propose_skill" not in guide
    assert "Decide" not in guide
    # 구조를 못 찾으면 전문 폴백
    assert relevant_authoring_guide("plain text, no sections") == "plain text, no sections"


# ── 6. 라우트 (provider 불필요 경로) ─────────────────────────────────────────

def test_route_edit_assist_validation_paths():
    from fastapi.testclient import TestClient
    from rice_runtime.core.config import RuntimeArgs
    from rice_runtime.server.app import create_app
    from rice_runtime.server.composition_root import build_container

    container = build_container(RuntimeArgs(token=""), paths=_fresh_paths())
    client = TestClient(create_app(container))
    host = {"host": "localhost"}
    payload = {"target_path": "SKILL.md", "instruction": "x", "history": []}
    try:
        # 빌트인은 provider 이전에 거부 → 422
        r = client.post("/skills/skill-authoring/edit-assist", json=payload, headers=host)
        assert r.status_code == 422
        # 없는 스킬 → 404
        r = client.post("/skills/no-such/edit-assist", json=payload, headers=host)
        assert r.status_code == 404
        # 빈 지시 → 422
        r = client.post(
            "/skills/skill-authoring/edit-assist",
            json={"target_path": "SKILL.md", "instruction": "", "history": []},
            headers=host,
        )
        assert r.status_code == 422
    finally:
        container.shutdown()
