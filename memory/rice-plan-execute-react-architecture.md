---
name: rice-plan-execute-react-architecture
description: "RICE 실행 모델(plan-and-execute × ReAct) 정본은 RICE_AGENT_EXECUTION.md — 2026-07-08 소스 동기화 완료. 여기는 문서에 없는 잔여 사항만"
metadata: 
  node_type: memory
  type: project
  originSessionId: c872ab7c-13fb-4865-b47e-5289f6123e37
---

RICE의 실행 모델(plan-and-execute × ReAct 하이브리드, plan 강제종결, 결정론적 lexical 라우터, 관련도 기반 tool/skill 선별, 스킬 승격 검증 게이트 등)은 [RICE_AGENT_EXECUTION.md](RICE_AGENT_EXECUTION.md)가 정본이며, 2026-07-08에 소스와 완전히 동기화됐다(§3.4 plan 강제종결, §5.2 관련도 스포트라이트, §7 검증 게이트 포인터 등 신규 반영). 아키텍처 상세를 여기 다시 적지 않는다 — 문서를 직접 참고할 것.

**Why:** memory에 아키텍처 설명을 중복 보관하면 문서와 따로 드리프트한다(이번 정리의 발단). 문서가 최신인 한 memory는 포인터 + 잔여사항만 들고 있는 게 맞다.

**미착수(문서에도 아직 없는 TODO)**: provider별 orchestration profile(frontier vs local-20b) 분리 — 2026-07-07 적대적 리뷰 잔여 항목.

관련: [[rice-connector-and-frontend-state]], [[user-runs-rendering]], [[rice-small-model-stabilization]]
