---
name: rice-small-model-stabilization
description: "소형 로컬 모델(qwen 9b) 무한 thinking 루프 방지 안정화 — 정본은 RICE_AGENT_EXECUTION.md, 여기는 문서에 없는 코드 트랩·인스턴스별 설정·미착수 항목만"
metadata: 
  node_type: memory
  type: project
  originSessionId: c744c884-190a-4c0c-a247-a5b88ca6d65e
---

2026-07-04 qwen3.5:9b가 플래너에서 무한 thinking 루프에 빠진 실측 사고 후 안정화 조치. 메커니즘(think=False+스키마 강제, sub-agent read-only 미니 ReAct 루프, 빈응답/빈tool-name 방어 등)은 이제 [RICE_AGENT_EXECUTION.md](RICE_AGENT_EXECUTION.md) §2·§3.8·§6에 정식 반영돼 있다(2026-07-08 동기화) — 여기 재기술 안 함. **주의**: 이 사고 당시엔 플래너에 "(planning…)" breadcrumb를 붙였으나, 2026-07-07 사용자 지시로 하드코딩 breadcrumb/preamble은 전량 제거됐다([[no-hardcoded-user-facing-text]]) — 지금은 그 부분이 없다.

**문서에 없는 코드 트랩(2026-07-06 실측, 계속 유효)**: `composition_root`의 utility provider(`OllamaProvider(cfg, think=False)`)는 titler 전용 비스트리밍 경로를 타며 `num_predict` 기본이 ~20토큰 — 제목엔 맞지만 그 외 용도로 재사용하면 전부 잘린다(스킬 analyzer가 한때 이걸로 빈 응답을 겪음). 구조화 단발 호출은 반드시 **메인 provider + per-call `think=False`/`response_schema`/`max_tokens`**로 부를 것. gpt-oss는 thinking을 끌 수 없어 reasoning 토큰이 max_tokens 예산을 같이 소모하므로 여유 있게 잡을 것(예: 3000).

**미이식(문서에도 없는 TODO, 향후 후보)**: streaming watchdog(TTFB/idle), ToolCallGuardrailController(동일 인자 반복 실패 추적), fallback provider 체인, thinking-prefill 복구.

**config.yaml 인스턴스 상태(2026-07-08 재확인, 여전히 유효)**: 사용자 로컬 `~/.rice/config.yaml`에 `planner_timeout_seconds: 240`이 저장돼 있다 — 코드 기본값은 90이지만 저장된 값이 우선하므로 실제로는 여전히 240으로 동작 중. 코드 기본값 변경이 이 사용자 환경엔 안 먹힌다는 뜻 — 필요하면 파일을 직접 고칠 것.

관련: [[rice-plan-execute-react-architecture]], [[hermes-agent-source-location]]
