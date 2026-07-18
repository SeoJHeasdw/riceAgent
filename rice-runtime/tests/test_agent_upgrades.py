"""2026-07-04 소형-로컬-모델 안정화 개편 테스트.

커버 대상:
  - provider per-call 노브(response_schema/think/max_tokens) → ollama wire 반영
  - planner thinking 폭주 fast-fail(무한 루프가 타임아웃까지 돌지 않는다)
  - 신규 빌트인: list_dir / edit_file / fetch_url(텍스트 추출·SSRF 가드)
  - sub-agent read-only 도구 미니 ReAct 루프(진짜 컨텍스트 격리)
"""

from __future__ import annotations

import asyncio
import json

import pytest

from rice_runtime.agent.builtins import build_subagent_toolset
from rice_runtime.agent.loop import AgentLoop, LoopResult
from rice_runtime.agent.tool_dispatch import ToolDispatcher
from rice_runtime.server.approval import ApprovalRegistry
from rice_runtime.agent.builtins.file_tools import tools as file_tools
from rice_runtime.agent.builtins.web_tools import _MarkdownExtractor, _reject_private_host
from rice_runtime.agent.planner import PlanOutcome, TurnPlanner
from rice_runtime.agent.providers.interfaces import ProviderCapabilities, ProviderChunk
from rice_runtime.agent.providers.ollama import OllamaProvider
from rice_runtime.agent.subagents import SubAgentRunner
from rice_runtime.core.config import AgentConfig, ContextConfig, ProviderConfig
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.types import Message, ToolCall, ToolSpec, TurnContext


# ── provider per-call knobs ───────────────────────────────────────────────────


class _CapturingOllama(OllamaProvider):
    """네트워크 대신 payload를 기록하고 빈 응답을 돌려주는 테스트 더블."""

    def __init__(self, *, think: bool = True) -> None:
        super().__init__(ProviderConfig(kind="ollama", model="test"), think=think)
        self.payloads: list[dict] = []

    async def _stream_chat(self, payload):
        self.payloads.append(payload)
        yield {"message": {"content": "{}"}, "done": True, "done_reason": "stop"}

    async def _post_chat(self, payload):
        self.payloads.append(payload)
        return {"message": {"content": "ok"}}


@pytest.mark.asyncio
async def test_ollama_streaming_payload_carries_schema_think_and_cap():
    provider = _CapturingOllama()
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    _ = [
        c
        async for c in provider.stream(
            system="s",
            messages=(Message(role="user", content="hi"),),
            response_schema=schema,
            think=False,
            max_tokens=777,
        )
    ]
    payload = provider.payloads[0]
    assert payload["format"] == schema
    assert payload["think"] is False  # per-call override가 인스턴스 기본(True)을 이긴다
    assert payload["options"]["num_predict"] == 777


@pytest.mark.asyncio
async def test_ollama_defaults_unchanged_without_knobs():
    provider = _CapturingOllama()
    _ = [c async for c in provider.stream(system="s", messages=(Message(role="user", content="hi"),))]
    payload = provider.payloads[0]
    assert "format" not in payload
    assert payload["think"] is True
    assert "num_predict" not in payload["options"]


@pytest.mark.asyncio
async def test_ollama_titler_path_honors_max_tokens():
    provider = _CapturingOllama(think=False)
    _ = [c async for c in provider.stream(system="s", messages=(Message(role="user", content="hi"),), max_tokens=64)]
    payload = provider.payloads[0]
    assert payload["stream"] is False and payload["options"]["num_predict"] == 64


# ── planner thinking runaway fast-fail ────────────────────────────────────────


class _RunawayThinkingProvider:
    """think=False 지시를 표현 못 하는 provider가 무한 thinking을 흘리는 상황."""

    name = "fake"

    def capabilities(self):
        return ProviderCapabilities()

    async def stream(self, **kwargs):
        while True:  # 끝나지 않는 추론 루프 — planner 쪽 상한이 끊어야 한다
            yield ProviderChunk(kind="thinking", text="Wait, I need to check. " * 40)
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_planner_cuts_runaway_thinking_without_burning_timeout():
    planner = TurnPlanner(provider=_RunawayThinkingProvider(), config=AgentConfig(planner_timeout_seconds=3600))
    ctx = TurnContext(
        session_id="s1",
        system_prompt="sys",
        history=(),
        tools=(ToolSpec(name="echo", description="d", parameters={"type": "object"}, source="builtin"),),
        max_iterations=5,
    )
    outcome = PlanOutcome()
    async with asyncio.timeout(5):  # 상한 가드가 없으면 여기서 터진다
        _ = [ev async for ev in planner.run(ctx, "복잡한 작업", outcome, asyncio.Event())]
    assert outcome.plan is None


