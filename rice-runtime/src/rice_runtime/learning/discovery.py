"""learning/discovery.py — 크로스세션 패턴 발굴 엔진 (Self-Improvement v3).

ReviewEngine이 "방금 끝난 대화 하나"를 보는 반면, 이 엔진은 여러 세션의
다이제스트를 놓고 **세션 경계를 넘는 반복 절차**를 스킬 후보로 캔다. 단일 세션
리뷰가 구조적으로 못 보는 것("이 사람은 매주 같은 리포트를 처음부터 다시
시킨다")이 이 패스의 존재 이유다.

산출물은 파킹되지 않는다 — 수동 버튼(`POST /skills/discover`) 또는 백그라운드
스케줄러가 트리거하므로 붙잡아 둘 스트림이 없다. 대신 제안함
(`ledger.add_suggestion`, LearningService.discover가 수행)으로 들어가 사용자가
자기 페이스로 결정한다. ReviewEngine과 같은 무쓰기(no-write) 원칙: 이 엔진은
`PatchProposal`만 만들고 디스크에 닿지 않는다.
"""

from __future__ import annotations

import logging
from typing import Callable, Sequence

from ..agent.providers.interfaces import ILLMProvider
from ..core.types import ToolCall
from ..prompts.discovery import (
    SessionDigest,
    build_discovery_messages,
    build_discovery_tool_specs,
)
from .interfaces import PatchProposal
from .ledger import LearningLedger
from .proposals import make_create_proposal
from .skill.store import SkillStore

log = logging.getLogger("rice.learning.discovery")

_DISCOVERY_TEMPERATURE = 0.3
_MAX_PROPOSALS_PER_RUN = 2


class DiscoveryEngine:
    """discover() — 1회성 LLM 호출로 크로스세션 신규 스킬 제안을 산출한다."""

    def __init__(
        self,
        *,
        resolve_provider: Callable[[], ILLMProvider],
        skill_store: SkillStore,
        max_body_chars: int,
        ledger: LearningLedger | None = None,
        rejection_cooldown_hours: float = 0.0,
        max_rejections: int = 0,
        builtin_catalog=None,
    ) -> None:
        self._resolve_provider = resolve_provider
        self._skill_store = skill_store
        self._max_body_chars = max_body_chars
        self._ledger = ledger
        self._rejection_cooldown_hours = rejection_cooldown_hours
        self._max_rejections = max_rejections
        # 빌트인 스킬 카탈로그 — 리뷰 엔진과 같은 이유(builtin 이 이미 덮는
        # 패턴의 재발명 방지 + 예약 이름 조기 드랍). None 허용(기존 테스트).
        self._builtin_catalog = builtin_catalog

    async def discover(self, digests: Sequence[SessionDigest]) -> list[PatchProposal]:
        provider = self._resolve_provider()
        if getattr(provider, "name", "") == "echo":
            return []  # review/consolidation 엔진과 동일 가드
        if len(digests) < 2:
            return []  # 반복(2회+)을 보려면 최소 두 세션은 있어야 한다

        catalog = [
            (s.name, s.description) for s in self._skill_store.list() if s.state != "archived"
        ]
        if self._builtin_catalog is not None:
            try:
                catalog += [
                    (b.name, f"(builtin) {b.description}")
                    for b in self._builtin_catalog.list()
                    if getattr(b, "state", "active") == "active"
                ]
            except Exception:  # noqa: BLE001 — 카탈로그 조회 실패가 발굴을 깨면 안 된다
                log.warning("builtin catalog unavailable for discovery context", exc_info=True)
        rejected = (
            [
                f"[{r['skill_id']}] {r['changelog']}".strip()
                for r in self._ledger.recent_rejections(within_hours=self._rejection_cooldown_hours)
            ]
            if self._ledger is not None and self._rejection_cooldown_hours > 0
            else []
        )
        system, messages = build_discovery_messages(digests, catalog, rejected_recently=rejected)
        tools = build_discovery_tool_specs()

        calls: list[ToolCall] = []
        try:
            async for chunk in provider.stream(
                system=system, messages=messages, tools=tools, temperature=_DISCOVERY_TEMPERATURE
            ):
                if chunk.kind == "tool_call" and chunk.tool_call is not None:
                    calls.append(chunk.tool_call)
        except Exception:
            log.exception("discovery LLM call failed")
            return []  # best-effort — 발굴 실패는 조용히 다음 기회로

        existing = {s.name for s in self._skill_store.list()}
        if self._builtin_catalog is not None:
            try:
                existing |= set(self._builtin_catalog.names())  # 예약 이름 — 재발명/충돌 조기 드랍
            except Exception:  # noqa: BLE001
                pass
        proposals: list[PatchProposal] = []
        for call in calls:
            if len(proposals) >= _MAX_PROPOSALS_PER_RUN:
                break  # 프롬프트의 최대 2개 지시를 코드로도 강제(폭주 방어)
            if call.tool_name != "propose_skill_create":
                continue
            args = call.arguments if isinstance(call.arguments, dict) else {}
            name = str(args.get("name") or "").strip()
            if not name or name in existing:
                # 이미 있는 스킬의 재발명 — 카탈로그를 무시한 제안은 버린다.
                continue
            if self._ledger is not None and self._ledger.is_suppressed(
                name, within_hours=self._rejection_cooldown_hours, max_rejections=self._max_rejections
            ):
                continue
            proposal = make_create_proposal(
                name=name,
                body=str(args.get("body") or ""),
                description=str(args.get("description") or ""),
                tags=tuple(str(t) for t in (args.get("tags") or ())),
                trigger="T4",
                max_body_chars=self._max_body_chars,
            )
            if proposal is not None:
                proposals.append(proposal)
        return proposals
