"""2026-07-10 턴 신뢰성·관측성 고도화 테스트.

커버 대상:
  - is_transient_provider_error: 과부하/rate limit/5xx/네트워크 판별(오탐 방지 포함)
  - AgentLoop transient 재시도: backoff 후 회복 / 소진 시 전파 / 스트림 중 실패는
    재시도 금지 / backoff 대기 중 abort는 조용한 턴 종료
  - Claude provider: malformed tool_use 인자 JSON → {} fail-soft(스트림 생존),
    stop_sequence → end_turn 매핑
  - OpenAI-compat: 스트림 usage 캡처 → done 청크의 input/output_tokens
    (TokenCalibrator 연결), 공식 엔드포인트만 stream_options.include_usage 요청
  - 컨텍스트 매니저 시크릿 마스킹 확장(AWS/Google/JWT/Bearer/PEM/webhook 등)
"""

from __future__ import annotations

import asyncio

import pytest

from rice_runtime.agent.context_manager import _mask_secrets
from rice_runtime.agent.loop import AgentLoop, LoopResult
from rice_runtime.agent.providers._openai_compat import assemble_stream
from rice_runtime.agent.providers.claude import ClaudeProvider, _map_stop_reason
from rice_runtime.agent.providers.interfaces import ProviderCapabilities, ProviderChunk
from rice_runtime.agent.providers.openai import OpenAIProvider
from rice_runtime.agent.tool_dispatch import ToolDispatcher
from rice_runtime.core.config import ProviderConfig
from rice_runtime.core.errors import is_transient_provider_error
from rice_runtime.core.types import Message, ToolSpec, TurnContext
from rice_runtime.server.approval import ApprovalRegistry


# ── transient 에러 판별 ───────────────────────────────────────────────────────


class TestTransientErrorDetection:
    def test_overloaded_and_rate_limit_are_transient(self):
        assert is_transient_provider_error(Exception("Error code: 529 - overloaded_error"))
        assert is_transient_provider_error(Exception("429 Too Many Requests"))
        assert is_transient_provider_error(Exception("rate limit exceeded, retry later"))

    def test_5xx_and_network_failures_are_transient(self):
        assert is_transient_provider_error(Exception("Server error '503 Service Unavailable'"))
        assert is_transient_provider_error(Exception("502 Bad Gateway"))
        assert is_transient_provider_error(Exception("httpx.ConnectError: connection refused"))
        assert is_transient_provider_error(Exception("read timed out"))

    def test_permanent_failures_are_not_transient(self):
        assert not is_transient_provider_error(Exception("401 Unauthorized"))
        assert not is_transient_provider_error(Exception("invalid_request_error: bad tool schema"))
        # 상태 코드는 단어 경계 매칭 — 토큰 수 같은 무관한 숫자에 오탐하지 않는다.
        assert not is_transient_provider_error(Exception("prompt is too long: 500123 tokens > 200000"))


# ── AgentLoop transient 재시도 ────────────────────────────────────────────────


class _FlakyProvider:
    """처음 fail_times번은 스트림 진입 즉시 transient 에러, 그 다음부터 정상 답변."""

    name = "flaky"

    def __init__(self, fail_times: int, error: str = "Error code: 529 - overloaded_error") -> None:
        self.fail_times = fail_times
        self.error = error
        self.calls = 0

    def capabilities(self):
        return ProviderCapabilities(native_tool_calling=True)

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(self.error)
        yield ProviderChunk(kind="text", text="recovered")
        yield ProviderChunk(kind="done", stop_reason="end_turn")


class _MidStreamFailProvider:
    """청크를 하나 내보낸 뒤 transient 에러 — 부분 출력이 나갔으니 재시도 금지."""

    name = "midfail"

    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self):
        return ProviderCapabilities(native_tool_calling=True)

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
        self.calls += 1
        yield ProviderChunk(kind="text", text="partial ")
        raise RuntimeError("503 Service Unavailable")


class _NoopService:
    async def invoke(self, call, owner_id=None):
        raise AssertionError(f"unexpected invoke: {call}")


def _loop(provider) -> AgentLoop:
    dispatcher = ToolDispatcher(
        connectors=_NoopService(),
        plugins=_NoopService(),
        approvals=ApprovalRegistry(),
        approval_timeout_s=1,
        builtins={},
    )
    return AgentLoop(provider=provider, dispatcher=dispatcher)


def _ctx() -> TurnContext:
    return TurnContext(
        session_id="s1",
        system_prompt="sys",
        history=(Message(role="user", content="go"),),
        tools=(),
        max_iterations=8,
    )