# ── new builtins: list_dir / edit_file ────────────────────────────────────────


def _workspace_tools(tmp_path):
    paths = RicePaths.from_env(tmp_path)
    return {spec.name: (spec, handler) for spec, handler in file_tools(paths)}, paths


@pytest.mark.asyncio
async def test_list_dir_walks_workspace_and_skips_noise(tmp_path):
    tools, paths = _workspace_tools(tmp_path)
    ws = paths.workspace
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "a.py").write_text("x = 1", encoding="utf-8")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "junk.js").write_text("junk", encoding="utf-8")
    (ws / ".hidden").write_text("h", encoding="utf-8")

    _, handler = tools["list_dir"]
    out = await handler({"depth": 3})
    listed = {e["path"] for e in out["entries"]}
    assert "src" in listed and "src/a.py" in listed
    assert not any("node_modules" in p or p.startswith(".") for p in listed)


@pytest.mark.asyncio
async def test_edit_file_replaces_unique_snippet_and_rejects_ambiguous(tmp_path):
    tools, paths = _workspace_tools(tmp_path)
    (paths.workspace / "f.txt").write_text("alpha beta alpha", encoding="utf-8")
    _, handler = tools["edit_file"]

    with pytest.raises(ValueError, match="occurs 2 times"):
        await handler({"path": "f.txt", "old_text": "alpha", "new_text": "gamma"})

    out = await handler({"path": "f.txt", "old_text": "beta", "new_text": "delta"})
    assert out["replacements"] == 1
    assert (paths.workspace / "f.txt").read_text(encoding="utf-8") == "alpha delta alpha"

    out = await handler({"path": "f.txt", "old_text": "alpha", "new_text": "g", "replace_all": True})
    assert out["replacements"] == 2


# ── fetch_url internals (no network) ─────────────────────────────────────────


def test_html_text_extractor_strips_scripts_and_keeps_blocks():
    extractor = _MarkdownExtractor()
    extractor.feed(
        "<html><head><title>t</title><script>var x=1;</script></head>"
        "<body><h1>Hello</h1><p>world <b>bold</b></p><style>.a{}</style></body></html>"
    )
    text = extractor.text()
    assert "# Hello" in text and "world bold" in text
    assert "var x" not in text and ".a{}" not in text
    assert extractor.title == "t"


def test_fetch_url_rejects_local_and_private_hosts():
    for host in ("localhost", "127.0.0.1", "10.0.0.5", "192.168.1.1", "[::1]", "printer.local"):
        with pytest.raises(ValueError):
            _reject_private_host(host)
    _reject_private_host("example.com")  # 공개 호스트는 통과
    _reject_private_host("93.184.216.34")  # 공개 IP 리터럴도 통과


# ── sub-agent read-only tool loop ─────────────────────────────────────────────


def test_subagent_toolset_is_read_only_subset(tmp_path):
    names = {spec.name for spec, _ in build_subagent_toolset(RicePaths.from_env(tmp_path))}
    assert names == {"read_file", "list_dir", "search_files", "fetch_url", "web_search"}


