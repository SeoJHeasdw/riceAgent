"""learning/candidate/rollout.py — 시뮬레이션 롤아웃 (LEARNING_LOOP §10.6).

한 arm(baseline 또는 candidate)의 한 과제 실행: 메인 에이전트 루프를 흉내낸
bounded 미니 루프를 돌리되, **도구는 절대 실제로 실행하지 않는다** — 평가가
사용자 파일·외부 세계에 부작용을 내면 안 되고(eval/runner.py 의 격리 원칙),
어떤 도구가 올지 스킬마다 다르므로 mock 시퀀스를 미리 쓸 수도 없다.

도구 결과는 두 단계로 답한다 (EVAL §10.2 trace-grounded replay):

  1. **재생(grounded)**: 제안을 낳은 실제 세션의 도구 전사(learning/episode.py)
     에서 같은 이름의 기록을 **기록 순서대로 소진**하며 그 결과를 그대로 돌려준다
     — 모델이 실제 세션에서 봤던 내용(정제·절단본)이므로 "도구 결과 내용에 따라
     갈리는 분기(진단→대응)"가 록화된 현실의 범위 안에서 관찰된다. 같은 이름의
     기록을 다 쓰면 마지막 기록을 반복한다(읽기형 도구의 안정 상태 가정).
  2. **중립 승인(ungrounded)**: 전사에 없는 도구는 이전과 같은 중립 승인 결과.

두 arm 은 같은 전사·같은 도구 표면을 본다 — 단일 변인(스킬 블록) 통제는 그대로다.
grounded/ungrounded 비율은 RolloutResult 로 노출되어 리포트의 증거 강도
(`grounded_tool_result_rate`)가 된다: 재생이 하나도 없던 평가와 대부분 재생이던
평가는 같은 "simulated" 라도 증거력이 다르다 — 그 차이를 숨기지 않는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence

from ...agent.providers.interfaces import ILLMProvider
from ...core.types import Message, ToolSpec

# 전사에 없는 도구의 중립 승인 결과 — 두 arm·모든 미기록 도구에 동일하게 돌아간다.
SIMULATED_TOOL_RESULT = json.dumps(
    {"ok": True, "simulated": True, "note": "executed in evaluation sandbox"},
    ensure_ascii=False,
)

_ROLLOUT_TEMPERATURE = 0.1  # 구조화/평가 경로 고정 온도(사용자 온도와 독립)


@dataclass(frozen=True)
class RolloutResult:
    """한 arm × 한 과제 × 한 run 의 관찰."""

    final_text: str
    tool_calls: tuple[dict, ...] = ()  # [{"name": str, "arguments": dict}, ...]
    llm_calls: int = 0
    output_chars: int = 0  # 답변 + 도구 인자 총 문자수 (비용 대리 지표)
    grounded_calls: int = 0  # 전사 재생으로 답한 도구 호출 수 (증거 강도 신호)


class _TranscriptReplayer:
    """도구 이름별 기록 큐 — 기록 순서 소진 후 마지막 반복.

    인자 유사도 매칭 대신 기록 순서를 따르는 이유: 전사의 순서는 환경의 시간적
    진실("get status → pending, 다시 get status → ready")이고, 평가 롤아웃의 같은
    이름 반복 호출이 그 진행을 따라가는 것이 유사도 휴리스틱보다 결정적이고
    설명 가능하다."""

    def __init__(self, transcript: Sequence[dict]) -> None:
        self._by_name: dict[str, list[dict]] = {}
        for record in transcript or ():
            name = str(record.get("name") or "")
            if name:
                self._by_name.setdefault(name, []).append(record)
        self._cursor: dict[str, int] = {}

    def reply(self, tool_name: str) -> tuple[str, bool, bool]:
        """→ (결과 content, ok, grounded). 기록이 없으면 중립 승인."""
        records = self._by_name.get(tool_name)
        if not records:
            return SIMULATED_TOOL_RESULT, True, False
        index = min(self._cursor.get(tool_name, 0), len(records) - 1)
        self._cursor[tool_name] = index + 1
        record = records[index]
        content = str(record.get("result") or "") or SIMULATED_TOOL_RESULT
        return content, bool(record.get("ok", True)), True


def build_sandbox_tool_specs(tool_names: Sequence[str]) -> tuple[ToolSpec, ...]:
    """스킬 본문이 지시하는 도구 이름들 → 관대한 시뮬레이션 spec.

    두 arm 은 **같은 합집합** 도구 표면을 본다(어느 한쪽만 아는 도구가 있으면
    표면 차이가 스킬 차이와 교락된다). 파라미터 스키마는 자유 객체 — 이 환경은
    실행하지 않으므로 인자 검증이 아니라 호출 의도 관찰이 목적이다."""
    specs = []
    for name in dict.fromkeys(n for n in tool_names if n):
        specs.append(
            ToolSpec(
                name=name,
                description="Available tool (results are simulated in this environment).",
                parameters={"type": "object", "additionalProperties": True},
            )
        )
    return tuple(specs)


async def run_rollout(
    provider: ILLMProvider,
    *,
    system_prompt: str,
    task_prompt: str,
    tools: Sequence[ToolSpec] = (),
    replay_transcript: Sequence[dict] = (),
    max_steps: int = 5,
    max_output_tokens: int | None = None,
) -> RolloutResult:
    """bounded 미니 루프 1회. provider 예외는 그대로 전파한다 — 호출자
    (harness)가 CandidateEvalError 로 번역해 재시도 계약을 태운다."""
    messages: list[Message] = [Message(role="user", content=task_prompt)]
    replayer = _TranscriptReplayer(replay_transcript)
    observed_calls: list[dict] = []
    llm_calls = 0
    output_chars = 0
    grounded = 0
    final_text = ""

    for _ in range(max(1, max_steps)):
        llm_calls += 1
        step_text_parts: list[str] = []
        step_calls = []
        async for chunk in provider.stream(
            system=system_prompt,
            messages=messages,
            tools=tuple(tools),
            temperature=_ROLLOUT_TEMPERATURE,
            max_tokens=max_output_tokens,
        ):
            if chunk.kind == "text" and chunk.text:
                step_text_parts.append(chunk.text)
            elif chunk.kind == "tool_call" and chunk.tool_call is not None:
                step_calls.append(chunk.tool_call)

        step_text = "".join(step_text_parts)
        output_chars += len(step_text)
        if not step_calls:
            final_text = step_text
            break

        for call in step_calls:
            args = dict(call.arguments or {})
            observed_calls.append({"name": call.tool_name, "arguments": args})
            output_chars += len(json.dumps(args, ensure_ascii=False))
        # 메인 루프의 wire 형태를 따른다: assistant(tool_calls) → 도구별 tool 결과.
        messages.append(
            Message(role="assistant", content=step_text, tool_calls=tuple(step_calls))
        )
        for call in step_calls:
            content, ok, was_grounded = replayer.reply(call.tool_name)
            grounded += 1 if was_grounded else 0
            messages.append(
                Message(
                    role="tool",
                    content=content,
                    metadata={
                        "tool_call_id": call.id,
                        "tool_name": call.tool_name,
                        "tool_source": "builtin",
                        "ok": ok,
                    },
                )
            )
    # 스텝 상한 소진 시 final_text 는 빈 답으로 남는다 — "절차만 밟다 끝났다"는
    # 관찰 그 자체가 심판 입력이 된다(무한 도구 루프는 좋은 응답이 아니다).
    return RolloutResult(
        final_text=final_text,
        tool_calls=tuple(observed_calls),
        llm_calls=llm_calls,
        output_chars=output_chars,
        grounded_calls=grounded,
    )
