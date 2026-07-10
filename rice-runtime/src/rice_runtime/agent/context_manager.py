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
from typing import Callable, Sequence

from ..core.config import ContextConfig
from ..core.tokens import estimate_tokens as _cjk_estimate_tokens
from ..core.types import Message


# 언어 인식 추정기(core.tokens)로 예산을 계산한다. 이전의 len//4는 한국어에서
# 토큰을 2~4배 과소평가하는 "거짓 안전판"이었다.
# _CHARS_PER_TOKEN은 _distill_text의 char 상한 환산에만 남는다(추정과 무관한
# "얼마나 잘라낼까"의 대략치).
_CHARS_PER_TOKEN = 4
_MASK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?([A-Za-z0-9_\-./+=]{12,})"), r"\1=[REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "sk-[REDACTED]"),
    (re.compile(r"ghp_[A-Za-z0-9_]{20,}"), "ghp_[REDACTED]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "github_pat_[REDACTED]"),
    (re.compile(r"glpat-[A-Za-z0-9_\-]{20,}"), "glpat-[REDACTED]"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"), "xox-[REDACTED]"),
    # AWS access key id — 접두사+16자 대문자/숫자 고정 포맷(오탐 여지 낮음).
    (re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"), "[REDACTED-AWS-KEY]"),
    # Google API key — AIza 접두사 + 35자 고정.
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "[REDACTED-GOOGLE-KEY]"),
    # Authorization 헤더의 bearer 토큰(도구가 HTTP 요청/응답 덤프를 돌려줄 때).
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-.~+/]{16,}=*"), "Bearer [REDACTED]"),
    # JWT — header.payload.signature 3분절(각 분절 base64url).
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"), "[REDACTED-JWT]"),
    # Slack incoming webhook — URL 자체가 자격증명이다.
    (re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_\-]+"), "https://hooks.slack.com/services/[REDACTED]"),
    # PEM 개인키 블록 — 파일을 read_file로 읽어온 경우.
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[A-Za-z0-9+/=\s]+?-----END [A-Z ]*PRIVATE KEY-----"),
        "[REDACTED PRIVATE KEY]",
    ),
)


@dataclass(frozen=True)
class ContextRenderStats:
    """Per-LLM-call render audit.

    카운트만으로는 "모델이 정확히 뭘 못 봤는가"를 설명할 수 없다 — 특히 wire
    repair(`_repair_tool_adjacency`)는 도구 메시지를 조용히 떨어뜨린다. id 튜플이
    그 구멍을 감사 가능하게 만든다: 렌더마다 어떤 메시지가 요약으로 대체됐고
    (omitted), 어떤 도구 결과가 wire 수리로 삭제됐는지(dropped) 그대로 남는다.
    Orchestrator가 턴 종료 시 ledger에 `context_rendered` 이벤트로 영속화한다."""

    estimated_tokens_before: int
    estimated_tokens_after: int
    messages_before: int
    messages_after: int
    omitted_messages: int = 0
    distilled_tool_outputs: int = 0
    masked_messages: int = 0
    did_apply_management: bool = False
    # ── audit ids ────────────────────────────────────────────────────────────
    omitted_message_ids: tuple[str, ...] = ()  # 압축 요약으로 대체된 메시지
    dropped_message_ids: tuple[str, ...] = ()  # wire repair/orphan 정리로 삭제된 메시지
    dropped_tool_call_ids: tuple[str, ...] = ()  # 결과를 잃어 제거된 tool_call id
    distilled_message_ids: tuple[str, ...] = ()  # head/tail 압축된 메시지
    masked_message_ids: tuple[str, ...] = ()  # 시크릿 마스킹된 메시지

    def to_dict(self) -> dict:
        """Compact JSON for the ledger event — empty audit lists are elided."""
        d: dict = {
            "tokens_before": self.estimated_tokens_before,
            "tokens_after": self.estimated_tokens_after,
            "messages_before": self.messages_before,
            "messages_after": self.messages_after,
            "managed": self.did_apply_management,
        }
        if self.omitted_message_ids:
            d["omitted_message_ids"] = list(self.omitted_message_ids)
        if self.dropped_message_ids:
            d["dropped_message_ids"] = list(self.dropped_message_ids)
        if self.dropped_tool_call_ids:
            d["dropped_tool_call_ids"] = list(self.dropped_tool_call_ids)
        if self.distilled_message_ids:
            d["distilled_message_ids"] = list(self.distilled_message_ids)
        if self.masked_message_ids:
            d["masked_message_ids"] = list(self.masked_message_ids)
        return d


@dataclass(frozen=True)
class RenderedContext:
    system_prompt: str
    history: tuple[Message, ...]
    stats: ContextRenderStats


# Module-level estimators default to the CJK-aware heuristic (core.tokens). They
# stay as free functions for back-compat with any direct importer; the class uses
# its injected estimator (which may be calibration-backed) instead.
def estimate_tokens(text: str) -> int:
    return _cjk_estimate_tokens(text)


