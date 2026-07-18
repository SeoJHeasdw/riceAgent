"""learning/candidate/rollout.py — 시뮬레이션 롤아웃 (LEARNING_LOOP §10.6).

한 arm(baseline 또는 candidate)의 한 과제 실행: 메인 에이전트 루프를 흉내낸
bounded 미니 루프를 돌리되, **도구는 절대 실제로 실행하지 않는다** — 평가가
사용자 파일·외부 세계에 부작용을 내면 안 되고(eval/runner.py 의 격리 원칙),
어떤 도구가 올지 스킬마다 다르므로 mock 시퀀스를 미리 쓸 수도 없다. 모든 도구
호출에는 동일한 중립 승인 결과가 돌아가고, 관찰 대상은 "모델이 어떤 절차를
어떤 순서로 밟으려 했고 최종적으로 뭐라고 답했는가"다.

이 시뮬레이션의 증거력 한계는 명시적이다(LEARNING_EVAL §10): 도구 결과 내용에
따라 갈리는 분기(진단→대응)는 여기서 관찰되지 않는다. 그 대신 결정적이고
부작용이 없으며, 두 arm 이 완전히 동일한 환경을 본다 — baseline-vs-candidate
비교의 단일 변인 통제(스킬 블록만 다름)는 성립한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence

from ...agent.providers.interfaces import ILLMProvider
from ...core.types import Message, ToolSpec

# 시뮬레이션 환경의 유일한 도구 결과 — 두 arm·모든 도구에 동일하게 돌아간다.
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
    max_steps: int = 5,
    max_output_tokens: int | None = None,
) -> RolloutResult:
    """bounded 미니 루프 1회. provider 예외는 그대로 전파한다 — 호출자
    (harness)가 CandidateEvalError 로 번역해 재시도 계약을 태운다."""
    messages: list[Message] = [Message(role="user", content=task_prompt)]
    observed_calls: list[dict] = []
    llm_calls = 0
    output_chars = 0
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
            messages.append(
                Message(
                    role="tool",
                    content=SIMULATED_TOOL_RESULT,
                    metadata={
                        "tool_call_id": call.id,
                        "tool_name": call.tool_name,
                        "tool_source": "builtin",
                        "ok": True,
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
    )