@pytest.mark.asyncio
async def test_loop_retries_transient_provider_error_and_recovers(monkeypatch):
    monkeypatch.setattr("rice_runtime.agent.loop._TRANSIENT_RETRY_DELAYS", (0.0, 0.0))
    provider = _FlakyProvider(fail_times=1)
    result = LoopResult()
    events = [ev async for ev in _loop(provider).run(_ctx(), "s1", result, asyncio.Event())]

    assert result.final_message == "recovered"
    assert provider.calls == 2
    # 재시도는 내부에서 조용히 — 에러 이벤트가 사용자에게 새지 않는다.
    assert not [ev for ev in events if ev.type == "error"]


@pytest.mark.asyncio
async def test_loop_gives_up_after_retry_budget_exhausted(monkeypatch):
    monkeypatch.setattr("rice_runtime.agent.loop._TRANSIENT_RETRY_DELAYS", (0.0, 0.0))
    provider = _FlakyProvider(fail_times=99)
    with pytest.raises(RuntimeError, match="overloaded"):
        _ = [ev async for ev in _loop(provider).run(_ctx(), "s1", LoopResult(), asyncio.Event())]
    # 최초 1회 + 재시도 2회
    assert provider.calls == 3


@pytest.mark.asyncio
async def test_loop_does_not_retry_permanent_error(monkeypatch):
    monkeypatch.setattr("rice_runtime.agent.loop._TRANSIENT_RETRY_DELAYS", (0.0, 0.0))
    provider = _FlakyProvider(fail_times=99, error="401 Unauthorized: bad api key")
    with pytest.raises(RuntimeError, match="Unauthorized"):
        _ = [ev async for ev in _loop(provider).run(_ctx(), "s1", LoopResult(), asyncio.Event())]
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_loop_does_not_retry_after_partial_output(monkeypatch):
    monkeypatch.setattr("rice_runtime.agent.loop._TRANSIENT_RETRY_DELAYS", (0.0, 0.0))
    provider = _MidStreamFailProvider()
    with pytest.raises(RuntimeError, match="503"):
        _ = [ev async for ev in _loop(provider).run(_ctx(), "s1", LoopResult(), asyncio.Event())]
    # 부분 출력이 이미 사용자에게 나갔으므로 같은 요청을 다시 보내면 중복 출력 —
    # 재시도 없이 그대로 전파해야 한다.
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_abort_during_backoff_ends_turn_quietly(monkeypatch):
    # 대기 60초 — abort가 대기를 즉시 끊지 못하면 이 테스트는 타임아웃으로 죽는다.
    monkeypatch.setattr("rice_runtime.agent.loop._TRANSIENT_RETRY_DELAYS", (60.0,))
    provider = _FlakyProvider(fail_times=99)
    abort = asyncio.Event()
    result = LoopResult()

    async def _abort_soon():
        await asyncio.sleep(0.02)
        abort.set()

    asyncio.get_event_loop().create_task(_abort_soon())
    events = await asyncio.wait_for(
        _collect_events(_loop(provider).run(_ctx(), "s1", result, abort)), timeout=5.0
    )

    assert result.aborted is True
    assert provider.calls == 1  # 재시도 없이 접었다
    assert not [ev for ev in events if ev.type == "error"]


async def _collect_events(gen):
    return [ev async for ev in gen]


# ── Claude provider 견고화 ────────────────────────────────────────────────────


class _AnthropicEvent:
    def __init__(self, type, **kwargs):
        self.type = type
        for key, value in kwargs.items():
            setattr(self, key, value)


class _AnthropicBlock:
    def __init__(self, type, **kwargs):
        self.type = type
        for key, value in kwargs.items():
            setattr(self, key, value)


class _AnthropicDelta:
    def __init__(self, type, **kwargs):
        self.type = type
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeAnthropicStream:
    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        async def gen():
            for event in self._events:
                yield event

        return gen()


class _FakeAnthropicClient:
    def __init__(self, events):
        self.messages = type(
            "M", (), {"stream": lambda _self, **kwargs: _FakeAnthropicStream(events)}
        )()


