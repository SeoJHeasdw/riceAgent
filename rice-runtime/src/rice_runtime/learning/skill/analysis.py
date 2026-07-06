"""skill/analysis.py — SKILL.md 설명용 분석 사이드카 (SkillAnalysis).

책임(single): 각 스킬의 "사람이 이해하는 번역본" 메타데이터를
`~/.rice/skills/<name>.analysis.json` 파일로 읽고 쓴다. usage 사이드카
(usage.py)와 동일한 원칙 — 스킬 본문과 별도 파일이라 본문을 절대 건드리지
않고, 재생성해도 본문/버전/히스토리에 아무 영향이 없다.

**설명 전용(explanatory-only) 계약**: 이 데이터는 MySkills 화면이 SKILL.md를
사람에게 풀어 보여주기 위한 것이다. 권한 판단·도구 실행·승인 정책의 입력으로
절대 사용해선 안 된다 — 그 판단은 런타임 도구 정책(connector/permission)과
HITL 승인 흐름이 소유한다. 여기서 아무리 틀려도 보이는 것만 틀린다.

Staleness는 저장이 아니라 **읽기 시점에 계산**한다: 사이드카에 분석 당시의
body_hash 를 기록해 두고, 현재 본문 해시와 다르면 stale 로 판정한다. 본문을
바꾸는 모든 경로(patch 승인, 직접 편집, rollback, 자기개선 승인 …)가 사이드카를
동기적으로 손댈 필요가 없어진다 — 어떤 경로로 바뀌었든 다음 조회가 stale 을
정확히 보고한다. analyzer_version 불일치도 stale 로 본다(프롬프트/휴리스틱이
바뀌면 재분석 대상).

포맷 (JSON):
  {
    "skill": "<name>",
    "body_hash": "<sha256[:16] of body>",
    "analyzer_version": "1",
    "status": "fresh" | "failed",         # 저장 시점 상태 (stale/missing은 계산값)
    "source": "llm" | "heuristic",        # 요약/단계의 산출 주체
    "summary": "...",
    "confidence": "high" | "medium" | "low",
    "procedural_steps": [{"title", "description", "confidence", "evidence"}, ...],
    "tool_mentions": [{"name", "kind": "mentioned", "confidence", "basis", "evidence"}, ...],
    "warnings": ["..."],
    "analyzed_at": "<ISO8601>"
  }

`tool_mentions` 에는 **mentioned(본문 감지 후보)만** 저장한다. observed(실행
로그)는 usage 사이드카가, declared(manifest/frontmatter 선언)는 SKILL.md
frontmatter 가 각자 소유한다 — API 응답 시점에 셋을 합쳐 내보낸다
(LearningService._analysis_payload). 출처가 다른 데이터를 한 파일에 섞어
저장하면 어느 쪽이 정본인지 금방 모호해진다.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from ...core.paths import RicePaths
from ...core.types import now_iso

# 프롬프트/휴리스틱/스키마가 바뀌면 올린다 → 기존 사이드카 전부 stale 판정.
ANALYZER_VERSION = "1"

AnalysisConfidence = Literal["high", "medium", "low"]


def body_hash(body: str) -> str:
    """SKILL.md 본문(frontmatter 제외)의 안정 해시 — staleness 판정의 근거."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


@dataclass
class SkillAnalysisData:
    """사이드카 1건. 저장 status 는 fresh|failed 뿐 — stale/missing 은 읽기 시점
    계산값이라 저장하지 않는다(저장하면 본문 변경 경로마다 갱신 의무가 생긴다)."""

    skill: str
    body_hash: str
    analyzer_version: str = ANALYZER_VERSION
    status: str = "fresh"  # fresh | failed
    source: str = "heuristic"  # llm | heuristic
    summary: str = ""
    confidence: AnalysisConfidence = "low"
    procedural_steps: list[dict] = field(default_factory=list)
    tool_mentions: list[dict] = field(default_factory=list)  # kind='mentioned' only
    warnings: list[str] = field(default_factory=list)
    analyzed_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SkillAnalysisData":
        return cls(
            skill=d.get("skill", ""),
            body_hash=d.get("body_hash", ""),
            analyzer_version=str(d.get("analyzer_version", "")),
            status=d.get("status", "fresh"),
            source=d.get("source", "heuristic"),
            summary=d.get("summary", ""),
            confidence=d.get("confidence", "low"),
            procedural_steps=list(d.get("procedural_steps") or []),
            tool_mentions=list(d.get("tool_mentions") or []),
            warnings=list(d.get("warnings") or []),
            analyzed_at=d.get("analyzed_at", ""),
        )


def effective_status(analysis: SkillAnalysisData | None, current_body: str) -> str:
    """읽기 시점 상태: missing | stale | failed | fresh.

    stale 판정이 사이드카 갱신보다 우선한다 — 본문이 바뀐 뒤라면 실패 기록조차
    낡은 정보다."""
    if analysis is None:
        return "missing"
    if analysis.body_hash != body_hash(current_body):
        return "stale"
    if analysis.analyzer_version != ANALYZER_VERSION:
        return "stale"
    if analysis.status == "failed":
        return "failed"
    return "fresh"


class AnalysisStore:
    """~/.rice/skills/<name>.analysis.json 파일 CRUD. usage.py 와 동일한
    원자 쓰기(temp → rename)·손상 시 기본값 복구(fail-safe) 원칙."""

    def __init__(self, paths: RicePaths) -> None:
        self._paths = paths

    def _path(self, name: str) -> Path:
        return self._paths.skills / f"{name}.analysis.json"

    def read(self, name: str) -> SkillAnalysisData | None:
        path = self._path(name)
        if not path.exists():
            return None
        try:
            return SkillAnalysisData.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            # 손상된 사이드카는 없는 것으로 취급 — 다음 분석이 덮어쓴다.
            return None

    def write(self, analysis: SkillAnalysisData) -> None:
        self._paths.skills.mkdir(parents=True, exist_ok=True)
        if not analysis.analyzed_at:
            analysis.analyzed_at = now_iso()
        path = self._path(analysis.skill)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(analysis.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)

    def delete(self, name: str) -> None:
        """스킬 삭제 시 함께 제거 (idempotent)."""
        self._path(name).unlink(missing_ok=True)
