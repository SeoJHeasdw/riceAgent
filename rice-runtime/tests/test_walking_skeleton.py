"""Walking-skeleton + seam tests (stdlib only — no fastapi/anthropic needed).

These assert the spine is wired and the two subtle mechanisms work:
  1. a full turn streams thinking → message → done and persists history;
  2. the ApprovalRegistry parks, resolves, times out, and cancels correctly
     (the shared HITL mechanism — RUNTIME_DESIGN §5.1).
"""

from __future__ import annotations

import asyncio
import tempfile

import pytest

from rice_runtime import build_container
from rice_runtime.agent.interfaces import ITranscript
from rice_runtime.agent.orchestrator import Orchestrator
from rice_runtime.agent.titler import SessionTitler
from rice_runtime.agent.providers.interfaces import ProviderCapabilities, ProviderChunk
from rice_runtime.core.config import Config, ProviderConfig, RuntimeArgs
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.types import ApprovalRequest, ApprovalResponse, EventType, Message, ToolCall, ToolSpec
from rice_runtime.server.approval import ApprovalRegistry


def _container():
    tmp = tempfile.mkdtemp(prefix="rice-test-")
    return build_container(RuntimeArgs(), paths=RicePaths.from_env(tmp))


@pytest.mark.asyncio
async def test_turn_streams_and_persists():
    c = _container()
    session = c.sessions.create(model="echo")
    types = [e.type async for e in c.agent.run_turn(session.id, "hi")]

    assert EventType.THINKING in types
    assert EventType.MESSAGE in types
    assert types[-1] == EventType.DONE  # done always closes the stream

    history = c.sessions.history(session.id)
    assert [m.role for m in history] == ["user", "assistant"]
    assert history[0].content == "hi"
    c.shutdown()


@pytest.mark.asyncio
async def test_live_provider_switch():
    """PUT /settings model toggle takes effect without re-wiring (C-1)."""
    c = _container()
    assert c.resolve_provider().name == "echo"
    cfg = c.config
    c.config_store.update(Config(provider=ProviderConfig(kind="echo"), learning=cfg.learning, ui=cfg.ui))
    assert c.resolve_provider().name == "echo"  # resolved live from the store
    c.shutdown()


def test_session_manager_is_transcript():
    """SessionManager structurally satisfies the agent-side ITranscript seam (B)."""
    c = _container()
    assert isinstance(c.sessions, ITranscript)
    c.shutdown()


class _ToolThenAnswer:
    """Fake provider: request a tool once, then answer (drives the tool path)."""

    name = "fake"

    def capabilities(self):
        return ProviderCapabilities(native_tool_calling=True)

    async def stream(self, *, system, messages, tools, temperature=0.7, **_):
        if not any(m.role == "tool" for m in messages):
            yield ProviderChunk(kind="tool_call", tool_call=ToolCall(tool_name="ping", arguments={"x": 1}))
            yield ProviderChunk(kind="done", stop_reason="tool_use")
        else:
            yield ProviderChunk(kind="text", text="final")
            yield ProviderChunk(kind="done", stop_reason="end_turn")


@pytest.mark.asyncio
async def test_tool_path_persists_all_messages():
    """Tool dispatch runs and every loop message is persisted (C-2, C-4)."""
    c = _container()

    async def ping(args):
        return {"pong": args.get("x")}

    class Orch(Orchestrator):
        def _builtin_specs(self):
            return (ToolSpec(name="ping", description="", parameters={}, source="builtin"),)

    orch = Orch(
        resolve_provider=lambda: _ToolThenAnswer(),
        config_store=c.config_store,
        learning=c.learning,
        connectors=c.connectors,
        plugins=c.plugins,
        approvals=c.approvals,
        transcript=c.sessions,
        builtins={"ping": ping},
    )
    s = c.sessions.create(model="fake")
    types = [e.type async for e in orch.run_turn(s.id, "go")]
    assert EventType.TOOL_CALL in types and EventType.TOOL_RESULT in types
    roles = [m.role for m in c.sessions.history(s.id)]
    assert roles == ["user", "assistant", "tool", "assistant"]
    c.shutdown()


