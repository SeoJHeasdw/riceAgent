"""eval/scripted_provider.py — 결정적 fake provider.

WHY THIS FILE EXISTS
--------------------
echo provider는 입력을 반사할 뿐이라 "스킬이 주입되면 에이전트가 다르게 행동한다"
를 증명할 수 없었다 — success 판정도 키워드 반사에 불과했다. 이 provider는
task YAML의 `script:` 섹션을 그대로 연기한다:

  - 시스템 프롬프트에 `skill_marker`(warm 시드 스킬 본문에만 있는 문구)가 보이면
    → `warm_tools` 시퀀스(스킬이 가르친 최소 절차)를 순서대로 호출.
  - 안 보이면 → `cold_tools` 시퀀스(시행착오가 섞인 더 긴 절차)를 호출.
  - 시퀀스를 다 소화하면 `final_answer` 텍스트로 턴을 마친다.

상태는 갖지 않는다: 매 stream() 호출에서 messages의 tool 결과 수를 세어 다음
호출을 결정한다(agent loop가 tool 결과를 붙여 재호출하는 구조를 그대로 이용).
그래서 같은 입력이면 항상 같은 tool sequence — 결정적(deterministic)이고,
runner의 `tool_sequence` success check가 이를 판정 근거로 쓴다.

도구 이름이 실제 빌트인과 일치할 필요는 없다: 미지의 도구는 loop가 "unknown
tool" 에러 결과를 붙일 뿐 턴은 계속되고, tool_calls 카운트는 그대로 측정된다.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Sequence

from rice_runtime.agent.providers.interfaces import ProviderCapabilities, ProviderChunk
from rice_runtime.core.types import Message, ToolCall, ToolSpec


class ScriptedToolProvider:
    """task YAML `script:` 섹션을 연기하는 ILLMProvider 구현."""

    name = "scripted"

    def __init__(self, script: dict[str, Any]) -> None:
        self._marker: str = str(script.get("skill_marker") or "")
        self._warm_plan: list[dict] = list(script.get("warm_tools") or [])
        self._cold_plan: list[dict] = list(script.get("cold_tools") or [])
        self._final_answer: str = str(script.get("final_answer") or "작업을 완료했습니다.")

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(native_tool_calling=True, streaming=True)

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        temperature: float = 0.7,
    ) -> AsyncIterator[ProviderChunk]:
        plan = self._warm_plan if (self._marker and self._marker in system) else self._cold_plan
        completed = sum(1 for m in messages if m.role == "tool")

        if completed < len(plan):
            step = plan[completed]
            yield ProviderChunk(
                kind="tool_call",
                tool_call=ToolCall(
                    tool_name=str(step.get("name") or "unknown_tool"),
                    arguments=dict(step.get("arguments") or {}),
                ),
            )
            yield ProviderChunk(kind="done", stop_reason="tool_use")
            return

        yield ProviderChunk(kind="text", text=self._final_answer)
        yield ProviderChunk(kind="done", stop_reason="end_turn")
