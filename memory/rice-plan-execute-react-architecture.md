---
name: rice-plan-execute-react-architecture
description: "RICE 실행 모델(plan-and-execute × ReAct) 정본은 RICE_AGENT_EXECUTION.md — 2026-07-10 오케스트레이션 이원화(§10)까지 소스 동기화 완료. 여기는 문서에 없는 잔여 사항만"
metadata: 
  node_type: memory
  type: project
  originSessionId: c872ab7c-13fb-4865-b47e-5289f6123e37
---

RICE의 실행 모델(plan-and-execute × ReAct 하이브리드, plan 강제종결, 결정론적 lexical 라우터, 관련도 기반 tool/skill 선별, 스킬 승격 검증 게이트 등)은 [RICE_AGENT_EXECUTION.md](RICE_AGENT_EXECUTION.md)가 정본이며, 2026-07-10에 소스와 완전히 동기화됐다. 아키텍처 상세를 여기 다시 적지 않는다 — 문서를 직접 참고할 것.

**Why:** memory에 아키텍처 설명을 중복 보관하면 문서와 따로 드리프트한다(이번 정리의 발단). 문서가 최신인 한 memory는 포인터 + 잔여사항만 들고 있는 게 맞다.

**2026-07-10 완료**: provider별 orchestration profile(local vs frontier) 분리 — `agent/profiles.py`, 정본 §10. 같은 개편에서: 프리픽스 캐시 불변식(시스템 불변 + plan 상태 ephemeral tail), 서사(NARRATE) 넛지, 계획 첫 스텝 자동 in_progress, 서브에이전트 TurnView 라이브 컨텍스트 주입, Ollama num_ctx/keep_alive config화, registry provider 캐싱, Claude rolling cache. 테스트 정본은 tests/test_orchestration_profiles.py.

**2026-07-10 2차(멀티에이전트·도구 고도화)**: 정본 문서에 반영 완료 — (1) sub-agent v2: 스킬 인덱스 주입+skill_search/skill_view, `tools` 화이트리스트, `.subagent/<thread>/` 격리 write_scratch 아티팩트 채널(§6, tests/test_subagent_v2.py); (2) fetch_url v2: 홉별 SSRF 가드(종전엔 리다이렉트 후 검사라 실구멍), markdown형 추출, 유한 재시도, HTTP 에러 구조화 반환, start_index 페이지네이션+TTL 캐시(tests/test_web_tools.py); (3) pptx: autofit 폰트 스케일, image 레이아웃, 차트 그리드라인, docProps/riceSpec.json 임베드 기반 edit_presentation(tests/test_pptx_v2.py); (4) **종료 계약**: 넛지 카운터는 턴-로컬·리셋 없이 소진, 새 방어 장치 추가 시 tests/test_loop_termination.py에 상호작용 시나리오 필수(§3.8) — 사용자가 명시적으로 요구한 불변식(안전장치끼리 창발 루프 금지).

**잔여 주의**: 사용자 ~/.rice/config.yaml에 구버전 `planner_timeout_seconds: 240`이 남아있다(기본은 90) — 해가 없지만 설정 리셋 시 정리 대상.

관련: [[rice-connector-and-frontend-state]], [[user-runs-rendering]], [[rice-small-model-stabilization]], [[no-hardcoded-user-facing-text]]
