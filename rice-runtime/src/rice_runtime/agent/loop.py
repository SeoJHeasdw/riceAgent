"""Conversation loop (#1) — plan-and-execute × ReAct (RICE_AGENT_EXECUTION.md §3).

Structure borrowed from Hermes' `run_conversation` (pattern only, re-implemented),
then extended with an explicit planning phase:

  PLAN     복합 질의면 TurnPlanner가 의도(goal)+단계 계획을 세운다(LLM 1회).
           단순 질의면 계획 없이 곧장 실행 — 이전과 동일한 순수 ReAct 루프.
  EXECUTE  one iteration = one LLM stream; if it asks for tools, dispatch them,
           append results, loop again. 계획이 있으면 매 반복 시스템 프롬프트에
           살아있는 계획 상태를 다시 렌더하고(스냅샷 프롬프팅 — 로컬 LLM이 긴
           작업에서 길을 잃지 않게), 모델은 `update_plan`(루프가 가로채는 가상
           빌트인)으로 단계 전이·계획 수정을 보고한다(ReAct의 관찰→재평가).
           N라운드 무보고면 drift guard가 재평가를 강제하고, 전 단계 종결 시
           propose_skill 유도가 켜진다. 반복 상한은 계획 크기에 맞춰 늘어난다.

The loop is provider- and storage-agnostic: it consumes a `TurnContext` and an
`ILLMProvider`, dispatches via an `IToolDispatcher`, and yields `SSEEvent`s. It owns
no persistence and no learning logic — the orchestrator wraps those around it.

`run(...)` is an async generator of SSE events; the turn's summary is written into
the passed-in `LoopResult` (async generators can't return a value cleanly), which
the orchestrator reads to build the `TurnOutcome` for learning.observe.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Sequence

from ..core.config import AgentConfig
from ..core.errors import is_transient_provider_error
from ..core.tokens import TokenCalibrator, is_context_length_error
from ..core.types import (
    IMAGES_METADATA_KEY,
    Message,
    SSEEvent,
    ToolResult,
    TurnContext,
    ev_error,
    ev_message,
    ev_plan_updated,
    ev_thinking,
    ev_tool_call,
    ev_tool_result,
)
from ..prompts.planning import PLAN_GUIDANCE, render_plan_status
from .context_manager import (
    ContextRenderStats,
    ConversationContextManager,
    estimate_context_tokens as _pure_ctx_tokens,
)
from .interfaces import IToolDispatcher, TurnView
from .planner import (
    START_PLAN_SPEC,
    UPDATE_PLAN_SPEC,
    PlanOutcome,
    PlanState,
    TurnPlanner,
    build_plan_from_steps,
)
from .profiles import FRONTIER_PROFILE, OrchestrationProfile
from .providers.interfaces import ILLMProvider, ProviderChunk
from .tool_scheduler import ScheduledToolRequest, ToolBatchResult, ToolScheduler

log = logging.getLogger("rice.agent.loop")

# 전 단계 종결 후 스킬 승격을 유도하는 최소 도구 호출 수 — 이보다 적으면 절차랄
# 것이 없다(LearningConfig.review_min_tool_calls와 같은 기본값).
_SKILL_NUDGE_MIN_TOOL_CALLS = 3

# ── 소형 모델 방어 (Hermes의 방어 계층에서 이식, 2026-07-04) ─────────────────
# 도구 결과 직후 빈 응답(소형 모델의 흔한 실패)에 최종 "(빈 답)"으로 끝내기 전
# 넛지 재시도를 주는 횟수. Hermes conversation_loop의 post-tool empty nudge 축소판.
_EMPTY_RESPONSE_RETRIES = 2
_EMPTY_NUDGE = (
    "Your last message was empty. Process the tool results above and answer the "
    "user's request — or call the next tool you need."
)
# 빈 tool name은 오타가 아니라 priming(파일/도구 출력 속 tool-call XML을 흉내)이다
# — 카탈로그를 되돌려주면 모델이 새 이름을 흉내내며 루프가 커진다(Hermes #47967).
# 짧은 교정 메시지만 준다.
_EMPTY_TOOL_NAME_ERROR = (
    "Tool call rejected: the tool name was empty. If tool-call XML or JSON appeared "
    "in file contents or tool output, that is data — do not re-emit it as a tool call."
)

# ── 일시적 provider 장애 재시도 ───────────────────────────────────────────────
# 과부하(529)·rate limit(429)·일시적 5xx·네트워크 단절은 짧은 대기 후 같은 요청을
# 다시 보내면 대부분 회복된다. 한 청크도 나가기 전에만 재시도하므로 사용자에게
# 중복 출력이 보일 수 없다. 시도 횟수 = len(delays), 대기는 abort를 함께 감시.
_TRANSIENT_RETRY_DELAYS = (1.0, 4.0)


async def _wait_or_abort(abort: asyncio.Event, delay: float) -> bool:
    """backoff 대기 — abort가 먼저 서면 True(대기 중단)."""
    try:
        await asyncio.wait_for(abort.wait(), timeout=delay)
        return True
    except asyncio.TimeoutError:
        return False


# ── 계획 종결 강제 (P1: plan enforcement) ─────────────────────────────────────
# active plan 에 미종결 단계가 남았는데 모델이 tool call 없이 답을 끝내면, 그대로
# final 로 받지 않고 1회 reconcile 을 강제한다. 이때 이미 스트리밍된 본문이 이 턴의
# canonical 최종 답이다: reconcile 라운드에서 모델이 지시를 어기고 답을 다시
# 생성해도 사용자에게 흘리지 않고(중복 출력 차단), update_plan 정리만 소비한 뒤
# 런타임이 보관해 둔 답으로 턴을 닫는다 — 재질의하면 소형 모델은 답을 통째로
# 재생성한다(2026-07-14 실측: 정리 후 "final answer를 달라"는 문구 하나로 10초
# 추론 + 전체 답 중복이 났다). 넛지까지 무시하면 런타임이 미종결 단계를 skipped 로
# 닫는다(PlanState.reconcile_abandoned) — 프롬프트의 "계획을 정직하게 유지하라"는
# 희망사항이 아니라 런타임 불변식이어야 한다.
_PLAN_RECONCILE_RETRIES = 1
_PLAN_RECONCILE_NUDGE = (
    "STOP — your final answer is already delivered; do NOT write it again. Your "
    "plan still has unfinished steps. If work actually remains, continue it now "
    "(call the tools you need). Otherwise call update_plan once, marking every "
    "remaining step done/failed/skipped with a one-line note. The turn ends "
    "there — no further text is needed."
)

# NOTE: 사용자용 1줄 preamble 을 런타임이 템플릿으로 합성하던 fallback 은 제거됐다
# (2026-07-07 사용자 결정). 서사는 모델의 몫이다 — BASE_SYSTEM_PROMPT 의 NARRATE
# 지시가 유일한 채널이고, 모델이 침묵한 라운드의 진행 표시는 프론트엔드
# (thinking 애니메이션·도구 카드)가 담당한다. 하드코딩 문장을 사용자에게
# 스트리밍하는 것은 제품 원칙 위반.
#
# 대신 런타임이 하는 것은 **모델-facing 교정**이다(local 프로파일): 서사 없이
# 도구만 부르는 침묵 라운드가 연속되면 synthetic user 넛지로 NARRATE 준수를
# 요구한다 — 사용자에게 문장을 합성하는 게 아니라 모델에게 지시를 상기시킨다.
# 소형 모델은 시스템 프롬프트 서두의 지시 하나로는 서사하지 않는다(실측:
# chat_flow.png, 2026-07-10) — recency 위치의 반복 지시가 준수율을 만든다.
_NARRATION_NUDGE = (
    "Reminder: before EVERY tool call, first write ONE short sentence to the user "
    "(in their language) saying what you're about to do and why. You have been "
    "calling tools silently. Continue the task now — narrate, then act."
)


@dataclass
class LoopResult:
    final_message: str = ""
    history: tuple[Message, ...] = ()
    tool_calls_made: int = 0
    aborted: bool = False
    # 매 LLM 호출의 컨텍스트 렌더 감사 스탯 — orchestrator가 턴 종료 시
    # ledger에 `context_rendered` 이벤트로 영속화한다.
    context_renders: list[ContextRenderStats] = field(default_factory=list)


def _render_tool_result(res: ToolResult) -> str:
    if res.ok:
        return json.dumps(res.result, ensure_ascii=False, default=str)
    return f"ERROR: {res.error}"


class AgentLoop:
    """Implements the #1 conversation loop. Stateless across turns: all per-turn
    state lives in locals + the injected `LoopResult`."""

    def __init__(
        self,
        *,
        provider: ILLMProvider,
        dispatcher: IToolDispatcher,
        temperature: float = 0.7,
        context_manager: ConversationContextManager | None = None,
        token_calibrator: "TokenCalibrator | None" = None,
        planner: TurnPlanner | None = None,
        agent_config: AgentConfig | None = None,
        max_output_tokens: int | None = None,
        profile: OrchestrationProfile | None = None,
        show_thinking: bool = True,
    ) -> None:
        self._provider = provider
        self._dispatcher = dispatcher
        self._temperature = temperature
        # 모델의 reasoning(thinking) 트레이스를 클라이언트로 흘려보낼지(로컬 LLM
        # 전용 토글, orchestrator가 kind로 게이팅). False면 thinking SSE 이벤트와
        # assistant 메타데이터 트레이스를 모두 억제한다 — 모델은 계속 추론하되
        # (도구 사용 품질·소형 모델 안정성 유지) 사용자에게는 노출하지 않는다.
        self._show_thinking = show_thinking
        self._context_manager = context_manager
        # 실측 usage 피드백 + 컨텍스트 길이 초과 시 재압축 재시도용
        self._calibrator = token_calibrator
        # None이면 계획 단계 없이 순수 ReAct(기존 동작·테스트 그대로).
        self._planner = planner
        self._agent_config = agent_config or AgentConfig()
        # 매 반복 생성의 출력 토큰 상한 — None이면 provider 기본값(Claude 4096)에
        # 맡겨 긴 답이 문장 중간에 잘리던 버그가 났다. 오케스트레이터가
        # cfg.provider.max_output_tokens를 주입한다.
        self._max_output_tokens = max_output_tokens
        # 소형 모델 보정 강도(서사 넛지 등). 기본 frontier = 보정 없음(기존 동작).
        self._profile = profile or FRONTIER_PROFILE

    def _render(self, system_prompt: str, messages: list[Message], *, budget_scale: float):
        """이 반복에 보낼 컨텍스트를 렌더한다. budget_scale<1이면 컨텍스트 길이
        초과 후 더 강하게 압축하는 재시도용. context_manager가 없으면 raw 그대로."""
        if self._context_manager is None:
            return None, system_prompt, tuple(messages)
        cm = self._context_manager
        if budget_scale != 1.0:
            # state를 공유한다: 축소 예산에서 새로 뜬 압축 체크포인트가 세션
            # 상태에 남아, 재시도 이후 라운드들도 같은(더 빡빡한) 스냅샷 위에
            # append만 하게 된다 — 컨텍스트 길이 에러의 재발과 프리픽스 재계산을
            # 동시에 줄인다.
            cm = ConversationContextManager(
                cm.config, estimate=cm.estimate_fn, budget_scale=budget_scale, state=cm.state
            )
        rendered = cm.render(system_prompt, tuple(messages))
        return rendered, rendered.system_prompt, rendered.history

    def _calibrate(self, system: str, messages: Sequence[Message], done: ProviderChunk) -> None:
        """provider가 보고한 실제 input_tokens로 토큰 추정 비율을 보정한다.
        보낸 컨텍스트의 *순수* 휴리스틱 추정과 실측을 비교한다.
        best-effort — 캘리브레이션 실패가 턴을 깨선 안 된다."""
        if self._calibrator is None or done.input_tokens is None:
            return
        try:
            pure = _pure_ctx_tokens(system, messages)  # 순수 CJK 휴리스틱 (비보정)
            self._calibrator.observe(pure, done.input_tokens)
        except Exception:  # noqa: BLE001
            log.debug("token calibration observe failed", exc_info=True)

    # ── plan phase helpers ───────────────────────────────────────────────────

    def _effective_max_iterations(self, ctx: TurnContext, plan_state: PlanState | None) -> int:
        """계획이 있으면 반복 예산을 계획 크기에 맞춘다: 단계당 ~6라운드 + 여유.
        상한은 agent.plan_max_iterations — "일을 잘하는" 쪽으로 늘리되 bounded.
        add_steps로 계획이 자라면 예산도 함께 자란다(매 반복 재계산)."""
        if plan_state is None:
            return ctx.max_iterations
        plan_budget = 12 + 6 * len(plan_state.plan.steps)
        return max(ctx.max_iterations, min(plan_budget, self._agent_config.plan_max_iterations))

    def _system_prompt_for(self, ctx: TurnContext, plan_state: PlanState | None) -> str:
        """이 반복의 시스템 프롬프트. 계획이 활성이면 **정적** 작업 지침(PLAN_GUIDANCE)만
        덧붙는다 — 턴 내 불변이라 프리픽스 캐시(Ollama KV·Claude cache_control)가
        살아있다. 반복마다 변하는 계획 *상태*는 시스템이 아니라 히스토리 끝의
        ephemeral 메시지로 실린다(_plan_status_message): 이전 설계는 상태를 시스템에
        렌더해 매 반복 전체 컨텍스트를 re-prefill시켰다(로컬 LLM 최대 비용 누수)."""
        if plan_state is None:
            return ctx.system_prompt
        return f"{ctx.system_prompt}\n\n{PLAN_GUIDANCE}"

    def _plan_status_message(self, plan_state: PlanState, tool_calls_made: int) -> Message:
        """살아있는 계획 상태를 담은 ephemeral tail 메시지(스냅샷 프롬프팅).

        durable 히스토리에 넣지 않고 렌더 직전 사본에만 덧붙인다 — 매 호출 최신
        전체 상태를 다시 싣되(로컬 LLM은 대화 중반의 계획을 잊는다), 마지막 메시지
        위치라 recency 주목도가 가장 높고 캐시는 tail 재계산만으로 유지된다.
        role="user"인 이유: 히스토리 중간 system은 Claude wire에서 떨어져 나가고,
        연속 user 메시지는 API가 한 턴으로 합쳐 허용된다."""
        return Message(
            role="user",
            content=render_plan_status(
                plan_state.plan,
                rounds_without_update=plan_state.rounds_without_update,
                drift_guard_rounds=self._agent_config.plan_drift_guard_rounds,
                nudge_skill_capture=(
                    plan_state.all_terminal and tool_calls_made >= _SKILL_NUDGE_MIN_TOOL_CALLS
                ),
            ),
            metadata={"synthetic": "plan_status", "ephemeral": True},
        )

    async def run(self, ctx: TurnContext, session_id: str, result: LoopResult, abort: asyncio.Event):
        tool_index = {t.name: t for t in ctx.tools}
        messages: list[Message] = list(ctx.history)
        tool_calls_made = 0
        final_text = ""
        # 이 턴의 사용자 요청 — 플래너와 TurnView(서브에이전트 라이브 컨텍스트)가 공유.
        user_message = next((m.content for m in reversed(messages) if m.role == "user"), "")

        # ── PLAN: 의도 파악 + 필요 시 계획 수립 (fail-soft — 실패해도 턴 진행) ──
        plan_state: PlanState | None = None
        # 계획 생성 스냅샷은 이 턴의 첫 어시스턴트 메시지 metadata에 실어 영속화한다
        # (합성 assistant 메시지를 히스토리에 끼우면 프로바이더 wire가 오염된다 —
        # trailing-assistant는 prefill로 해석하는 API가 있다).
        pending_plan_meta: dict | None = None
        if self._planner is not None and ctx.tools and not abort.is_set():
            if user_message.strip():
                outcome = PlanOutcome()
                async for ev in self._planner.run(ctx, user_message, outcome, abort):
                    yield ev
                if outcome.plan is not None and not abort.is_set():
                    plan_state = PlanState(outcome.plan)
                    pending_plan_meta = {"plan": plan_state.plan.to_dict()}
                    yield ev_plan_updated(plan_state.plan)
                    log.info(
                        "turn planned: %d steps (session %s) — %s",
                        len(plan_state.plan.steps), session_id, plan_state.plan.goal,
                    )

        # ── EXECUTE: ReAct 루프 (계획이 있으면 계획을 작업하며) ─────────────────
        iteration = 0
        empty_retries = 0
        plan_reconcile_nudges = 0
        # reconcile 모드: 미종결 계획인 채 최종 답이 이미 스트리밍됐을 때 그 답을
        # 보관한다. None이 아닌 동안 이후 라운드의 본문은 사용자에게 흘리지 않고
        # (중복 답 재생성 차단), 계획 정리(update_plan)만 소비한 뒤 런타임이 이
        # 텍스트로 턴을 닫는다. 실제 작업 도구가 다시 불리면 조기 final이었던
        # 것이므로 해제하고 정상 흐름으로 복귀한다.
        reconcile_final_text: str | None = None
        # 서사(NARRATE) 준수 추적 — 본문 없이 실제 도구만 부른 연속 라운드 수와
        # 이 턴에 이미 쓴 넛지 수(프로파일 상한, local만 활성).
        silent_tool_rounds = 0
        narration_nudges = 0
        while True:
            if iteration >= self._effective_max_iterations(ctx, plan_state):
                # budget exhausted without a clean stop
                yield ev_error(
                    "max_iterations",
                    f"turn exceeded {self._effective_max_iterations(ctx, plan_state)} iterations",
                )
                break
            iteration += 1
            if abort.is_set():
                result.aborted = True
                break

            # 계획이 있으면 update_plan(진행 보고)을, 없고 도구가 있으면 start_plan
            # (ReAct 도중 계획으로 승격하는 escalation trigger)을 광고한다. 둘은 상호
            # 배타 — start_plan은 plan_state가 없을 때만 존재하므로 한 번 승격하면
            # 사라진다(이중 계획 불가). 순수 대화(도구 없음)엔 둘 다 붙이지 않는다.
            if plan_state is not None:
                iteration_tools = ctx.tools + (UPDATE_PLAN_SPEC,)
            elif ctx.tools:
                iteration_tools = ctx.tools + (START_PLAN_SPEC,)
            else:
                iteration_tools = ctx.tools
            iteration_system = self._system_prompt_for(ctx, plan_state)
            # 살아있는 계획 상태는 렌더 **사본**의 tail에만 싣는다 — durable 히스토리를
            # 오염시키지 않으면서 컨텍스트 예산·recent 보호(컨텍스트 매니저)는 그대로
            # 적용받고, 시스템 프롬프트는 턴 내 불변으로 남는다(프리픽스 캐시 유지).
            render_messages = list(messages)
            if plan_state is not None:
                render_messages.append(self._plan_status_message(plan_state, tool_calls_made))

            assistant_parts: list[str] = []
            thinking_parts: list[str] = []
            pending_calls = []

            # 스트림 실패 시 두 갈래의 fail-soft 재시도. 두 에러 모두 요청 진입
            # 시점에 나므로(스트림 델타 전) 한 청크도 내보내기 전에만 재시도한다 —
            # 청크가 이미 나갔다면 사용자에게 부분 답이 보였으니 그대로 전파한다.
            #   ① 컨텍스트 길이 초과 → 더 강한 압축(budget 0.45)으로 1회 재렌더.
            #   ② 일시적 provider 장애(429/529/5xx/네트워크) → 같은 컨텍스트로
            #      backoff 후 재시도. 과부하·rate limit 하나가 턴 전체를 죽이던
            #      실패를 회복 가능하게 한다. 대기는 abort를 함께 감시한다.
            budget_scale = 1.0
            compress_retries_left = 1
            transient_retries_left = len(_TRANSIENT_RETRY_DELAYS)
            while True:
                rendered_context, provider_system, provider_messages = self._render(
                    iteration_system, render_messages, budget_scale=budget_scale
                )
                produced_any = False
                try:
                    async for chunk in self._provider.stream(
                        system=provider_system,
                        messages=provider_messages,
                        tools=iteration_tools,
                        temperature=self._temperature,
                        max_tokens=self._max_output_tokens,
                    ):
                        produced_any = True
                        if abort.is_set():
                            # interrupt mid-stream, not only between iterations (C-3)
                            result.aborted = True
                            break
                        if chunk.kind == "thinking" and chunk.text:
                            thinking_parts.append(chunk.text)
                            # 스트림 노출이 꺼져 있으면(로컬 + show_thinking=False)
                            # thinking 이벤트를 클라이언트로 보내지 않는다. parts는
                            # 계속 모은다 — 아래 빈-답변 방어(도구만/침묵 라운드 판정)에
                            # 여전히 필요하기 때문. 억제는 방출 지점에서만 한다.
                            if self._show_thinking:
                                yield ev_thinking(chunk.text)
                        elif chunk.kind == "text" and chunk.text:
                            assistant_parts.append(chunk.text)
                            # reconcile 모드의 본문은 일단 버퍼만 한다 — 라운드가
                            # 끝나야 재작성(억제)인지 작업 재개(방출)인지 판별된다.
                            if reconcile_final_text is None:
                                yield ev_message(delta=chunk.text)
                        elif chunk.kind == "tool_call" and chunk.tool_call is not None:
                            pending_calls.append(chunk.tool_call)
                        elif chunk.kind == "done":
                            self._calibrate(provider_system, provider_messages, chunk)
                    break  # stream consumed cleanly
                except Exception as exc:  # noqa: BLE001
                    if produced_any or abort.is_set():
                        raise
                    # ①은 ②보다 먼저 — 컨텍스트 초과 문구가 transient 마커와
                    # 겹치는 provider가 있어도 압축 재시도가 우선한다.
                    if (
                        compress_retries_left > 0
                        and self._context_manager is not None
                        and is_context_length_error(exc)
                    ):
                        compress_retries_left -= 1
                        budget_scale = 0.45
                        log.warning("context length exceeded — retrying with tighter budget (%s)", exc)
                    elif transient_retries_left > 0 and is_transient_provider_error(exc):
                        delay = _TRANSIENT_RETRY_DELAYS[-transient_retries_left]
                        transient_retries_left -= 1
                        log.warning("transient provider error — retrying in %.1fs (%s)", delay, exc)
                        if await _wait_or_abort(abort, delay):
                            # 대기 중 사용자가 턴을 중단 — 재시도 대신 조용히 접는다.
                            result.aborted = True
                            break
                    else:
                        raise
                    assistant_parts.clear()
                    thinking_parts.clear()
                    pending_calls.clear()

            if rendered_context is not None:
                result.context_renders.append(rendered_context.stats)

            if abort.is_set():
                break  # stop without emitting a partial answer as final

            assistant_text = "".join(assistant_parts)
            # ── reconcile 모드 판별: 확정된 답 이후의 라운드다 ────────────────────
            # 실제 작업 도구가 있으면 그 "final"은 조기 종료였던 것 — 버퍼해 둔 이번
            # 라운드 서사를 지금 흘리고 정상 흐름으로 복귀한다. 계획 정리 호출뿐이거나
            # 도구가 없으면 본문은 이미 전달된 답의 재작성이므로 사용자에게도 durable
            # 히스토리 content에도 싣지 않는다(리로드가 라이브와 같은 그림을 그리도록
            # metadata로만 보관).
            suppress_round_text = False
            if reconcile_final_text is not None:
                resumed_real_work = any(
                    c.tool_name not in (UPDATE_PLAN_SPEC.name, START_PLAN_SPEC.name) for c in pending_calls
                )
                if resumed_real_work:
                    if assistant_text.strip():
                        yield ev_message(delta=assistant_text)
                    reconcile_final_text = None
                else:
                    suppress_round_text = True
            # Stash this round's reasoning on the assistant message so it persists
            # for trace reconstruction on reload. Providers ignore metadata when
            # assembling history, so the model never re-sees stale thinking.
            # show_thinking=False면 트레이스를 히스토리에도 남기지 않는다 — 리로드
            # 시 assistant 메타데이터로도 클라이언트에 건너가지 않도록(백엔드가
            # 노출 여부의 단일 권위). 라이브 억제(위)와 짝을 이룬다.
            thinking_text = "".join(thinking_parts) if self._show_thinking else ""
            metadata: dict = {"thinking": thinking_text} if thinking_text else {}
            if suppress_round_text and assistant_text:
                metadata["suppressed_text"] = assistant_text
            if pending_plan_meta is not None:
                # 계획 생성 스냅샷은 턴의 첫 어시스턴트 메시지에 1회 부착(리로드 복원용).
                metadata.update(pending_plan_meta)
                pending_plan_meta = None
            messages.append(
                Message(
                    role="assistant",
                    content="" if suppress_round_text else assistant_text,
                    tool_calls=tuple(pending_calls),
                    metadata=metadata,
                )
            )

            # No tool calls → this is the final answer for the turn.
            if not pending_calls:
                # 소형 모델 방어: 도구를 이미 썼는데 본문 없이 끝났다(또는 thinking만
                # 있다) — 빈 답을 확정하기 전에 넛지를 주고 재시도한다. 순수 대화
                # 첫 턴(tool_calls_made==0)의 빈 응답은 provider 문제라 그대로 둔다.
                # (reconcile 모드에선 빈 응답 방어를 건너뛴다 — 답은 이미 확정됐고,
                # 여기서 받아낼 것은 계획 정리뿐이다.)
                if (
                    not assistant_text.strip()
                    and tool_calls_made > 0
                    and empty_retries < _EMPTY_RESPONSE_RETRIES
                    and reconcile_final_text is None
                ):
                    empty_retries += 1
                    log.warning("empty final answer after tool use — nudging (retry %d)", empty_retries)
                    messages.append(Message(role="user", content=_EMPTY_NUDGE, metadata={"synthetic": "empty_nudge"}))
                    continue
                # ── 계획 종결 강제: 미종결 단계가 남은 채 final 로 끝나려 하면 1회
                # reconcile 을 강제하고, 그래도 무시하면 런타임이 skipped 로 닫는다
                # (계획 카드가 영원히 열린 채 남는 상태를 런타임 불변식으로 차단).
                if plan_state is not None and not plan_state.all_terminal:
                    if plan_reconcile_nudges < _PLAN_RECONCILE_RETRIES:
                        plan_reconcile_nudges += 1
                        log.warning(
                            "final answer with unfinished plan — forcing reconciliation (nudge %d)",
                            plan_reconcile_nudges,
                        )
                        if assistant_text.strip():
                            # 이 본문이 이 턴의 canonical 최종 답이다 — authoritative
                            # content 이벤트로 지금 확정해 클라이언트가 버블을 봉인하게
                            # 한다. 이후 라운드는 계획 정리만 소비하고 런타임이 이
                            # 답으로 턴을 닫는다(모델에게 답을 재요구하지 않는다).
                            reconcile_final_text = assistant_text
                            yield ev_message(content=assistant_text)
                        messages.append(
                            Message(role="user", content=_PLAN_RECONCILE_NUDGE, metadata={"synthetic": "plan_reconcile"})
                        )
                        continue
                    log.warning("plan still unfinished after reconcile nudge — auto-closing remaining steps as skipped")
                    plan_state.reconcile_abandoned()
                    yield ev_plan_updated(plan_state.plan)
                    # 강제 종결 스냅샷을 마지막 어시스턴트 메시지 metadata에도 싣는다 —
                    # 리로드가 "진행 중처럼 보이는" 이전 스냅샷으로 복원되던 한계
                    # (AGENT_EXECUTION §3.4의 알려진 한계) 해소. metadata dict는
                    # frozen Message에서도 가변이라 append 이후 부착이 안전하다.
                    messages[-1].metadata["plan"] = plan_state.plan.to_dict()
                if reconcile_final_text is not None:
                    # 답은 넛지 시점에 이미 확정·전송됐다 — 재전송하면 클라이언트에
                    # 중복 버블이 생긴다. 이번 라운드의 재작성 본문은 위에서 억제됐다.
                    final_text = reconcile_final_text
                else:
                    final_text = assistant_text
                    yield ev_message(content=assistant_text)
                break

            # Dispatch requested tools through the scheduler. Safe independent
            # calls can run in parallel; destructive/approval-gated calls remain
            # ordered. Results are appended in model-call order.
            scheduled: list[ScheduledToolRequest] = []
            for call in pending_calls:
                # ── start_plan 인터셉트: ReAct 도중 계획으로 승격(#2 lazy escalation).
                # plan_state가 없을 때만 광고·유효하다. 검증(≥2 distinct steps)을
                # 통과하면 plan_state를 세우고 스냅샷을 SSE(plan_updated)와 tool
                # metadata(plan)로 흘려 리로드가 같은 지점에 카드를 복원하게 하고,
                # 다음 반복부터 update_plan 광고 + 계획 블록 렌더로 전환된다. 승격
                # 실패(1단계 등, 분해 너비 부족)는 "그냥 계속 일하라" 넛지로 되돌린다.
                if plan_state is None and call.tool_name == START_PLAN_SPEC.name:
                    yield ev_tool_call(call)
                    new_plan = build_plan_from_steps(call.arguments.get("goal"), call.arguments.get("steps"))
                    if new_plan is None:
                        res = ToolResult(
                            call.id,
                            ok=False,
                            error=(
                                "start_plan needs a goal and 2+ distinct steps. If this is really "
                                "one deliverable, don't plan — keep working and verify your result."
                            ),
                        )
                        yield ev_tool_result(res)
                        messages.append(
                            Message(
                                role="tool",
                                content=_render_tool_result(res),
                                metadata={"tool_call_id": call.id, "tool_source": "builtin", "tool_name": call.tool_name},
                            )
                        )
                        tool_calls_made += 1
                        continue
                    plan_state = PlanState(new_plan)
                    yield ev_plan_updated(plan_state.plan)
                    res = ToolResult(call.id, ok=True, result={"ok": True, "plan": plan_state.plan.to_dict()})
                    yield ev_tool_result(res)
                    messages.append(
                        Message(
                            role="tool",
                            content=_render_tool_result(res),
                            metadata={
                                "tool_call_id": call.id,
                                "tool_source": "builtin",
                                "tool_name": call.tool_name,
                                "plan": plan_state.plan.to_dict(),
                            },
                        )
                    )
                    tool_calls_made += 1
                    log.info(
                        "turn escalated to plan mid-flight: %d steps (session %s) — %s",
                        len(plan_state.plan.steps), session_id, plan_state.plan.goal,
                    )
                    continue
                # ── update_plan 인터셉트: 계획 상태는 턴-로컬이다 — dispatcher를
                # 태우지 않고 여기서 적용한다(외부 실행·승인·영속 부작용 없음).
                # 갱신마다 전체 스냅샷을 SSE로 흘리고, tool 메시지 metadata에도 실어
                # 리로드가 같은 카드를 복원하게 한다.
                if plan_state is not None and call.tool_name == UPDATE_PLAN_SPEC.name:
                    yield ev_tool_call(call)
                    payload = plan_state.apply(call.arguments)
                    yield ev_plan_updated(plan_state.plan)
                    res = ToolResult(call.id, ok=True, result=payload)
                    yield ev_tool_result(res)
                    messages.append(
                        Message(
                            role="tool",
                            content=_render_tool_result(res),
                            metadata={
                                "tool_call_id": call.id,
                                "tool_source": "builtin",  # 내부 상태 갱신 — 외부 콘텐츠 아님
                                "tool_name": call.tool_name,
                                "plan": plan_state.plan.to_dict(),
                            },
                        )
                    )
                    tool_calls_made += 1
                    continue
                spec = tool_index.get(call.tool_name)
                if spec is None:
                    # 미지의 도구도 SSE로 서피싱한다(이전엔 모델만 에러를 보고 UI/
                    # 지표에는 보이지 않았다 — 감사 가능성 P0와 같은 원칙). tool
                    # 메시지에 tool_call_id 메타데이터가 없으면 컨텍스트 압축의
                    # wire repair가 이 라운드를 통째로 떨궈버리므로 반드시 싣는다.
                    error = (
                        _EMPTY_TOOL_NAME_ERROR
                        if not call.tool_name.strip()
                        else f"unknown tool: {call.tool_name}"
                    )
                    res = ToolResult(call.id, ok=False, error=error)
                    yield ev_tool_call(call)
                    yield ev_tool_result(res)
                    messages.append(
                        Message(role="tool", content=_render_tool_result(res), metadata={"tool_call_id": call.id})
                    )
                    tool_calls_made += 1
                    continue
                scheduled.append(ScheduledToolRequest(call=call, spec=spec))

            if scheduled:
                # 이번 턴의 라이브 스냅샷을 dispatcher에 갱신 — transcript는 턴 종료에야
                # 영속화되므로, 이것 없이는 delegate_subagent 워커가 이번 턴의 도구
                # 관찰·계획을 전혀 못 본다(컨텍스트 주입 공백). duck-typed optional:
                # 테스트 fake dispatcher는 구현하지 않아도 된다.
                set_view = getattr(self._dispatcher, "set_turn_view", None)
                if set_view is not None:
                    set_view(
                        TurnView(
                            user_message=user_message,
                            plan=plan_state.plan if plan_state is not None else None,
                            turn_messages=tuple(messages[len(ctx.history):]),
                        )
                    )
                batch = ToolBatchResult()
                scheduler = ToolScheduler(self._dispatcher)
                async for ev in scheduler.schedule(session_id, scheduled, batch, abort):
                    yield ev
                for req, res in zip(scheduled, batch.ordered([req.call for req in scheduled]), strict=True):
                    call = req.call
                    # tool_source: 이 결과가 외부 콘텐츠(connector/plugin/bridge =
                    # Gmail·웹·원격 도구)에서 왔는지 표시한다. 학습 리뷰가 메모리를
                    # 격리할지 판단하는 근거(메모리 오염 방지).
                    tool_metadata: dict = {
                        "tool_call_id": call.id,
                        "tool_source": req.spec.source,
                        "tool_name": call.tool_name,
                    }
                    # 이미지 첨부(core.types 계약): metadata로 나르면 transcript
                    # JSON에 그대로 영속되고, provider 어댑터가 wire 이미지 블록으로
                    # 렌더한다 — base64는 텍스트 본문에 실리지 않는다.
                    if res.images:
                        tool_metadata[IMAGES_METADATA_KEY] = list(res.images)
                    messages.append(
                        Message(role="tool", content=_render_tool_result(res), metadata=tool_metadata)
                    )
                    tool_calls_made += 1

            # ── reconcile 종결: 확정된 답 이후, 계획 정리 호출만으로 끝난 라운드 —
            # 추가 LLM 라운드를 돌리지 않고 여기서 턴을 닫는다(재질의하면 소형 모델은
            # 답을 통째로 재생성한다). 모델이 일부 단계만 닫았다면 나머지는 런타임이
            # skipped 로 정직하게 정리한다 — reconcile 기회는 이미 소진됐다.
            if reconcile_final_text is not None:
                if plan_state is not None and not plan_state.all_terminal:
                    log.warning("reconcile round left steps open — auto-closing remainder as skipped")
                    plan_state.reconcile_abandoned()
                    yield ev_plan_updated(plan_state.plan)
                    messages[-1].metadata["plan"] = plan_state.plan.to_dict()
                final_text = reconcile_final_text
                break

            # ── ReAct drift guard 카운터: 도구 라운드가 계획 보고 없이 지나가면
            # 누적 — 임계 도달 시 다음 반복 프롬프트가 재평가를 강제한다. 계획 관련
            # 호출(update_plan·start_plan)이 하나라도 있으면 그 라운드는 "보고"다.
            made_plan_report = any(
                c.tool_name in (UPDATE_PLAN_SPEC.name, START_PLAN_SPEC.name) for c in pending_calls
            )
            has_real_tool_call = any(
                c.tool_name not in (UPDATE_PLAN_SPEC.name, START_PLAN_SPEC.name)
                for c in pending_calls
            )
            if plan_state is not None and not made_plan_report:
                plan_state.note_tool_round()

            # ── 서사(NARRATE) 넛지: 본문 없이 실제 도구만 부른 라운드가 연속되면
            # synthetic user 넛지 1회로 교정한다(local 프로파일만, 턴당 상한).
            # plan 메타 호출만 있는 라운드는 계획 카드가 보이므로 침묵으로 안 센다.
            if assistant_text.strip():
                silent_tool_rounds = 0
            elif has_real_tool_call:
                silent_tool_rounds += 1
                if (
                    self._profile.narration_nudge_after > 0
                    and silent_tool_rounds >= self._profile.narration_nudge_after
                    and narration_nudges < self._profile.narration_nudge_max
                ):
                    narration_nudges += 1
                    silent_tool_rounds = 0
                    log.info("silent tool rounds — injecting narration nudge (%d)", narration_nudges)
                    messages.append(
                        Message(role="user", content=_NARRATION_NUDGE, metadata={"synthetic": "narration_nudge"})
                    )
            # loop continues: provider sees the tool results next iteration

        result.final_message = final_text
        result.history = tuple(messages)
        result.tool_calls_made = tool_calls_made
