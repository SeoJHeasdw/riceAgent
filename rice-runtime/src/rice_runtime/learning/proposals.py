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

import hashlib
import logging

from .episode import sanitize_tool_transcript
from .interfaces import PatchProposal, PatchTrigger
from .skill import guard as skill_guard
from .skill.patch import build_diff
from .skill.store import SkillData

log = logging.getLogger("rice.learning.proposals")

MAX_CHANGELOG_CHARS = 200
MAX_DESCRIPTION_CHARS = 1_000
MAX_TAGS = 5
MAX_TAG_CHARS = 80
# candidate 평가(§10.6)의 held-out 과제 원천 상한 — 사용자 발화라 시크릿 scrub 을
# 반드시 거치고, 평가 프롬프트 예산을 위해 개수/길이를 여기서 한 번에 묶는다.
MAX_SEED_PROMPTS = 3
MAX_SEED_PROMPT_CHARS = 600
EXTERNAL_CONTENT_REVIEW_WARNING = "external_content_review"
# 매개변수가 있는 warning은 "코드:값" 형태로 싣는다 — 로케일 문장으로 푸는 것은
# 프론트(learningWarnings.ts)의 몫. 런타임이 자연어를 합성하면 UI 언어와 어긋난다.
MASKED_WARNING_PREFIX = "masked:"
RISK_FLAG_WARNING_PREFIX = "risk_flag:"
DESCRIPTION_CHANGE_WARNING_PREFIX = "description_change:"
TAGS_CHANGE_WARNING_PREFIX = "tags_change:"


def body_fingerprint(body: str) -> str:
    """Stable SkillStore-roundtrip fingerprint for multi-card consolidation.

    ``parse_skill_md`` removes separator-leading blank lines and text reads
    normalize platform newlines.  Hash the same canonical body so a correctly
    applied umbrella proposal does not reject its sibling archive merely because
    serialization normalized those bytes.
    """
    canonical = body.replace("\r\n", "\n").replace("\r", "\n").lstrip("\n")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _clean_changelog(text: str) -> str:
    """LLM이 준 changelog 정리 — 폴백 문장을 합성하지 않는다(사용자용 텍스트
    하드코딩 금지). 비면 빈 문자열 그대로 두고, 표시 폴백은 프론트
    (learningData.ts renderChangelog)가 로케일로 결정한다. 시크릿 scrub은
    `sanitize_metadata`가 이미 적용한 상태로 들어온다."""
    return text.strip()[:MAX_CHANGELOG_CHARS]


def sanitize_metadata(
    description: str | None, tags: tuple[str, ...] | None, changelog: str | None
) -> tuple[str | None, tuple[str, ...] | None, str | None, list[str]]:
    """Bound, normalize, and scrub every persisted/displayed metadata field.

    The builder is not the only caller: final write sinks call it again so a
    legacy suggestion payload or a direct REST request cannot bypass the same
    boundary.  Bounding is part of the safety contract too — descriptions and
    tags are rendered into the always-on skill index and must not become an
    unmetered prompt side channel.
    """
    labels: list[str] = []
    if description is not None:
        description = " ".join(str(description).split())[:MAX_DESCRIPTION_CHARS]
        description, found = skill_guard.scrub_secrets(description)
        # Redaction labels can be longer than the secret they replace.  Reapply
        # the persisted-field bound after scrubbing so the safety transform
        # itself cannot push metadata over its prompt budget.
        description = description[:MAX_DESCRIPTION_CHARS]
        labels.extend(found)
    if tags is not None:
        cleaned: list[str] = []
        for raw_tag in tags:
            tag = " ".join(str(raw_tag).split())[:MAX_TAG_CHARS]
            if not tag:
                continue
            clean_tag, found = skill_guard.scrub_secrets(tag)
            clean_tag = clean_tag[:MAX_TAG_CHARS]
            labels.extend(found)
            if not clean_tag or clean_tag in cleaned:
                continue
            cleaned.append(clean_tag)
            if len(cleaned) >= MAX_TAGS:
                break
        tags = tuple(cleaned)
    if changelog is not None:
        changelog = " ".join(str(changelog).split())[:MAX_CHANGELOG_CHARS]
        changelog, found = skill_guard.scrub_secrets(changelog)
        changelog = changelog[:MAX_CHANGELOG_CHARS]
        labels.extend(found)
    return description, tags, changelog, labels


