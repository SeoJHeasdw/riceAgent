"""learning/review/engine.py — LLM 기반 스킬/메모리 저작 엔진 (Phase A.3).

`observe()`가 정규식으로 하던 저작 판단(무엇을 스킬로 만들지, patch 본문에 뭘
쓸지)을 대체한다. 실제로 판단력이 필요한 일이라 LLM 1회 호출을 쓴다 — 대신
`AgentLoop` 전체(다중 라운드 tool 디스패치)를 쓰지 않고 `ILLMProvider.stream()`을
직접 한 번만 소비한다: 이 엔진은 파일을 쓸 권한이 전혀 없고(propose_* pseudo-tool은
`ToolDispatcher`를 거치지 않는다 — 절대 실행되지 않고 인자만 읽힌다), 오직 구조화된
제안(`LearningEffect`)만 산출한다.

WHY `ReviewEngine`이 `LearningService`의 provider 의존을 만드는가: 이전까지
`LearningService`는 "provider를 보지 않는 순수 계층"이었다(observe()가 정규식만
썼으므로). 이 엔진이 그 경계를 의도적으로 옮긴다 — 진짜 저작 지능을 넣는 것이
이번 진화의 핵심이므로 이 트레이드오프는 불가피하다. (learning/service.py의
`LearningService.__init__` docstring 참고.)

WHY `agent.providers.interfaces`를 여기서 import해도 순환 의존이 아닌가: 그 파일은
`core.types`만 본다(다른 어느 것도 `learning`을 보지 않는다) — orchestrator.py가
이미 `learning.interfaces`를 보는 것과 대칭인 반대 방향의 **Protocol 전용** 참조일
뿐, 구현체(concrete provider)는 여전히 `server/composition_root.py`에서만 조립된다.
"""

from __future__ import annotations

import logging
from typing import Callable, Sequence

from ...agent.providers.interfaces import ILLMProvider
from ...core.errors import NotFoundError
from ...core.types import Message, ToolCall
from ...prompts.review import (
    LEARN_SYSTEM_PROMPT,
    REVIEW_SYSTEM_PROMPT,
    build_learn_instruction,
    build_review_messages,
    build_review_tool_specs,
)
from ..interfaces import LearningEffect, MemoryWritten, PatchProposal
from ..ledger import LearningLedger
from ..memory.store import MemoryStore
from ..proposals import make_create_proposal, make_patch_proposal
from ..skill import guard as skill_guard
from ..skill.store import SkillStore
from ..skill.usage import UsageStore
from .context import LearningContextManager, filter_learning_history

log = logging.getLogger("rice.learning.review")

_REVIEW_TEMPERATURE = 0.3  # titler.py와 동일 원칙: 구조화된 산출물은 낮은 변동성이 낫다
_MAX_KNOWN_FACTS = 20

# 외부 콘텐츠로 간주하는 도구 출처(메모리 오염 방지). connector(Gmail/
# Calendar/Drive), plugin(설치된 플러그인 도구), bridge(progressive disclosure로
# 접힌 connector/plugin 도구) — 모두 사용자 발화 밖의 데이터를 끌어온다. builtin
# (파일/터미널/스킬)은 로컬이라 제외.
_EXTERNAL_TOOL_SOURCES = frozenset({"connector", "plugin", "bridge"})


def _external_evidence(history: Sequence[Message]) -> tuple[bool, tuple[str, ...]]:
    """대화에 외부 콘텐츠(도구 결과)가 섞였는지 + 그 근거 메시지 id들을 반환한다.
    loop가 tool 메시지에 심은 `tool_source` 메타데이터로 판단한다. 이 판단은 반드시
    *원본* history에서 해야 한다 — 리뷰 렌더는 순수 텍스트라 메타데이터가 없다."""
    ids: list[str] = []
    for m in history:
        if m.role == "tool" and m.metadata.get("tool_source") in _EXTERNAL_TOOL_SOURCES:
            ids.append(m.id)
    return (bool(ids), tuple(ids))


