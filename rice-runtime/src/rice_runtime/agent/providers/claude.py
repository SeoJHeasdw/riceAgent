"""Claude provider adapter for the normalized `ILLMProvider` seam (RUNTIME_DESIGN §6).

This module keeps Anthropic-specific protocol details behind a provider-local
adapter so the agent loop only ever sees `ProviderChunk`s. History conversion is
split into pure helpers because assistant tool_use ↔ tool_result pairing is the
most fragile part of the seam and needs isolated tests.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from ...core.config import ProviderConfig
from ...core.errors import ProviderError, classify_provider_error
from ...core.types import IMAGES_METADATA_KEY, Message, ToolCall, ToolSpec
from .interfaces import ILLMProvider, ProviderCapabilities, ProviderChunk

log = logging.getLogger("rice.provider.claude")

_CLAUDE_KEY_SERVICE = "rice.provider.claude"
_CLAUDE_KEY_USERNAME = "api_key"


@dataclass(frozen=True)
class _ToolUseState:
    id: str
    name: str
    partial_json: str = ""


def _to_claude_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters,
        }
        for tool in tools
    ]


def _claude_system_blocks(system: str) -> list[dict[str, Any]]:
    if not system:
        return []
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def _apply_rolling_cache(native: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """마지막 메시지의 마지막 콘텐츠 블록에 rolling cache breakpoint를 찍는다.

    system 블록의 breakpoint는 tools+system 프리픽스만 커버한다 — 히스토리는
    매 반복 자라므로 tail에 두 번째 breakpoint를 둬야 이전 반복까지의 대화가
    캐시 히트가 된다(공식 multi-turn 패턴: "가장 최근 턴의 마지막 블록").
    이전 위치의 breakpoint는 유효한 read point로 남으므로 반복마다 이동해도
    안전하다. str 콘텐츠는 블록 리스트로 승격해서 찍는다."""
    if not native:
        return native
    last = native[-1]
    content = last.get("content")
    if isinstance(content, str):
        if not content:
            return native
        last["content"] = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
        return native
    if isinstance(content, list) and content:
        content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}
    return native


def _image_blocks(message: Message) -> list[dict[str, Any]]:
    """metadata["images"] 첨부(core.types 계약)를 Claude image 콘텐츠 블록으로.
    잘못된 항목은 조용히 건너뛴다 — 첨부 하나가 턴을 깨면 안 된다."""
    blocks: list[dict[str, Any]] = []
    for item in message.metadata.get(IMAGES_METADATA_KEY) or ():
        if isinstance(item, dict) and item.get("data_b64"):
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": item.get("mime_type") or "image/png",
                        "data": item["data_b64"],
                    },
                }
            )
    return blocks


def _to_claude_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    native: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "user":
            images = _image_blocks(message)
            if images:
                content: list[dict[str, Any]] = list(images)
                if message.content:
                    content.append({"type": "text", "text": message.content})
                native.append({"role": "user", "content": content})
            else:
                native.append({"role": "user", "content": message.content})
            continue
        if message.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            for call in message.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.tool_name,
                        "input": call.arguments,
                    }
                )
            native.append({"role": "assistant", "content": blocks or message.content})
            continue
        if message.role == "tool":
            tool_call_id = message.metadata.get("tool_call_id")
            if not tool_call_id:
                raise ProviderError("tool message missing tool_call_id metadata")
            images = _image_blocks(message)
            # 이미지 첨부가 있으면 tool_result content를 블록 리스트로 승격 —
            # 모델이 이 도구 결과의 이미지를 실제로 "본다"(vision_analyze 경로).
            # 빈 text 블록은 API가 거부하므로 본문이 있을 때만 싣는다.
            result_content: Any = message.content
            if images:
                text_part = [{"type": "text", "text": message.content}] if message.content else []
                result_content = [*text_part, *images]
            native.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_call_id,
                            "content": result_content,
                        }
                    ],
                }
            )
    return native


def _load_api_key() -> str:
    try:
        import keyring  # noqa: PLC0415 - optional dependency by design
    except ImportError:
        keyring = None

    if keyring is not None:
        key = keyring.get_password(_CLAUDE_KEY_SERVICE, _CLAUDE_KEY_USERNAME)
        if key:
            return key

    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key:
        return env_key
    raise ProviderError("claude API key not found in keychain or ANTHROPIC_API_KEY")


def _map_stop_reason(stop_reason: str | None) -> str:
    return {
        "end_turn": "end_turn",
        # stop_sequence도 정상 종료다 — "error"로 오분류하면 관측/레저가 멀쩡한
        # 턴을 실패로 기록한다.
        "stop_sequence": "end_turn",
        "tool_use": "tool_use",
        "max_tokens": "max_tokens",
    }.get(stop_reason or "", "error")


class ClaudeProvider(ILLMProvider):
    name = "claude"

    def __init__(self, cfg: ProviderConfig, *, api_key: str | None = None) -> None:
        self._cfg = cfg
        # Prefer the key registered in state.db (passed by the registry); fall back
        # to the OS keychain / ANTHROPIC_API_KEY for the env-configured path.
        self._api_key = api_key or _load_api_key()
        # 인스턴스 수명 동안 재사용하는 SDK 클라이언트(커넥션 풀 유지) — registry가
        # 설정 시그니처로 인스턴스를 캐싱하므로 ReAct 반복 간 재수립이 사라진다.
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from anthropic import AsyncAnthropic  # noqa: PLC0415 - optional dependency by design

            self._client = AsyncAnthropic(api_key=self._api_key, base_url=self._cfg.base_url)
        return self._client

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            streaming=True,
            prompt_caching=True,
            vision=True,
        )

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        temperature: float = 0.7,
        response_schema: dict | None = None,  # noqa: ARG002 — no constrained decoding; callers keep the shape in the prompt
        think: bool | None = None,  # noqa: ARG002 — extended thinking is not enabled on this wire
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        client = self._get_client()
        tool_states: dict[int, _ToolUseState] = {}
        # Anthropic streams stop_reason on the `message_delta` event, NOT on
        # `message_stop` — capture it there and emit on stop (RUNTIME_DESIGN §6).
        final_stop_reason: str | None = None
        # Usage: input_tokens arrives on `message_start`, output_tokens accrues on
        # `message_delta`. Emitted on the done chunk → TokenCalibrator.
        input_tokens: int | None = None
        output_tokens: int | None = None
        yielded_done = False

        try:
            async with client.messages.stream(
                model=self._cfg.model,
                max_tokens=max_tokens or 4096,
                system=_claude_system_blocks(system),
                messages=_apply_rolling_cache(_to_claude_messages(messages)),
                tools=_to_claude_tools(tools),
                temperature=temperature,
            ) as stream:
                async for event in stream:
                    event_type = getattr(event, "type", None)
                    if event_type == "message_start":
                        usage = getattr(getattr(event, "message", None), "usage", None)
                        if usage is not None:
                            # Anthropic의 input_tokens는 캐시에서 읽은/새로 쓴 프리픽스를
                            # **제외**한 비캐시 분량만 센다. 캘리브레이터에는 "이 프롬프트의
                            # 전체 토큰"이 필요하므로 셋을 합산한다 — rolling cache가 히트할
                            # 수록 input_tokens 단독은 전체의 극히 일부가 되어(Ollama의
                            # prompt_eval_count와 같은 캐시 아티팩트) 보정 신호가 사라진다.
                            base = getattr(usage, "input_tokens", None)
                            if base is not None:
                                input_tokens = (
                                    base
                                    + (getattr(usage, "cache_creation_input_tokens", None) or 0)
                                    + (getattr(usage, "cache_read_input_tokens", None) or 0)
                                )
                            output_tokens = getattr(usage, "output_tokens", None) or output_tokens
                    if event_type == "content_block_start":
                        block = event.content_block
                        if getattr(block, "type", None) == "tool_use":
                            tool_states[event.index] = _ToolUseState(id=block.id, name=block.name)
                            if getattr(block, "input", None):
                                yield ProviderChunk(
                                    kind="tool_call",
                                    tool_call=ToolCall(tool_name=block.name, arguments=block.input, id=block.id),
                                )
                        elif getattr(block, "type", None) == "thinking" and getattr(block, "thinking", None):
                            yield ProviderChunk(kind="thinking", text=block.thinking)
                    elif event_type == "content_block_delta":
                        delta = event.delta
                        delta_type = getattr(delta, "type", None)
                        if delta_type == "text_delta":
                            yield ProviderChunk(kind="text", text=delta.text)
                        elif delta_type == "thinking_delta":
                            yield ProviderChunk(kind="thinking", text=delta.thinking)
                        elif delta_type == "input_json_delta":
                            state = tool_states.get(event.index)
                            if state is not None:
                                tool_states[event.index] = _ToolUseState(
                                    id=state.id,
                                    name=state.name,
                                    partial_json=state.partial_json + delta.partial_json,
                                )
                    elif event_type == "content_block_stop":
                        state = tool_states.pop(event.index, None)
                        if state is not None and state.partial_json:
                            # malformed 인자 JSON은 fail-soft — {}로 폴백해 도구
                            # 검증 에러(모델이 보고 교정할 수 있는 데이터)로 흐르게
                            # 한다. raise 하면 스트림 전체가 죽어 턴이 실패한다
                            # (_openai_compat.assemble_stream과 같은 정책).
                            try:
                                arguments = json.loads(state.partial_json)
                            except (json.JSONDecodeError, ValueError):
                                log.warning(
                                    "malformed tool_use input JSON for %s — falling back to {}",
                                    state.name,
                                )
                                arguments = {}
                            yield ProviderChunk(
                                kind="tool_call",
                                tool_call=ToolCall(
                                    tool_name=state.name,
                                    arguments=arguments,
                                    id=state.id,
                                ),
                            )
                    elif event_type == "message_delta":
                        stop_reason = getattr(getattr(event, "delta", None), "stop_reason", None)
                        if stop_reason:
                            final_stop_reason = stop_reason
                        usage = getattr(event, "usage", None)
                        if usage is not None and getattr(usage, "output_tokens", None):
                            output_tokens = usage.output_tokens
                    elif event_type == "message_stop":
                        yield ProviderChunk(
                            kind="done",
                            stop_reason=_map_stop_reason(final_stop_reason),
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                        )
                        yielded_done = True
        except ProviderError:
            raise
        except Exception as exc:  # pragma: no cover - exercised via mocks in tests
            raise classify_provider_error(exc, self.name) from exc

        if not yielded_done:
            yield ProviderChunk(kind="done", stop_reason="error")
