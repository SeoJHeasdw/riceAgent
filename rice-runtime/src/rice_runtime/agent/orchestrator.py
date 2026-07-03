"""Orchestrator — the agent↔learning↔connector↔execution convergence point.

THIS IS THE SEAM THE WHOLE BACKEND TURNS ON. Every other module is reachable from
exactly one place: here, and only through its `interfaces.py` Protocol. The
orchestrator is deliberately **thin** ("얇은 조율" — RUNTIME_DIRECTORY): it owns
*sequencing*, not behavior. Each step's logic lives in the owning module.

THE TURN, AS A CONTRACT (RUNTIME_DESIGN §2, LEARNING_LOOP §1)
------------------------------------------------------------
    run_turn(session_id, user_message) → stream of SSEEvent:

      1. PERSIST    transcript.append(user message)
      2. PREFETCH   learning.prefetch(...)            → skills + memories + guidance
      3. ASSEMBLE   tools = builtin + connector.list_tools() + plugins.list_tools()
                    (connector+plugin 합이 예산을 넘으면 tool_search/describe/call 3개
                    브릿지로 접힌다 — §2.1/§11 progressive disclosure, tool_dispatch.py)
                    system_prompt = base ⊕ prefetch additions
                    → TurnContext
      4. RUN        AgentLoop.run(ctx)                 → thinking/message/tool_* events
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
from ..execution.interfaces import IPluginLoader
from ..learning.interfaces import ILearningService
from ..prompts.system import compose_system_prompt, render_skills_block
from .context_manager import ConversationContextManager
from .effect_surfacing import surface_learning_effect
from .interfaces import IApprovalGate, ITranscript
from .loop import AgentLoop, LoopResult
from .providers.interfaces import ILLMProvider
from .tool_dispatch import BuiltinTool, ToolDispatcher, fold_tools_for_budget

log = logging.getLogger("rice.agent.orchestrator")


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
        self._aborts: dict[str, asyncio.Event] = {}

    # ── IAgent.run_turn ──────────────────────────────────────────────────────
    async def run_turn(self, session_id: str, user_message: str) -> AsyncIterator[SSEEvent]:
        # 1. PERSIST user turn, then drive the turn over the (now-updated) history.
        self._transcript.append(session_id, Message(role="user", content=user_message))
        async for event in self._drive_turn(session_id, user_message):
            yield event

    # ── IAgent.regenerate ─────────────────────────────────────────────────────
    async def regenerate(self, session_id: str) -> AsyncIterator[SSEEvent]:
        """Re-answer the last user message. The route already dropped the stale
        assistant turn (delete_after), so history ends with that user message —
        we drive a fresh turn on it WITHOUT re-persisting a duplicate user row."""
        history = self._transcript.history(session_id)
        last_user = next((m for m in reversed(history) if m.role == "user"), None)
        if last_user is None:
            yield ev_error("no_user_message", "재생성할 사용자 메시지가 없습니다.")
            yield ev_done()
            return
        async for event in self._drive_turn(session_id, last_user.content):
            yield event

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
            foldable_tools = tuple(self._connectors.list_tools()) + tuple(self._plugins.list_tools())
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
            loop = AgentLoop(
                provider=self._resolve_provider(),
                dispatcher=dispatcher,
                temperature=cfg.provider.temperature,
                context_manager=ConversationContextManager(cfg.context),
            )
            result = LoopResult()
            async for event in loop.run(ctx, session_id, result, abort):
                yield event

            # 5. PERSIST every message the loop produced this turn — assistant
            #    tool-call rounds + tool results + final answer (C-4), not just the
            #    final text. Slice off the base history we started from.
            for msg in result.history[len(base_history):]:
                self._transcript.append(session_id, msg)

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

    # ── builtin tool catalog (override/extend in composition root) ───────────
    def _builtin_specs(self):
        """The builtin catalog advertised to the LLM. Empty unless the composition
        root injects `builtins=` (name→handler) + `builtin_specs=` (the matching
        ToolSpecs). Kept as a method so wiring can supply specs without touching
        the loop."""
        return self._builtin_specs_catalog
