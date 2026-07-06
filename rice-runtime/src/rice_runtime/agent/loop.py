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
from ..core.tokens import TokenCalibrator, is_context_length_error
from ..core.types import (
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
from ..prompts.planning import render_plan_block
from .context_manager import (
    ContextRenderStats,
    ConversationContextManager,
    estimate_context_tokens as _pure_ctx_tokens,
)
from .interfaces import IToolDispatcher
from .planner import UPDATE_PLAN_SPEC, PlanOutcome, PlanState, TurnPlanner
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
    ) -> None:
        self._provider = provider
        self._dispatcher = dispatcher
        self._temperature = temperature
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

    def _render(self, system_prompt: str, messages: list[Message], *, budget_scale: float):
        """이 반복에 보낼 컨텍스트를 렌더한다. budget_scale<1이면 컨텍스트 길이
        초과 후 더 강하게 압축하는 재시도용. context_manager가 없으면 raw 그대로."""
        if self._context_manager is None:
            return None, system_prompt, tuple(messages)
        cm = self._context_manager
        if budget_scale != 1.0:
            cm = ConversationContextManager(
                cm.config, estimate=cm.estimate_fn, budget_scale=budget_scale
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

    def _system_prompt_for(self, ctx: TurnContext, plan_state: PlanState | None, tool_calls_made: int) -> str:
        """계획이 있으면 시스템 프롬프트 뒤에 살아있는 계획 블록을 덧붙인다 —
        매 반복 전체 상태를 다시 싣는다(토큰 절약보다 수행 품질, AgentConfig 참고)."""
        if plan_state is None:
            return ctx.system_prompt
        block = render_plan_block(
            plan_state.plan,
            rounds_without_update=plan_state.rounds_without_update,
            drift_guard_rounds=self._agent_config.plan_drift_guard_rounds,
            nudge_skill_capture=(
                plan_state.all_terminal and tool_calls_made >= _SKILL_NUDGE_MIN_TOOL_CALLS
            ),
        )
        return f"{ctx.system_prompt}\n\n{block}"

    async def run(self, ctx: TurnContext, session_id: str, result: LoopResult, abort: asyncio.Event):
        tool_index = {t.name: t for t in ctx.tools}
        messages: list[Message] = list(ctx.history)
        tool_calls_made = 0
        final_text = ""

        # ── PLAN: 의도 파악 + 필요 시 계획 수립 (fail-soft — 실패해도 턴 진행) ──
        plan_state: PlanState | None = None
        # 계획 생성 스냅샷은 이 턴의 첫 어시스턴트 메시지 metadata에 실어 영속화한다
        # (합성 assistant 메시지를 히스토리에 끼우면 프로바이더 wire가 오염된다 —
        # trailing-assistant는 prefill로 해석하는 API가 있다).
        pending_plan_meta: dict | None = None
        if self._planner is not None and ctx.tools and not abort.is_set():
            user_message = next((m.content for m in reversed(messages) if m.role == "user"), "")
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

            # update_plan은 계획이 있는 동안만 광고한다(계획 없는 턴에 노이즈 금지).
            iteration_tools = ctx.tools + (UPDATE_PLAN_SPEC,) if plan_state is not None else ctx.tools
            iteration_system = self._system_prompt_for(ctx, plan_state, tool_calls_made)

            assistant_parts: list[str] = []
            thinking_parts: list[str] = []
            pending_calls = []

            # 컨텍스트 길이 초과 시 더 강한 압축으로 1회 재시도한다.
            # 초과 에러는 요청 진입 시점에 나므로(스트림 델타 전) 한 청크도 내보내기
            # 전에 안전하게 재렌더할 수 있다. 재시도 후에도 실패하면 상위로 전파.
            for attempt, budget_scale in enumerate((1.0, 0.45)):
                rendered_context, provider_system, provider_messages = self._render(
                    iteration_system, messages, budget_scale=budget_scale
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
                            yield ev_thinking(chunk.text)
                        elif chunk.kind == "text" and chunk.text:
                            assistant_parts.append(chunk.text)
                            yield ev_message(delta=chunk.text)
                        elif chunk.kind == "tool_call" and chunk.tool_call is not None:
                            pending_calls.append(chunk.tool_call)
                        elif chunk.kind == "done":
                            self._calibrate(provider_system, provider_messages, chunk)
                    break  # stream consumed cleanly
                except Exception as exc:  # noqa: BLE001
                    can_retry = (
                        attempt == 0
                        and not produced_any
                        and self._context_manager is not None
                        and is_context_length_error(exc)
                    )
                    if not can_retry:
                        raise
                    log.warning("context length exceeded — retrying with tighter budget (%s)", exc)
                    assistant_parts.clear()
                    thinking_parts.clear()
                    pending_calls.clear()

            if rendered_context is not None:
                result.context_renders.append(rendered_context.stats)

            if abort.is_set():
                break  # stop without emitting a partial answer as final

            assistant_text = "".join(assistant_parts)
            # Stash this round's reasoning on the assistant message so it persists
            # for trace reconstruction on reload. Providers ignore metadata when
            # assembling history, so the model never re-sees stale thinking.
            thinking_text = "".join(thinking_parts)
            metadata: dict = {"thinking": thinking_text} if thinking_text else {}
            if pending_plan_meta is not None:
                # 계획 생성 스냅샷은 턴의 첫 어시스턴트 메시지에 1회 부착(리로드 복원용).
                metadata.update(pending_plan_meta)
                pending_plan_meta = None
            messages.append(
                Message(role="assistant", content=assistant_text, tool_calls=tuple(pending_calls), metadata=metadata)
            )

            # No tool calls → this is the final answer for the turn.
            if not pending_calls:
                # 소형 모델 방어: 도구를 이미 썼는데 본문 없이 끝났다(또는 thinking만
                # 있다) — 빈 답을 확정하기 전에 넛지를 주고 재시도한다. 순수 대화
                # 첫 턴(tool_calls_made==0)의 빈 응답은 provider 문제라 그대로 둔다.
                if not assistant_text.strip() and tool_calls_made > 0 and empty_retries < _EMPTY_RESPONSE_RETRIES:
                    empty_retries += 1
                    log.warning("empty final answer after tool use — nudging (retry %d)", empty_retries)
                    messages.append(Message(role="user", content=_EMPTY_NUDGE, metadata={"synthetic": "empty_nudge"}))
                    continue
                final_text = assistant_text
                yield ev_message(content=assistant_text)
                break

            # Dispatch requested tools through the scheduler. Safe independent
            # calls can run in parallel; destructive/approval-gated calls remain
            # ordered. Results are appended in model-call order.
            scheduled: list[ScheduledToolRequest] = []
            for call in pending_calls:
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
                batch = ToolBatchResult()
                scheduler = ToolScheduler(self._dispatcher)
                async for ev in scheduler.schedule(session_id, scheduled, batch, abort):
                    yield ev
                for req, res in zip(scheduled, batch.ordered([req.call for req in scheduled]), strict=True):
                    call = req.call
                    # tool_source: 이 결과가 외부 콘텐츠(connector/plugin/bridge =
                    # Gmail·웹·원격 도구)에서 왔는지 표시한다. 학습 리뷰가 메모리를
                    # 격리할지 판단하는 근거(메모리 오염 방지).
                    messages.append(
                        Message(
                            role="tool",
                            content=_render_tool_result(res),
                            metadata={"tool_call_id": call.id, "tool_source": req.spec.source, "tool_name": call.tool_name},
                        )
                    )
                    tool_calls_made += 1

            # ── ReAct drift guard 카운터: 실제 도구 라운드가 계획 보고 없이
            # 지나가면 누적 — 임계 도달 시 다음 반복 프롬프트가 재평가를 강제한다.
            if plan_state is not None and not any(c.tool_name == UPDATE_PLAN_SPEC.name for c in pending_calls):
                plan_state.note_tool_round()
            # loop continues: provider sees the tool results next iteration

        result.final_message = final_text
        result.history = tuple(messages)
        result.tool_calls_made = tool_calls_made
