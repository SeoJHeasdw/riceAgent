"""Focused sub-agent execution — 컨텍스트 격리형 병렬 워커.

Sub-agents are exposed to the main model as a builtin tool. The main agent can
spawn several focused workers in one tool-use round; the scheduler can run those
workers in parallel, and the next main LLM call synthesizes their results.

2026-07-04: 워커에게 read-only 도구(read_file/list_dir/grep/search_files/fetch_url,
2026-07-18부터 web_search 포함)를
주는 미니 ReAct 루프가 됐다. 이전의 "제공된 컨텍스트만 요약" 워커는 컨텍스트
격리 효과가 없었다 — 메인 에이전트가 파일 원문을 읽어 넘겨야 했으므로 오염은
메인에서 이미 발생한 뒤였다. 이제 워커가 직접 읽고, 메인은 구조화 결과만 본다.

2026-07-10 v2 — 주입/산출 채널 확장:

  스킬 주입     learning이 배선되면 (a) 태스크 관련 스킬 **인덱스**(이름+한 줄)를
                워커 프롬프트에 싣고, (b) skill_search/skill_view 도구를 주어
                워커가 절차 전문을 온디맨드로 당긴다 — 메인 루프의 하이브리드
                주입(인덱스 → top-K → skill_view)과 같은 철학, 같은 핸들러.
  도구 주입     delegate_subagent의 `tools` 화이트리스트로 메인 모델이 워커의
                도구 표면을 태스크에 맞게 좁힌다(웹 전용 리서치엔 web_search/fetch_url만).
                미지의 이름은 무시하고, 교집합이 비면 전체 셋으로 폴백(fail-soft).
  아티팩트 채널  워커는 write_scratch로 격리 영역(.subagent/<thread>/)에만 긴
                산출물(초안·표·추출문)을 파일로 남긴다. 요약은 메인 컨텍스트로,
                본문은 파일로 — 컨텍스트 오염 없이 대용량 산출이 가능해진다.
                실제로 쓰인 경로는 러너가 추적해 `artifacts`로 보고한다(모델
                자기신고가 아니라 관찰). 승인 게이트를 우회하지 않는 이유:
                쓰기는 격리 dot-디렉토리 안으로만 가능하고, 사용자 가시 영역으로의
                승격은 메인 에이전트의 (승인 게이트를 타는) 도구가 담당한다.

  — provenance/한도:
  - 결과는 자유 텍스트 요약이 아니라 {findings, evidence, confidence,
    open_questions}로 구조화된다. "누가 왜 그렇게 판단했는가"를 메인 모델과
    사용자가 추적할 수 있어야 멀티에이전트 결과를 신뢰할 수 있다. 모델이 JSON
    규약을 어기면 원문을 summary로 강등하고 confidence="unknown"으로 표시한다
    (fail-soft — 구조 강제 때문에 결과 자체를 버리지 않는다).
  - timeout은 전용 노브(ContextConfig.subagent_timeout_seconds)를 쓴다. 도구
    라운드는 subagent_max_tool_rounds로 bounded.
  - 동시 실행은 세마포어(subagent_max_concurrent)로 상한 — 한 라운드에 병렬
    스폰이 폭주해도 토큰/비용이 bounded.
  - thinking은 호출당 _MAX_THINK_CHARS에서 끊는다 — 소형 모델의 무한 추론
    루프가 timeout까지 자원을 태우지 않게(planner와 같은 fast-fail 원칙).
  - 워커는 재위임 불가(delegate_subagent 미주입) — 깊이 1 고정, 폭만 병렬.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ..core.config import ContextConfig
from ..core.model_json import extract_json_object
from ..core.tokens import TokenCalibrator
from ..core.types import Message, PluginAgent, ToolCall, ToolSpec, new_id
from .context_manager import ConversationContextManager
from .interfaces import ITranscript, TurnView
from .providers.interfaces import ILLMProvider
from .tool_dispatch import BuiltinTool

if TYPE_CHECKING:
    from ..learning.interfaces import ILearningService

log = logging.getLogger("rice.agent.subagents")

# 호출당 thinking 수집 상한(자) — 초과는 추론 폭주로 보고 그 호출을 끊는다.
_MAX_THINK_CHARS = 16_000
# qwen 계열 thinking 모드의 권장 온도 — 저온(0.2)은 반복 루프 어트랙터를 만든다.
_SUBAGENT_TEMPERATURE = 0.6
# 태스크 관련 스킬 인덱스 상한 — 워커 프롬프트는 얇게 유지한다.
_SKILL_INDEX_LIMIT = 5

# 역할부와 보고 계약부를 분리한다(2026-07-19 페르소나 지원): 플러그인 agents/*.md
# 페르소나가 지정되면 역할부만 그 본문으로 바뀌고, 보고 계약(JSON 구조·정직한
# confidence)은 페르소나와 무관하게 항상 붙는다 — 메인 모델의 결과 소비 계약은
# 페르소나가 바꿀 수 없는 불변식이다.
_SUBAGENT_ROLE = (
    "You are a focused RICE sub-agent. Work only on the delegated task.\n"
    "You may call the provided read-only tools (read files, list directories, "
    "search, fetch web pages) to gather evidence — as many rounds as you need. "
    "When you have enough evidence, stop calling tools and give your final report."
)
_SUBAGENT_CONTRACT = (
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
_SUBAGENT_SYSTEM = f"{_SUBAGENT_ROLE}\n\n{_SUBAGENT_CONTRACT}"
# 페르소나 본문에 덧붙는 실행 환경 노트 — 페르소나는 Claude Code 등 다른 하니스를
# 전제로 쓰였을 수 있어, 실제 도구 표면은 여기(RICE 워커)의 것임을 명시한다.
_PERSONA_TOOL_NOTE = (
    "You are running as a RICE sub-agent: only the tools actually provided in "
    "this conversation are available — ignore any tool names in the instructions "
    "above that are not offered here."
)

# 도구 가용성에 따라 붙는 시스템 프롬프트 노트 — 없는 도구를 언급하면 소형 모델이
# 흉내(priming)낸다. 실제로 주입된 도구에 대해서만 지시한다.
_SCRATCH_NOTE = (
    "\n\nLong output: if you produce a long draft, table, or extraction (roughly 30+ "
    "lines), do NOT paste it into the report. Save it with write_scratch and cite the "
    "returned path in your findings — the main agent reads the file from there."
)
_SKILL_NOTE = (
    "\n\nSkills: a saved-skill index may be listed in your task context. If one matches "
    "the task, call skill_view with its name FIRST to load its exact steps; use "
    "skill_search to find others. Don't guess a procedure a skill would give you. "
    "If a skill lists bundled files (references/, examples/), read them with "
    "skill_resource. "
    # 의도 우선 서열(SKILL_PRECEDENCE_NOTE의 워커판) — 태스크 지시가 스킬 절차를 이긴다.
    "A skill is a default procedure, not an order: where it conflicts with your task "
    "instructions, the task instructions win."
)

_CONFIDENCE_VALUES = ("high", "medium", "low")


@dataclass(frozen=True)
class SubAgentResult:
    thread_id: str
    task: str
    summary: str
    truncated: bool = False
    # ── provenance ───────────────────────────────────────────────
    # 실제 적용된 페르소나(플러그인 agents/*.md 이름) — 요청했지만 못 찾았으면 ""
    # 이고, 사유는 open_questions 로 보고된다(감사: "누구로서 판단했는가").
    persona: str = ""
    tools_used: tuple[str, ...] = ()  # 워커가 실제 호출한 도구 이름들(중복 제거, 순서 유지)
    findings: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    confidence: str = "unknown"  # high|medium|low|unknown (unknown = 비구조 응답)
    open_questions: tuple[str, ...] = ()
    recommended_next_action: str = ""
    structured: bool = False  # 모델이 JSON 규약을 지켰는가
    # 워커가 write_scratch로 실제 남긴 파일들(워크스페이스 상대 경로) — 러너가
    # 도구 결과에서 관찰해 채운다(모델 자기신고가 아님). 메인은 read_file로 읽는다.
    artifacts: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "thread_id": self.thread_id,
            "task": self.task,
            "summary": self.summary,
            "truncated": self.truncated,
            "persona": self.persona,
            "tools_used": list(self.tools_used),
            "findings": list(self.findings),
            "evidence": list(self.evidence),
            "confidence": self.confidence,
            "open_questions": list(self.open_questions),
            "recommended_next_action": self.recommended_next_action,
            "structured": self.structured,
            "artifacts": list(self.artifacts),
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
        context_config: ContextConfig | Callable[[], ContextConfig],
        token_calibrator: TokenCalibrator | None = None,
        tools: Sequence[tuple[ToolSpec, BuiltinTool]] = (),
        learning: "ILearningService | None" = None,
        scratch_tool_factory: Callable[[str], tuple[ToolSpec, BuiltinTool]] | None = None,
        resolve_agents: Callable[[], Sequence[PluginAgent]] | None = None,
    ) -> None:
        self._resolve_provider = resolve_provider
        self._transcript = transcript
        self._context_config = context_config if callable(context_config) else lambda: context_config
        self._calibrator = token_calibrator
        self._semaphore: asyncio.Semaphore | None = None
        # read-only 워커 도구 (builtins.build_subagent_toolset). 비어 있으면 이전과
        # 동일한 "제공된 컨텍스트만" 워커로 동작한다(기존 테스트/독립 사용 호환).
        self._static_tools: tuple[tuple[ToolSpec, BuiltinTool], ...] = tuple(tools)
        # 스킬 인덱스 렌더용(search_skills) — 도구 주입 여부와 별개의 얕은 참조.
        self._learning = learning
        # thread별 write_scratch 도구를 만드는 팩토리(builtins.file_tools) — 러너는
        # 파일시스템을 모른다(storage-agnostic 유지).
        self._scratch_factory = scratch_tool_factory
        # 플러그인 agents/*.md 페르소나 스냅샷(2026-07-19) — composition root 가
        # plugins.injected_agents 를 넘긴다(활성 플러그인 기준 라이브 조회).
        self._resolve_agents = resolve_agents

    def available_personas(self) -> tuple[PluginAgent, ...]:
        """활성 플러그인의 워커 페르소나 목록 — delegate 도구 스펙 렌더와 run()의
        해석이 공유한다. 조회 실패는 빈 목록(fail-soft — 페르소나 없음 ≠ 위임 불능)."""
        if self._resolve_agents is None:
            return ()
        try:
            return tuple(self._resolve_agents())
        except Exception:  # noqa: BLE001 — 페르소나 조회 실패가 위임을 깨면 안 된다
            log.warning("plugin agent lookup failed", exc_info=True)
            return ()

    def _limit(self) -> asyncio.Semaphore:
        # 첫 실행 시점의 config로 고정 — 라이브 변경은 재시작 후 적용(세마포어를
        # 매번 갈아끼우면 in-flight 카운트가 깨진다).
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(max(1, self._context_config().subagent_max_concurrent))
        return self._semaphore

    def _compose_tools(
        self, thread_id: str, tool_names: Sequence[str], web_search_enabled: bool = True
    ) -> tuple[tuple[ToolSpec, ...], dict[str, BuiltinTool]]:
        """이 실행의 도구 표면을 조립한다: 정적 read-only 셋에 화이트리스트를
        적용하고, scratch 팩토리가 있으면 thread 격리 write_scratch를 더한다.
        화이트리스트의 미지 이름은 무시, 교집합이 비면 전체 셋 폴백(fail-soft —
        메인 모델의 오타가 워커를 벙어리로 만들면 안 된다)."""
        available = [
            (spec, handler)
            for spec, handler in self._static_tools
            if web_search_enabled or not spec.is_web_tool
        ]
        if self._scratch_factory is not None:
            available.append(self._scratch_factory(thread_id))
        wanted = {str(n).strip() for n in tool_names if str(n).strip()}
        if wanted:
            filtered = [(s, h) for s, h in available if s.name in wanted]
            if filtered:
                available = filtered
            else:
                log.warning("sub-agent tool whitelist %s matched nothing — using full set", sorted(wanted))
        specs = tuple(spec for spec, _ in available)
        handlers = {spec.name: handler for spec, handler in available}
        return specs, handlers

    def _skill_index(self, task: str, *, has_skill_view: bool) -> str:
        """태스크 관련 스킬 인덱스(이름+한 줄) — skill_view가 실제로 주입됐을 때만.
        본문은 싣지 않는다: 워커가 필요하면 스스로 당긴다(하이브리드 주입)."""
        if self._learning is None or not has_skill_view or not task.strip():
            return ""
        try:
            rows = self._learning.search_skills(task, limit=_SKILL_INDEX_LIMIT)
        except Exception:  # noqa: BLE001 — 스킬 인덱스 실패가 워커를 깨면 안 된다
            log.warning("sub-agent skill index lookup failed", exc_info=True)
            return ""
        lines = [
            f"- {row.get('name')} — {row.get('description') or ''}".rstrip(" —")
            for row in rows
            if row.get("name")
        ]
        if not lines:
            return ""
        return "Saved skills that may apply (index only — call skill_view to load steps):\n" + "\n".join(lines)

    async def run(
        self,
        *,
        session_id: str,
        task: str,
        context: str = "",
        expected_output: str = "",
        max_chars: int = 8_000,
        turn_view: TurnView | None = None,
        tool_names: Sequence[str] = (),
        web_search_enabled: bool = True,
        agent: str = "",
    ) -> SubAgentResult:
        thread_id = new_id("sub")
        history = self._transcript.history(session_id)
        context_config = self._context_config()
        estimate = self._calibrator.estimate if self._calibrator is not None else None
        rendered = ConversationContextManager(context_config, estimate=estimate).render(
            "",
            history[-context_config.subagent_history_messages :],
        )
        tool_specs, tool_handlers = self._compose_tools(
            thread_id, tool_names, web_search_enabled=web_search_enabled
        )
        # 페르소나 해석(2026-07-19): 이름이 매칭되면 역할부를 페르소나 본문으로
        # 교체한다(보고 계약은 불변). 미지의 이름은 기본 역할로 fail-soft 진행하되
        # 그 사실을 open_questions 로 보고해 메인 모델이 이름을 교정할 수 있게
        # 한다(도구 화이트리스트의 "오타가 워커를 벙어리로 만들면 안 된다" 원칙).
        persona_name = ""
        persona_miss = ""
        role = _SUBAGENT_ROLE
        requested = str(agent or "").strip()
        if requested:
            personas = self.available_personas()
            match = next((p for p in personas if p.name == requested), None)
            if match is not None:
                persona_name = match.name
                role = f"{match.body.strip()}\n\n{_PERSONA_TOOL_NOTE}"
            else:
                known = ", ".join(p.name for p in personas) or "none"
                persona_miss = f"unknown agent persona '{requested}' — available: {known}"
                log.warning("sub-agent persona %r not found (available: %s)", requested, known)
        extra_questions: tuple[str, ...] = (persona_miss,) if persona_miss else ()
        system = f"{role}\n\n{_SUBAGENT_CONTRACT}"
        if "write_scratch" in tool_handlers:
            system += _SCRATCH_NOTE
        if "skill_view" in tool_handlers:
            system += _SKILL_NOTE
        prompt = self._prompt(
            task=task,
            context=context,
            expected_output=expected_output,
            recent_history=_format_history(rendered.history),
            live_turn=_format_turn_view(turn_view),
            skill_index=self._skill_index(task, has_skill_view="skill_view" in tool_handlers),
        )

        provider = self._resolve_provider()
        timeout_s = max(10, context_config.subagent_timeout_seconds)
        max_rounds = max(0, context_config.subagent_max_tool_rounds)
        messages: list[Message] = [Message(role="user", content=prompt)]
        raw = ""
        truncated = False
        tools_used: list[str] = []
        artifacts: list[str] = []
        try:
            async with self._limit():
                async with asyncio.timeout(timeout_s):
                    # 미니 ReAct 루프: 도구 라운드 max_rounds회 + 최종 보고 1회.
                    # 마지막 반복은 tools=()로 불러 최종 JSON을 강제한다.
                    for round_no in range(max_rounds + 1):
                        final_round = round_no >= max_rounds or not tool_specs
                        text, calls, overflow = await self._one_call(
                            provider, system, messages,
                            tools=() if final_round else tool_specs,
                            max_chars=max_chars,
                        )
                        if overflow:
                            truncated = True
                        if not calls or final_round:
                            raw = text.strip()
                            break
                        messages.append(Message(role="assistant", content=text, tool_calls=tuple(calls)))
                        for call in calls:
                            result_text = await self._execute(
                                call, tool_handlers, session_id=session_id, artifacts=artifacts
                            )
                            tools_used.append(call.tool_name)
                            messages.append(
                                Message(
                                    role="tool",
                                    content=result_text,
                                    metadata={"tool_call_id": call.id, "tool_name": call.tool_name},
                                )
                            )
                    if not raw and max_rounds and tool_specs:
                        # 라운드 소진 시(위 break 없이 끝) 최종 보고를 한 번 더 요구.
                        messages.append(
                            Message(
                                role="user",
                                content="Tool budget exhausted. Produce your final JSON report now from the evidence gathered so far.",
                            )
                        )
                        raw, _, overflow = await self._one_call(
                            provider, system, messages, tools=(), max_chars=max_chars
                        )
                        raw = raw.strip()
                        truncated = truncated or overflow
        except TimeoutError:
            return SubAgentResult(
                thread_id=thread_id,
                task=task,
                summary="Sub-agent timed out before completing.",
                truncated=True,
                persona=persona_name,
                tools_used=tuple(dict.fromkeys(tools_used)),
                confidence="unknown",
                open_questions=(*extra_questions, "timed out — no findings were produced"),
                # timeout 전에 남긴 산출물은 디스크에 살아 있다 — 부분 성과도 보고.
                artifacts=tuple(dict.fromkeys(artifacts)),
            )

        used = tuple(dict.fromkeys(tools_used))
        kept_artifacts = tuple(dict.fromkeys(artifacts))
        if not raw:
            return SubAgentResult(
                thread_id=thread_id,
                task=task,
                summary="Sub-agent completed without producing text.",
                persona=persona_name,
                tools_used=used,
                open_questions=extra_questions,
                artifacts=kept_artifacts,
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
                persona=persona_name,
                tools_used=used,
                findings=(raw,) if raw else (),
                confidence="unknown",
                open_questions=extra_questions,
                structured=False,
                artifacts=kept_artifacts,
            )

        findings = _str_tuple(data.get("findings"))
        evidence = _str_tuple(data.get("evidence"))
        confidence = str(data.get("confidence") or "").lower()
        if confidence not in _CONFIDENCE_VALUES:
            confidence = "unknown"
        open_questions = (*extra_questions, *_str_tuple(data.get("open_questions")))
        next_action = str(data.get("recommended_next_action") or "").strip()
        summary_lines = list(findings) or [raw]
        if next_action:
            summary_lines.append(f"next: {next_action}")
        return SubAgentResult(
            thread_id=thread_id,
            task=task,
            summary="\n".join(summary_lines),
            truncated=truncated,
            persona=persona_name,
            tools_used=used,
            findings=findings,
            evidence=evidence,
            confidence=confidence,
            open_questions=open_questions,
            recommended_next_action=next_action,
            structured=True,
            artifacts=kept_artifacts,
        )

    async def _one_call(
        self,
        provider: ILLMProvider,
        system: str,
        messages: Sequence[Message],
        *,
        tools: tuple[ToolSpec, ...],
        max_chars: int,
    ) -> tuple[str, list[ToolCall], bool]:
        """LLM 1회 호출. (text, tool_calls, overflow)를 돌려준다 — overflow는
        텍스트/thinking이 상한을 넘어 스트림을 중도에 끊었음을 뜻한다.
        tools=()면 도구를 광고하지 않아 최종 보고(JSON)를 강제한다."""
        chunks: list[str] = []
        calls: list[ToolCall] = []
        think_chars = 0
        overflow = False
        async for chunk in provider.stream(
            system=system,
            messages=tuple(messages),
            tools=tools,
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

    async def _execute(
        self,
        call: ToolCall,
        handlers: dict[str, BuiltinTool],
        *,
        session_id: str,
        artifacts: list[str],
    ) -> str:
        """워커 도구 하나를 직접 실행한다 — 전부 격리/read-only 빌트인이라 승인
        게이트 없이 인프로세스 호출로 충분하다. needs_session 핸들러(skill_view)는
        dispatcher와 같은 관례로 session_id를 받는다. write_scratch 성공 결과의
        경로는 artifacts로 관찰 수집한다. 실패는 모델이 보고 경로를 바꾸도록
        ERROR 문자열로 돌려준다(메인 루프의 _render_tool_result와 같은 관례)."""
        handler = handlers.get(call.tool_name)
        if handler is None:
            return f"ERROR: unknown tool: {call.tool_name}"
        try:
            fn = cast(Callable[..., Awaitable[object]], handler)
            kwargs: dict[str, object] = {}
            if getattr(handler, "needs_session", False):
                kwargs["session_id"] = session_id
            result = await fn(dict(call.arguments or {}), **kwargs)
            if call.tool_name == "write_scratch" and isinstance(result, dict) and result.get("path"):
                artifacts.append(str(result["path"]))
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as exc:  # noqa: BLE001 — 도구 실패가 워커를 깨면 안 된다
            return f"ERROR: {exc}"

    @staticmethod
    def _prompt(
        *,
        task: str,
        context: str,
        expected_output: str,
        recent_history: str,
        live_turn: str = "",
        skill_index: str = "",
    ) -> str:
        sections = [f"Task:\n{task.strip()}"]
        if expected_output.strip():
            sections.append(f"Expected output:\n{expected_output.strip()}")
        if context.strip():
            sections.append(f"Context from main agent:\n{context.strip()}")
        if skill_index.strip():
            sections.append(skill_index.strip())
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
