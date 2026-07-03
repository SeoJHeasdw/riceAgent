"""Focused sub-agent execution.

Sub-agents are exposed to the main model as a builtin tool. The main agent can
spawn several focused workers in one tool-use round; the scheduler can run those
workers in parallel, and the next main LLM call synthesizes their results.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..core.config import ContextConfig, LearningConfig
from ..core.types import Message, new_id
from .context_manager import ConversationContextManager
from .interfaces import ITranscript
from .providers.interfaces import ILLMProvider


_SUBAGENT_SYSTEM = (
    "You are a focused RICE sub-agent. Work only on the delegated task. "
    "Return a concise report with: findings, evidence, risks/open questions, "
    "and recommended next action. Do not call tools; ask for missing inputs only "
    "when the task cannot be completed from the provided context."
)


@dataclass(frozen=True)
class SubAgentResult:
    thread_id: str
    task: str
    summary: str
    truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "thread_id": self.thread_id,
            "task": self.task,
            "summary": self.summary,
            "truncated": self.truncated,
        }


class SubAgentRunner:
    def __init__(
        self,
        *,
        resolve_provider: Callable[[], ILLMProvider],
        transcript: ITranscript,
        learning_config: LearningConfig | Callable[[], LearningConfig],
        context_config: ContextConfig | Callable[[], ContextConfig],
    ) -> None:
        self._resolve_provider = resolve_provider
        self._transcript = transcript
        self._learning_config = learning_config if callable(learning_config) else lambda: learning_config
        self._context_config = context_config if callable(context_config) else lambda: context_config

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
        rendered = ConversationContextManager(context_config).render(
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
        learning_config = self._learning_config()
        chunks: list[str] = []
        truncated = False
        try:
            async with asyncio.timeout(max(10, min(120, learning_config.approval_timeout_seconds))):
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
            return SubAgentResult(thread_id=thread_id, task=task, summary="Sub-agent timed out before completing.", truncated=True)

        summary = "".join(chunks).strip()
        if not summary:
            summary = "Sub-agent completed without producing text."
        if len(summary) > max_chars:
            summary = summary[:max_chars].rstrip() + "\n[truncated]"
            truncated = True
        return SubAgentResult(thread_id=thread_id, task=task, summary=summary, truncated=truncated)

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
