"""learning/curator/consolidate.py — LLM 기반 스킬 통합 분석 (Phase B).

`Curator`(curator.py)의 규칙 기반 active→stale→archived 전이와는 별개 관심사다:
이 엔진은 **내용을 읽고 판단**해야 하는 일(유사/중복 스킬을 하나의 class-level
umbrella로 합칠지)이라 LLM 1회 호출을 쓴다. RICE_LEARNING_LOOP_DESIGN.md §5.1이
"향후 고도화로 연기"라고 적어뒀던 바로 그 부분.

`review/engine.py::ReviewEngine`과 같은 원칙: **쓰기 권한 없음.** 모든 산출물은
`PatchProposal`(병합 대상 umbrella) 또는 `ArchiveProposal`(흡수되는 형제 스킬)
제안이고, 실제 적용은 호출자가 HITL 승인 후 `apply_patch`/`decide_archive`로 한다.

수동 트리거(POST /skills/curate?mode=consolidate)라 review()와 달리 게이트가
없다 — 호출된 이상 항상 분석을 시도한다(비용/지연은 사용자가 명시적으로 감수).
"""

from __future__ import annotations

import logging
from typing import Callable, Sequence

from ...agent.providers.interfaces import ILLMProvider
from ...core.errors import NotFoundError
from ...core.types import ToolCall
from ...prompts.curator import build_curator_messages, build_curator_tool_specs
from ..interfaces import ArchiveProposal, PatchProposal
from ..proposals import (
    body_fingerprint,
    make_create_proposal,
    make_patch_proposal,
    sanitize_metadata,
)
from ..skill import guard as skill_guard
from ..skill.store import SkillStore
from ..skill.usage import UsageStore

log = logging.getLogger("rice.learning.curator.consolidate")

_CONSOLIDATE_TEMPERATURE = 0.3
_PLUGIN_SOURCE_PREFIX = "plugin:"


