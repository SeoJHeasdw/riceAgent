"""Turn-time context rendering for the agent loop.

This is RICE's Python adaptation of the useful parts of Gemini CLI's context
management layer:

* the durable transcript remains the source of truth;
* every LLM call receives a rendered view of that transcript;
* older pressure is compressed into a summary message at the head of history;
* tool output is masked/distilled before it is re-sent to the model;
* recent tool-call adjacency is preserved so provider wire formats stay valid.

It is intentionally smaller than Gemini's graph pipeline. RICE already has a
clean orchestrator seam, so this module provides the same product behavior
without importing a large graph engine before we need it.

★ 프리픽스 캐시 불변식 (2026-07-11): 렌더는 **결정을 기억한다**. Ollama(및 Claude
prompt cache)는 직전 요청과의 최장 공통 프리픽스만 재사용하므로, 라운드마다
압축 요약·증류 형태가 조금씩 달라지면 매 호출 17K+ 토큰 전체가 re-prefill된다
(실측 101~132초/호출 — "4~5턴부터 130초" 피드백의 근본 원인). 그래서:

  1. 압축 요약은 시스템 프롬프트가 아니라 **히스토리 선두의 synthetic 메시지**다
     — 시스템 프롬프트(프롬프트 최선두)는 턴 내 불변으로 남는다.
  2. 한 번 렌더된 메시지 형태(원문/증류본/마스킹본)는 `ContextRenderState.forms`에
     동결된다 — recency 창이 밀리거나 캘리브레이터 비율이 흔들려도 이미 렌더된
     프리픽스는 바뀌지 않는다(hysteresis).
  3. 압축은 **체크포인트**다: 예산을 넘는 순간에만 요약·생략 집합을 새로 계산해
     동결하고(그 1회만 프리픽스가 깨진다), 이후 렌더는 동결된 스냅샷에 새 라운드를
     append만 한다. 재압축은 다시 예산을 넘을 때까지 일어나지 않는다
     (max_tokens ↔ retained_tokens 사이의 headroom이 자연 hysteresis 밴드).

`ContextRenderState`는 세션 스코프다(orchestrator가 세션별로 보관·주입) — 턴
경계에서도 동결이 유지돼 턴 시작의 전체 re-prefill을 막는다.
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


@dataclass
class _RenderedForm:
    """한 메시지의 동결된 렌더 형태 — 마스킹/증류가 적용된 최종 본문."""

    content: str
    masked: bool
    distilled: bool


@dataclass(frozen=True)
class _CompressionCheckpoint:
    """동결된 압축 스냅샷. 다음 체크포인트까지 모든 렌더가 이 생략 집합과 요약을
    그대로 재사용한다 — 요약 본문·생략 경계가 렌더 간 불변이어야 프리픽스 캐시가
    산다. summary_id가 고정이라 요약 메시지는 wire 상 매 렌더 동일하다."""

    omitted_ids: frozenset[str]
    summary_content: str
    summary_id: str


class ContextRenderState:
    """세션 스코프 렌더 메모리(모듈 docstring ★ 불변식의 저장소).

    orchestrator가 세션별로 하나를 보관하고 매 턴 ConversationContextManager에
    주입한다. 주지 않으면(테스트·sub-agent 1회성 렌더) 매니저가 사적 인스턴스를
    만들어 기존의 무상태 동작과 동일해진다."""

    def __init__(self) -> None:
        self.forms: dict[str, _RenderedForm] = {}
        self.checkpoint: _CompressionCheckpoint | None = None
        self.checkpoint_seq: int = 0


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

# 체크포인트 구성 시 wire-수리 고정점 반복 상한(`_build_checkpoint`). 실측상 2패스면
# 수렴한다 — 상한은 무한루프 방어용이다.
_MAX_REPAIR_PASSES = 8


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

    The durable session history stays untouched; the orchestrator creates a fresh
    instance per turn from the live config, but injects a session-scoped
    `ContextRenderState` so render *decisions* (frozen message forms + compression
    checkpoint) survive across calls and turns — the prefix-cache invariant in the
    module docstring.

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
        state: ContextRenderState | None = None,
    ) -> None:
        self._config = config
        self._estimate = estimate or _cjk_estimate_tokens
        self._scale = budget_scale
        self._state = state if state is not None else ContextRenderState()

    @property
    def config(self) -> ContextConfig:
        return self._config

    @property
    def estimate_fn(self) -> Callable[[str], int]:
        return self._estimate

    @property
    def state(self) -> ContextRenderState:
        return self._state

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

        # 형태 캐시 위생은 매 렌더 수행한다 — 체크포인트 시점에만 하면, 예산을
        # 오래 안 넘는 세션에서 regenerate 절단으로 사라진 메시지의 동결 형태가
        # 무기한 잔류한다(세션 상태는 턴을 넘어 산다). 현재 트랜스크립트에 있는
        # id는 건드리지 않으므로 동결(프리픽스 캐시) 불변식은 그대로다.
        self._prune_forms(messages)
        managed, masked_ids, distilled_ids = self._manage_messages(messages)
        checkpoint = self._state.checkpoint

        def _apply(msgs: list[Message], cp: _CompressionCheckpoint | None) -> tuple[Message, ...]:
            return tuple(m for m in msgs if cp is None or m.id not in cp.omitted_ids)

        # 동결된 체크포인트를 적용한 뷰가 예산을 넘는 순간에만 새 체크포인트를
        # 뜬다(모듈 docstring ★ 3). 새 체크포인트는 형태 캐시도 현재 recency 창
        # 기준으로 전면 재계산한다 — 어차피 프리픽스가 깨지는 지점이므로, 이때
        # relief로 원문 유지되던 오래된 관찰들을 접어 예산을 회수한다.
        assembled = self._assemble(_apply(managed, checkpoint), checkpoint)
        if self._ctx_tokens(system_prompt, assembled) > self._limit(self._config.max_tokens):
            managed, masked_ids, distilled_ids = self._manage_messages(messages, refresh=True)
            checkpoint = self._build_checkpoint(system_prompt, tuple(managed))
            self._state.checkpoint = checkpoint

        visible = _apply(managed, checkpoint)
        visible, orphan_ids = self._drop_orphaned_tool_prefix(visible)
        # wire 수리는 매 렌더 최종 시퀀스 위에서 돈다 — 순수 함수이고 블록 지역적
        # 판정이라 append-only 시퀀스에선 프리픽스가 안정적이며, 체크포인트 생략이
        # 도구 라운드 중간을 자른 경우의 고아 결과를 결정적으로 걷어낸다.
        visible, repair_msg_ids, repair_call_ids = _repair_tool_adjacency(visible)
        final_history = self._assemble(visible, checkpoint)

        omitted_ids = (
            tuple(m.id for m in messages if m.id in checkpoint.omitted_ids)
            if checkpoint is not None
            else ()
        )
        after_tokens = self._ctx_tokens(system_prompt, final_history)
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
            dropped_message_ids=tuple(orphan_ids) + repair_msg_ids,
            dropped_tool_call_ids=repair_call_ids,
            distilled_message_ids=distilled_ids,
            masked_message_ids=masked_ids,
        )
        return RenderedContext(system_prompt, final_history, stats)

    def _assemble(
        self, visible: Sequence[Message], checkpoint: _CompressionCheckpoint | None
    ) -> tuple[Message, ...]:
        """압축 요약을 히스토리 **선두**의 synthetic user 메시지로 싣는다.

        시스템 프롬프트에 붙이지 않는 이유(모듈 docstring ★ 1): 요약은 체크포인트
        마다 변하는 텍스트라, 시스템에 실으면 프롬프트 최선두부터 달라져 전체가
        re-prefill된다. role="user"인 이유: 히스토리 중간/선두 system은 Claude
        wire에서 떨어져 나가고, 연속 user 메시지는 API가 한 턴으로 합쳐 허용된다
        (loop.py의 plan_status tail과 같은 규칙)."""
        if checkpoint is None or not checkpoint.summary_content:
            return tuple(visible)
        summary = Message(
            role="user",
            content=checkpoint.summary_content,
            id=checkpoint.summary_id,
            ts=0,  # 고정 — wire에 안 실리지만 렌더 간 동일 객체 의미를 유지한다
            metadata={"synthetic": "context_summary", "ephemeral": True},
        )
        return (summary,) + tuple(visible)

    def _manage_messages(
        self, messages: Sequence[Message], *, refresh: bool = False
    ) -> tuple[list[Message], tuple[str, ...], tuple[str, ...]]:
        """마스킹/증류를 적용하되 형태는 **첫 렌더에 동결**한다(모듈 docstring ★ 2).

        recency 창이 라운드마다 한 칸씩 밀리며 경계의 도구 출력이 원문→증류본으로
        플립되던 것이 프리픽스를 깨던 요인 — 형태 캐시가 그 플립을 없앤다.
        `refresh=True`(체크포인트 시점)면 현재 창 기준으로 전면 재계산해 캐시를
        덮어쓴다. ephemeral 메시지(plan_status 등 렌더 사본 전용 tail)는 캐시하지
        않는다 — id가 매 라운드 새로 발급돼 캐시만 불린다."""
        managed: list[Message] = []
        masked_ids: list[str] = []
        distilled_ids: list[str] = []
        recent_start = max(0, len(messages) - self._config.recent_messages)
        forms = self._state.forms
        for i, message in enumerate(messages):
            ephemeral = bool(message.metadata.get("ephemeral"))
            form = None if (refresh or ephemeral) else forms.get(message.id)
            if form is None:
                form = self._compute_form(message, recent=i >= recent_start)
                if not ephemeral:
                    forms[message.id] = form
            if form.masked:
                masked_ids.append(message.id)
            if form.distilled:
                distilled_ids.append(message.id)

            if form.content == message.content:
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
                        content=form.content,
                        id=message.id,
                        ts=message.ts,
                        tool_calls=message.tool_calls,
                        metadata=metadata,
                    )
                )
        return managed, tuple(masked_ids), tuple(distilled_ids)

    def _compute_form(self, message: Message, *, recent: bool) -> _RenderedForm:
        # ReAct 재검증 보호: 최근 메시지(마지막 recent_messages개)의 도구 출력은
        # 증류 임계·보존량을 3배로 완화한다. 모델이 방금 만든 산출물을 read_file로
        # 확인하는 관찰이 head/tail로 접히면 "검증했다"가 착각이 된다 — 방금의
        # 관찰은 원문에 가깝게, 오래된 관찰은 공격적으로 접는다. 압축의 suffix
        # 보호(recent_messages)와 같은 노브를 쓴다.
        content = message.content
        masked_flag = False
        masked = _mask_secrets(content)
        if masked != content:
            masked_flag = True
            content = masked

        recency_relief = 3 if recent else 1
        distilled_flag = False
        if message.role == "tool" and self._estimate(content) > self._limit(
            self._config.tool_output_threshold_tokens * recency_relief
        ):
            content = _distill_text(
                content,
                max_tokens=self._limit(self._config.tool_output_retained_tokens * recency_relief),
                label="tool output",
            )
            distilled_flag = True
        elif message.role != "tool" and self._estimate(content) > self._limit(
            self._config.message_threshold_tokens
        ):
            content = _distill_text(
                content,
                max_tokens=self._limit(self._config.message_retained_tokens),
                label=f"{message.role} message",
            )
            distilled_flag = True
        return _RenderedForm(content=content, masked=masked_flag, distilled=distilled_flag)

    def _build_checkpoint(
        self, system_prompt: str, managed: tuple[Message, ...]
    ) -> _CompressionCheckpoint | None:
        """예산 초과 시점의 압축 스냅샷을 새로 뜬다 — 렌더당 최대 1회, 이 지점만
        프리픽스가 깨진다. 요약은 managed(마스킹/증류 적용) 형태 위에서 만든다:
        시크릿이 요약으로 새지 않게 하는 기존 불변식 유지."""
        if not managed:
            return None

        target = self._limit(min(self._config.retained_tokens, self._config.max_tokens))
        retained: list[Message] = []
        retained_tokens = self._estimate(system_prompt)

        # Always protect a small suffix. This mirrors Gemini's recent-turn
        # protection and keeps the current tool-call adjacency intact.
        protected_ids = {m.id for m in managed[-self._config.recent_messages :]}

        for message in reversed(managed):
            cost = self._msg_tokens(message)
            if message.id in protected_ids or retained_tokens + cost <= target:
                retained.append(message)
                retained_tokens += cost

        retained_ids = {m.id for m in retained}
        omitted_ids = {m.id for m in managed if m.id not in retained_ids}

        # ── wire 수리로 사라질 메시지를 미리 생략 집합에 합친다 ──────────────
        # greedy 선택은 "큰 건 건너뛰고 작은 옛 메시지는 담는" 방식이라 히스토리에
        # 구멍을 낸다. 그 구멍은 렌더 마지막의 `_repair_tool_adjacency`가 정리하는데,
        # 거기서 지워지는 메시지는 **retained 였으므로 요약에도 안 들어간다** — 요약
        # 에도 없고 wire 에도 없는 조용한 유실이다(부모 assistant 가 생략된 tool
        # 결과가 대표적). 수리를 여기서 미리 돌려 그 메시지들을 omitted 로 넘기면
        # 최소한 요약에는 남는다.
        #
        # 한 번 걷어내면 새 고아가 생길 수 있어(결과를 잃은 부모의 tool_calls 가
        # 다시 불완전해진다) 고정점까지 반복한다. 매 패스가 집합을 단조 증가시키고
        # 후보는 유한하므로 종료가 보장된다 — 상한은 방어적 안전핀이다.
        for _ in range(_MAX_REPAIR_PASSES):
            visible = tuple(m for m in managed if m.id not in omitted_ids)
            visible, orphan_ids = self._drop_orphaned_tool_prefix(visible)
            _, repair_msg_ids, _ = _repair_tool_adjacency(visible)
            newly_lost = (set(orphan_ids) | set(repair_msg_ids)) - omitted_ids
            if not newly_lost:
                break
            omitted_ids |= newly_lost

        omitted = [m for m in managed if m.id in omitted_ids]
        if not omitted:
            # 압축할 것이 없다(전부 보호 suffix 안) — 예산 초과는 용인하고
            # 프로바이더/재시도 경로에 맡긴다(기존 동작과 동일).
            return None

        summary = _summarize_omitted(omitted, self._config.summary_tokens)
        self._state.checkpoint_seq += 1
        return _CompressionCheckpoint(
            omitted_ids=frozenset(m.id for m in omitted),
            summary_content=(
                "## Compressed Conversation Context\n"
                "Older transcript messages were compressed to keep the active context bounded. "
                "Use this summary as background, but rely on the retained recent messages for exact details.\n\n"
                f"{summary}"
            ),
            summary_id=f"ctx-summary-{self._state.checkpoint_seq}",
        )

    def _prune_forms(self, messages: Sequence[Message]) -> None:
        """캐시 위생 — 현재 트랜스크립트에 없는 id(재생성으로 잘린 메시지 등)의
        동결 형태를 걷어낸다. 매 렌더 호출된다: 평상시(stale 없음)엔 삭제가
        일어나지 않아 기존 dict와 동결 형태가 그대로 유지된다."""
        keep = {m.id for m in messages}
        stale = [k for k in self._state.forms if k not in keep]
        for k in stale:
            del self._state.forms[k]

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
    """요약 한 줄의 머리말. **모델이 읽을 문장**이지 wire 덤프가 아니다.

    도구 결과를 `tool result call_1` 로 적던 것이 특히 나빴다(2026-07-25): call id
    는 프로바이더 내부 식별자라 모델에게 아무 의미가 없고, 컨텍스트에 존재하지도
    않는 id 를 요약이 언급하는 꼴이라 소형 모델이 그걸 되짚으려 든다. 도구 메시지는
    이미 `tool_name`·`ok` 를 metadata 로 갖고 있으므로 그걸 쓴다 — "무슨 도구가
    성공/실패했나"는 모델이 실제로 쓸 수 있는 정보다."""
    if message.role == "assistant" and message.tool_calls:
        names = ", ".join(call.tool_name for call in message.tool_calls[:3])
        return f"assistant requested tool(s) {names}"
    if message.role == "tool":
        name = message.metadata.get("tool_name")
        # 성패 정본은 metadata["ok"] 다 — content 의 "ERROR: " 접두사 sniffing 금지
        # (loop.py `_tool_message` 계약).
        ok = message.metadata.get("ok")
        status = "" if ok is None else (" ok" if ok else " failed")
        return f"{name} result{status}" if name else "tool result"
    return message.role


def _compact_one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
