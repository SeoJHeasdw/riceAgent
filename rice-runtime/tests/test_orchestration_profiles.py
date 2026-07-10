"""2026-07-10 orchestrate 강화 개편 테스트.

커버 대상:
  - OrchestrationProfile resolve(auto/local/frontier, 루프백 유추, override 우선)
  - 프리픽스 캐시 불변식: 시스템 프롬프트는 턴 내 불변, 계획 상태는 ephemeral tail
  - 서사(NARRATE) 넛지: local은 연속 침묵 도구 라운드에 synthetic 넛지, frontier는 없음
  - 계획 첫 스텝 자동 in_progress (성공 위조가 아닌 "실행 시작" 사실)
  - 강제 종결(reconcile) 스냅샷의 메시지 metadata 영속화 (§3.4 한계 해소)
  - Ollama 효율 계약: num_ctx/keep_alive payload + 실측 토큰 → done 청크
  - registry 인스턴스 캐싱(같은 설정=같은 인스턴스, 설정 변경=새 인스턴스)
  - dispatcher → needs_turn_view 빌트인으로 라이브 턴 컨텍스트 주입(delegate_subagent)
  - 컨텍스트 예산 num_ctx 클램프(effective_context_config)
  - 최근 도구 출력 증류 완화(재검증 관찰 보호)
  - Claude rolling cache breakpoint(_apply_rolling_cache)
"""

from __future__ import annotations

import asyncio

import pytest

from rice_runtime.agent.interfaces import TurnView
from rice_runtime.agent.loop import AgentLoop, LoopResult
from rice_runtime.agent.orchestrator import effective_context_config
from rice_runtime.agent.planner import build_plan_from_steps
from rice_runtime.agent.profiles import (
    FRONTIER_PROFILE,
    LOCAL_PROFILE,
    resolve_profile,
)
from rice_runtime.agent.providers.claude import _apply_rolling_cache
from rice_runtime.agent.providers.interfaces import ProviderCapabilities, ProviderChunk
from rice_runtime.agent.providers.ollama import OllamaProvider
from rice_runtime.agent.providers.registry import ProviderRegistry
from rice_runtime.agent.subagents import _format_turn_view
from rice_runtime.agent.tool_dispatch import ToolDispatcher
from rice_runtime.core.config import (
    AgentConfig,
    Config,
    ConfigStore,
    ContextConfig,
    ProviderConfig,
)
from rice_runtime.core.types import Message, ToolCall, ToolSpec, TurnContext, TurnPlan, PlanStep
from rice_runtime.prompts.system import ACTION_RULES_EPILOGUE, compose_system_prompt
from rice_runtime.server.approval import ApprovalRegistry


class _NoopService:
    async def invoke(self, call, owner_id=None):
        raise AssertionError(f"unexpected invoke: {call}")


def _spec(name: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} test tool",
        parameters={"type": "object", "properties": {}},
        source="builtin",
    )


class _ScriptedProvider:
    name = "fake"

    def __init__(self, rounds: list[list[ProviderChunk]]) -> None:
        self._rounds = list(rounds)
        self.calls: list[dict] = []

    def capabilities(self):
        return ProviderCapabilities(native_tool_calling=True)

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **knobs):
        self.calls.append({"system": system, "messages": tuple(messages), "tools": tuple(tools)})
        assert self._rounds, "provider called more times than scripted"
        for chunk in self._rounds.pop(0):
            yield chunk
        yield ProviderChunk(kind="done", stop_reason="end_turn")


def _tool_round(name: str, call_id: str) -> list[ProviderChunk]:
    return [ProviderChunk(kind="tool_call", tool_call=ToolCall(tool_name=name, arguments={}, id=call_id))]


def _text(raw: str) -> list[ProviderChunk]:
    return [ProviderChunk(kind="text", text=raw)]


def _ctx(tools: tuple[ToolSpec, ...]) -> TurnContext:
    return TurnContext(
        session_id="s1",
        system_prompt="sys",
        history=(Message(role="user", content="작업 해줘"),),
        tools=tools,
        max_iterations=10,
    )


def _dispatcher(builtins: dict) -> ToolDispatcher:
    return ToolDispatcher(
        connectors=_NoopService(),
        plugins=_NoopService(),
        approvals=ApprovalRegistry(),
        approval_timeout_s=1,
        builtins=builtins,
    )


# ── profile resolve ───────────────────────────────────────────────────────────


