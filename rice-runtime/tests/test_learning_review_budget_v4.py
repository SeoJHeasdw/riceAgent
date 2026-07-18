"""Learning-review request-boundary regressions.

These tests deliberately exercise the boundaries that broad happy-path tests miss:
tiny CJK budgets, negated failure phrases near real failures, and the complete
provider request (system + history + skill catalog + tool schemas).
"""

from __future__ import annotations

import json
import tempfile
from typing import AsyncIterator, Sequence

import pytest

from rice_runtime.agent.providers.interfaces import ProviderCapabilities, ProviderChunk
from rice_runtime.core.config import Config, LearningConfig, ProviderConfig
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.core.tokens import estimate_tokens
from rice_runtime.core.types import Message, ToolSpec
from rice_runtime.learning.memory.store import MemoryStore
from rice_runtime.learning.review.context import LearningContextManager, ReviewContextConfig
from rice_runtime.learning.review.engine import ReviewEngine
from rice_runtime.learning.signals import has_correction_signal, has_failure_signal
from rice_runtime.learning.skill.store import SkillData, SkillStore
from rice_runtime.learning.skill.usage import UsageStore
from rice_runtime.server.composition_root import review_token_budgets


def _fresh_paths() -> RicePaths:
    return RicePaths.from_env(tempfile.mkdtemp(prefix="rice-review-budget-v4-")).ensure()


def _fresh_db() -> Database:
    return Database(":memory:").connect()


def _skill(name: str, body: str) -> SkillData:
    return SkillData(
        name=name,
        version=1,
        description="한국어 리뷰 예산 검증 스킬",
        state="active",
        body=body,
        tags=["review"],
        source="user",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )


class _CapturingProvider:
    """Protocol-complete fake that records the exact review request."""

    name = "capturing"

    def __init__(self) -> None:
        self.system = ""
        self.messages: list[Message] = []
        self.tools: list[ToolSpec] = []
        self.max_tokens: int | None = None

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        temperature: float = 0.7,
        response_schema: dict | None = None,
        think: bool | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        self.system = system
        self.messages = list(messages)
        self.tools = list(tools)
        self.max_tokens = max_tokens
        yield ProviderChunk(kind="done", stop_reason="end_turn")


def _provider_request_tokens(provider: _CapturingProvider) -> int:
    """Independent copy of the provider-wire estimate used by the invariant."""

    message_tokens = sum(estimate_tokens(message.content) + 4 for message in provider.messages)
    tool_payload = json.dumps(
        [getattr(tool, "__dict__", str(tool)) for tool in provider.tools],
        ensure_ascii=False,
        default=str,
    )
    return estimate_tokens(provider.system) + message_tokens + estimate_tokens(tool_payload)


class TestTinyCjkContextBudgets:
    @pytest.mark.parametrize("budget", [0, 1, 2, 5, 10, 25, 100])
    def test_total_budget_is_a_hard_ceiling_even_below_old_clip_minimum(self, budget: int):
        manager = LearningContextManager(
            ReviewContextConfig(
                recent_messages=1,
                max_total_tokens=budget,
                tool_output_max_tokens=10_000,
                message_max_tokens=10_000,
                older_summary_tokens=100,
            )
        )
        rendered = manager.render([Message(role="user", content="한국어" * 1_000)])

        actual = sum(estimate_tokens(message.content) for message in rendered)
        assert actual <= budget, f"rendered {actual} estimated tokens with budget {budget}"

    @pytest.mark.parametrize("budget", [1, 10, 100])
    def test_per_call_budget_override_is_also_a_hard_ceiling(self, budget: int):
        manager = LearningContextManager(
            ReviewContextConfig(max_total_tokens=24_000, message_max_tokens=24_000)
        )
        rendered = manager.render(
            [Message(role="user", content="별도 호출 예산" * 1_000)],
            max_total_tokens=budget,
        )

        assert sum(estimate_tokens(message.content) for message in rendered) <= budget


