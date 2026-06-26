"""Provider seam tests for the real adapters (RUNTIME_DESIGN §6).

These tests stay offline by mocking SDK/network edges. The fragile part of this
module is history conversion, so the native-message mappers are tested directly in
addition to the normalized `ProviderChunk` stream contract.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import AsyncIterator

import pytest

from rice_runtime import build_container
from rice_runtime.agent.providers._openai_compat import to_oai_messages
from rice_runtime.agent.providers.claude import ClaudeProvider, _to_claude_messages
from rice_runtime.agent.providers.interfaces import ILLMProvider
from rice_runtime.agent.providers.ollama import OllamaProvider, _to_ollama_messages
from rice_runtime.agent.providers.openai import OpenAIProvider
from rice_runtime.agent.providers.watsonx import WatsonxProvider
from rice_runtime.core.config import Config, ProviderConfig, RuntimeArgs
from rice_runtime.core.errors import ProviderError
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.types import Message, ToolCall


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


class _FakeAnthropicMessages:
    def __init__(self, events):
        self._events = events
        self.calls = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeAnthropicStream(self._events)


class _FakeAnthropicClient:
    def __init__(self, events):
        self.messages = _FakeAnthropicMessages(events)


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


async def _collect(stream: AsyncIterator):
    return [chunk async for chunk in stream]


def _container_with(kind: str):
    tmp = tempfile.mkdtemp(prefix="rice-provider-")
    c = build_container(RuntimeArgs(), paths=RicePaths.from_env(tmp))
    cfg = c.config
    c.config_store.update(Config(provider=ProviderConfig(kind=kind, model="m"), learning=cfg.learning, ui=cfg.ui))
    return c


def test_provider_protocols_are_structural(monkeypatch):
    monkeypatch.setattr("rice_runtime.agent.providers.claude._load_api_key", lambda: "k")
    assert isinstance(ClaudeProvider(ProviderConfig(kind="claude", model="m")), ILLMProvider)
    assert isinstance(OllamaProvider(ProviderConfig(kind="ollama", model="m")), ILLMProvider)


def test_claude_history_mapping_round_trip():
    call = ToolCall(tool_name="lookup", arguments={"q": "rice"}, id="call_1")
    messages = (
        Message(role="user", content="find it"),
        Message(role="assistant", content="", tool_calls=(call,)),
        Message(role="tool", content='{"ok": true}', metadata={"tool_call_id": "call_1"}),
    )

    native = _to_claude_messages(messages)

    assert native[0] == {"role": "user", "content": "find it"}
    assert native[1]["role"] == "assistant"
    assert native[1]["content"][0] == {
        "type": "tool_use",
        "id": "call_1",
        "name": "lookup",
        "input": {"q": "rice"},
    }
    assert native[2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": '{"ok": true}'}],
    }


def test_ollama_history_mapping_round_trip():
    call = ToolCall(tool_name="lookup", arguments={"q": "rice"}, id="call_1")
    messages = (
        Message(role="user", content="find it"),
        Message(role="assistant", content="", tool_calls=(call,)),
        Message(role="tool", content='{"ok": true}', metadata={"tool_call_id": "call_1"}),
    )

    native = _to_ollama_messages(messages)

    assert native[0] == {"role": "user", "content": "find it"}
    assert native[1]["tool_calls"][0]["function"] == {"name": "lookup", "arguments": {"q": "rice"}}
    assert native[2] == {"role": "tool", "content": '{"ok": true}', "tool_call_id": "call_1"}


@pytest.mark.asyncio
async def test_claude_stream_emits_text_then_one_done(monkeypatch):
    # Real Anthropic streaming carries stop_reason on `message_delta`, then a bare
    # `message_stop` — mirror that exactly so the test can't mask the mapping bug.
    fake_client = _FakeAnthropicClient(
        [
            _AnthropicEvent("content_block_delta", delta=_AnthropicDelta("text_delta", text="hello "), index=0),
            _AnthropicEvent("content_block_delta", delta=_AnthropicDelta("text_delta", text="world"), index=0),
            _AnthropicEvent("message_delta", delta=_AnthropicDelta("", stop_reason="end_turn")),
            _AnthropicEvent("message_stop"),
        ]
    )

    monkeypatch.setattr("rice_runtime.agent.providers.claude._load_api_key", lambda: "k")
    monkeypatch.setattr("anthropic.AsyncAnthropic", lambda **kwargs: fake_client)
    provider = ClaudeProvider(ProviderConfig(kind="claude", model="claude-test"))

    chunks = await _collect(provider.stream(system="sys", messages=(Message(role="user", content="hi"),)))

    assert [chunk.kind for chunk in chunks] == ["text", "text", "done"]
    assert chunks[-1].stop_reason == "end_turn"
    assert fake_client.messages.calls[0]["model"] == "claude-test"


@pytest.mark.asyncio
async def test_claude_stream_assembles_tool_call_from_json_deltas(monkeypatch):
    """The 2nd-fragile path: tool_use input streams as input_json_delta fragments
    and must be reassembled into one ToolCall at content_block_stop."""
    fake_client = _FakeAnthropicClient(
        [
            _AnthropicEvent("content_block_start", index=0, content_block=_AnthropicBlock("tool_use", id="call_9", name="lookup", input={})),
            _AnthropicEvent("content_block_delta", index=0, delta=_AnthropicDelta("input_json_delta", partial_json='{"q": ')),
            _AnthropicEvent("content_block_delta", index=0, delta=_AnthropicDelta("input_json_delta", partial_json='"rice"}')),
            _AnthropicEvent("content_block_stop", index=0),
            _AnthropicEvent("message_delta", delta=_AnthropicDelta("", stop_reason="tool_use")),
            _AnthropicEvent("message_stop"),
        ]
    )
    monkeypatch.setattr("rice_runtime.agent.providers.claude._load_api_key", lambda: "k")
    monkeypatch.setattr("anthropic.AsyncAnthropic", lambda **kwargs: fake_client)
    provider = ClaudeProvider(ProviderConfig(kind="claude", model="claude-test"))

    chunks = await _collect(provider.stream(system="sys", messages=(Message(role="user", content="hi"),)))

    assert [c.kind for c in chunks] == ["tool_call", "done"]
    assert chunks[0].tool_call.tool_name == "lookup"
    assert chunks[0].tool_call.arguments == {"q": "rice"}
    assert chunks[0].tool_call.id == "call_9"
    assert chunks[-1].stop_reason == "tool_use"


@pytest.mark.asyncio
async def test_ollama_stream_emits_text_then_one_done(monkeypatch):
    provider = OllamaProvider(ProviderConfig(kind="ollama", model="qwen", base_url="http://ollama.local"))

    async def fake_stream_chat(payload):
        assert payload["model"] == "qwen"
        yield {"message": {"content": "hello "}, "done": False}
        yield {"message": {"content": "world"}, "done": True, "done_reason": "stop"}

    monkeypatch.setattr(provider, "_stream_chat", fake_stream_chat)
    chunks = await _collect(provider.stream(system="sys", messages=(Message(role="user", content="hi"),)))

    assert [chunk.kind for chunk in chunks] == ["text", "text", "done"]
    assert chunks[-1].stop_reason == "end_turn"


def test_missing_claude_key_falls_back_to_echo():
    c = _container_with("claude")
    try:
        assert c.resolve_provider().name == "echo"
    finally:
        c.shutdown()


def test_claude_mapping_rejects_tool_message_without_link():
    with pytest.raises(ProviderError):
        _to_claude_messages((Message(role="tool", content="oops"),))


# ── OpenAI / watsonx (OpenAI-compatible wire shape) ──────────────────────────
def test_oai_history_mapping_round_trip():
    call = ToolCall(tool_name="lookup", arguments={"q": "rice"}, id="call_1")
    messages = (
        Message(role="user", content="find it"),
        Message(role="assistant", content="", tool_calls=(call,)),
        Message(role="tool", content='{"ok": true}', metadata={"tool_call_id": "call_1"}),
    )

    native = to_oai_messages("sys", messages)

    assert native[0] == {"role": "system", "content": "sys"}
    assert native[1] == {"role": "user", "content": "find it"}
    assert native[2]["role"] == "assistant"
    assert native[2]["tool_calls"][0]["function"]["name"] == "lookup"
    assert json.loads(native[2]["tool_calls"][0]["function"]["arguments"]) == {"q": "rice"}
    assert native[3] == {"role": "tool", "content": '{"ok": true}', "tool_call_id": "call_1"}


@pytest.mark.asyncio
async def test_openai_stream_emits_text_then_one_done(monkeypatch):
    provider = OpenAIProvider(ProviderConfig(kind="openai", model="gpt-x"), api_key="sk-x")

    async def fake_raw(payload):
        assert payload["model"] == "gpt-x"
        yield {"choices": [{"delta": {"content": "hello "}}]}
        yield {"choices": [{"delta": {"content": "world"}, "finish_reason": "stop"}]}

    monkeypatch.setattr(provider, "_stream_raw", fake_raw)
    chunks = await _collect(provider.stream(system="sys", messages=(Message(role="user", content="hi"),)))

    assert [c.kind for c in chunks] == ["text", "text", "done"]
    assert chunks[-1].stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_openai_stream_assembles_tool_call_from_fragments(monkeypatch):
    provider = OpenAIProvider(ProviderConfig(kind="openai", model="gpt-x"), api_key="sk-x")

    async def fake_raw(payload):
        yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_9", "function": {"name": "lookup", "arguments": '{"q": '}}]}}]}
        yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"rice"}'}}]}}]}
        yield {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}

    monkeypatch.setattr(provider, "_stream_raw", fake_raw)
    chunks = await _collect(provider.stream(system="sys", messages=(Message(role="user", content="hi"),)))

    assert [c.kind for c in chunks] == ["tool_call", "done"]
    assert chunks[0].tool_call.tool_name == "lookup"
    assert chunks[0].tool_call.arguments == {"q": "rice"}
    assert chunks[0].tool_call.id == "call_9"
    assert chunks[-1].stop_reason == "tool_use"


@pytest.mark.asyncio
async def test_watsonx_stream_text(monkeypatch):
    provider = WatsonxProvider(
        ProviderConfig(kind="watsonx", model="ibm/granite"),
        api_key="k",
        project_id="p",
        base_url="https://us-south.ml.cloud.ibm.com",
    )

    async def fake_token():
        return "tok"

    async def fake_raw(payload, token):
        assert payload["model_id"] == "ibm/granite"
        assert payload["project_id"] == "p"
        assert token == "tok"
        yield {"choices": [{"delta": {"content": "안녕"}}]}
        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

    monkeypatch.setattr(provider, "_iam_token", fake_token)
    monkeypatch.setattr(provider, "_stream_raw", fake_raw)
    chunks = await _collect(provider.stream(system="sys", messages=(Message(role="user", content="hi"),)))

    assert [c.kind for c in chunks] == ["text", "done"]
    assert chunks[-1].stop_reason == "end_turn"


def test_registered_key_selects_claude_provider():
    """A SaaS key stored in provider_models is injected so the provider resolves
    (no fall-back to echo) — the gap that made SaaS non-functional before."""
    tmp = tempfile.mkdtemp(prefix="rice-cred-")
    c = build_container(RuntimeArgs(), paths=RicePaths.from_env(tmp))
    try:
        c.db.execute(
            "INSERT INTO provider_models(model_id, provider, provider_label, api_key) VALUES (?, ?, ?, ?)",
            ("claude-x", "anthropic", "Anthropic", "sk-ant-test"),
        )
        cfg = c.config
        c.config_store.update(Config(provider=ProviderConfig(kind="claude", model="claude-x"), learning=cfg.learning, ui=cfg.ui))
        assert c.resolve_provider().name == "claude"
    finally:
        c.shutdown()


def test_openai_without_key_falls_back_to_echo():
    c = _container_with("openai")  # model "m", no provider_models row → no creds
    try:
        assert c.resolve_provider().name == "echo"
    finally:
        c.shutdown()


@pytest.mark.asyncio
async def test_settings_registered_models_round_trip():
    from rice_runtime.server.routes.settings import SettingsIn, get_settings, update_settings

    tmp = tempfile.mkdtemp(prefix="rice-set-")
    c = build_container(RuntimeArgs(), paths=RicePaths.from_env(tmp))
    try:
        await update_settings(
            SettingsIn(
                registered_models=[
                    {"modelId": "gpt-x", "provider": "openai", "providerLabel": "OpenAI", "apiKey": "sk-x", "baseUrl": "https://gw.local/v1"},
                    {"modelId": "granite", "provider": "watsonx", "providerLabel": "watsonx", "apiKey": "k", "projectId": "p", "baseUrl": "https://us-south.ml.cloud.ibm.com"},
                ]
            ),
            c,
        )
        res = await get_settings(c)
        models = {m["modelId"]: m for m in res["data"]["registered_models"]}
        assert models["gpt-x"]["baseUrl"] == "https://gw.local/v1"
        assert "projectId" not in models["gpt-x"]
        assert models["granite"]["projectId"] == "p"
        assert models["granite"]["baseUrl"].endswith("ibm.com")
        assert models["granite"]["apiKey"] == "k"
    finally:
        c.shutdown()