class ConsolidationEngine:
    """analyze() — 1회성 LLM 호출로 통합/정리 제안을 산출한다."""

    def __init__(
        self,
        *,
        resolve_provider: Callable[[], ILLMProvider],
        skill_store: SkillStore,
        usage_store: UsageStore,
        candidate_budget_chars: int,
        max_body_chars: int,
    ) -> None:
        self._resolve_provider = resolve_provider
        self._skill_store = skill_store
        self._usage_store = usage_store
        self._candidate_budget_chars = candidate_budget_chars
        self._max_body_chars = max_body_chars

    async def analyze(self) -> list[PatchProposal | ArchiveProposal]:
        provider = self._resolve_provider()
        if getattr(provider, "name", "") == "echo":
            return []  # titler.py/review 엔진과 동일 가드

        candidates = self._build_candidates()
        if len(candidates) < 2:
            return []  # 통합 대상이 되려면 최소 2개는 있어야 한다

        candidate_ids = tuple(c[0] for c in candidates)
        system, messages = build_curator_messages(candidates)
        tools = build_curator_tool_specs(candidate_ids)

        calls: list[ToolCall] = []
        try:
            async for chunk in provider.stream(
                system=system, messages=messages, tools=tools, temperature=_CONSOLIDATE_TEMPERATURE
            ):
                if chunk.kind == "tool_call" and chunk.tool_call is not None:
                    calls.append(chunk.tool_call)
        except Exception:
            log.exception("consolidation LLM call failed")
            return []  # best-effort — 실패해도 규칙 기반 curate()는 이미 끝난 뒤다

        proposals: list[PatchProposal | ArchiveProposal] = []
        for call in calls:
            proposals.extend(self._to_proposals(call, candidate_ids))
        return proposals

    def _build_candidates(self) -> list[tuple[str, str, str, int]]:
        """(skill_id, description, body, use_count) — active+stale이고 plugin
        제공이 아닌 것만, 예산 내에서 전체 본문(review()와 달리 이 패스는
        수동+저빈도라 지연에 관대함). 예산 초과 시 use 하위부터 후보 제외."""
        all_skills = [
            s
            for s in self._skill_store.list()
            if s.state in ("active", "stale") and not (s.source or "").startswith(_PLUGIN_SOURCE_PREFIX)
        ]

        def _use_count(name: str) -> int:
            return self._usage_store.read(name).use

        sorted_skills = sorted(all_skills, key=lambda s: _use_count(s.name), reverse=True)

        candidates: list[tuple[str, str, str, int]] = []
        remaining = self._candidate_budget_chars
        for skill in sorted_skills:
            body = skill.body or ""
            if len(body) > remaining:
                continue
            candidates.append((skill.name, skill.description, body, _use_count(skill.name)))
            remaining -= len(body)
        return candidates

    def _to_proposals(
        self, call: ToolCall, candidate_ids: Sequence[str]
    ) -> list[PatchProposal | ArchiveProposal]:
        args = call.arguments if isinstance(call.arguments, dict) else {}
        if call.tool_name == "propose_consolidate":
            return self._consolidate_proposals(args, candidate_ids)
        if call.tool_name == "propose_prune":
            skill_id = str(args.get("skill_id") or "").strip()
            reason = str(args.get("reason") or "curator 정리 제안")
            if skill_id not in candidate_ids:
                return []
            _, _, clean_reason, _ = sanitize_metadata(None, None, reason)
            return [
                ArchiveProposal(
                    skill_id=skill_id,
                    reason=clean_reason or "",
                    absorbed_into=None,
                )
            ]
        log.warning("consolidation engine: unknown tool call %r", call.tool_name)
        return []

    def _consolidate_proposals(
        self, args: dict, candidate_ids: Sequence[str]
    ) -> list[PatchProposal | ArchiveProposal]:
        """umbrella PatchProposal **하나** + 흡수되는 각 형제 스킬의 ArchiveProposal
        (absorbed_into=umbrella_id)을 함께 산출한다. 버그#B1 교정: 처음엔
        absorbed_skill_ids를 인자로만 받고 실제로 archive 제안을 만들지 않아서,
        "합치기"의 절반(흡수 쪽)이 통째로 누락돼 있었다."""
        umbrella_id = str(args.get("umbrella_id") or "").strip()
        is_new = bool(args.get("is_new"))
        body = str(args.get("umbrella_body") or "")
        # 폴백 문장을 합성하지 않는다 — 비면 빈 채로 두고 표시 폴백은 프론트 몫.
        raw_changelog = str(args.get("changelog") or "")
        umbrella_description = str(args.get("umbrella_description") or "")
        absorbed_ids = [
            str(sid).strip()
            for sid in (args.get("absorbed_skill_ids") or ())
            if str(sid).strip() and str(sid).strip() != umbrella_id and str(sid).strip() in candidate_ids
        ]
        if not umbrella_id or not body.strip():
            return []
        umbrella_proposal: PatchProposal | None = None
        target_version: int | None = None
        target_hash: str | None = None
        if is_new and umbrella_id not in candidate_ids:
            # 공용 builder를 반드시 통과한다: 이름/크기/secret뿐 아니라 body와
            # metadata의 risk scan까지 review/discovery와 같은 승인 경계로 만든다.
            umbrella_proposal = make_create_proposal(
                name=umbrella_id,
                body=body,
                description=umbrella_description,
                changelog=raw_changelog,
                trigger="T3",
                max_body_chars=self._max_body_chars,
            )
            if umbrella_proposal is None:
                return []
            target_version = umbrella_proposal.to_version
            target_hash = body_fingerprint(umbrella_proposal.proposed_body)
        else:
            # is_new=false, 또는 모델이 기존 스킬 이름을 "신규"라고 잘못 표시한 경우
            # (existing으로 정정) — 둘 다 patch 경로로 처리.
            try:
                current = self._skill_store.read(umbrella_id)
            except NotFoundError:
                return []  # 실존하지 않는데 is_new=false — 잘못된 제안, 버림
            # An absorbed skill must be replaced by something that is actually
            # injectable.  Patching a stale/inactive umbrella does not itself
            # change lifecycle state, so such a multi-card proposal could never
            # satisfy the archive endpoint's active-target precondition.
            if current.state != "active" or not current.enabled:
                return []
            umbrella_proposal = make_patch_proposal(
                current=current,
                new_body=body,
                changelog=raw_changelog,
                trigger="T3",
                max_body_chars=self._max_body_chars,
            )
            if umbrella_proposal is not None:
                target_version = umbrella_proposal.to_version
                target_hash = body_fingerprint(umbrella_proposal.proposed_body)
            else:
                # No-op body는 이미 적용된 umbrella로 흡수할 수 있다. Builder가
                # 다른 이유(크기/빈 본문)로 drop한 경우까지 archive를 허용하면
                # 대체 본문 없이 원본만 사라지므로, scrub 후 완전 동일할 때만 허용.
                sanitized_body, _ = skill_guard.scrub_secrets(body)
                if sanitized_body != current.body:
                    return []
                target_version = current.version
                target_hash = body_fingerprint(current.body)

        if umbrella_proposal is None and not absorbed_ids:
            return []  # umbrella도 실질 변경 없고 흡수 대상도 없음 — 제안할 게 없다

        proposals: list[PatchProposal | ArchiveProposal] = []
        if umbrella_proposal is not None:
            proposals.append(umbrella_proposal)
        _, _, clean_changelog, _ = sanitize_metadata(None, None, raw_changelog)
        proposals.extend(
            ArchiveProposal(
                skill_id=sid,
                reason=f"{umbrella_id}로 통합됨 · {clean_changelog or ''}",
                absorbed_into=umbrella_id,
                absorbed_into_version=target_version,
                absorbed_into_body_hash=target_hash,
            )
            for sid in absorbed_ids
        )
        return proposals