class TestSignalNegationEdges:
    @pytest.mark.parametrize(
        "message",
        [
            "The tests have not failed",
            "실패하지 않았습니다",
            "오류가 하나도 없었습니다",
            "오류도 전혀 없었습니다",
        ],
    )
    def test_success_or_negation_is_not_failure(self, message: str):
        assert has_failure_signal(message) is False

    @pytest.mark.parametrize(
        "message",
        [
            "No fallback meant deployment failed",
            "There was no fallback so deploy failed",
            "No error occurred in lint, but the deploy failed",
        ],
    )
    def test_unrelated_no_clause_does_not_hide_real_failure(self, message: str):
        assert has_failure_signal(message) is True

    def test_extended_korean_additive_phrase_is_not_correction(self):
        assert has_correction_signal("성능뿐만이 아니라 안정성도 중요해") is False


class TestCompleteReviewRequestBudget:
    def test_live_ollama_budget_keeps_4k_protocol_viable(self):
        cfg = Config(
            provider=ProviderConfig(kind="ollama", model="tiny", num_ctx=4_096),
            learning=LearningConfig(
                review_request_max_tokens=28_000,
                review_output_max_tokens=8_192,
            ),
        )

        assert review_token_budgets(cfg) == (2_816, 1_024)

    def test_saas_budget_uses_independent_configured_caps(self):
        cfg = Config(
            provider=ProviderConfig(kind="claude", model="test", num_ctx=4_096),
            learning=LearningConfig(
                review_request_max_tokens=12_345,
                review_output_max_tokens=2_345,
            ),
        )

        assert review_token_budgets(cfg) == (12_345, 2_345)

    @pytest.mark.asyncio
    async def test_system_history_catalog_and_tools_share_one_input_budget(self):
        paths = _fresh_paths()
        store = SkillStore(paths)
        for index in range(6):
            store.write(_skill(f"budget-skill-{index}", "## 단계\n1. " + "한" * 450))

        provider = _CapturingProvider()
        request_budget = 8_000
        output_budget = 777
        engine = ReviewEngine(
            resolve_provider=lambda: provider,
            skill_store=store,
            usage_store=UsageStore(paths),
            mem_store=MemoryStore(_fresh_db()),
            skill_catalog_token_budget=24_000,
            request_token_budget=request_budget,
            max_output_tokens=output_budget,
            context=LearningContextManager(
                ReviewContextConfig(
                    recent_messages=12,
                    max_total_tokens=24_000,
                    tool_output_max_tokens=600,
                    message_max_tokens=2_000,
                    older_summary_tokens=2_000,
                )
            ),
        )
        history = [
            Message(
                role="user" if index % 2 == 0 else "assistant",
                content=f"대화근거-{index} " + "한" * 2_000,
            )
            for index in range(12)
        ]

        await engine.review("budget-session", history)

        actual = _provider_request_tokens(provider)
        assert actual <= request_budget, f"complete request {actual} > budget {request_budget}"
        assert provider.max_tokens == output_budget
        rendered_text = "\n".join(message.content for message in provider.messages)
        assert "Existing skills" in rendered_text  # catalog was included, not trivially dropped
        assert "대화근거" in rendered_text  # history received the remaining shared budget
        assert provider.tools  # tool schemas were included in the measured request

    @pytest.mark.asyncio
    async def test_large_catalog_is_trimmed_before_the_provider_call(self):
        paths = _fresh_paths()
        store = SkillStore(paths)
        for index in range(120):
            store.write(_skill(f"crowded-skill-{index:03d}", "## 단계\n1. 짧은 절차"))

        provider = _CapturingProvider()
        request_budget = 4_000
        engine = ReviewEngine(
            resolve_provider=lambda: provider,
            skill_store=store,
            usage_store=UsageStore(paths),
            mem_store=MemoryStore(_fresh_db()),
            skill_catalog_token_budget=24_000,
            request_token_budget=request_budget,
            max_output_tokens=512,
            context=LearningContextManager(
                ReviewContextConfig(max_total_tokens=24_000, message_max_tokens=2_000)
            ),
        )

        await engine.review(
            "crowded-catalog-session",
            [Message(role="user", content="방금 사용한 절차를 더 안전하게 개선해줘")],
            priority_skill_ids=("crowded-skill-000",),
        )

        assert provider.messages  # the oversized catalog did not turn into a retry-only failure
        assert _provider_request_tokens(provider) <= request_budget
        rendered_text = "\n".join(message.content for message in provider.messages)
        assert "crowded-skill-000" in rendered_text  # causal-priority row survives trimming
        assert "crowded-skill-119" not in rendered_text  # low-priority rows are shed first