def test_resolve_profile_auto_maps_provider_kind():
    assert resolve_profile("ollama") is LOCAL_PROFILE
    assert resolve_profile("claude") is FRONTIER_PROFILE
    assert resolve_profile("watsonx") is FRONTIER_PROFILE
    # openai 호환 로컬 서버(LM Studio 등)는 루프백 base_url로 유추
    assert resolve_profile("openai", "http://localhost:1234/v1") is LOCAL_PROFILE
    assert resolve_profile("openai", "https://api.openai.com/v1") is FRONTIER_PROFILE


def test_resolve_profile_override_wins():
    assert resolve_profile("ollama", override="frontier") is FRONTIER_PROFILE
    assert resolve_profile("claude", override="local") is LOCAL_PROFILE


def test_action_rules_epilogue_sits_last_in_system_prompt():
    prompt = compose_system_prompt("## Skills\n- a", ACTION_RULES_EPILOGUE)
    assert prompt.endswith(ACTION_RULES_EPILOGUE)  # 소형 모델 recency 위치 보장


# ── 서사(NARRATE) 넛지 ─────────────────────────────────────────────────────────


async def _run_silent_rounds(profile) -> tuple[_ScriptedProvider, LoopResult]:
    """서사 없이 도구만 2라운드 부른 뒤 답하는 시나리오(계획 없음 — planner 미배선)."""
    provider = _ScriptedProvider(
        [
            _tool_round("echo", "c1"),  # silent round 1
            _tool_round("echo", "c2"),  # silent round 2 → local이면 넛지
            _text("답변"),
        ]
    )

    async def echo(args):
        return {"ok": True}

    loop = AgentLoop(
        provider=provider,
        dispatcher=_dispatcher({"echo": echo}),
        agent_config=AgentConfig(),
        profile=profile,
    )
    result = LoopResult()
    _ = [ev async for ev in loop.run(_ctx((_spec("echo"),)), "s1", result, asyncio.Event())]
    return provider, result


@pytest.mark.asyncio
async def test_local_profile_nudges_after_consecutive_silent_tool_rounds():
    provider, result = await _run_silent_rounds(LOCAL_PROFILE)
    nudges = [m for m in result.history if m.metadata.get("synthetic") == "narration_nudge"]
    assert len(nudges) == 1  # 2연속 침묵 후 1회 — 매 라운드 오염이 아니다
    # 다음 LLM 호출(3번째)이 넛지를 본다
    assert any(
        m.metadata.get("synthetic") == "narration_nudge" for m in provider.calls[2]["messages"]
    )
    assert result.final_message == "답변"


@pytest.mark.asyncio
async def test_frontier_profile_never_injects_narration_nudge():
    _, result = await _run_silent_rounds(FRONTIER_PROFILE)
    assert not any(m.metadata.get("synthetic") == "narration_nudge" for m in result.history)


@pytest.mark.asyncio
async def test_narration_text_resets_silence_counter():
    provider = _ScriptedProvider(
        [
            _tool_round("echo", "c1"),  # silent 1
            [ProviderChunk(kind="text", text="파일을 읽어볼게요")]  # 서사 있음 → 리셋
            + _tool_round("echo", "c2"),
            _tool_round("echo", "c3"),  # silent 1 (리셋 후)
            _text("답변"),
        ]
    )

    async def echo(args):
        return {"ok": True}

    loop = AgentLoop(
        provider=provider,
        dispatcher=_dispatcher({"echo": echo}),
        agent_config=AgentConfig(),
        profile=LOCAL_PROFILE,
    )
    result = LoopResult()
    _ = [ev async for ev in loop.run(_ctx((_spec("echo"),)), "s1", result, asyncio.Event())]
    assert not any(m.metadata.get("synthetic") == "narration_nudge" for m in result.history)


# ── 계획: 첫 스텝 자동 in_progress + 강제 종결 스냅샷 영속화 ───────────────────


def test_build_plan_marks_first_step_in_progress():
    plan = build_plan_from_steps("goal", [{"title": "a"}, {"title": "b"}])
    assert plan is not None
    assert plan.steps[0].status == "in_progress"
    assert plan.steps[1].status == "pending"


