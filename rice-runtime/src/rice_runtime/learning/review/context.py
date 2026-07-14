"""learning/review/context.py — 리뷰 전용 컨텍스트 매니저.

WHY THIS FILE EXISTS
--------------------
메인 에이전트 턴은 `agent/context_manager.py`를 타지만, 리뷰 엔진은 지금까지
세션 히스토리를 거의 그대로 `build_review_messages()`에 넘겼다. 긴 세션에서는
(1) 리뷰 호출 자체가 provider 한도에서 터지고, (2) 메인 모델이 (컨텍스트 관리로)
못 본 내용을 리뷰 모델이 전부 보고 다른 판단을 할 수 있었다.

이 모듈은 리뷰 목적에 맞는 **별도** 렌더러다 — agent 쪽 구현을 import하지 않는
이유는 모듈 경계 규칙(RUNTIME_DIRECTORY §5: 모듈 간 직접 import 금지) 때문이고,
목적도 다르다: 메인 턴 렌더는 wire 형식(tool_use 블록 인접성)을 보존해야 하지만,
리뷰는 "무슨 일이 있었는가"만 판단하면 되므로 아예 **순수 텍스트 전사**로
변환한다. 그 결과:

  - 최근 턴: 전문 유지(과대 tool 출력만 head/tail 증류), 도구 호출/결과는
    텍스트 라벨로 표현 → tool_use wire 규칙에서 완전히 해방(항상 유효).
  - 오래된 턴: message id가 달린 구조화 요약 한 블록 — "관찰된 사실" 수준.
  - user/assistant 역할 교대는 병합으로 보장(공급자 어댑터 안전).

메시지 id 라벨은 감사/근거 추적용이다: 리뷰 제안이 어느 메시지에서 왔는지
사람이 역추적할 수 있다(P0 "감사 가능한 렌더 로그"와 같은 원칙).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from ...core.types import Message

_CHARS_PER_TOKEN = 4  # _clip의 char 상한 환산용(추정과 무관)


def filter_learning_history(history: Sequence[Message]) -> list[Message]:
    """학습 판단의 근거가 될 실제 대화만 남긴다.

    AgentLoop는 계획 종결·빈 응답·서사 교정을 위해 모델에게 synthetic
    user 메시지를 넣는다. 이 메시지는 런타임 감사와 다음 라운드 역할
    인접을 위해 세션에 보존될 수 있지만, 사용자가 밝힌 선호·교정·사실은
    아니다. 마찬가지로 ephemeral 렌더 메시지도 영속 학습 근거가 아니다.

    이 필터는 리뷰 프롬프트뿐 아니라 리뷰 게이트와 usage/friction
    텔레메트리에서도 공유해, 내부 제어문이 학습 신호로 변질되는
    경로를 한 곳에서 차단한다.
    """
    return [
        message
        for message in history
        if not message.metadata.get("synthetic") and not message.metadata.get("ephemeral")
    ]


def _estimate_tokens(text: str) -> int:
    from ...core.tokens import estimate_tokens  # noqa: PLC0415

    return estimate_tokens(text)


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _clip(text: str, max_tokens: int, label: str) -> str:
    """head/tail 증류 — agent 쪽 `_distill_text`와 같은 모양이지만 리뷰 톤."""
    max_chars = max(160, max_tokens * _CHARS_PER_TOKEN)
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head
    return (
        f"[{label} — {len(text)}자 중 중간 생략]\n"
        f"{text[:head].rstrip()}\n[...]\n{text[-tail:].lstrip()}"
    )


@dataclass(frozen=True)
class ReviewContextConfig:
    """LearningConfig의 review_context_* 노브를 담는 값 객체."""

    recent_messages: int = 12
    max_total_tokens: int = 24_000
    tool_output_max_tokens: int = 600
    message_max_tokens: int = 2_000
    older_summary_tokens: int = 2_000


class LearningContextManager:
    """세션 히스토리 → 리뷰 모델용 bounded 순수 텍스트 전사."""

    def __init__(self, config: ReviewContextConfig) -> None:
        self._config = config

    def render(self, history: Sequence[Message]) -> list[Message]:
        cfg = self._config
        reviewable = filter_learning_history(history)
        recent = reviewable[-cfg.recent_messages :] if cfg.recent_messages > 0 else reviewable
        older = reviewable[: len(reviewable) - len(recent)]

        blocks: list[tuple[str, str]] = []  # (role, text) — 병합 전
        if older:
            blocks.append(("user", self._summarize_older(older)))

        for message in recent:
            role, text = self._render_recent(message)
            if text:
                blocks.append((role, text))

        merged = self._merge_roles(blocks)

        # 총 예산 초과 시 오래된 블록부터 버린다(요약 블록 우선 제거 — 최근 전문이
        # 리뷰 판단의 핵심이다). 남는 것이 하나뿐이면 그것을 잘라서라도 맞춘다.
        while (
            sum(_estimate_tokens(t) for _, t in merged) > cfg.max_total_tokens and len(merged) > 1
        ):
            merged.pop(0)
        if merged and _estimate_tokens(merged[0][1]) > cfg.max_total_tokens:
            role, text = merged[0]
            merged[0] = (role, _clip(text, cfg.max_total_tokens, "리뷰 컨텍스트"))

        return [Message(role=role, content=text) for role, text in merged]  # type: ignore[arg-type]

    # ── internals ────────────────────────────────────────────────────────────
    def _render_recent(self, message: Message) -> tuple[str, str]:
        cfg = self._config
        if message.role == "tool":
            call_id = message.metadata.get("tool_call_id") or "?"
            body = _clip(message.content, cfg.tool_output_max_tokens, "도구 출력")
            # 도구 결과는 assistant 행동 라운드의 일부로 묶는다(역할 병합 유도).
            return "assistant", f"[tool result {call_id} · msg {message.id}]\n{body}"

        text = _clip(message.content, cfg.message_max_tokens, f"{message.role} 메시지")
        if message.role == "assistant" and message.tool_calls:
            names = ", ".join(c.tool_name for c in message.tool_calls)
            suffix = f"[requested tools: {names}]"
            text = f"{text}\n{suffix}" if text else suffix
        role = message.role if message.role in ("user", "assistant") else "user"
        return role, text

    def _summarize_older(self, older: Sequence[Message]) -> str:
        budget_chars = max(400, self._config.older_summary_tokens * _CHARS_PER_TOKEN)
        lines = [
            "## Earlier conversation (compressed for learning review)",
            "Older messages are summarized one per line with their message ids — "
            "treat them as observed facts, and rely on the recent messages below for exact detail.",
        ]
        remaining = budget_chars
        rendered = 0
        for message in older:
            if remaining <= 0:
                break
            if message.role == "tool":
                ok = "error" if message.content.startswith("ERROR:") else "ok"
                prefix = f"[{message.id}] tool result ({ok})"
            elif message.role == "assistant" and message.tool_calls:
                names = ", ".join(c.tool_name for c in message.tool_calls[:3])
                prefix = f"[{message.id}] assistant → tools: {names}"
            else:
                prefix = f"[{message.id}] {message.role}"
            body = _one_line(message.content)
            limit = max(60, min(400, remaining - len(prefix) - 8))
            if len(body) > limit:
                body = body[:limit].rstrip() + "..."
            line = f"- {prefix}: {body}" if body else f"- {prefix}"
            lines.append(line)
            remaining -= len(line) + 1
            rendered += 1
        if rendered < len(older):
            lines.append(f"- ... {len(older) - rendered} more older messages omitted.")
        return "\n".join(lines)

    @staticmethod
    def _merge_roles(blocks: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """연속 동일 역할을 하나로 합쳐 user/assistant 교대를 보장한다."""
        merged: list[tuple[str, str]] = []
        for role, text in blocks:
            if merged and merged[-1][0] == role:
                merged[-1] = (role, merged[-1][1] + "\n\n" + text)
            else:
                merged.append((role, text))
        return merged
