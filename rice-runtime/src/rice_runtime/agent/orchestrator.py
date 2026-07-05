"""Orchestrator — the agent↔learning↔connector↔execution convergence point.

THIS IS THE SEAM THE WHOLE BACKEND TURNS ON. Every other module is reachable from
exactly one place: here, and only through its `interfaces.py` Protocol. The
orchestrator is deliberately **thin** ("얇은 조율" — RUNTIME_DIRECTORY): it owns
*sequencing*, not behavior. Each step's logic lives in the owning module.

THE TURN, AS A CONTRACT (RUNTIME_DESIGN §2, AGENT_EXECUTION §2, LEARNING_LOOP §1)
------------------------------------------------------------
    run_turn(session_id, user_message) → stream of SSEEvent:

      1. PERSIST    transcript.append(user message)
      2. PREFETCH   learning.prefetch(...)            → skills + memories + guidance
      3. ASSEMBLE   tools = builtin + connector.list_tools() + plugins.list_tools()
                    (connector+plugin 합이 예산을 넘으면 tool_search/describe/call 3개
                    브릿지로 접힌다 — §2.1/§11 progressive disclosure, tool_dispatch.py)
                    system_prompt = base ⊕ prefetch additions
                    → TurnContext
      4. RUN        AgentLoop.run(ctx)                 → plan-and-execute × ReAct
                    (AGENT_EXECUTION §3, agent/planner.py):
                      4a. PLAN     복합 질의면 TurnPlanner가 의도+단계 계획 수립
                                   → plan_updated 이벤트(단순 질의면 계획 없음)
                      4b. EXECUTE  thinking/message/tool_* events; 계획이 있으면 매
                                   반복 계획 상태를 프롬프트에 렌더 + update_plan
                                   인터셉트로 단계 전이/수정 → plan_updated
                    (tool dispatch + HITL approval handled inside the loop/dispatcher)
      5. PERSIST    transcript.append(every message the loop produced this turn)
      6. OBSERVE    learning.observe(outcome)          → cheap effects (memory only —
                                                           skill judgement moved to the
                                                           separate `/learning-review`
                                                           endpoint, see agent/effect_surfacing.py)
      7. SURFACE    effects → SSE via agent.effect_surfacing.surface_learning_effect:
                      MemoryWritten  → memory_written
                      PatchProposal  → skill_patch_proposed → await IApprovalGate
                                       → learning.apply_patch → skill_patched
                                       (from_version=0 covers skill *creation* too —
                                       Phase A.1 unified create+patch into one flow)
      8. DONE       done   (or error → done on any failure)

WHY THE APPROVAL DANCE LIVES HERE (not in learning)
---------------------------------------------------
HITL parking needs the SSE stream *and* the `IApprovalGate` — both of which the
orchestrator already holds. Learning stays pure (proposes + applies-on-verdict);
the orchestrator is the one place that bridges a proposal to the user and back.
This mirrors the tool-approval flow in `tool_dispatch.py` — one mechanism, two
call sites (RUNTIME_DESIGN §5.1).

The orchestrator imports only `*.interfaces` + `core` — never a concrete impl.
Wiring is `server/composition_root.py`'s job.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Callable

from ..connector.interfaces import IConnectorService
from ..core.config import ConfigStore
from ..core.types import (
    Message,
    SSEEvent,
    ToolSpec,
    TurnContext,
    TurnOutcome,
    ev_done,
    ev_error,
)
from ..core.tokens import TokenCalibrator
from ..execution.interfaces import IPluginLoader
from ..learning.interfaces import ILearningService
from ..prompts.system import compose_system_prompt, render_skills_block
from .context_manager import ConversationContextManager
from .effect_surfacing import surface_learning_effect
from .interfaces import IApprovalGate, ITranscript
from .loop import AgentLoop, LoopResult
from .planner import TurnPlanner
from .providers.interfaces import ILLMProvider
from .tool_dispatch import BuiltinTool, ToolDispatcher, fold_tools_for_budget

log = logging.getLogger("rice.agent.orchestrator")


def _dedupe_tool_names(
    builtin: tuple[ToolSpec, ...], foldable: tuple[ToolSpec, ...]
) -> tuple[ToolSpec, ...]:
    """connector/plugin 도구 중 이름이 겹치는 것을 카탈로그 조립 시점에 걸러낸다.

    같은 이름이 둘이면 (a) 프로바이더 API 가 중복 tool 스키마를 거부하거나
    (b) loop 의 name→spec 인덱스와 실행 라우팅이 서로 다른 쪽을 고를 수 있다.
    빌트인과 겹치는 connector 도구(예: 로컬 MCP 가 노출한 "read_file")는 빌트인
    새도잉이므로 역시 제외한다. 먼저 등장한 스펙이 이긴다(로컬 MCP → 원격 커넥터
    → 플러그인 순). 제외는 경고 로그로 가시화한다 — 조용한 오배송보다 낫다.
    """
    taken = {t.name for t in builtin}
    kept: list[ToolSpec] = []
    for spec in foldable:
        if spec.name in taken:
            log.warning(
                "duplicate tool name %r (owner=%s) dropped from catalog — "
                "shadowed by a builtin or an earlier connector/plugin tool",
                spec.name, spec.owner_id,
            )
            continue
        taken.add(spec.name)
        kept.append(spec)
    return tuple(kept)


class Orchestrator:
    """Implements `agent.interfaces.IAgent`. Constructed once by the composition
    root with every collaborator injected as a Protocol."""

    def __init__(
        self,
        *,
        resolve_provider: Callable[[], ILLMProvider],
        config_store: ConfigStore,
        learning: ILearningService,
        connectors: IConnectorService,
        plugins: IPluginLoader,
        approvals: IApprovalGate,
        transcript: ITranscript,
        builtins: dict[str, BuiltinTool] | None = None,
        builtin_specs: tuple[ToolSpec, ...] = (),
        token_calibrator: TokenCalibrator | None = None,
    ) -> None:
        # `resolve_provider` is a thunk, not a captured instance, so a runtime
        # model switch (PUT /settings) takes effect next turn (C-1). Likewise
        # tuning params are read live from `config_store` each turn.
        self._resolve_provider = resolve_provider
        self._config_store = config_store
        self._learning = learning
        self._connectors = connectors
        self._plugins = plugins
        self._approvals = approvals
        self._transcript = transcript
        self._builtins = builtins
        self._builtin_specs_catalog = builtin_specs
        # 세션 전반에 걸쳐 토큰 추정을 실측으로 보정하는 싱글턴. None이면
        # 순수 CJK 휴리스틱만 쓴다(테스트/오프라인).
        self._token_calibrator = token_calibrator or TokenCalibrator()
        self._aborts: dict[str, asyncio.Event] = {}
        # 세션별 진행 중 턴 락 — 같은 세션 동시 POST /messages 차단. 값은 "턴 종료"
        # 이벤트로, abort_and_drain 이 진행 중 턴이 실제로 끝날 때까지 기다리는 데
        # 쓴다(DELETE /sessions 등 mutation 이 스트리밍 중 transcript 를 찢지 않게).
        self._active: dict[str, asyncio.Event] = {}

    # ── IAgent.run_turn ──────────────────────────────────────────────────────
    async def run_turn(self, session_id: str, user_message: str) -> AsyncIterator[SSEEvent]:
        # 세션 동시 턴 방어: 이미 이 세션에서 턴이 진행 중이면 두 번째
        # POST /messages는 히스토리를 건드리기 전에 거절한다 — 두 스트림이 같은
        # transcript를 교차 append하면 순서가 깨진다. 단일 이벤트 루프라 set
        # 검사-추가 사이에 await가 없어 원자적이다.
        if session_id in self._active:
            yield ev_error("session_busy", "이 세션에서 이미 응답을 생성하고 있습니다. 잠시 후 다시 시도하세요.")
            yield ev_done()
            return
        self._active[session_id] = asyncio.Event()
        try:
            # 1. PERSIST user turn, then drive the turn over the (now-updated) history.
            self._transcript.append(session_id, Message(role="user", content=user_message))
            async for event in self._drive_turn(session_id, user_message):
                yield event
        finally:
            done = self._active.pop(session_id, None)
            if done is not None:
                done.set()

    # ── IAgent.regenerate ─────────────────────────────────────────────────────
    async def regenerate(self, session_id: str) -> AsyncIterator[SSEEvent]:
        """Re-answer the last user message. The route already dropped the stale
        assistant turn (delete_after), so history ends with that user message —
        we drive a fresh turn on it WITHOUT re-persisting a duplicate user row."""
        if session_id in self._active:
            yield ev_error("session_busy", "이 세션에서 이미 응답을 생성하고 있습니다. 잠시 후 다시 시도하세요.")
            yield ev_done()
            return
        self._active[session_id] = asyncio.Event()
        try:
            history = self._transcript.history(session_id)
            last_user = next((m for m in reversed(history) if m.role == "user"), None)
            if last_user is None:
                yield ev_error("no_user_message", "재생성할 사용자 메시지가 없습니다.")
                yield ev_done()
                return
            async for event in self._drive_turn(session_id, last_user.content):
                yield event
        finally:
            done = self._active.pop(session_id, None)
            if done is not None:
                done.set()

    async def _drive_turn(self, session_id: str, user_message: str) -> AsyncIterator[SSEEvent]:
        """Steps 2–7 of a turn (prefetch → assemble → loop → persist → observe →
        surface), shared by run_turn and regenerate. The user message is assumed
        already persisted as the tail of history."""
        abort = self._aborts.setdefault(session_id, asyncio.Event())
        abort.clear()
        cfg = self._config_store.config  # snapshot for this turn (live across turns)
        try:
            # 2. PREFETCH (learning assembles injectable context)
            pf = self._learning.prefetch(session_id, user_message)

            # Plugin-provided SKILL.md bundles also inject into the system prompt
            # (CONTRACTS §2.2 — "plugin = 머리에 추가되는 전문성"). v1 버그#2: the
            # orchestrator never called injected_skills(), so plugin skills were
            # silently dropped. Dedupe by name (a learned skill of the same name wins).
            learned_names = {s.name for s in pf.skills}
            plugin_skills = tuple(
                s for s in self._plugins.injected_skills() if s.name not in learned_names
            )

            # 3. ASSEMBLE context (tools from every tool-bearing module).
            # builtin = "core", always exposed as-is. connector+plugin = "non-core"; if
            # their combined schema exceeds the tool budget, fold_tools_for_budget folds
            # them behind 3 bridge tools (tool_search/describe/call — §2.1/§11) instead of
            # sending every schema every turn. The dispatcher gets the full foldable
            # catalog regardless, so tool_call can resolve a folded tool by name.
            foldable_tools = _dedupe_tool_names(
                self._builtin_specs(),
                tuple(self._connectors.list_tools()) + tuple(self._plugins.list_tools()),
            )
            tools = fold_tools_for_budget(self._builtin_specs(), foldable_tools)
            # The model reads ctx.system_prompt only (the loop ignores injected_skills,
            # which is telemetry) → plugin skills must be rendered INTO the prompt.
            plugin_skill_block = render_skills_block(
                plugin_skills, header="## Plugin Skills (from installed plugins — follow when relevant)"
            )
            system_prompt = compose_system_prompt(pf.system_prompt_additions, plugin_skill_block)
            base_history = self._transcript.history(session_id)
            ctx = TurnContext(
                session_id=session_id,
                system_prompt=system_prompt,
                history=base_history,
                tools=tools,
                injected_skills=pf.skills + plugin_skills,
                injected_memories=pf.memories,
                max_iterations=cfg.learning.max_iterations,
            )

            # 4. RUN the loop (fresh dispatcher per turn → no cross-turn state, C-2)
            dispatcher = ToolDispatcher(
                connectors=self._connectors,
                plugins=self._plugins,
                approvals=self._approvals,
                approval_timeout_s=cfg.learning.approval_timeout_seconds,
                builtins=self._builtins,
                folded_catalog={t.name: t for t in foldable_tools},
            )
            # 플래너와 실행 루프는 같은 provider 인스턴스를 쓴다(로컬 LLM 하나가
            # 겸임). planning_enabled=False면 플래너 없이 순수 ReAct — 기존 동작.
            provider = self._resolve_provider()
            planner = (
                TurnPlanner(provider=provider, config=cfg.agent)
                if cfg.agent.planning_enabled
                else None
            )
            loop = AgentLoop(
                provider=provider,
                dispatcher=dispatcher,
                temperature=cfg.provider.temperature,
                context_manager=ConversationContextManager(
                    cfg.context, estimate=self._token_calibrator.estimate
                ),
                token_calibrator=self._token_calibrator,
                planner=planner,
                agent_config=cfg.agent,
            )
            result = LoopResult()
            async for event in loop.run(ctx, session_id, result, abort):
                yield event

            # 5. PERSIST every message the loop produced this turn — assistant
            #    tool-call rounds + tool results + final answer (C-4), not just the
            #    final text. Slice off the base history we started from.
            for msg in result.history[len(base_history):]:
                self._transcript.append(session_id, msg)

            # 5b. AUDIT context renders — 이 턴의 매 LLM 호출에서
            #     컨텍스트 매니저가 뭘 생략/삭제/증류했는지 ledger에 남긴다.
            #     "모델이 정확히 뭘 못 봤는가"를 제품적으로 설명 가능하게 하는 로그.
            if result.context_renders:
                try:
                    self._learning.record_context_renders(
                        session_id, [s.to_dict() for s in result.context_renders]
                    )
                except Exception:  # noqa: BLE001 — 감사 실패가 턴을 깨면 안 된다
                    log.warning("context render audit failed for %s", session_id, exc_info=True)

            # 6. OBSERVE (learning decides what to keep/propose)
            outcome = TurnOutcome(
                session_id=session_id,
                final_message=result.final_message,
                history=result.history,
                tool_calls_made=result.tool_calls_made,
                injected_skill_ids=tuple(s.skill_id for s in pf.skills),
                aborted=result.aborted,
            )
            effects = await self._learning.observe(outcome)

            # 7. SURFACE effects (patches/creations go through the HITL gate) —
            #    surface_learning_effect (agent/effect_surfacing.py) is a pure
            #    extraction of what used to be this method's own body, shared
            #    with the standalone /learning-review·/learn routes.
            timeout_s = self._config_store.config.learning.approval_timeout_seconds
            for effect in effects:
                async for event in surface_learning_effect(
                    session_id=session_id,
                    effect=effect,
                    learning=self._learning,
                    approvals=self._approvals,
                    approval_timeout_s=timeout_s,
                ):
                    yield event

        except Exception as exc:  # any failure → one terminal error event
            log.exception("turn failed for session %s", session_id)
            yield ev_error("turn_failed", str(exc))
        finally:
            # 8. DONE always closes the stream; drop the per-session abort flag so
            #    `_aborts` can't grow unbounded (C-3).
            self._aborts.pop(session_id, None)
            yield ev_done()

    # ── IAgent.abort ─────────────────────────────────────────────────────────
    async def abort(self, session_id: str) -> None:
        if session_id in self._aborts:
            self._aborts[session_id].set()
        # cancel any parked approvals for this session (RUNTIME_DESIGN §5.1)
        self._approvals.cancel_session(session_id)

    # ── IAgent.is_active / abort_and_drain ──────────────────────────────────
    def is_active(self, session_id: str) -> bool:
        """이 세션에서 턴이 스트리밍 중인가 — 라우트가 transcript mutation
        (delete/regenerate)을 하기 전에 반드시 확인해야 하는 게이트."""
        return session_id in self._active

    async def abort_and_drain(self, session_id: str, timeout_s: float = 10.0) -> bool:
        """진행 중 턴을 abort 하고 실제로 **끝날 때까지** 기다린다 (DELETE /sessions 용).

        abort 플래그만 세우고 바로 지우면 턴 후반의 append/observe 가 삭제된
        세션에 쓰다 FK 오류로 죽는다 — drain 이 끝난 뒤에야 mutation 이 안전하다.
        반환: True = 턴 없음/정상 종료, False = timeout 내 안 끝남(호출자는
        진행하되 턴 꼬리의 실패 이벤트를 감수한다 — 로그로 남긴다)."""
        done = self._active.get(session_id)
        if done is None:
            return True
        await self.abort(session_id)
        try:
            await asyncio.wait_for(done.wait(), timeout=timeout_s)
            return True
        except asyncio.TimeoutError:
            log.warning("abort_and_drain: turn for %s did not finish within %.1fs", session_id, timeout_s)
            return False

    def abort_all(self) -> None:
        """모든 활성 턴 abort + 파킹된 승인 취소 (shutdown 용, best-effort sync).

        drain 은 하지 않는다 — shutdown 경로는 sync 라 기다릴 수 없고, 직후
        supervisor 가 프로세스를 내리므로 '새 작업 차단 + 대기 해제'가 목표다."""
        for session_id in list(self._active):
            abort = self._aborts.get(session_id)
            if abort is not None:
                abort.set()
            self._approvals.cancel_session(session_id)

    # ── builtin tool catalog (override/extend in composition root) ───────────
    def _builtin_specs(self):
        """The builtin catalog advertised to the LLM. Empty unless the composition
        root injects `builtins=` (name→handler) + `builtin_specs=` (the matching
        ToolSpecs). Kept as a method so wiring can supply specs without touching
        the loop."""
        return self._builtin_specs_catalog