@pytest.mark.asyncio
async def test_forced_reconcile_snapshot_lands_on_final_message_metadata():
    """모델이 reconcile 넛지까지 무시 → 런타임 강제 종결 스냅샷이 마지막 어시스턴트
    메시지 metadata에 실려 리로드가 최종 상태(completed)를 복원한다(§3.4 한계 해소)."""
    provider = _ScriptedProvider(
        [
            [  # round 1: start_plan으로 계획 승격(planner 없이 lazy 경로)
                ProviderChunk(
                    kind="tool_call",
                    tool_call=ToolCall(
                        tool_name="start_plan",
                        arguments={"goal": "g", "steps": [{"title": "a"}, {"title": "b"}]},
                        id="sp1",
                    ),
                ),
            ],
            _text("끝1"),  # 미종결 final 시도 → reconcile 넛지
            _text("끝2"),  # 무시 → 강제 종결
        ]
    )
    loop = AgentLoop(provider=provider, dispatcher=_dispatcher({}), agent_config=AgentConfig())
    result = LoopResult()
    _ = [ev async for ev in loop.run(_ctx((_spec("echo"),)), "s1", result, asyncio.Event())]

    final_assistant = [m for m in result.history if m.role == "assistant"][-1]
    snap = final_assistant.metadata.get("plan")
    assert snap is not None and snap["status"] == "completed"
    assert all(s["status"] == "skipped" for s in snap["steps"])


# ── Ollama 효율 계약 ──────────────────────────────────────────────────────────


class _CapturingOllama(OllamaProvider):
    def __init__(self, cfg: ProviderConfig | None = None, *, think: bool = True) -> None:
        super().__init__(cfg or ProviderConfig(kind="ollama", model="test"), think=think)
        self.payloads: list[dict] = []

    async def _stream_chat(self, payload):
        self.payloads.append(payload)
        yield {
            "message": {"content": "ok"},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 123,
            "eval_count": 7,
        }

    async def _post_chat(self, payload):
        self.payloads.append(payload)
        return {"message": {"content": "ok"}}


@pytest.mark.asyncio
async def test_ollama_payload_carries_num_ctx_and_keep_alive():
    provider = _CapturingOllama(ProviderConfig(kind="ollama", model="m", num_ctx=32_768, keep_alive="30m"))
    chunks = [c async for c in provider.stream(system="s", messages=(Message(role="user", content="hi"),))]
    payload = provider.payloads[0]
    assert payload["options"]["num_ctx"] == 32_768  # 미설정 시 서버 기본(4096)이 조용히 자른다
    assert payload["keep_alive"] == "30m"
    # 실측 토큰이 done 청크로 흘러 TokenCalibrator를 먹인다
    done = chunks[-1]
    assert done.input_tokens == 123 and done.output_tokens == 7


@pytest.mark.asyncio
async def test_ollama_titler_path_also_carries_num_ctx_and_keep_alive():
    provider = _CapturingOllama(
        ProviderConfig(kind="ollama", model="m", num_ctx=8_192, keep_alive="10m"), think=False
    )
    _ = [c async for c in provider.stream(system="s", messages=(Message(role="user", content="hi"),))]
    payload = provider.payloads[0]
    assert payload["options"]["num_ctx"] == 8_192 and payload["keep_alive"] == "10m"


# ── registry 인스턴스 캐싱 ─────────────────────────────────────────────────────


def test_registry_reuses_provider_until_config_changes():
    store = ConfigStore(Config(provider=ProviderConfig(kind="ollama", model="a")))
    registry = ProviderRegistry(store)
    p1 = registry.get("ollama")
    p2 = registry.get("ollama")
    assert p1 is p2  # 같은 설정 → 같은 인스턴스(커넥션 풀/클라이언트 유지)
    store.update(Config(provider=ProviderConfig(kind="ollama", model="b")))
    p3 = registry.get("ollama")
    assert p3 is not p1  # 설정 변경(C-1) → 새 인스턴스


