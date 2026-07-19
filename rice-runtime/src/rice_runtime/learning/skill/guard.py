"""learning/skill/guard.py — core/guard.py 재수출 심 (학습 경로의 적용 지점 문서).

구현은 core/guard.py 로 승격됐다(2026-07-19) — 플러그인 설치 경로(execution)도
같은 스캐너를 쓰게 되면서, 모듈 간 직접 import 금지선을 지키려면 core 가 유일한
자리다. 이 파일은 학습 모듈 내부 소비자(service/proposals/episode/memory·review)
의 import 경로를 보존하는 심으로 남는다. **패턴 추가/수정은 core/guard.py 에서.**

학습 경로의 두 층위 방어(정책은 여기 소비자들이 결정):
  1. `scrub_secrets` — "대화에서 본 시크릿을 LLM이 무심코 스킬/메모리 본문에
     옮겨적어, 이후 매 턴 시스템 프롬프트에 다시 주입되는 사고"를 막는다(치환).
  2. `scan_risks` — Gmail/웹/도구 결과 같은 **외부 콘텐츠가 스킬로 승격되면
     prompt injection이 영구 시스템 프롬프트가 되는** 경로에 대한 탐지.
     차단이 아니라 **플래그**: 결과는 PatchProposal.warnings로 승인 카드에
     표시되고, 저장 여부는 사용자가 판단한다(스킬은 HITL이 있으므로).
     단 하나의 예외: memory는 HITL 없이 즉시 주입되므로 위험 플래그가 있으면
     저장 자체를 거부한다(fail-closed — review/engine.py::_memory_effect).

적용 지점:
  - 스킬: `LearningService.apply_patch`/`create_skill`/`update_skill` 세 곳
    (SkillStore.write() 직전) — SkillStore 자체는 순수 I/O 레이어로 남겨둔다.
  - 메모리: `MemoryStore.add()` 내부 — 메모리는 HITL 없이 즉시 저장·주입 후보가
    되므로 저장소 경계에 가드를 둔다(memory/store.py 참고, 기존 "content는 호출자
    책임" 원칙에 대한 좁고 명시적인 예외).
"""

from __future__ import annotations

from ...core.guard import (  # noqa: F401 — 재수출(기존 학습 소비자 경로 보존)
    DEFAULT_MAX_BODY_CHARS,
    VALID_SKILL_NAME,
    scan_risks,
    scrub_secrets,
    validate_content_size,
)

__all__ = [
    "DEFAULT_MAX_BODY_CHARS",
    "VALID_SKILL_NAME",
    "scan_risks",
    "scrub_secrets",
    "validate_content_size",
]