class _TitleProvider:
    """Fake provider that returns a fixed one-line title (drives the titler)."""

    name = "fake"

    def __init__(self, title: str = "Quarterly Sales Summary") -> None:
        self._title = title

    def capabilities(self):
        return ProviderCapabilities()

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
        yield ProviderChunk(kind="text", text=self._title)
        yield ProviderChunk(kind="done", stop_reason="end_turn")


@pytest.mark.asyncio
async def test_titler_names_untitled_session_once():
    """An untitled session is titled from its opening exchange, and never relabeled."""
    titles: dict[str, str] = {}
    titler = SessionTitler(
        resolve_provider=lambda: _TitleProvider(),
        get_title=titles.get,
        set_title=lambda sid, t: titles.__setitem__(sid, t),
    )
    history = (
        Message(role="user", content="이번 분기 매출 정리해줘"),
        Message(role="assistant", content="정리했어요: 1) ... 2) ..."),
    )
    assert await titler.maybe_title("s1", history) == "Quarterly Sales Summary"
    assert titles["s1"] == "Quarterly Sales Summary"
    # already titled → no second model call, no relabel
    assert await titler.maybe_title("s1", history) is None


@pytest.mark.asyncio
async def test_titler_skips_echo_fallback():
    """The echo fallback (no model configured) can't summarize → leave it untitled."""

    class _Echo:
        name = "echo"

        def capabilities(self):
            return ProviderCapabilities()

        async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
            yield ProviderChunk(kind="text", text="hi")
            yield ProviderChunk(kind="done", stop_reason="end_turn")

    titles: dict[str, str] = {}
    titler = SessionTitler(
        resolve_provider=lambda: _Echo(),
        get_title=titles.get,
        set_title=lambda sid, t: titles.__setitem__(sid, t),
    )
    assert await titler.maybe_title("s1", (Message(role="user", content="hi"),)) is None
    assert titles == {}


@pytest.mark.asyncio
async def test_titler_caps_a_rambly_model():
    """A model that won't stop is cut off, so titling can't run unbounded and stall
    the client's post-turn title request (the ~30s blocking bug)."""

    class _Rambly:
        name = "fake"

        def capabilities(self):
            return ProviderCapabilities()

        async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
            for _ in range(100_000):
                yield ProviderChunk(kind="text", text="아주 긴 제목 ")
            yield ProviderChunk(kind="done", stop_reason="end_turn")

    titles: dict[str, str] = {}
    titler = SessionTitler(
        resolve_provider=lambda: _Rambly(),
        get_title=titles.get,
        set_title=lambda sid, t: titles.__setitem__(sid, t),
    )
    t = await titler.maybe_title("s1", (Message(role="user", content="hi"),))
    assert t is not None and len(t) <= 60  # bounded by TITLE_MAX_CHARS


@pytest.mark.asyncio
async def test_approval_resolve():
    gate = ApprovalRegistry()
    req = ApprovalRequest(kind="tool_approval", payload={"tool_name": "x", "arguments": {}})

    async def answer():
        await asyncio.sleep(0.01)
        assert any(r.approval_id == req.approval_id for r in gate.pending("s1"))
        gate.resolve(req.approval_id, ApprovalResponse(req.approval_id, "approve"))

    resp, _ = await asyncio.gather(gate.request("s1", req, timeout_s=5), answer())
    assert resp.decision == "approve"
    assert gate.pending("s1") == []  # cleared after resolve


@pytest.mark.asyncio
async def test_approval_timeout_safe_default():
    gate = ApprovalRegistry()
    req = ApprovalRequest(kind="skill_patch", payload={})
    resp = await gate.request("s1", req, timeout_s=0.01)
    assert resp.decision == "timeout"  # safe default = reject/cancel


@pytest.mark.asyncio
async def test_approval_cancel_session():
    gate = ApprovalRegistry()
    req = ApprovalRequest(kind="tool_approval", payload={})

    async def canceller():
        await asyncio.sleep(0.01)
        gate.cancel_session("s1")

    resp, _ = await asyncio.gather(gate.request("s1", req, timeout_s=5), canceller())
    assert resp.decision == "cancelled"


@pytest.mark.asyncio
async def test_late_duplicate_resolve_is_noop():
    gate = ApprovalRegistry()
    # unknown id → idempotent no-op (route surfaces 409/410), must not raise
    gate.resolve("appr_missing", ApprovalResponse("appr_missing", "approve"))
