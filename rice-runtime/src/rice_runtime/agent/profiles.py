"""Orchestration profiles — 소형 로컬 모델과 프론티어 모델의 이원화 지점.

하나의 오케스트레이션 코드가 두 체급의 모델을 서빙한다. 차이는 알고리즘이 아니라
**보정 장치의 강도**다: 프론티어는 지시 한 번이면 따르고, 소형 로컬 모델은 지시의
위치(recency)·반복(넛지)·물리적 예산(num_ctx)이 곧 준수율이다. 그 차이를 여기
하나의 불변 프로파일로 모아 두고, 오케스트레이터가 턴마다 resolve해서 루프와
프롬프트 조립에 주입한다 — 루프 본문에 "이 모델은 작으니까" 분기가 흩어지는 것을
막는 단 하나의 장소.

프로파일이 켜고 끄는 것:

  action_rules_epilogue   시스템 프롬프트 **최후미**에 액션 규칙(서사·검증·계획
                          보고) 요약 블록을 붙인다. 소형 모델은 프롬프트 끝의
                          지시에 가장 강하게 반응한다 — 같은 규칙이 앞(BASE)에도
                          있지만, 스킬/메모리 additions가 그 위를 덮은 뒤의
                          마지막 발화권을 확보하는 장치.
  narration_nudge_after   서사 없이 도구만 부르는 침묵 라운드가 이만큼 연속되면
                          synthetic user 넛지로 교정한다(0 = off). 프론티어는
                          BASE의 NARRATE 지시만으로 서사하므로 끈다.
  narration_nudge_max     턴당 서사 넛지 상한 — 넛지가 히스토리를 오염시키는
                          것도 비용이므로 bounded.
  clamp_context_to_model_window
                          Ollama num_ctx에서 컨텍스트 예산을 역산해 ContextConfig를
                          클램프한다. 예산(96K)이 모델 창(예: 32K)보다 크면 Ollama가
                          프롬프트 앞부분을 소리 없이 자른다 — 시스템 프롬프트와
                          도구 스키마가 먼저 죽는다. 클램프는 그 잘림을 컨텍스트
                          매니저의 지능적 압축(요약·증류)으로 바꾼다.

결정 규칙(resolve_profile): config의 `agent.orchestration_profile`이 명시되면
그대로, "auto"면 provider에서 유추한다 — kind=ollama 또는 로컬 주소의 base_url을
가진 openai 호환 서버(LM Studio 등)는 local, SaaS(claude/openai/watsonx)는 frontier.
"""

from __future__ import annotations

from dataclasses import dataclass

# 로컬 유추에 쓰는 루프백 호스트 표식 — base_url이 이 중 하나를 가리키면 로컬 서버다.
_LOOPBACK_MARKERS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")


@dataclass(frozen=True)
class OrchestrationProfile:
    name: str  # "local" | "frontier" — 로그/감사용 라벨
    action_rules_epilogue: bool
    narration_nudge_after: int
    narration_nudge_max: int
    clamp_context_to_model_window: bool


LOCAL_PROFILE = OrchestrationProfile(
    name="local",
    action_rules_epilogue=True,
    narration_nudge_after=2,
    narration_nudge_max=2,
    clamp_context_to_model_window=True,
)

FRONTIER_PROFILE = OrchestrationProfile(
    name="frontier",
    action_rules_epilogue=False,
    narration_nudge_after=0,
    narration_nudge_max=0,
    clamp_context_to_model_window=False,
)


def resolve_profile(
    provider_kind: str, base_url: str | None = None, override: str = "auto"
) -> OrchestrationProfile:
    """이 턴의 프로파일을 정한다. 명시 override가 항상 이긴다 — auto 유추가 틀리는
    배치(원격 Ollama를 프론티어급으로 쓰는 경우 등)의 탈출구."""
    if override == "local":
        return LOCAL_PROFILE
    if override == "frontier":
        return FRONTIER_PROFILE
    if provider_kind == "ollama":
        return LOCAL_PROFILE
    if base_url and any(marker in base_url for marker in _LOOPBACK_MARKERS):
        # openai 호환 로컬 서버(LM Studio, vLLM 등)를 커스텀 엔드포인트로 등록한 경우.
        return LOCAL_PROFILE
    return FRONTIER_PROFILE
