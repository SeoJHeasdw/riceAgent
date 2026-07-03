"""Turn-time context rendering for the agent loop.

This is RICE's Python adaptation of the useful parts of Gemini CLI's context
management layer:

* the durable transcript remains the source of truth;
* every LLM call receives a rendered view of that transcript;
* older pressure is compressed into a system-level summary;
* tool output is masked/distilled before it is re-sent to the model;
* recent tool-call adjacency is preserved so provider wire formats stay valid.

It is intentionally smaller than Gemini's graph pipeline. RICE already has a
clean orchestrator seam, so this module provides the same product behavior
without importing a large graph engine before we need it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Sequence

from ..core.config import ContextConfig
from ..core.types import Message


_CHARS_PER_TOKEN = 4
_MASK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?([A-Za-z0-9_\-./+=]{12,})"), r"\1=[REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "sk-[REDACTED]"),
    (re.compile(r"ghp_[A-Za-z0-9_]{20,}"), "ghp_[REDACTED]"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"), "xox-[REDACTED]"),
)


@dataclass(frozen=True)
class ContextRenderStats:
    estimated_tokens_before: int
    estimated_tokens_after: int
    messages_before: int
    messages_after: int
    omitted_messages: int = 0
    distilled_tool_outputs: int = 0
    masked_messages: int = 0
    did_apply_management: bool = False


@dataclass(frozen=True)
class RenderedContext:
    system_prompt: str
    history: tuple[Message, ...]
    stats: ContextRenderStats


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN) if text else 0


def estimate_message_tokens(message: Message) -> int:
    tool_cost = 0
    if message.tool_calls:
        tool_cost = estimate_tokens(json.dumps([c.__dict__ for c in message.tool_calls], ensure_ascii=False, default=str))
    return estimate_tokens(message.content) + tool_cost + 4


def estimate_context_tokens(system_prompt: str, messages: Sequence[Message]) -> int:
    return estimate_tokens(system_prompt) + sum(estimate_message_tokens(m) for m in messages)


class ConversationContextManager:
    """Render a bounded provider-facing context from the full transcript.

    The manager is stateless across calls. That keeps the durable session history
    untouched and lets the orchestrator create a fresh instance per turn from the
    live config.
    """

    def __init__(self, config: ContextConfig) -> None:
        self._config = config

    def render(self, system_prompt: str, messages: Sequence[Message]) -> RenderedContext:
        before_tokens = estimate_context_tokens(system_prompt, messages)
        messages_before = len(messages)

        if not self._config.enabled:
            stats = ContextRenderStats(
                estimated_tokens_before=before_tokens,
                estimated_tokens_after=before_tokens,
                messages_before=messages_before,
                messages_after=messages_before,
            )
            return RenderedContext(system_prompt, tuple(messages), stats)

        managed, masked_count, distilled_count = self._manage_messages(messages)
        managed = self._drop_orphaned_tool_prefix(tuple(managed))

        after_manage = estimate_context_tokens(system_prompt, managed)
        omitted_messages = 0
        final_system = system_prompt
        final_history = tuple(managed)

        if after_manage > self._config.max_tokens:
            final_system, final_history, omitted_messages = self._compress_history(system_prompt, final_history)

        after_tokens = estimate_context_tokens(final_system, final_history)
        stats = ContextRenderStats(
            estimated_tokens_before=before_tokens,
            estimated_tokens_after=after_tokens,
            messages_before=messages_before,
            messages_after=len(final_history),
            omitted_messages=omitted_messages,
            distilled_tool_outputs=distilled_count,
            masked_messages=masked_count,
            did_apply_management=(
                masked_count > 0
                or distilled_count > 0
                or omitted_messages > 0
                or len(final_history) != messages_before
            ),
        )
        return RenderedContext(final_system, final_history, stats)

    def _manage_messages(self, messages: Sequence[Message]) -> tuple[list[Message], int, int]:
        managed: list[Message] = []
        masked_count = 0
        distilled_count = 0
        for message in messages:
            content = message.content
            masked = _mask_secrets(content)
            if masked != content:
                masked_count += 1
                content = masked

            if message.role == "tool" and estimate_tokens(content) > self._config.tool_output_threshold_tokens:
                content = _distill_text(
                    content,
                    max_tokens=self._config.tool_output_retained_tokens,
                    label="tool output",
                )
                distilled_count += 1
            elif estimate_tokens(content) > self._config.message_threshold_tokens:
                content = _distill_text(
                    content,
                    max_tokens=self._config.message_retained_tokens,
                    label=f"{message.role} message",
                )
                distilled_count += 1

            if content == message.content:
                managed.append(message)
            else:
                metadata = {
                    **message.metadata,
                    "context_management": {
                        **(message.metadata.get("context_management") or {}),
                        "masked_or_distilled": True,
                    },
                }
                managed.append(
                    Message(
                        role=message.role,
                        content=content,
                        id=message.id,
                        ts=message.ts,
                        tool_calls=message.tool_calls,
                        metadata=metadata,
                    )
                )
        return managed, masked_count, distilled_count

    def _compress_history(self, system_prompt: str, messages: tuple[Message, ...]) -> tuple[str, tuple[Message, ...], int]:
        if not messages:
            return system_prompt, messages, 0

        target = min(self._config.retained_tokens, self._config.max_tokens)
        retained: list[Message] = []
        retained_tokens = estimate_tokens(system_prompt)

        # Always protect a small suffix. This mirrors Gemini's recent-turn
        # protection and keeps the current tool-call adjacency intact.
        protected_suffix = list(messages[-self._config.recent_messages :])
        protected_ids = {m.id for m in protected_suffix}

        for message in reversed(messages):
            cost = estimate_message_tokens(message)
            if message.id in protected_ids or retained_tokens + cost <= target:
                retained.append(message)
                retained_tokens += cost
            else:
                continue

        retained.reverse()
        retained_ids_before_wire_repair = {m.id for m in retained}
        omitted = [m for m in messages if m.id not in retained_ids_before_wire_repair]
        retained = list(_repair_tool_adjacency(tuple(retained)))

        if not omitted:
            return system_prompt, tuple(retained), 0

        summary = _summarize_omitted(omitted, self._config.summary_tokens)
        final_system = "\n\n".join(
            part
            for part in (
                system_prompt.strip(),
                "## Compressed Conversation Context\n"
                "Older transcript messages were compressed to keep the active context bounded. "
                "Use this summary as background, but rely on the retained recent messages for exact details.\n\n"
                f"{summary}",
            )
            if part
        )
        return final_system, tuple(retained), len(omitted)

    @staticmethod
    def _drop_orphaned_tool_prefix(messages: tuple[Message, ...]) -> tuple[Message, ...]:
        trimmed = list(messages)
        while trimmed and trimmed[0].role == "tool":
            trimmed.pop(0)
        return tuple(trimmed)


def _mask_secrets(text: str) -> str:
    masked = text
    for pattern, replacement in _MASK_PATTERNS:
        masked = pattern.sub(replacement, masked)
    return masked


def _repair_tool_adjacency(messages: tuple[Message, ...]) -> tuple[Message, ...]:
    """Drop tool-use fragments that became invalid after budget selection.

    Compression may skip an oversized message but keep a shorter neighbor. That
    is useful for retaining more context, but provider APIs require assistant
    tool_calls and their tool results to remain adjacent. Wire-format repair is
    not counted as "omitted context" and is not summarized.
    """
    repaired: list[Message] = []
    i = 0
    while i < len(messages):
        message = messages[i]
        if message.role == "tool":
            i += 1
            continue

        if message.role == "assistant" and message.tool_calls:
            expected_ids = {call.id for call in message.tool_calls}
            tool_block: list[Message] = []
            j = i + 1
            while j < len(messages) and messages[j].role == "tool":
                tool_block.append(messages[j])
                j += 1
            tool_ids = {m.metadata.get("tool_call_id") for m in tool_block}
            if expected_ids.issubset(tool_ids):
                repaired.append(message)
                repaired.extend(m for m in tool_block if m.metadata.get("tool_call_id") in expected_ids)
            elif message.content:
                repaired.append(
                    Message(
                        role=message.role,
                        content=message.content,
                        id=message.id,
                        ts=message.ts,
                        metadata=message.metadata,
                    )
                )
            i = j
            continue

        repaired.append(message)
        i += 1

    return tuple(repaired)


def _distill_text(text: str, *, max_tokens: int, label: str) -> str:
    max_chars = max(200, max_tokens * _CHARS_PER_TOKEN)
    if len(text) <= max_chars:
        return text
    head_chars = max_chars // 2
    tail_chars = max_chars - head_chars
    omitted = len(text) - max_chars
    return (
        f"[Context management distilled this {label}; original length {len(text)} chars, "
        f"omitted {omitted} chars.]\n"
        f"{text[:head_chars].rstrip()}\n"
        "\n[... middle omitted ...]\n"
        f"{text[-tail_chars:].lstrip()}"
    )


def _summarize_omitted(messages: Sequence[Message], summary_tokens: int) -> str:
    budget_chars = max(400, summary_tokens * _CHARS_PER_TOKEN)
    lines: list[str] = []
    remaining = budget_chars
    for message in messages:
        if remaining <= 0:
            break
        prefix = _message_prefix(message)
        body = _compact_one_line(message.content)
        limit = max(80, min(500, remaining - len(prefix) - 8))
        if len(body) > limit:
            body = body[:limit].rstrip() + "..."
        line = f"- {prefix}: {body}"
        lines.append(line)
        remaining -= len(line) + 1
    if len(messages) > len(lines):
        lines.append(f"- ... {len(messages) - len(lines)} more older messages omitted.")
    return "\n".join(lines)


def _message_prefix(message: Message) -> str:
    if message.role == "assistant" and message.tool_calls:
        names = ", ".join(call.tool_name for call in message.tool_calls[:3])
        return f"assistant requested tool(s) {names}"
    if message.role == "tool":
        tool_call_id = message.metadata.get("tool_call_id")
        return f"tool result {tool_call_id}" if tool_call_id else "tool result"
    return message.role


def _compact_one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