@pytest.mark.asyncio
async def test_claude_malformed_tool_json_falls_back_to_empty_args(monkeypatch):
    """모델이 잘린/깨진 인자 JSON을 스트리밍해도 스트림이 죽지 않고 {}로
    폴백한다 — 도구 검증 에러는 모델이 보고 교정할 수 있는 데이터다."""
    fake_client = _FakeAnthropicClient(
        [
            _AnthropicEvent(
                "content_block_start",
                index=0,
                content_block=_AnthropicBlock("tool_use", id="call_1", name="lookup", input={}),
            ),
            _AnthropicEvent(
                "content_block_delta", index=0, delta=_AnthropicDelta("input_json_delta", partial_json='{"q": bro')
            ),
            _AnthropicEvent("content_block_stop", index=0),
            _AnthropicEvent("message_delta", delta=_AnthropicDelta("", stop_reason="tool_use")),
            _AnthropicEvent("message_stop"),
        ]
    )
    monkeypatch.setattr("rice_runtime.agent.providers.claude._load_api_key", lambda: "k")
    monkeypatch.setattr("anthropic.AsyncAnthropic", lambda **kwargs: fake_client)
    provider = ClaudeProvider(ProviderConfig(kind="claude", model="claude-test"))

    chunks = [c async for c in provider.stream(system="sys", messages=(Message(role="user", content="hi"),))]

    assert [c.kind for c in chunks] == ["tool_call", "done"]
    assert chunks[0].tool_call.tool_name == "lookup"
    assert chunks[0].tool_call.arguments == {}
    assert chunks[0].tool_call.id == "call_1"


def test_claude_stop_sequence_maps_to_end_turn():
    assert _map_stop_reason("stop_sequence") == "end_turn"
    assert _map_stop_reason("end_turn") == "end_turn"
    assert _map_stop_reason("tool_use") == "tool_use"
    assert _map_stop_reason(None) == "error"


# ── OpenAI-compat usage 캡처 ──────────────────────────────────────────────────


async def _dict_chunks(items):
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_assemble_stream_captures_usage_into_done_chunk():
    chunks = [
        {"choices": [{"delta": {"content": "hi"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        # 공식 API의 include_usage 최종 청크: choices 빈 배열 + usage
        {"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 8}},
    ]
    out = [c async for c in assemble_stream(_dict_chunks(chunks))]
    done = out[-1]
    assert done.kind == "done"
    assert done.input_tokens == 120
    assert done.output_tokens == 8


@pytest.mark.asyncio
async def test_assemble_stream_without_usage_keeps_none():
    chunks = [{"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]}]
    out = [c async for c in assemble_stream(_dict_chunks(chunks))]
    assert out[-1].input_tokens is None
    assert out[-1].output_tokens is None


class _PayloadCapturingOpenAI(OpenAIProvider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.payloads = []

    async def _stream_raw(self, payload):
        self.payloads.append(payload)
        yield {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}


@pytest.mark.asyncio
async def test_openai_default_endpoint_requests_usage_custom_does_not():
    default = _PayloadCapturingOpenAI(ProviderConfig(kind="openai", model="m"), api_key="k")
    _ = [c async for c in default.stream(system="s", messages=(Message(role="user", content="hi"),))]
    assert default.payloads[0]["stream_options"] == {"include_usage": True}

    # 커스텀 호환 엔드포인트는 미지원 서버의 400을 피해 보내지 않는다.
    custom = _PayloadCapturingOpenAI(
        ProviderConfig(kind="openai", model="m"), api_key="k", base_url="http://vllm.local/v1"
    )
    _ = [c async for c in custom.stream(system="s", messages=(Message(role="user", content="hi"),))]
    assert "stream_options" not in custom.payloads[0]


# ── 시크릿 마스킹 확장 ────────────────────────────────────────────────────────


class TestSecretMasking:
    def test_aws_and_google_keys_masked(self):
        assert "AKIAIOSFODNN7EXAMPLE" not in _mask_secrets("key id AKIAIOSFODNN7EXAMPLE in config")
        assert "AIza" + "B" * 35 not in _mask_secrets("google key AIza" + "B" * 35 + " found")

    def test_bearer_and_jwt_masked(self):
        masked = _mask_secrets("Authorization: Bearer abcdef0123456789abcdef0123456789")
        assert "abcdef0123456789" not in masked
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9P"
        assert jwt not in _mask_secrets(f"token: {jwt}")

    def test_pem_private_key_block_masked(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA7\nqwer+ty==\n-----END RSA PRIVATE KEY-----"
        masked = _mask_secrets(f"id_rsa contents:\n{pem}")
        assert "MIIEpAIBAAKCAQEA7" not in masked
        assert "[REDACTED PRIVATE KEY]" in masked

    def test_platform_tokens_masked(self):
        assert "github_pat_" + "A" * 30 not in _mask_secrets("pat github_pat_" + "A" * 30)
        assert "glpat-" + "b" * 24 not in _mask_secrets("gitlab glpat-" + "b" * 24)
        hook = "https://hooks.slack.com/services/T000/B000/XXXXXXXX"
        assert hook not in _mask_secrets(f"notify via {hook}")

    def test_plain_text_untouched(self):
        text = "사용자는 2026-07-10에 보고서를 요청했다. 파일은 report_v2.xlsx이다."
        assert _mask_secrets(text) == text
