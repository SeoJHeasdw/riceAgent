"""learning/proposals.py — PatchProposal 공용 빌더 (Self-Improvement v3).

같은 "본문 → 검증(가드) → 스크러빙 → diff → PatchProposal" 조립이 세 곳에서
필요해졌다: ReviewEngine(사후 리뷰 LLM), 미드턴 propose_skill 빌트인 도구
(에이전트 발의), DiscoveryEngine(크로스세션 발굴). 이 모듈이 그 조립을 한 곳에
모은다 — 셋이 각자 가드 호출을 복붙하면 하나가 스크러빙을 잊는 순간 §8의 위협
모델(시크릿이 매 턴 시스템 프롬프트로 재주입)이 다시 열린다.

반환은 `PatchProposal | None` — None은 "제안으로 성립하지 않음"(빈 본문, 크기
초과, 실질 변경 없음)이며 호출자는 조용히 버린다(fail-closed). 예외를 던지지
않는 이유: 세 호출처 모두 LLM/에이전트가 만든 인자를 다루므로 잘못된 입력이
일상이고, 그것이 상위 흐름(턴/리뷰)을 깨서는 안 된다.
"""

from __future__ import annotations

import logging

from .interfaces import PatchProposal, PatchTrigger
from .skill import guard as skill_guard
from .skill.patch import build_diff
from .skill.store import SkillData

log = logging.getLogger("rice.learning.proposals")

_MAX_CHANGELOG_CHARS = 200


def _clean_changelog(text: str, fallback: str) -> str:
    return (text.strip() or fallback)[:_MAX_CHANGELOG_CHARS]


def _build_warnings(
    body: str, scrubbed_labels: list[str], derived_from_external: bool = False
) -> tuple[str, ...]:
    """승인 카드에 실을 warnings: 시크릿 마스킹 알림 + 위험 플래그(learning safety
    scanner) + 외부 콘텐츠 유래 경고. 전부 차단이 아니라 표시다 —
    스킬은 HITL이 있으므로 카드에 보여주고 사용자가 판단한다."""
    warnings: list[str] = []
    if derived_from_external:
        warnings.append("외부 콘텐츠(도구 결과)가 포함된 대화에서 생성됨 — 본문을 특히 주의 깊게 확인하세요")
    warnings.extend(f"{label} 자동 마스킹됨" for label in scrubbed_labels)
    warnings.extend(f"위험 플래그: {label}" for label in skill_guard.scan_risks(body))
    return tuple(warnings)


def make_create_proposal(
    *,
    name: str,
    body: str,
    description: str = "",
    tags: tuple[str, ...] = (),
    changelog: str | None = None,
    trigger: PatchTrigger = "T4",
    max_body_chars: int = skill_guard.DEFAULT_MAX_BODY_CHARS,
    derived_from_external: bool = False,
    evidence_message_ids: tuple[str, ...] = (),
) -> PatchProposal | None:
    """신규 스킬 제안(from_version=0). 이름 규약(kebab)·크기·시크릿 가드를 여기서
    일괄 적용한다. 이름이 규약 위반이면 None — LLM/에이전트가 이상한 이름을 주는
    경우를 파일시스템에 닿기 전에 거른다."""
    name = name.strip()
    if not name or not skill_guard.VALID_SKILL_NAME.match(name):
        log.warning("create proposal dropped: invalid skill name %r", name)
        return None
    if not body.strip():
        return None
    try:
        skill_guard.validate_content_size(body, max_body_chars)
    except Exception:
        log.warning("create proposal dropped: body too large for %r", name)
        return None
    body, scrubbed = skill_guard.scrub_secrets(body)
    return PatchProposal(
        skill_id=name,
        changelog=_clean_changelog(
            changelog or "", f"신규 스킬 제안 · {description}" if description else "신규 스킬 제안"
        ),
        diff=build_diff("", body),
        trigger=trigger,
        proposed_body=body,
        from_version=0,
        to_version=1,
        description=description,
        tags=tags,
        warnings=_build_warnings(body, scrubbed, derived_from_external),
        derived_from_external=derived_from_external,
        evidence_message_ids=evidence_message_ids,
    )