class _ToolLoopProvider:
    """1라운드: read_file 호출 → 2라운드: 최종 JSON 보고."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def capabilities(self):
        return ProviderCapabilities(native_tool_calling=True)

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
        self.calls.append({"messages": tuple(messages), "tools": tuple(tools)})
        if len(self.calls) == 1:
            yield ProviderChunk(
                kind="tool_call",
                tool_call=ToolCall(tool_name="read_file", arguments={"path": "notes.md"}, id="c1"),
            )
            yield ProviderChunk(kind="done", stop_reason="tool_use")
            return
        report = {
            "findings": ["the note says hello"],
            "evidence": ["notes.md: hello"],
            "confidence": "high",
            "open_questions": [],
            "recommended_next_action": "none",
        }
        yield ProviderChunk(kind="text", text=json.dumps(report))
        yield ProviderChunk(kind="done", stop_reason="end_turn")


class _EmptyTranscript:
    def history(self, session_id):
        return ()


@pytest.mark.asyncio
async def test_subagent_loop_executes_read_only_tools_then_reports(tmp_path):
    paths = RicePaths.from_env(tmp_path)
    toolset = build_subagent_toolset(paths)
    (paths.workspace / "notes.md").write_text("hello", encoding="utf-8")

    provider = _ToolLoopProvider()
    runner = SubAgentRunner(
        resolve_provider=lambda: provider,
        transcript=_EmptyTranscript(),
        context_config=ContextConfig(),
        tools=toolset,
    )
    result = await runner.run(session_id="s1", task="read the note")

    assert result.structured and result.confidence == "high"
    assert result.tools_used == ("read_file",)
    assert result.findings == ("the note says hello",)
    # 1라운드는 도구를 광고하고, 도구 결과가 2라운드 문맥에 실린다
    assert {t.name for t in provider.calls[0]["tools"]} == {"read_file", "list_dir", "search_files", "fetch_url", "web_search"}
    tool_msg = next(m for m in provider.calls[1]["messages"] if m.role == "tool")
    assert "hello" in tool_msg.content and tool_msg.metadata["tool_call_id"] == "c1"


# ── 소형 모델 방어 (Hermes 이식): 빈 응답 넛지 / 빈 tool name 감쇠 ────────────


class _NoopService:
    async def invoke(self, call, owner_id=None):
        raise AssertionError(f"unexpected invoke: {call}")


def _loop(provider) -> AgentLoop:
    async def echo(args):
        return {"ok": True}

    dispatcher = ToolDispatcher(
        connectors=_NoopService(),
        plugins=_NoopService(),
        approvals=ApprovalRegistry(),
        approval_timeout_s=1,
        builtins={"echo": echo},
    )
    return AgentLoop(provider=provider, dispatcher=dispatcher)


def _loop_ctx() -> TurnContext:
    return TurnContext(
        session_id="s1",
        system_prompt="sys",
        history=(Message(role="user", content="go"),),
        tools=(ToolSpec(name="echo", description="d", parameters={"type": "object"}, source="builtin"),),
        max_iterations=8,
    )


class _EmptyAfterToolProvider:
    """1라운드 도구 호출 → 2라운드 빈 응답 → (넛지 후) 3라운드 정상 답변."""

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self.seen_messages: list[tuple[Message, ...]] = []

    def capabilities(self):
        return ProviderCapabilities(native_tool_calling=True)

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
        self.calls += 1
        self.seen_messages.append(tuple(messages))
        if self.calls == 1:
            yield ProviderChunk(kind="tool_call", tool_call=ToolCall(tool_name="echo", arguments={}, id="c1"))
            yield ProviderChunk(kind="done", stop_reason="tool_use")
        elif self.calls == 2:
            yield ProviderChunk(kind="done", stop_reason="end_turn")  # 빈 응답
        else:
            yield ProviderChunk(kind="text", text="recovered answer")
            yield ProviderChunk(kind="done", stop_reason="end_turn")


class _ThinkingProvider:
    """thinking 델타 → 본문 → done. show_thinking 게이팅 검증용."""

    name = "fake"

    def capabilities(self):
        return ProviderCapabilities(native_tool_calling=True)

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
        yield ProviderChunk(kind="thinking", text="reasoning trace")
        yield ProviderChunk(kind="text", text="visible answer")
        yield ProviderChunk(kind="done", stop_reason="end_turn")


def _loop_with(provider, *, show_thinking: bool) -> AgentLoop:
    async def echo(args):
        return {"ok": True}

    dispatcher = ToolDispatcher(
        connectors=_NoopService(),
        plugins=_NoopService(),
        approvals=ApprovalRegistry(),
        approval_timeout_s=1,
        builtins={"echo": echo},
    )
    return AgentLoop(provider=provider, dispatcher=dispatcher, show_thinking=show_thinking)


@pytest.mark.asyncio
async def test_show_thinking_on_emits_and_persists_trace():
    from rice_runtime.core.types import EventType

    result = LoopResult()
    events = [
        ev
        async for ev in _loop_with(_ThinkingProvider(), show_thinking=True).run(
            _loop_ctx(), "s1", result, asyncio.Event()
        )
    ]
    thinking_evs = [e for e in events if e.type == EventType.THINKING]
    assert [e.data["text"] for e in thinking_evs] == ["reasoning trace"]
    assistant = next(m for m in result.history if m.role == "assistant")
    assert assistant.metadata.get("thinking") == "reasoning trace"
    assert result.final_message == "visible answer"


@pytest.mark.asyncio
async def test_show_thinking_off_suppresses_events_and_metadata():
    from rice_runtime.core.types import EventType

    result = LoopResult()
    events = [
        ev
        async for ev in _loop_with(_ThinkingProvider(), show_thinking=False).run(
            _loop_ctx(), "s1", result, asyncio.Event()
        )
    ]
    # 클라이언트로 thinking이 한 조각도 새지 않는다 — 라이브 이벤트도, 히스토리
    # 메타데이터도 없다. 모델은 계속 추론하되 노출만 백엔드에서 차단된다.
    assert [e for e in events if e.type == EventType.THINKING] == []
    assistant = next(m for m in result.history if m.role == "assistant")
    assert "thinking" not in assistant.metadata
    # 본문은 정상적으로 스트리밍·확정된다.
    assert result.final_message == "visible answer"


@pytest.mark.asyncio
async def test_loop_nudges_empty_answer_after_tool_use():
    provider = _EmptyAfterToolProvider()
    result = LoopResult()
    _ = [ev async for ev in _loop(provider).run(_loop_ctx(), "s1", result, asyncio.Event())]

    assert result.final_message == "recovered answer"
    assert provider.calls == 3
    # 넛지는 synthetic user 메시지로 문맥에 실린다
    nudge = next(m for m in provider.seen_messages[2] if m.metadata.get("synthetic") == "empty_nudge")
    assert "empty" in nudge.content


class _EmptyToolNameProvider:
    """소형 모델의 priming: 빈 이름의 tool call → 교정 후 정상 종료."""

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self):
        return ProviderCapabilities(native_tool_calling=True)

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
        self.calls += 1
        if self.calls == 1:
            yield ProviderChunk(kind="tool_call", tool_call=ToolCall(tool_name="", arguments={}, id="c1"))
            yield ProviderChunk(kind="done", stop_reason="tool_use")
        else:
            yield ProviderChunk(kind="text", text="done")
            yield ProviderChunk(kind="done", stop_reason="end_turn")


@pytest.mark.asyncio
async def test_loop_dampens_empty_tool_name_without_catalog():
    provider = _EmptyToolNameProvider()
    result = LoopResult()
    _ = [ev async for ev in _loop(provider).run(_loop_ctx(), "s1", result, asyncio.Event())]

    tool_msg = next(m for m in result.history if m.role == "tool")
    assert "that is data" in tool_msg.content  # terse 교정 메시지
    assert "echo" not in tool_msg.content  # 카탈로그를 되돌려주지 않는다(priming 억제)
    assert result.final_message == "done"


@pytest.mark.asyncio
async def test_subagent_without_tools_keeps_legacy_single_call(tmp_path):
    provider = _ToolLoopProvider()

    class _OneShot(_ToolLoopProvider):
        async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
            self.calls.append({"messages": tuple(messages), "tools": tuple(tools)})
            yield ProviderChunk(kind="text", text="plain summary")
            yield ProviderChunk(kind="done", stop_reason="end_turn")

    provider = _OneShot()
    runner = SubAgentRunner(
        resolve_provider=lambda: provider,
        transcript=_EmptyTranscript(),
        context_config=ContextConfig(),
    )
    result = await runner.run(session_id="s1", task="summarize")
    assert result.summary == "plain summary"
    assert provider.calls[0]["tools"] == ()  # 도구 미주입 → 이전과 동일한 단발 호출