# ── dispatcher → needs_turn_view 주입 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_feeds_live_turn_view_to_turn_aware_builtin():
    """delegate형 빌트인은 이번 턴의 도구 관찰을 본다 — transcript는 턴 종료에야
    영속화되므로 이 주입 없이는 워커가 장님이다(사용자 리뷰의 컨텍스트 공백)."""
    seen: list[TurnView | None] = []

    async def probe(args):  # 1라운드: 관찰 하나 만든다
        return {"ok": True}

    async def delegate(args, session_id: str = "", turn_view: TurnView | None = None):
        seen.append(turn_view)
        return {"done": True}

    delegate.needs_session = True  # type: ignore[attr-defined]
    delegate.needs_turn_view = True  # type: ignore[attr-defined]

    provider = _ScriptedProvider(
        [
            _tool_round("probe", "c1"),
            _tool_round("delegate", "c2"),
            _text("답변"),
        ]
    )
    loop = AgentLoop(
        provider=provider,
        dispatcher=_dispatcher({"probe": probe, "delegate": delegate}),
        agent_config=AgentConfig(),
    )
    result = LoopResult()
    _ = [
        ev
        async for ev in loop.run(
            _ctx((_spec("probe"), _spec("delegate"))), "s1", result, asyncio.Event()
        )
    ]

    assert len(seen) == 1 and seen[0] is not None
    view = seen[0]
    assert view.user_message == "작업 해줘"
    # 이번 턴의 1라운드(assistant tool 요청 + probe 결과)가 뷰에 들어있다
    assert any(m.role == "tool" for m in view.turn_messages)
    assert any(m.tool_calls and m.tool_calls[0].tool_name == "probe" for m in view.turn_messages)


def test_format_turn_view_renders_plan_and_observations():
    plan = TurnPlan(
        goal="분석해서 전달",
        steps=(PlanStep(id="s1", title="수집", status="done", note="42 files"), PlanStep(id="s2", title="분석")),
    )
    view = TurnView(
        user_message="프론트 분석해줘",
        plan=plan,
        turn_messages=(
            Message(role="assistant", content="", tool_calls=(ToolCall(tool_name="read_file", arguments={}, id="c1"),)),
            Message(role="tool", content="{\"lines\": 120}", metadata={"tool_call_id": "c1"}),
        ),
    )
    text = _format_turn_view(view)
    assert "프론트 분석해줘" in text
    assert "[done] s1. 수집 — 42 files" in text
    assert "read_file" in text
    assert _format_turn_view(None) == ""


# ── 컨텍스트 예산 클램프 + 최근 증류 완화 ─────────────────────────────────────


def test_effective_context_config_clamps_to_model_window():
    cfg = Config(
        provider=ProviderConfig(kind="ollama", model="m", num_ctx=32_768, max_output_tokens=16_384),
        context=ContextConfig(max_tokens=96_000, retained_tokens=64_000),
    )
    clamped = effective_context_config(cfg, LOCAL_PROFILE)
    assert clamped.max_tokens == 32_768 - 16_384 - 1_024
    assert clamped.retained_tokens <= clamped.max_tokens
    # frontier(클램프 off)와 SaaS provider는 그대로
    assert effective_context_config(cfg, FRONTIER_PROFILE).max_tokens == 96_000
    saas = Config(provider=ProviderConfig(kind="claude", model="m"), context=cfg.context)
    assert effective_context_config(saas, LOCAL_PROFILE).max_tokens == 96_000


def test_recent_tool_outputs_get_distillation_relief():
    from rice_runtime.agent.context_manager import ConversationContextManager

    cm = ConversationContextManager(
        ContextConfig(
            tool_output_threshold_tokens=50,
            tool_output_retained_tokens=30,
            recent_messages=2,
            max_tokens=100_000,
        )
    )
    big = "x" * 1_000  # 임계(50tok) 초과, 3배 완화(150tok≈600자)도 초과
    mid = "y" * 400  # 임계 초과지만 3배 완화 안엔 들어옴
    old_tool = Message(role="tool", content=big, metadata={"tool_call_id": "c0"})
    recent_tool = Message(role="tool", content=mid, metadata={"tool_call_id": "c1"})
    tail_user = Message(role="user", content="확인해줘")
    rendered = cm.render("sys", (old_tool, recent_tool, tail_user))

    assert old_tool.id in rendered.stats.distilled_message_ids  # 오래된 관찰은 접힌다
    assert recent_tool.id not in rendered.stats.distilled_message_ids  # 방금의 관찰은 원문


# ── Claude rolling cache ──────────────────────────────────────────────────────


def test_claude_rolling_cache_marks_last_block():
    native = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
    ]
    out = _apply_rolling_cache(native)
    assert out[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    # 마지막이 아닌 메시지는 건드리지 않는다
    assert "cache_control" not in out[1]["content"][-1]

    # str 콘텐츠는 블록으로 승격해 찍는다
    out2 = _apply_rolling_cache([{"role": "user", "content": "hello"}])
    assert out2[-1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert _apply_rolling_cache([]) == []
