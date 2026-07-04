"""Turn planning — intent triage + plan authoring + live plan state.

Plan-and-execute × ReAct의 실행 절반(계획 상태 기계)이 여기 있다. 프롬프트 텍스트는
prompts/planning.py, 계약 타입(TurnPlan/PlanStep)은 core.types — 이 모듈은 그 둘을
잇는 행동을 소유한다:

  TurnPlanner  — 턴 시작 시 1회 LLM 호출로 의도를 해석한다. 단순 질의면 None
                 (루프는 이전과 동일한 순수 ReAct로 진행), 복합 질의면 TurnPlan.
                 스트리밍 thinking을 SSE로 그대로 흘려 "계획 중" 이 보이게 한다.
                 실패는 전부 None(fail-soft) — 계획이 턴을 깨는 일은 없다.
  PlanState    — 실행 중의 가변 계획 홀더. 모델이 `update_plan`(루프가 가로채는
                 가상 빌트인)으로 단계 전이/수정을 보고하면 새 revision의 불변
                 TurnPlan 스냅샷으로 교체한다. 전 단계가 종결되면 자동으로
                 status="completed" — 루프가 스킬 승격 유도를 켜는 신호.

update_plan이 dispatcher가 아니라 루프 인터셉트인 이유: 계획은 턴-로컬 상태다.
외부 실행도, 승인도, 영속 부작용도 없다 — dispatcher를 태우면 무해한 상태 갱신이
도구 실행 파이프라인(승인/스케줄링/결과 스태싱)을 공짜로 통과하는 척하게 된다.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import AsyncIterator

from ..core.config import AgentConfig
from ..core.model_json import extract_json_object
from ..core.types import (
    Message,
    PlanStep,
    SSEEvent,
    ToolSpec,
    TurnContext,
    TurnPlan,
    ev_thinking,
)
from ..prompts.planning import PLANNING_TEMPERATURE, build_planning_messages
from .providers.interfaces import ILLMProvider

log = logging.getLogger("rice.agent.planner")

# 코드 수준 안전 상한 — 프롬프트가 plan_max_steps를 지시하지만 모델이 어기면
# 여기서 자른다(계획이 무한히 길어져 매 반복 프롬프트를 폭파하지 않게).
_HARD_MAX_STEPS = 12
# 플래너 출력 수집 상한(자) — JSON 계획 하나에 충분하고도 남는다.
_MAX_PLAN_CHARS = 8_000

_VALID_STEP_STATUS = ("pending", "in_progress", "done", "failed", "skipped")
_TERMINAL = ("done", "failed", "skipped")


# ── update_plan — 루프가 가로채는 가상 빌트인의 스키마 ─────────────────────────
UPDATE_PLAN_SPEC = ToolSpec(
    name="update_plan",
    description=(
        "Report progress on the active plan or revise it. Call this whenever you "
        "start a step (in_progress), finish one (done, with a one-line note of the "
        "outcome), hit a dead end (failed), or when observations changed what the "
        "plan should be (add_steps / skip obsolete steps). Keeping the plan honest "
        "is part of the task — the user watches it live."
    ),
    parameters={
        "type": "object",
        "properties": {
            "updates": {
                "type": "array",
                "description": "Status transitions for existing steps.",
                "items": {
                    "type": "object",
                    "properties": {
                        "step_id": {"type": "string", "description": "e.g. 's2'"},
                        "status": {"type": "string", "enum": list(_VALID_STEP_STATUS)},
                        "note": {"type": "string", "description": "One-line outcome/observation."},
                    },
                    "required": ["step_id", "status"],
                },
            },
            "add_steps": {
                "type": "array",
                "description": "New steps appended to the plan (when observations demand more work).",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "detail": {"type": "string"},
                    },
                    "required": ["title"],
                },
            },
            "reason": {"type": "string", "description": "One line: why the plan changed (for revisions)."},
        },
    },
    source="builtin",
    requires_approval=False,
)


@dataclass
class PlanOutcome:
    """TurnPlanner.run의 결과 홀더 — async generator는 값을 반환할 수 없어
    LoopResult와 같은 관례를 쓴다."""

    plan: TurnPlan | None = None


class TurnPlanner:
    """의도 파악 + 계획 수립 1회 호출. 상태 없음 — 턴마다 새로 만들어도, 재사용해도
    무방하다. provider는 메인 루프와 같은 인스턴스를 쓴다(로컬 LLM 하나가 플래너와
    실행자를 겸한다 — 별도 모델 배선은 필요해질 때 넓힌다)."""

    def __init__(self, *, provider: ILLMProvider, config: AgentConfig) -> None:
        self._provider = provider
        self._config = config

    async def run(
        self, ctx: TurnContext, user_message: str, outcome: PlanOutcome, abort: asyncio.Event
    ) -> AsyncIterator[SSEEvent]:
        """플래너 호출을 스트리밍한다: thinking 델타는 SSE로 그대로 통과(사용자가
        '계획 중'을 본다), 텍스트는 모아 JSON 파싱. 어떤 실패든 outcome.plan=None."""
        # echo(모델 미설정 스켈레톤)는 프롬프트를 되돌려줄 뿐 계획을 세울 수 없다
        # — titler와 같은 스킵(agent/titler.py 참조).
        if getattr(self._provider, "name", "") == "echo":
            return
        recent = ctx.history[-max(1, self._config.planner_recent_messages) :]
        # history 꼬리가 방금 들어온 user 메시지면 문맥에서 제외(요청 섹션과 중복).
        if recent and recent[-1].role == "user" and recent[-1].content == user_message:
            recent = recent[:-1]
        system, messages = build_planning_messages(
            user_message=user_message,
            recent_history=recent,
            tools=ctx.tools,
            skill_names=[s.name for s in ctx.injected_skills],
            max_steps=self._config.plan_max_steps,
        )
        chunks: list[str] = []
        try:
            async with asyncio.timeout(max(10, self._config.planner_timeout_seconds)):
                async for chunk in self._provider.stream(
                    system=system, messages=messages, tools=(), temperature=PLANNING_TEMPERATURE
                ):
                    if abort.is_set():
                        return
                    if chunk.kind == "thinking" and chunk.text:
                        yield ev_thinking(chunk.text)
                    elif chunk.kind == "text" and chunk.text:
                        chunks.append(chunk.text)
                        if sum(len(c) for c in chunks) > _MAX_PLAN_CHARS:
                            break  # 폭주 출력 — 지금까지 모인 것으로 파싱 시도
        except TimeoutError:
            log.warning("planner timed out after %ss — proceeding without a plan", self._config.planner_timeout_seconds)
            return
        except Exception:  # noqa: BLE001 — 계획 실패가 턴을 깨면 안 된다
            log.exception("planner call failed — proceeding without a plan")
            return
        outcome.plan = self._parse("".join(chunks))

    def _parse(self, raw: str) -> TurnPlan | None:
        data = extract_json_object(raw)
        if data is None:
            if raw.strip():
                log.warning("planner returned unparseable output (%d chars) — no plan", len(raw))
            return None
        if not data.get("plan_needed"):
            return None
        goal = str(data.get("goal") or "").strip()
        raw_steps = data.get("steps")
        if not goal or not isinstance(raw_steps, list):
            return None
        steps: list[PlanStep] = []
        for item in raw_steps[:_HARD_MAX_STEPS]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            steps.append(
                PlanStep(
                    id=f"s{len(steps) + 1}",
                    title=title,
                    detail=str(item.get("detail") or "").strip(),
                )
            )
        # 1단계짜리 "계획"은 계획이 아니다 — 단순 실행으로 처리.
        if len(steps) < 2:
            return None
        return TurnPlan(goal=goal, steps=tuple(steps))


@dataclass
class PlanState:
    """실행 중의 계획 상태 기계. 스냅샷(TurnPlan)은 불변 — apply가 새 revision을
    만들어 교체한다. 루프는 매 반복 .plan을 프롬프트에 렌더하고, update_plan 호출을
    apply로 넘긴다."""

    plan: TurnPlan
    rounds_without_update: int = 0

    @property
    def all_terminal(self) -> bool:
        return all(s.status in _TERMINAL for s in self.plan.steps)

    def note_tool_round(self) -> None:
        """update_plan 없이 도구 라운드가 지나갔음을 기록 — drift guard 카운터."""
        self.rounds_without_update += 1

    def apply(self, arguments: dict) -> dict:
        """update_plan 호출 하나를 적용하고 도구 결과 payload를 돌려준다.
        관용 원칙: 모르는 step_id/status는 경고로 강등하고 나머지는 적용한다 —
        로컬 LLM의 부분적으로 틀린 호출 때문에 진행 자체를 막지 않는다."""
        self.rounds_without_update = 0
        warnings: list[str] = []
        steps = {s.id: s for s in self.plan.steps}

        updates = arguments.get("updates")
        if isinstance(updates, list):
            for u in updates:
                if not isinstance(u, dict):
                    continue
                sid = str(u.get("step_id") or "").strip()
                status = str(u.get("status") or "").strip()
                if sid not in steps:
                    warnings.append(f"unknown step_id: {sid!r}")
                    continue
                if status not in _VALID_STEP_STATUS:
                    warnings.append(f"invalid status for {sid}: {status!r}")
                    continue
                prev = steps[sid]
                note = str(u.get("note") or "").strip() or prev.note
                steps[sid] = PlanStep(id=prev.id, title=prev.title, detail=prev.detail, status=status, note=note)

        new_steps = list(steps.values())
        added = arguments.get("add_steps")
        if isinstance(added, list):
            for item in added:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                if not title:
                    continue
                if len(new_steps) >= _HARD_MAX_STEPS:
                    warnings.append(f"plan step cap ({_HARD_MAX_STEPS}) reached — extra steps dropped")
                    break
                new_steps.append(
                    PlanStep(id=f"s{len(new_steps) + 1}", title=title, detail=str(item.get("detail") or "").strip())
                )

        all_terminal = bool(new_steps) and all(s.status in _TERMINAL for s in new_steps)
        self.plan = TurnPlan(
            goal=self.plan.goal,
            steps=tuple(new_steps),
            plan_id=self.plan.plan_id,
            revision=self.plan.revision + 1,
            status="completed" if all_terminal else "active",
        )
        result: dict = {"ok": True, "plan": self.plan.to_dict()}
        if warnings:
            result["warnings"] = warnings
        return result
