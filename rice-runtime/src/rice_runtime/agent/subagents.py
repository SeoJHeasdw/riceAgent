"""Focused sub-agent execution.

Sub-agents are exposed to the main model as a builtin tool. The main agent can
spawn several focused workers in one tool-use round; the scheduler can run those
workers in parallel, and the next main LLM call synthesizes their results.

  — provenance/한도:
  - 결과는 자유 텍스트 요약이 아니라 {findings, evidence, confidence,
    open_questions}로 구조화된다. "누가 왜 그렇게 판단했는가"를 메인 모델과
    사용자가 추적할 수 있어야 멀티에이전트 결과를 신뢰할 수 있다. 모델이 JSON
    규약을 어기면 원문을 summary로 강등하고 confidence="unknown"으로 표시한다
    (fail-soft — 구조 강제 때문에 결과 자체를 버리지 않는다).
  - timeout은 전용 노브(ContextConfig.subagent_timeout_seconds)를 쓴다 — 이전엔
    approval_timeout_seconds를 빌려 썼다(무관한 커플링).
  - 동시 실행은 세마포어(subagent_max_concurrent)로 상한 — 한 라운드에 병렬
    스폰이 폭주해도 토큰/비용이 bounded.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..core.config import ContextConfig, LearningConfig
from ..core.tokens import TokenCalibrator
from ..core.types import Message, new_id
from .context_manager import ConversationContextManager
from .interfaces import ITranscript
from .providers.interfaces import ILLMProvider


_SUBAGENT_SYSTEM = (
    "You are a focused RICE sub-agent. Work only on the delegated task. "
    "Do not call tools; ask for missing inputs only when the task cannot be "
    "completed from the provided context.\n\n"
    "Respond with ONLY a JSON object (no prose before or after, no code fence):\n"
    "{\n"
    '  "findings": ["each concrete finding as one sentence"],\n'
    '  "evidence": ["for each finding: the quote, fact, or context reference it rests on"],\n'
    '  "confidence": "high" | "medium" | "low",\n'
    '  "open_questions": ["what you could not verify from the provided context"],\n'
    '  "recommended_next_action": "one sentence"\n'
    "}\n"
    "Set confidence honestly: high only when the evidence is directly in the "
    "provided context; low when you had to assume or extrapolate."
)

_CONFIDENCE_VALUES = ("high", "medium", "low")


@dataclass(frozen=True)
class SubAgentResult:
    thread_id: str
    task: str
    summary: str
    truncated: bool = False
    # ── provenance ───────────────────────────────────────────────
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
            "findings": list(self.findings),
            "evidence": list(self.evidence),
            "confidence": self.confidence,
            "open_questions": list(self.open_questions),
            "recommended_next_action": self.recommended_next_action,
            "structured": self.structured,
        }


def _parse_structured(raw: str) -> dict | None:
    """모델 출력에서 JSON 객체를 최선 노력으로 파싱한다. 코드펜스/앞뒤 잡담 허용."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    elif not text.startswith("{"):
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace is None:
            return None
        text = brace.group(0)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


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
    ) -> None:
        self._resolve_provider = resolve_provider
        self._transcript = transcript
        # learning_config는 더 이상 쓰지 않는다(예전 timeout 차용의 잔재) — 기존
        # 호출부/테스트 호환을 위해 파라미터만 받는다. timeout/동시성은 context_config.
        self._context_config = context_config if callable(context_config) else lambda: context_config
        self._calibrator = token_calibrator
        self._semaphore: asyncio.Semaphore | None = None

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
        )

        provider = self._resolve_provider()
        timeout_s = max(10, context_config.subagent_timeout_seconds)
        chunks: list[str] = []
        truncated = False
        try:
            async with self._limit():
                async with asyncio.timeout(timeout_s):
                    async for chunk in provider.stream(
                        system=_SUBAGENT_SYSTEM,
                        messages=(Message(role="user", content=prompt),),
                        tools=(),
                        temperature=0.2,
                    ):
                        if chunk.kind == "text" and chunk.text:
                            chunks.append(chunk.text)
                        if sum(len(part) for part in chunks) > max_chars:
                            truncated = True
                            break
        except TimeoutError:
            return SubAgentResult(
                thread_id=thread_id,
                task=task,
                summary="Sub-agent timed out before completing.",
                truncated=True,
                confidence="unknown",
                open_questions=("timed out — no findings were produced",),
            )

        raw = "".join(chunks).strip()
        if not raw:
            return SubAgentResult(
                thread_id=thread_id, task=task, summary="Sub-agent completed without producing text."
            )
        if len(raw) > max_chars:
            raw = raw[:max_chars].rstrip() + "\n[truncated]"
            truncated = True

        data = None if truncated else _parse_structured(raw)
        if data is None:
            # 비구조 응답 — 원문을 summary로 강등하고 그 사실을 표시한다(fail-soft).
            return SubAgentResult(
                thread_id=thread_id,
                task=task,
                summary=raw,
                truncated=truncated,
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
            findings=findings,
            evidence=evidence,
            confidence=confidence,
            open_questions=open_questions,
            recommended_next_action=next_action,
            structured=True,
        )

    @staticmethod
    def _prompt(*, task: str, context: str, expected_output: str, recent_history: str) -> str:
        sections = [f"Task:\n{task.strip()}"]
        if expected_output.strip():
            sections.append(f"Expected output:\n{expected_output.strip()}")
        if context.strip():
            sections.append(f"Context from main agent:\n{context.strip()}")
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