_MAX_TAGS = 5


def _clean_metadata_change(
    current: SkillData, new_description: str | None, new_tags: tuple[str, ...] | None
) -> tuple[str | None, tuple[str, ...] | None]:
    """메타데이터 변경 제안 정규화 — 현재 값과 같으면 None(변경 없음)으로 접는다.
    태그는 개수 상한과 공백 제거만 본다(내용 판단은 HITL 카드의 몫)."""
    desc = None
    if new_description is not None:
        cleaned = " ".join(new_description.split())
        if cleaned and cleaned != current.description:
            desc = cleaned
    tags = None
    if new_tags is not None:
        cleaned_tags = tuple(dict.fromkeys(t.strip() for t in new_tags if t.strip()))[:_MAX_TAGS]
        if cleaned_tags and list(cleaned_tags) != list(current.tags):
            tags = cleaned_tags
    return desc, tags


def make_patch_proposal(
    *,
    current: SkillData,
    new_body: str,
    changelog: str,
    trigger: PatchTrigger,
    max_body_chars: int = skill_guard.DEFAULT_MAX_BODY_CHARS,
    derived_from_external: bool = False,
    evidence_message_ids: tuple[str, ...] = (),
    new_description: str | None = None,
    new_tags: tuple[str, ...] | None = None,
) -> PatchProposal | None:
    """기존 스킬 개선 제안. 실질 변경이 없으면(diff 빈 줄 + 메타데이터 변경 없음)
    None — 빈 제안 카드로 사용자를 방해하지 않는다.

    `new_description`/`new_tags`(2026-07-11): 발견성 자기개선 — routing_miss 가
    쌓인 스킬은 본문이 아니라 색인 신호(설명/태그)가 문제라서, 리뷰 LLM 이 그걸
    고칠 채널이 필요했다. 변경이 있으면 승인 카드 warnings 로 명시된다(diff 는
    본문 전용 렌더러라서)."""
    if not new_body.strip():
        return None
    try:
        skill_guard.validate_content_size(new_body, max_body_chars)
    except Exception:
        log.warning("patch proposal dropped: body too large for %r", current.name)
        return None
    new_body, scrubbed = skill_guard.scrub_secrets(new_body)
    diff = build_diff(current.body, new_body)
    desc_change, tags_change = _clean_metadata_change(current, new_description, new_tags)
    if not diff["lines"] and desc_change is None and tags_change is None:
        return None
    warnings = list(_build_warnings(new_body, scrubbed, derived_from_external))
    if desc_change is not None:
        warnings.append(f"설명 변경 포함: “{desc_change}”")
    if tags_change is not None:
        warnings.append(f"태그 변경 포함: {', '.join(tags_change)}")
    return PatchProposal(
        skill_id=current.name,
        changelog=_clean_changelog(changelog, "개선 제안"),
        diff=diff,
        trigger=trigger,
        proposed_body=new_body,
        from_version=current.version,
        to_version=current.version + 1,
        warnings=tuple(warnings),
        derived_from_external=derived_from_external,
        evidence_message_ids=evidence_message_ids,
        new_description=desc_change,
        new_tags=tags_change,
    )


def proposal_to_payload(p: PatchProposal) -> dict:
    """PatchProposal → 제안함(skill_suggestions.payload) 직렬화. SSE 이벤트
    (`ev_skill_patch_proposed`)와 같은 어휘를 쓴다 — 프론트가 파킹 카드와 제안함
    카드를 동일 렌더러로 그릴 수 있게."""
    return {
        "skill_id": p.skill_id,
        "changelog": p.changelog,
        "diff": p.diff,
        "trigger": p.trigger,
        "from_version": p.from_version,
        "to_version": p.to_version,
        "proposed_body": p.proposed_body,
        "description": p.description,
        "tags": list(p.tags),
        "warnings": list(p.warnings),
        "derived_from_external": p.derived_from_external,
        "evidence_message_ids": list(p.evidence_message_ids),
        "new_description": p.new_description,
        "new_tags": list(p.new_tags) if p.new_tags is not None else None,
    }