def sanitize_seed_prompts(prompts) -> tuple[str, ...]:
    """held-out 평가 seed 정규화 — scrub·길이·개수 상한. 어떤 경로(리뷰 엔진,
    미드턴 스테이징, 구형 payload 복원)로 오든 영속 전에 여기를 거친다."""
    cleaned: list[str] = []
    for raw in prompts or ():
        text = " ".join(str(raw).split())[:MAX_SEED_PROMPT_CHARS]
        if not text:
            continue
        text, _ = skill_guard.scrub_secrets(text)
        text = text[:MAX_SEED_PROMPT_CHARS]
        if text and text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= MAX_SEED_PROMPTS:
            break
    return tuple(cleaned)


def _build_warnings(
    body: str,
    scrubbed_labels: list[str],
    derived_from_external: bool = False,
    metadata_text: str = "",
) -> tuple[str, ...]:
    """승인 카드에 실을 warnings: 시크릿 마스킹 알림 + 위험 플래그(learning safety
    scanner) + 외부 콘텐츠 유래 경고. 전부 차단이 아니라 표시다 —
    스킬은 HITL이 있으므로 카드에 보여주고 사용자가 판단한다.
    `metadata_text`: description/tags/changelog 결합 — 위험 플래그 스캔을 본문
    밖 필드까지 확장한다(주입 지시가 description으로 우회하는 경로 차단)."""
    warnings: list[str] = []
    if derived_from_external:
        warnings.append(EXTERNAL_CONTENT_REVIEW_WARNING)
    warnings.extend(f"{MASKED_WARNING_PREFIX}{label}" for label in scrubbed_labels)
    scan_target = f"{body}\n{metadata_text}" if metadata_text else body
    warnings.extend(f"{RISK_FLAG_WARNING_PREFIX}{label}" for label in skill_guard.scan_risks(scan_target))
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
    seed_prompts: tuple[str, ...] = (),
    tool_transcript: tuple[dict, ...] = (),
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
    description, tags, changelog, meta_labels = sanitize_metadata(description, tags, changelog)
    description = description or ""
    tags = tags or ()
    return PatchProposal(
        skill_id=name,
        changelog=_clean_changelog(changelog or ""),
        diff=build_diff("", body),
        trigger=trigger,
        proposed_body=body,
        from_version=0,
        to_version=1,
        description=description,
        tags=tags,
        warnings=_build_warnings(
            body,
            scrubbed + meta_labels,
            derived_from_external,
            metadata_text="\n".join((description, *tags, changelog or "")),
        ),
        derived_from_external=derived_from_external,
        evidence_message_ids=evidence_message_ids,
        seed_prompts=sanitize_seed_prompts(seed_prompts),
        tool_transcript=sanitize_tool_transcript(tool_transcript),
    )


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
        cleaned_tags = tuple(dict.fromkeys(t.strip() for t in new_tags if t.strip()))[:MAX_TAGS]
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
    seed_prompts: tuple[str, ...] = (),
    tool_transcript: tuple[dict, ...] = (),
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
    new_description, new_tags, scrubbed_changelog, meta_labels = sanitize_metadata(
        new_description, new_tags, changelog
    )
    changelog = scrubbed_changelog or ""
    diff = build_diff(current.body, new_body)
    desc_change, tags_change = _clean_metadata_change(current, new_description, new_tags)
    if not diff["lines"] and desc_change is None and tags_change is None:
        return None
    warnings = list(
        _build_warnings(
            new_body,
            scrubbed + meta_labels,
            derived_from_external,
            metadata_text="\n".join((desc_change or "", *(tags_change or ()), changelog or "")),
        )
    )
    if desc_change is not None:
        warnings.append(f"{DESCRIPTION_CHANGE_WARNING_PREFIX}{desc_change}")
    if tags_change is not None:
        warnings.append(f"{TAGS_CHANGE_WARNING_PREFIX}{', '.join(tags_change)}")
    return PatchProposal(
        skill_id=current.name,
        changelog=_clean_changelog(changelog),
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
        seed_prompts=sanitize_seed_prompts(seed_prompts),
        tool_transcript=sanitize_tool_transcript(tool_transcript),
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
        "seed_prompts": list(p.seed_prompts),
        "tool_transcript": [dict(r) for r in p.tool_transcript],
    }