# 이미지 첨부 1장의 보수적 토큰 추정 — Claude 기준 최대 해상도(1568px) 이미지가
# ~1,600토큰이다. 크기를 디코드하지 않으므로 상한으로 잡는다(과대추정이 안전한
# 방향 — core/tokens.py의 철학과 동일). 예산이 이 무게를 보므로 이미지가 쌓이면
# 압축이 오래된 이미지 메시지를 먼저 접는다.
_IMAGE_TOKEN_ESTIMATE = 1_600


def estimate_message_tokens(message: Message, estimate: Callable[[str], int] = _cjk_estimate_tokens) -> int:
    tool_cost = 0
    if message.tool_calls:
        tool_cost = estimate(json.dumps([c.__dict__ for c in message.tool_calls], ensure_ascii=False, default=str))
    image_cost = _IMAGE_TOKEN_ESTIMATE * len(message.metadata.get("images") or ())
    return estimate(message.content) + tool_cost + image_cost + 4


def estimate_context_tokens(
    system_prompt: str, messages: Sequence[Message], estimate: Callable[[str], int] = _cjk_estimate_tokens
) -> int:
    return estimate(system_prompt) + sum(estimate_message_tokens(m, estimate) for m in messages)


class ConversationContextManager:
    """Render a bounded provider-facing context from the full transcript.

    The manager is stateless across calls. That keeps the durable session history
    untouched and lets the orchestrator create a fresh instance per turn from the
    live config.

    `estimate`: the token estimator used for all budgeting. Defaults
    to the CJK-aware heuristic; the orchestrator injects `TokenCalibrator.estimate`
    so real provider usage feedback refines the budget over the session.
    `budget_scale` (< 1.0) shrinks every token limit for the context-length-error
    retry path — the loop re-renders tighter after a provider "prompt too long".
    """

    def __init__(
        self,
        config: ContextConfig,
        *,
        estimate: Callable[[str], int] | None = None,
        budget_scale: float = 1.0,
    ) -> None:
        self._config = config
        self._estimate = estimate or _cjk_estimate_tokens
        self._scale = budget_scale

    @property
    def config(self) -> ContextConfig:
        return self._config

    @property
    def estimate_fn(self) -> Callable[[str], int]:
        return self._estimate

    def _msg_tokens(self, message: Message) -> int:
        return estimate_message_tokens(message, self._estimate)

    def _ctx_tokens(self, system_prompt: str, messages: Sequence[Message]) -> int:
        return estimate_context_tokens(system_prompt, messages, self._estimate)

    def _limit(self, base: int) -> int:
        return max(1, int(base * self._scale))

    def render(self, system_prompt: str, messages: Sequence[Message]) -> RenderedContext:
        before_tokens = self._ctx_tokens(system_prompt, messages)
        messages_before = len(messages)

        if not self._config.enabled:
            stats = ContextRenderStats(
                estimated_tokens_before=before_tokens,
                estimated_tokens_after=before_tokens,
                messages_before=messages_before,
                messages_after=messages_before,
            )
            return RenderedContext(system_prompt, tuple(messages), stats)

        managed, masked_ids, distilled_ids = self._manage_messages(messages)
        managed, orphan_ids = self._drop_orphaned_tool_prefix(tuple(managed))

        after_manage = self._ctx_tokens(system_prompt, managed)
        omitted_ids: tuple[str, ...] = ()
        dropped_msg_ids: list[str] = list(orphan_ids)
        dropped_call_ids: list[str] = []
        final_system = system_prompt
        final_history = tuple(managed)

        if after_manage > self._limit(self._config.max_tokens):
            final_system, final_history, omitted_ids, repair_msg_ids, repair_call_ids = (
                self._compress_history(system_prompt, final_history)
            )
            dropped_msg_ids.extend(repair_msg_ids)
            dropped_call_ids.extend(repair_call_ids)

        after_tokens = self._ctx_tokens(final_system, final_history)
        stats = ContextRenderStats(
            estimated_tokens_before=before_tokens,
            estimated_tokens_after=after_tokens,
            messages_before=messages_before,
            messages_after=len(final_history),
            omitted_messages=len(omitted_ids),
            distilled_tool_outputs=len(distilled_ids),
            masked_messages=len(masked_ids),
            did_apply_management=(
                bool(masked_ids)
                or bool(distilled_ids)
                or bool(omitted_ids)
                or len(final_history) != messages_before
            ),
            omitted_message_ids=omitted_ids,
            dropped_message_ids=tuple(dropped_msg_ids),
            dropped_tool_call_ids=tuple(dropped_call_ids),
            distilled_message_ids=distilled_ids,
            masked_message_ids=masked_ids,
        )
        return RenderedContext(final_system, final_history, stats)

    def _manage_messages(
        self, messages: Sequence[Message]
    ) -> tuple[list[Message], tuple[str, ...], tuple[str, ...]]:
        managed: list[Message] = []
        masked_ids: list[str] = []
        distilled_ids: list[str] = []
        # ReAct 재검증 보호: 최근 메시지(마지막 recent_messages개)의 도구 출력은
        # 증류 임계·보존량을 3배로 완화한다. 모델이 방금 만든 산출물을 read_file로
        # 확인하는 관찰이 head/tail로 접히면 "검증했다"가 착각이 된다 — 방금의
        # 관찰은 원문에 가깝게, 오래된 관찰은 공격적으로 접는다. 압축의 suffix
        # 보호(recent_messages)와 같은 노브를 쓴다.
        recent_start = max(0, len(messages) - self._config.recent_messages)
        for i, message in enumerate(messages):
            content = message.content
            masked = _mask_secrets(content)
            if masked != content:
                masked_ids.append(message.id)
                content = masked

            recency_relief = 3 if i >= recent_start else 1
            if message.role == "tool" and self._estimate(content) > self._limit(
                self._config.tool_output_threshold_tokens * recency_relief
            ):
                content = _distill_text(
                    content,
                    max_tokens=self._limit(self._config.tool_output_retained_tokens * recency_relief),
                    label="tool output",
                )
                distilled_ids.append(message.id)
            elif message.role != "tool" and self._estimate(content) > self._limit(
                self._config.message_threshold_tokens
            ):
                content = _distill_text(
                    content,
                    max_tokens=self._limit(self._config.message_retained_tokens),
                    label=f"{message.role} message",
                )
                distilled_ids.append(message.id)

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
        return managed, tuple(masked_ids), tuple(distilled_ids)

    def _compress_history(
        self, system_prompt: str, messages: tuple[Message, ...]
    ) -> tuple[str, tuple[Message, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        """Returns (system, history, omitted_message_ids, wire-repair-dropped
        message ids, wire-repair-dropped tool_call ids) — id 단위 감사가 목적
        (ContextRenderStats docstring 참고)."""
        if not messages:
            return system_prompt, messages, (), (), ()

        target = self._limit(min(self._config.retained_tokens, self._config.max_tokens))
        retained: list[Message] = []
        retained_tokens = self._estimate(system_prompt)

        # Always protect a small suffix. This mirrors Gemini's recent-turn
        # protection and keeps the current tool-call adjacency intact.
        protected_suffix = list(messages[-self._config.recent_messages :])
        protected_ids = {m.id for m in protected_suffix}

        for message in reversed(messages):
            cost = self._msg_tokens(message)
            if message.id in protected_ids or retained_tokens + cost <= target:
                retained.append(message)
                retained_tokens += cost
            else:
                continue

        retained.reverse()
        retained_ids_before_wire_repair = {m.id for m in retained}
        omitted = [m for m in messages if m.id not in retained_ids_before_wire_repair]
        retained, dropped_msg_ids, dropped_call_ids = _repair_tool_adjacency(tuple(retained))
        retained = list(retained)

        if not omitted:
            return system_prompt, tuple(retained), (), dropped_msg_ids, dropped_call_ids

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
        return (
            final_system,
            tuple(retained),
            tuple(m.id for m in omitted),
            dropped_msg_ids,
            dropped_call_ids,
        )

    @staticmethod
    def _drop_orphaned_tool_prefix(
        messages: tuple[Message, ...],
    ) -> tuple[tuple[Message, ...], tuple[str, ...]]:
        trimmed = list(messages)
        dropped: list[str] = []
        while trimmed and trimmed[0].role == "tool":
            dropped.append(trimmed.pop(0).id)
        return tuple(trimmed), tuple(dropped)


def _mask_secrets(text: str) -> str:
    masked = text
    for pattern, replacement in _MASK_PATTERNS:
        masked = pattern.sub(replacement, masked)
    return masked


def _repair_tool_adjacency(
    messages: tuple[Message, ...],
) -> tuple[tuple[Message, ...], tuple[str, ...], tuple[str, ...]]:
    """Drop tool-use fragments that became invalid after budget selection.

    Compression may skip an oversized message but keep a shorter neighbor. That
    is useful for retaining more context, but provider APIs require assistant
    tool_calls and their tool results to remain adjacent. Wire-format repair is
    not counted as "omitted context" and is not summarized — which is exactly
    why it must be *audited*: returns (repaired, dropped message ids, dropped
    tool_call ids) so the render log can say what silently fell out.
    """
    repaired: list[Message] = []
    dropped_msg_ids: list[str] = []
    dropped_call_ids: list[str] = []
    i = 0
    while i < len(messages):
        message = messages[i]
        if message.role == "tool":
            # orphaned tool result (its assistant tool_call didn't survive)
            dropped_msg_ids.append(message.id)
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
                for m in tool_block:
                    if m.metadata.get("tool_call_id") in expected_ids:
                        repaired.append(m)
                    else:
                        dropped_msg_ids.append(m.id)
            else:
                # incomplete tool round: strip the tool_calls (and their results)
                dropped_call_ids.extend(call.id for call in message.tool_calls)
                dropped_msg_ids.extend(m.id for m in tool_block)
                if message.content:
                    repaired.append(
                        Message(
                            role=message.role,
                            content=message.content,
                            id=message.id,
                            ts=message.ts,
                            metadata=message.metadata,
                        )
                    )
                else:
                    dropped_msg_ids.append(message.id)
            i = j
            continue

        repaired.append(message)
        i += 1

    return tuple(repaired), tuple(dropped_msg_ids), tuple(dropped_call_ids)


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