def _estimate_tokens(text: str) -> int:
    from ...core.tokens import estimate_tokens  # noqa: PLC0415

    return estimate_tokens(text)


class ReviewEngine:
    """review()/learn() — 1회성 LLM 호출로 구조화된 skill/memory 제안을 산출한다.

    쓰기 권한은 오직 memory에만 있다(`propose_memory_write` → 즉시 반영, memory는
    이 앱 설계상 원래도 HITL 대상이 아니다). skill 관련 제안은 전부 `PatchProposal`
    (생성도 patch로 통일 — Phase A.1)로 돌려주고, 실제 파일 쓰기는 호출자가
    HITL 승인 후 `apply_patch`로 한다.
    """

    def __init__(
        self,
        *,
        resolve_provider: Callable[[], ILLMProvider],
        skill_store: SkillStore,
        usage_store: UsageStore,
        mem_store: MemoryStore,
        skill_catalog_token_budget: int,
        max_body_chars: int = skill_guard.DEFAULT_MAX_BODY_CHARS,
        ledger: LearningLedger | None = None,
        rejection_cooldown_hours: float = 0.0,
        max_rejections: int = 0,
        context: LearningContextManager | None = None,
        builtin_catalog=None,
    ) -> None:
        self._resolve_provider = resolve_provider
        self._skill_store = skill_store
        self._usage_store = usage_store
        self._mem_store = mem_store
        self._catalog_budget = skill_catalog_token_budget
        self._max_body_chars = max_body_chars
        # 빌트인 스킬 카탈로그(BuiltinSkillCatalog 호환: .list()/.names()) —
        # 없으면(None, 기존 테스트) 리뷰 LLM 은 빌트인의 존재를 모른 채 동작한다.
        # 있으면 (1) compact 카탈로그에 실어 빌트인이 이미 덮는 절차의 재발명을
        # 막고, (2) 빌트인 이름으로 온 create 제안을 조기 드랍한다 — 이전엔 승인
        # 시점(_ensure_not_builtin)에야 막혀 사용자가 승인하고 에러를 보는 막다른
        # 카드였다.
        self._builtin_catalog = builtin_catalog
        # 리뷰 전용 컨텍스트 렌더러: None(기존 테스트)이면 v3와 동일하게
        # 히스토리를 그대로 넘긴다. 실배선(composition)은 항상 렌더러를 준다.
        self._context = context
        # Self-Improvement v3: ledger가 있으면 (1) 최근 거부 목록을 프롬프트에 실어
        # LLM이 재제안을 피하게 하고(사전), (2) 그래도 나온 억제 대상 제안을 버리고
        # (사후), (3) 제안/메모리 기록을 감사 로그에 남긴다. ledger=None(기존 테스트
        # 대부분)이면 v2와 동일하게 동작.
        self._ledger = ledger
        self._rejection_cooldown_hours = rejection_cooldown_hours
        self._max_rejections = max_rejections

    async def review(self, session_id: str, history: Sequence[Message]) -> list[LearningEffect]:
        """정기 리뷰 — `learning/review/gate.py::ReviewGate`가 호출 여부를 이미
        결정했다고 가정한다(이 메서드 자체는 게이팅하지 않는다)."""
        return await self._run(
            session_id, history, system_prompt=REVIEW_SYSTEM_PROMPT, instruction=None, origin="review"
        )

    async def learn(self, session_id: str, history: Sequence[Message], instruction: str) -> list[LearningEffect]:
        """명시적 사용자 요청("이 대화를 스킬로 저장") — Phase C. 게이트 없이 항상
        실행한다(호출 자체가 명시적 의도)."""
        return await self._run(
            session_id,
            history,
            system_prompt=LEARN_SYSTEM_PROMPT,
            instruction=build_learn_instruction(instruction),
            origin="learn",
        )

    # ── internals ────────────────────────────────────────────────────────────

    def _is_suppressed(self, skill_id: str) -> bool:
        if self._ledger is None:
            return False
        return self._ledger.is_suppressed(
            skill_id,
            within_hours=self._rejection_cooldown_hours,
            max_rejections=self._max_rejections,
        )

    def _recent_rejection_lines(self) -> list[str]:
        if self._ledger is None or self._rejection_cooldown_hours <= 0:
            return []
        return [
            f"[{r['skill_id']}] {r['changelog']}".strip()
            for r in self._ledger.recent_rejections(within_hours=self._rejection_cooldown_hours)
        ]

    async def _run(
        self,
        session_id: str,
        history: Sequence[Message],
        *,
        system_prompt: str,
        instruction: str | None,
        origin: str,
    ) -> list[LearningEffect]:
        provider = self._resolve_provider()
        if getattr(provider, "name", "") == "echo":
            # titler.py와 동일 가드 — API 키 미설정/오프라인 폴백 provider로는
            # 구조화된 제안을 만들 수 없다(에코는 입력을 그대로 반사할 뿐).
            return []

        # 런타임이 모델에게만 주입한 계획 정리/서사 넛지는 사용자 발화가
        # 아니다. 리뷰 컨텍스트 렌더러가 없는 단위 조합에서도 안전하게 먼저
        # 제거한다. 외부 콘텐츠(도구 결과) 근거는 메타데이터가 남은 이
        # 필터된 원본에서 계산한다.
        review_history = filter_learning_history(history)
        external, evidence_ids = _external_evidence(review_history)

        full_body_skills, compact_skills, patchable_ids = self._build_catalog()
        known_facts = self._recent_fact_strings()
        kwargs = {} if instruction is None else {"instruction": instruction}
        # 리뷰 전용 렌더: 긴 세션에서도 호출이 bounded가 되고, 도구
        # 라운드는 순수 텍스트라 wire 규칙 위반이 구조적으로 불가능해진다.
        rendered_history = self._context.render(review_history) if self._context is not None else review_history
        _, messages = build_review_messages(
            rendered_history,
            full_body_skills=full_body_skills,
            compact_skills=compact_skills,
            known_facts=known_facts,
            rejected_recently=self._recent_rejection_lines(),
            usage_hints=self._usage_hints(patchable_ids),
            **kwargs,
        )
        tools = build_review_tool_specs(patchable_ids)

        calls: list[ToolCall] = []
        try:
            async for chunk in provider.stream(
                system=system_prompt, messages=messages, tools=tools, temperature=_REVIEW_TEMPERATURE
            ):
                if chunk.kind == "tool_call" and chunk.tool_call is not None:
                    calls.append(chunk.tool_call)
        except Exception:
            # best-effort — 리뷰 실패가 (이미 사용자에게 답변이 끝난) 상위 흐름을
            # 깨서는 안 된다. titler.py의 "titling must never break a turn"과 동일.
            log.exception("review LLM call failed for session %s", session_id)
            return []

        effects: list[LearningEffect] = []
        for call in calls:
            effect = self._to_effect(session_id, call, external=external, evidence_ids=evidence_ids)
            if effect is None:
                continue
            if isinstance(effect, PatchProposal):
                if self._is_suppressed(effect.skill_id):
                    # 프롬프트 회피 지시에도 불구하고 나온 재제안 — 쿨다운 창 안에서는
                    # 사용자를 다시 방해하지 않는다(사후 억제, fail-closed).
                    log.info("proposal for %r suppressed (recent rejections)", effect.skill_id)
                    continue
                if self._ledger is not None:
                    self._ledger.record(
                        "proposed",
                        skill_id=effect.skill_id,
                        session_id=session_id,
                        origin=origin,
                        trigger=effect.trigger,
                        detail={"changelog": effect.changelog},
                    )
            effects.append(effect)
        return effects

    def _build_catalog(
        self,
    ) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]], tuple[str, ...]]:
        """active 스킬을 use 상위 정렬 후 예산 내에서 전체 본문으로, 나머지는
        name+description만 담는다. `prefetch()`의 budget-fit과 같은 모양이지만
        렌더링 목적이 달라(리뷰는 skill_id를 명시적으로 라벨링해야 모델이
        propose_skill_patch에서 정확히 참조할 수 있다) 별도로 둔다."""
        all_active = [s for s in self._skill_store.list() if s.state == "active"]

        def _use_count(name: str) -> int:
            return self._usage_store.read(name).use

        active_sorted = sorted(all_active, key=lambda s: _use_count(s.name), reverse=True)

        full_body: list[tuple[str, str, str]] = []
        compact: list[tuple[str, str]] = []
        remaining_budget = self._catalog_budget
        for skill in active_sorted:
            body = skill.body or ""
            tokens = _estimate_tokens(body)
            if tokens <= remaining_budget:
                full_body.append((skill.name, skill.description, body))
                remaining_budget -= tokens
            else:
                compact.append((skill.name, skill.description))

        # 빌트인 스킬은 patch 불가(이름 예약)지만 **존재는 보여야** 한다 — 안
        # 보이면 리뷰 LLM 이 빌트인이 이미 덮는 절차를 신규 스킬로 재발명한다.
        # compact(do-NOT-patch) 목록에 싣는 이유다.
        if self._builtin_catalog is not None:
            try:
                for b in self._builtin_catalog.list():
                    if getattr(b, "state", "active") == "active" and getattr(b, "enabled", True):
                        compact.append((b.name, f"(builtin) {b.description}"))
            except Exception:  # noqa: BLE001 — 카탈로그 조회 실패가 리뷰를 깨면 안 된다
                log.warning("builtin catalog unavailable for review context", exc_info=True)

        patchable_ids = tuple(s[0] for s in full_body)
        return full_body, compact, patchable_ids

    def _usage_hints(self, skill_ids: Sequence[str]) -> dict[str, str]:
        """patch 가능한 스킬들의 실측 텔레메트리 한 줄 — friction(관여 턴의
        교정/실패 동시 발생)과 routing_miss(라우터 미스 수동 로딩)가 0이면
        굳이 싣지 않는다(신호 없는 잡음)."""
        hints: dict[str, str] = {}
        for sid in skill_ids:
            try:
                u = self._usage_store.read(sid)
            except Exception:  # noqa: BLE001
                continue
            if u.friction <= 0 and u.routing_miss <= 0:
                continue
            hints[sid] = f"use={u.use}, friction={u.friction}, routing_miss={u.routing_miss}"
        return hints

    def _recent_fact_strings(self, limit: int = _MAX_KNOWN_FACTS) -> list[str]:
        return [m.content for m in self._mem_store.list()[:limit]]

    def _to_effect(
        self,
        session_id: str,
        call: ToolCall,
        *,
        external: bool = False,
        evidence_ids: tuple[str, ...] = (),
    ) -> LearningEffect | None:
        args = call.arguments if isinstance(call.arguments, dict) else {}
        if call.tool_name == "propose_skill_create":
            return self._skill_create_effect(args, external=external, evidence_ids=evidence_ids)
        if call.tool_name == "propose_skill_patch":
            return self._skill_patch_effect(args, external=external, evidence_ids=evidence_ids)
        if call.tool_name == "propose_memory_write":
            return self._memory_effect(session_id, args, external=external, evidence_ids=evidence_ids)
        log.warning("review engine: unknown tool call %r", call.tool_name)
        return None

    def _skill_create_effect(
        self, args: dict, *, external: bool = False, evidence_ids: tuple[str, ...] = ()
    ) -> PatchProposal | None:
        name = str(args.get("name") or "").strip()
        body = str(args.get("body") or "")
        if not name or not body.strip():
            return None
        if self._builtin_catalog is not None:
            try:
                if name in self._builtin_catalog.names():
                    # 빌트인 이름은 예약 — 승인 시점에야 막히는 막다른 카드를
                    # 만들지 않고 여기서 버린다(fail-closed).
                    log.warning("review engine: create proposal for builtin name %r ignored", name)
                    return None
            except Exception:  # noqa: BLE001
                pass
        try:
            self._skill_store.read(name)
            # 모델이 실제로 존재하는 이름으로 "생성"을 제안했다 — patch를 썼어야
            # 한다. 잘못된 제안을 그대로 흘리지 않고 버린다(fail-closed).
            log.warning("review engine: propose_skill_create for existing skill %r ignored", name)
            return None
        except NotFoundError:
            pass
        return make_create_proposal(
            name=name,
            body=body,
            description=str(args.get("description") or ""),
            tags=tuple(str(t) for t in (args.get("tags") or ())),
            trigger="T4",
            max_body_chars=self._max_body_chars,
            derived_from_external=external,
            evidence_message_ids=evidence_ids,
        )

    def _skill_patch_effect(
        self, args: dict, *, external: bool = False, evidence_ids: tuple[str, ...] = ()
    ) -> PatchProposal | None:
        skill_id = str(args.get("skill_id") or "").strip()
        new_body = str(args.get("new_body") or "")
        changelog = str(args.get("changelog") or "review 제안")
        trigger = str(args.get("trigger_reason") or "T1a")
        if trigger not in ("T1a", "T1b", "T1c"):
            trigger = "T1a"
        if not skill_id:
            return None
        try:
            current = self._skill_store.read(skill_id)
        except Exception:
            # 모델이 카탈로그에 없는/유효하지 않은 skill_id를 줬다 — 스키마의 enum이
            # 이미 이걸 막지만(build_review_tool_specs), 방어적으로 한 번 더 확인.
            return None
        raw_new_desc = args.get("new_description")
        raw_new_tags = args.get("new_tags")
        return make_patch_proposal(
            current=current,
            new_body=new_body,
            changelog=changelog,
            trigger=trigger,  # type: ignore[arg-type]
            max_body_chars=self._max_body_chars,
            derived_from_external=external,
            evidence_message_ids=evidence_ids,
            new_description=str(raw_new_desc) if raw_new_desc else None,
            new_tags=tuple(str(t) for t in raw_new_tags) if isinstance(raw_new_tags, (list, tuple)) else None,
        )

    def _memory_effect(
        self, session_id: str, args: dict, *, external: bool = False, evidence_ids: tuple[str, ...] = ()
    ) -> MemoryWritten | None:
        kind = args.get("kind")
        content = str(args.get("content") or "").strip()
        if kind not in ("fact", "preference") or not content:
            return None
        # Learning safety scanner: 메모리는 HITL 없이 매 턴 시스템
        # 프롬프트에 재주입되는 유일한 경로다 — 위험 플래그(주입/exfil/파괴 명령)가
        # 하나라도 있으면 카드에 물어볼 기회조차 없으므로 저장 자체를 거부한다
        # (fail-closed). 스킬 경로는 같은 스캐너 결과를 warnings로 카드에 싣는다.
        risks = skill_guard.scan_risks(content)
        if risks:
            log.warning("memory write blocked (risk flags: %s)", risks)
            if self._ledger is not None:
                self._ledger.record(
                    "memory_blocked",
                    session_id=session_id,
                    origin="review",
                    detail={"kind": kind, "risks": risks, "content": content[:120]},
                )
            return None
        # 외부 콘텐츠(도구 결과)가 섞인 턴에서 나온 메모리는 격리(quarantined)로
        # 저장 — "그럴듯한 거짓 사실"을 정규식은 못 잡으므로, 즉시 주입 대신 사용자
        # 승인을 기다린다. 순수 대화 유래 메모리만 즉시 active.
        try:
            item = self._mem_store.add(
                kind,
                content,
                source_session_id=session_id,
                derived_from_external=external,
                source_message_ids=evidence_ids,
            )
        except Exception:
            return None
        if self._ledger is not None:
            self._ledger.record(
                "memory_quarantined" if item.status == "quarantined" else "memory_written",
                session_id=session_id,
                origin="review",
                detail={"kind": kind, "content": content[:120], "memory_id": item.id, "status": item.status},
            )
        return MemoryWritten(
            memory_type=kind,  # type: ignore[arg-type]
            content=content,
            status=item.status,
            memory_id=item.id,
            derived_from_external=external,
        )
