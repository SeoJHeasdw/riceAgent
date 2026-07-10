"""2026-07-10 멀티모달 이미지 첨부 계약 + spec 기반 병렬화 판정 테스트.

커버 대상:
  - core.types.split_image_attachments: `_images` 분리·검증·비-dict 통과
  - ToolDispatcher: 첨부 분리(SSE payload에 base64 미누출) + bridge 전파
  - AgentLoop: ToolResult.images → tool Message metadata["images"]
    (다음 반복 provider가 첨부를 본다 — 본문에는 base64 없음)
  - provider 매핑 3종: Claude tool_result image block / Ollama message.images /
    OpenAI-compat 합성 user 메시지의 image_url 파트(tool 블록 뒤 flush)
  - vision_analyze: 로컬 파일 → 첨부 반환(본문에 base64 없음), 5MB 상한
  - 컨텍스트 예산: 이미지 첨부가 토큰 비용에 잡힌다
  - ToolScheduler: parallel_safe=False 도구는 하드코딩 이름 없이 직렬 배치
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

from rice_runtime.agent.builtins.vision_tools import tools as vision_tools
from rice_runtime.agent.context_manager import estimate_message_tokens
from rice_runtime.agent.loop import AgentLoop, LoopResult
from rice_runtime.agent.providers._openai_compat import to_oai_messages
from rice_runtime.agent.providers.claude import _to_claude_messages
from rice_runtime.agent.providers.interfaces import ProviderCapabilities, ProviderChunk
from rice_runtime.agent.providers.ollama import _to_ollama_messages
from rice_runtime.agent.tool_dispatch import ToolDispatcher
from rice_runtime.agent.tool_scheduler import ScheduledToolRequest, _batches
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.types import (
    IMAGES_METADATA_KEY,
    Message,
    ToolCall,
    ToolSpec,
    TurnContext,
    split_image_attachments,
)
from rice_runtime.server.approval import ApprovalRegistry

_PNG_1PX = base64.standard_b64encode(
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 32  # magic bytes + filler — mime 감지용
).decode("ascii")

_IMG = {"mime_type": "image/png", "data_b64": _PNG_1PX}


# ── split_image_attachments ───────────────────────────────────────────────────


class TestSplitImageAttachments:
    def test_valid_attachment_is_split_out(self):
        cleaned, images = split_image_attachments({"note": "ok", "_images": [_IMG]})
        assert cleaned == {"note": "ok"}
        assert images == (_IMG,)

    def test_non_dict_and_plain_results_pass_through(self):
        assert split_image_attachments("text") == ("text", ())
        assert split_image_attachments({"a": 1}) == ({"a": 1}, ())
        assert split_image_attachments(None) == (None, ())

    def test_invalid_items_are_dropped(self):
        cleaned, images = split_image_attachments(
            {"_images": [_IMG, {"mime_type": "image/png"}, "junk", {"data_b64": ""}]}
        )
        assert images == (_IMG,)
        assert "_images" not in cleaned


# ── dispatcher: 분리 + SSE 미누출 + bridge 전파 ──────────────────────────────


def _spec(name: str, **kw) -> ToolSpec:
    return ToolSpec(name=name, description="d", parameters={"type": "object"}, source="builtin", **kw)


def _dispatcher(builtins, folded=None) -> ToolDispatcher:
    return ToolDispatcher(
        connectors=None,
        plugins=None,
        approvals=ApprovalRegistry(),
        approval_timeout_s=1,
        builtins=builtins,
        folded_catalog=folded,
    )


@pytest.mark.asyncio
async def test_dispatcher_strips_images_from_sse_payload():
    async def camera(args):
        return {"note": "see attached", "_images": [_IMG]}

    dispatcher = _dispatcher({"camera": camera})
    call = ToolCall(tool_name="camera", arguments={}, id="c1")
    events = [ev async for ev in dispatcher.dispatch("s1", call, _spec("camera"))]

    # SSE tool_result payload에 base64가 없다
    result_ev = next(ev for ev in events if ev.type.value == "tool_result")
    assert _PNG_1PX not in json.dumps(result_ev.data)
    assert result_ev.data["result"] == {"note": "see attached"}
    # ToolResult.images로 옮겨졌다
    res = await dispatcher.result_for(call)
    assert res.images == (_IMG,)


@pytest.mark.asyncio
async def test_bridge_tool_call_propagates_images():
    async def camera(args):
        return {"note": "ok", "_images": [_IMG]}

    camera_spec = ToolSpec(name="camera", description="d", parameters={"type": "object"}, source="builtin")
    dispatcher = _dispatcher({"camera": camera}, folded={"camera": camera_spec})
    bridge_spec = ToolSpec(name="tool_call", description="d", parameters={"type": "object"}, source="bridge")
    call = ToolCall(tool_name="tool_call", arguments={"name": "camera", "arguments": {}}, id="b1")

    _ = [ev async for ev in dispatcher.dispatch("s1", call, bridge_spec)]
    res = await dispatcher.result_for(call)
    assert res.ok is True
    assert res.images == (_IMG,)


# ── loop: metadata 부착 → 다음 반복 provider가 본다 ──────────────────────────


class _VisionRoundProvider:
    """1라운드 camera 도구 호출 → 2라운드에서 받은 히스토리를 기록하고 종료."""

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self.seen: list[tuple[Message, ...]] = []

    def capabilities(self):
        return ProviderCapabilities(native_tool_calling=True, vision=True)

    async def stream(self, *, system, messages, tools=(), temperature=0.7, **_):
        self.calls += 1
        self.seen.append(tuple(messages))
        if self.calls == 1:
            yield ProviderChunk(kind="tool_call", tool_call=ToolCall(tool_name="camera", arguments={}, id="c1"))
            yield ProviderChunk(kind="done", stop_reason="tool_use")
        else:
            yield ProviderChunk(kind="text", text="I see a red square.")
            yield ProviderChunk(kind="done", stop_reason="end_turn")


@pytest.mark.asyncio
async def test_loop_attaches_images_to_tool_message_metadata():
    async def camera(args):
        return {"note": "attached", "_images": [_IMG]}

    provider = _VisionRoundProvider()
    loop = AgentLoop(provider=provider, dispatcher=_dispatcher({"camera": camera}))
    ctx = TurnContext(
        session_id="s1",
        system_prompt="sys",
        history=(Message(role="user", content="what is in the image?"),),
        tools=(_spec("camera"),),
        max_iterations=4,
    )
    result = LoopResult()
    _ = [ev async for ev in loop.run(ctx, "s1", result, asyncio.Event())]

    tool_msg = next(m for m in result.history if m.role == "tool")
    assert tool_msg.metadata[IMAGES_METADATA_KEY] == [_IMG]
    # 본문(다음 반복 컨텍스트)에는 base64가 없다
    assert _PNG_1PX not in tool_msg.content
    # 2라운드 provider가 실제로 첨부 달린 tool 메시지를 받았다
    round2_tool = next(m for m in provider.seen[1] if m.role == "tool")
    assert round2_tool.metadata[IMAGES_METADATA_KEY] == [_IMG]


# ── provider 매핑 ─────────────────────────────────────────────────────────────


def _tool_msg_with_image(content: str = '{"note": "attached"}') -> Message:
    return Message(
        role="tool",
        content=content,
        metadata={"tool_call_id": "c1", IMAGES_METADATA_KEY: [_IMG]},
    )


def _assistant_call() -> Message:
    return Message(role="assistant", content="", tool_calls=(ToolCall(tool_name="camera", arguments={}, id="c1"),))


def test_claude_tool_result_carries_image_block():
    native = _to_claude_messages([_assistant_call(), _tool_msg_with_image()])
    tool_result = native[-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    blocks = tool_result["content"]
    assert blocks[0] == {"type": "text", "text": '{"note": "attached"}'}
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"] == {"type": "base64", "media_type": "image/png", "data": _PNG_1PX}


def test_claude_empty_tool_content_omits_empty_text_block():
    native = _to_claude_messages([_assistant_call(), _tool_msg_with_image(content="")])
    blocks = native[-1]["content"][0]["content"]
    # 빈 text 블록은 API가 거부한다 — 이미지 블록만 남아야 한다
    assert [b["type"] for b in blocks] == ["image"]


def test_claude_user_message_with_image():
    msg = Message(role="user", content="이 그림 뭐야?", metadata={IMAGES_METADATA_KEY: [_IMG]})
    native = _to_claude_messages([msg])
    blocks = native[0]["content"]
    assert blocks[0]["type"] == "image"
    assert blocks[1] == {"type": "text", "text": "이 그림 뭐야?"}


def test_ollama_tool_message_carries_images_array():
    native = _to_ollama_messages([_assistant_call(), _tool_msg_with_image()])
    assert native[-1]["images"] == [_PNG_1PX]
    # 첨부 없는 메시지에는 images 키 자체가 없다(불필요한 필드 미전송)
    assert "images" not in native[0]


def test_oai_tool_images_flush_as_user_message_after_tool_block():
    followup = Message(role="assistant", content="done")
    native = to_oai_messages("sys", [_assistant_call(), _tool_msg_with_image(), followup])
    roles = [m["role"] for m in native]
    # system, assistant(tool_calls), tool, [flush user], assistant
    assert roles == ["system", "assistant", "tool", "user", "assistant"]
    flush = native[3]
    kinds = [part["type"] for part in flush["content"]]
    assert kinds == ["text", "image_url"]
    assert flush["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    # tool 메시지 자체는 텍스트만(오픈AI wire 제약)
    assert isinstance(native[2]["content"], str)


def test_oai_trailing_tool_images_flush_at_end():
    native = to_oai_messages("", [_assistant_call(), _tool_msg_with_image()])
    assert native[-1]["role"] == "user"
    assert native[-1]["content"][1]["type"] == "image_url"


def test_oai_user_message_with_image_parts():
    msg = Message(role="user", content="what is this?", metadata={IMAGES_METADATA_KEY: [_IMG]})
    native = to_oai_messages("", [msg])
    parts = native[0]["content"]
    assert parts[0] == {"type": "text", "text": "what is this?"}
    assert parts[1]["type"] == "image_url"


# ── vision_analyze ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vision_analyze_attaches_image_without_base64_body(tmp_path):
    paths = RicePaths.from_env(str(tmp_path))
    img = paths.workspace
    img.mkdir(parents=True, exist_ok=True)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    (img / "shot.png").write_bytes(png)

    spec, handler = vision_tools(paths)[0]
    result = await handler({"image_path": "shot.png", "question": "뭐가 보여?"})

    assert result["mime_type"] == "image/png"
    assert result["size_bytes"] == len(png)
    attachments = result["_images"]
    assert attachments[0]["mime_type"] == "image/png"
    assert base64.standard_b64decode(attachments[0]["data_b64"]) == png
    # 본문 필드에는 base64가 없다(구 image_data_url 제거)
    assert "image_data_url" not in result


@pytest.mark.asyncio
async def test_vision_analyze_rejects_oversize_image(tmp_path):
    paths = RicePaths.from_env(str(tmp_path))
    paths.workspace.mkdir(parents=True, exist_ok=True)
    (paths.workspace / "big.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * 1024 * 1024 + 1))

    _, handler = vision_tools(paths)[0]
    with pytest.raises(ValueError, match="size limit"):
        await handler({"image_path": "big.png"})


# ── 컨텍스트 예산 ─────────────────────────────────────────────────────────────


def test_image_attachment_counts_toward_token_budget():
    plain = Message(role="tool", content="{}", metadata={"tool_call_id": "c1"})
    with_img = Message(role="tool", content="{}", metadata={"tool_call_id": "c1", IMAGES_METADATA_KEY: [_IMG]})
    assert estimate_message_tokens(with_img) - estimate_message_tokens(plain) >= 1_600


# ── 스케줄러: spec 기반 병렬화 판정 ───────────────────────────────────────────


class TestParallelSafeScheduling:
    def _req(self, name: str, **spec_kw) -> ScheduledToolRequest:
        return ScheduledToolRequest(call=ToolCall(tool_name=name, arguments={}, id=name), spec=_spec(name, **spec_kw))

    def test_parallel_safe_false_forces_sequential_batch(self):
        reqs = [self._req("read_a"), self._req("writer", parallel_safe=False), self._req("read_b")]
        batches = _batches(reqs)
        assert [len(b) for b in batches] == [1, 1, 1]

    def test_safe_tools_still_group_into_one_batch(self):
        reqs = [self._req("read_a"), self._req("read_b"), self._req("read_c")]
        assert [len(b) for b in _batches(reqs)] == [3]

    def test_requires_approval_still_sequential(self):
        reqs = [self._req("read_a"), self._req("gate", requires_approval=True)]
        assert [len(b) for b in _batches(reqs)] == [1, 1]

    def test_builtin_mutating_tools_declare_parallel_safe_false(self):
        # 하드코딩 이름 목록을 대체한 선언 — 4종이 실제로 선언했는지 고정한다.
        from rice_runtime.agent.builtins import build_builtin_tools

        specs, _ = build_builtin_tools(RicePaths.from_env("/tmp/rice-test-specs"))
        by_name = {s.name: s for s in specs}
        for name in ("write_file", "edit_file", "terminal"):
            assert by_name[name].parallel_safe is False, name
        # 읽기 도구는 병렬 허용 유지
        assert by_name["read_file"].parallel_safe is True
