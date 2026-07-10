"""Focused sub-agent execution.

Sub-agents are exposed to the main model as a builtin tool. The main agent can
spawn several focused workers in one tool-use round; the scheduler can run those
workers in parallel, and the next main LLM call synthesizes their results.

2026-07-04: 워커에게 read-only 도구(read_file/list_dir/search_files/fetch_url)를
주는 미니 ReAct 루프가 됐다. 이전의 "제공된 컨텍스트만 요약" 워커는 컨텍스트
격리 효과가 없었다 — 메인 에이전트가 파일 원문을 읽어 넘겨야 했으므로 오염은
메인에서 이미 발생한 뒤였다. 이제 워커가 직접 읽고, 메인은 구조화 결과만 본다.
도구는 부작용 없는 것만 주입한다(쓰기/셸/재위임 제외) — 워커는 승인 게이트
밖에서 돈다.

  — provenance/한도:
  - 결과는 자유 텍스트 요약이 아니라 {findings, evidence, confidence,
    open_questions}로 구조화된다. "누가 왜 그렇게 판단했는가"를 메인 모델과
    사용자가 추적할 수 있어야 멀티에이전트 결과를 신뢰할 수 있다. 모델이 JSON
    규약을 어기면 원문을 summary로 강등하고 confidence="unknown"으로 표시한다
    (fail-soft — 구조 강제 때문에 결과 자체를 버리지 않는다).
  - timeout은 전용 노브(ContextConfig.subagent_timeout_seconds)를 쓴다 — 이전엔
    approval_timeout_seconds를 빌려 썼다(무관한 커플링). 도구 라운드는
    subagent_max_tool_rounds로 bounded.
  - 동시 실행은 세마포어(subagent_max_concurrent)로 상한 — 한 라운드에 병렬
    스폰이 폭주해도 토큰/비용이 bounded.
  - thinking은 호출당 _MAX_THINK_CHARS에서 끊는다 — 소형 모델의 무한 추론
    루프가 timeout까지 자원을 태우지 않게(planner와 같은 fast-fail 원칙).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import cast

from ..core.config import ContextConfig, LearningConfig
from ..core.model_json import extract_json_object
from ..core.tokens import TokenCalibrator
from ..core.types import Message, ToolCall, ToolSpec, new_id
from .context_manager import ConversationContextManager
from .interfaces import ITranscript, TurnView
from .providers.interfaces import ILLMProvider
from .tool_dispatch import BuiltinTool

log = logging.getLogger("rice.agent.subagents")

# 호출당 thinking 수집 상한(자) — 초과는 추론 폭주로 보고 그 호출을 끊는다.
_MAX_THINK_CHARS = 16_000
# qwen 계열 thinking 모드의 권장 온도 — 저온(0.2)은 반복 루프 어트랙터를 만든다.
_SUBAGENT_TEMPERATURE = 0.6

_SUBAGENT_SYSTEM = (
    "You are a focused RICE sub-agent. Work only on the delegated task.\n"
    "You may call the provided read-only tools (read files, list directories, "
    "search, fetch web pages) to gather evidence — as many rounds as you need. "
    "When you have enough evidence, stop calling tools and give your final report.\n\n"
    "Your final message must be a single JSON object:\n"
    "{\n"
    '  "findings": ["each concrete finding as one sentence"],\n'
    '  "evidence": ["for each finding: the quote, file path, or fact it rests on"],\n'
    '  "confidence": "high" | "medium" | "low",\n'
    '  "open_questions": ["what you could not verify"],\n'
    '  "recommended_next_action": "one sentence"\n'
    "}\n"
    "Set confidence honestly: high only when the evidence is something you read "
    "directly; low when you had to assume or extrapolate."
)

_CONFIDENCE_VALUES = ("high", "medium", "low")


@dataclass(frozen=True)
class SubAgentResult:
    thread_id: str
    task: str
    summary: str
    truncated: bool = False
    # ── provenance ───────────────────────────────────────────────
    tools_used: tuple[str, ...] = ()  # 워커가 실제 호출한 도구 이름들(중복 제거, 순서 유지)
    findings: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    confidence: str = "unknown"  # high|medium|low|unknown (unknown = 비구조 응답)
    open_questions: tuple[str, ...] = ()
    recommended_next_action: str = ""
    structured: bool = False  # 모델이 JSON 규약을 지켰는가

    def to_dict(self) -> dict:
        return {
            "thread_id": self.thread_id,
            "task": self.task,
            "summary": self.summary,
            "truncated": self.truncated,
            "tools_used": list(self.tools_used),
            "findings": list(self.findings),
            "evidence": list(self.evidence),
            "confidence": self.confidence,
            "open_questions": list(self.open_questions),
            "recommended_next_action": self.recommended_next_action,
            "structured": self.structured,
        }


def _str_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(v) for v in value if str(v).strip())
    if isinstance(value, str) and value.strip():
        return (value,)
    return ()


class SubAgentRunner:
    def __init__(
        self,
        *,
        resolve_provider: Callable[[], ILLMProvider],
        transcript: ITranscript,
        learning_config: LearningConfig | Callable[[], LearningConfig] | None = None,
        context_config: ContextConfig | Callable[[], ContextConfig],
        token_calibrator: TokenCalibrator | None = None,
        tools: Sequence[tuple[ToolSpec, BuiltinTool]] = (),
    ) -> None:
        self._resolve_provider = resolve_provider
        self._transcript = transcript
        # learning_config는 더 이상 쓰지 않는다(예전 timeout 차용의 잔재) — 기존
        # 호출부/테스트 호환을 위해 파라미터만 받는다. timeout/동시성은 context_config.
        self._context_config = context_config if callable(context_config) else lambda: context_config
        self._calibrator = token_calibrator
        self._semaphore: asyncio.Semaphore | None = None
        # read-only 워커 도구 (builtins.build_subagent_toolset). 비어 있으면 이전과
        # 동일한 "제공된 컨텍스트만" 워커로 동작한다(기존 테스트/독립 사용 호환).
        self._tool_specs: tuple[ToolSpec, ...] = tuple(spec for spec, _ in tools)
        self._tool_handlers: dict[str, BuiltinTool] = {spec.name: handler for spec, handler in tools}

    def _limit(self) -> asyncio.Semaphore:
        # 첫 실행 시점의 config로 고정 — 라이브 변경은 재시작 후 적용(세마포어를
        # 매번 갈아끼우면 in-flight 카운트가 깨진다).
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(max(1, self._context_config().subagent_max_concurrent))
        return self._semaphore

    async def run(
        self,
        *,
        session_id: str,
        task: str,
        context: str = "",
        expected_output: str = "",
        max_chars: int = 8_000,
        turn_view: TurnView | None = None,
    ) -> SubAgentResult:
        thread_id = new_id("sub")
        history = self._transcript.history(session_id)
        context_config = self._context_config()
        estimate = self._calibrator.estimate if self._calibrator is not None else None
        rendered = ConversationContextManager(context_config, estimate=estimate).render(
            "",
            history[-context_config.subagent_history_messages :],
        )
        prompt = self._prompt(
            task=task,
            context=context,
            expected_output=expected_output,
            recent_history=_format_history(rendered.history),
            live_turn=_format_turn_view(turn_view),
        )

        provider = self._resolve_provider()
        timeout_s = max(10, context_config.subagent_timeout_seconds)
        max_rounds = max(0, context_config.subagent_max_tool_rounds)
        messages: list[Message] = [Message(role="user", content=prompt)]
        raw = ""
        truncated = False
        tools_used: list[str] = []
        try:
            async with self._limit():
                async with asyncio.timeout(timeout_s):
                    # 미니 ReAct 루프: 도구 라운드 max_rounds회 + 최종 보고 1회.
                    # 마지막 반복은 tools=()로 불러 최종 JSON을 강제한다.
                    for round_no in range(max_rounds + 1):
                        final_round = round_no >= max_rounds or not self._tool_specs
                        text, calls, overflow = await self._one_call(
                            provider, messages, final_round=final_round, max_chars=max_chars
                        )
                        if overflow:
                            truncated = True
                        if not calls or final_round:
                            raw = text.strip()
                            break
                        messages.append(Message(role="assistant", content=text, tool_calls=tuple(calls)))
                        for call in calls:
                            result_text = await self._execute(call)
                            tools_used.append(call.tool_name)
                            messages.append(
                                Message(
                                    role="tool",
                                    content=result_text,
                                    metadata={"tool_call_id": call.id, "tool_name": call.tool_name},
                                )
                            )
                    if not raw and max_rounds and self._tool_specs:
                        # 라운드 소진 시(위 break 없이 끝) 최종 보고를 한 번 더 요구.
                        messages.append(
                            Message(
                                role="user",
                                content="Tool budget exhausted. Produce your final JSON report now from the evidence gathered so far.",
                            )
                        )
                        raw, _, overflow = await self._one_call(
                            provider, messages, final_round=True, max_chars=max_chars
                        )
                        raw = raw.strip()
                        truncated = truncated or overflow
        except TimeoutError:
            return SubAgentResult(
                thread_id=thread_id,
                task=task,
                summary="Sub-agent timed out before completing.",
                truncated=True,
                tools_used=tuple(dict.fromkeys(tools_used)),
                confidence="unknown",
                open_questions=("timed out — no findings were produced",),
            )

        used = tuple(dict.fromkeys(tools_used))
        if not raw:
            return SubAgentResult(
                thread_id=thread_id,
                task=task,
                summary="Sub-agent completed without producing text.",
                tools_used=used,
            )
        if len(raw) > max_chars:
            raw = raw[:max_chars].rstrip() + "\n[truncated]"
            truncated = True

        data = None if truncated else extract_json_object(raw)
        if data is None:
            # 비구조 응답 — 원문을 summary로 강등하고 그 사실을 표시한다(fail-soft).
            return SubAgentResult(
                thread_id=thread_id,
                task=task,
                summary=raw,
                truncated=truncated,
                tools_used=used,
                findings=(raw,) if raw else (),
                confidence="unknown",
                structured=False,
            )

        findings = _str_tuple(data.get("findings"))
        evidence = _str_tuple(data.get("evidence"))
        confidence = str(data.get("confidence") or "").lower()
        if confidence not in _CONFIDENCE_VALUES:
            confidence = "unknown"
        open_questions = _str_tuple(data.get("open_questions"))
        next_action = str(data.get("recommended_next_action") or "").strip()
        summary_lines = list(findings) or [raw]
        if next_action:
            summary_lines.append(f"next: {next_action}")
        return SubAgentResult(
            thread_id=thread_id,
            task=task,
            summary="\n".join(summary_lines),
            truncated=truncated,
            tools_used=used,
            findings=findings,
            evidence=evidence,
            confidence=confidence,
            open_questions=open_questions,
            recommended_next_action=next_action,
            structured=True,
        )

    async def _one_call(
        self,
        provider: ILLMProvider,
        messages: Sequence[Message],
        *,
        final_round: bool,
        max_chars: int,
    ) -> tuple[str, list[ToolCall], bool]:
        """LLM 1회 호출. (text, tool_calls, overflow)를 돌려준다 — overflow는
        텍스트/thinking이 상한을 넘어 스트림을 중도에 끊었음을 뜻한다.
        final_round면 도구를 광고하지 않아 최종 보고(JSON)를 강제한다."""
        chunks: list[str] = []
        calls: list[ToolCall] = []
        think_chars = 0
        overflow = False
        async for chunk in provider.stream(
            system=_SUBAGENT_SYSTEM,
            messages=tuple(messages),
            tools=() if final_round else self._tool_specs,
            temperature=_SUBAGENT_TEMPERATURE,
        ):
            if chunk.kind == "thinking" and chunk.text:
                think_chars += len(chunk.text)
                if think_chars > _MAX_THINK_CHARS:
                    # 추론 폭주 — 이 호출을 끊는다. 도구 호출이 이미 나왔으면
                    # 그것까지는 실행되고, 아니면 상위가 fail-soft로 처리한다.
                    log.warning("sub-agent thinking exceeded %d chars — cutting the call", _MAX_THINK_CHARS)
                    overflow = True
                    break
            elif chunk.kind == "text" and chunk.text:
                chunks.append(chunk.text)
                if sum(len(part) for part in chunks) > max_chars:
                    overflow = True
                    break
            elif chunk.kind == "tool_call" and chunk.tool_call is not None:
                calls.append(chunk.tool_call)
        return "".join(chunks), calls, overflow

    async def _execute(self, call: ToolCall) -> str:
        """워커 도구 하나를 직접 실행한다 — 전부 read-only 빌트인이라 승인 게이트
        없이 인프로세스 호출로 충분하다. 실패는 모델이 보고 경로를 바꾸도록
        ERROR 문자열로 돌려준다(메인 루프의 _render_tool_result와 같은 관례)."""
        handler = self._tool_handlers.get(call.tool_name)
        if handler is None:
            return f"ERROR: unknown tool: {call.tool_name}"
        try:
            # 워커 도구셋은 세션 무관(read-only)이지만, dispatcher와 같은 관례를
            # 지켜 needs_session 핸들러가 섞여도 동작하게 한다.
            fn = cast(Callable[[dict], Awaitable[object]], handler)
            result = await fn(dict(call.arguments or {}))
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as exc:  # noqa: BLE001 — 도구 실패가 워커를 깨면 안 된다
            return f"ERROR: {exc}"

    @staticmethod
    def _prompt(
        *, task: str, context: str, expected_output: str, recent_history: str, live_turn: str = ""
    ) -> str:
        sections = [f"Task:\n{task.strip()}"]
        if expected_output.strip():
            sections.append(f"Expected output:\n{expected_output.strip()}")
        if context.strip():
            sections.append(f"Context from main agent:\n{context.strip()}")
        if live_turn.strip():
            # transcript(recent_history)는 이전 턴까지다 — 이번 턴의 목표·계획·
            # 도구 관찰은 여기로만 워커에게 도달한다(TurnView 주입).
            sections.append(f"Current turn in progress (not yet in the log):\n{live_turn.strip()}")
        if recent_history.strip():
            sections.append(f"Recent conversation context:\n{recent_history.strip()}")
        return "\n\n".join(sections)


def _format_history(messages: Sequence[Message]) -> str:
    lines: list[str] = []
    for message in messages:
        text = " ".join(message.content.split())
        if len(text) > 700:
            text = text[:700].rstrip() + "..."
        if message.role == "assistant" and message.tool_calls:
            calls = ", ".join(call.tool_name for call in message.tool_calls)
            lines.append(f"assistant tool request: {calls}")
            if text:
                lines.append(f"assistant: {text}")
            continue
        lines.append(f"{message.role}: {text}")
    return "\n".join(lines)


# 라이브 턴 섹션의 상한(자) — 워커 컨텍스트를 보호한다. 이번 턴이 길면 앞이 아니라
# **뒤**(최근 관찰)를 남긴다: 워커에게 유용한 건 방금의 관찰이다.
_TURN_VIEW_MAX_CHARS = 4_000


def _format_turn_view(view: TurnView | None) -> str:
    """이번 턴의 라이브 스냅샷을 워커 프롬프트 섹션으로 렌더한다."""
    if view is None:
        return ""
    lines: list[str] = []
    if view.user_message.strip():
        text = " ".join(view.user_message.split())
        if len(text) > 500:
            text = text[:500].rstrip() + "..."
        lines.append(f"The user's request this turn: {text}")
    if view.plan is not None:
        lines.append(f"Active plan (goal: {view.plan.goal}):")
        for s in view.plan.steps:
            note = f" — {s.note}" if s.note else ""
            lines.append(f"  [{s.status}] {s.id}. {s.title}{note}")
    body = _format_history(view.turn_messages)
    if body:
        lines.append("What has happened so far this turn:")
        lines.append(body)
    text = "\n".join(lines)
    if len(text) > _TURN_VIEW_MAX_CHARS:
        text = "[... earlier this turn omitted ...]\n" + text[-_TURN_VIEW_MAX_CHARS:]
    return text
