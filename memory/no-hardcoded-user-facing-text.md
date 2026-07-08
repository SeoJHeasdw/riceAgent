---
name: no-hardcoded-user-facing-text
description: "사용자에게 보이는 텍스트를 런타임이 하드코딩/템플릿 합성 금지 — 서사는 모델이 스트리밍, 진행 표시는 프론트엔드 담당"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: a329734d-6911-4e7b-9f9e-d1f9b620e055
---

RICE 백엔드에서 사용자에게 보이는 문장(preamble, "계획 중" breadcrumb 등)을 런타임이 하드코딩 템플릿으로 합성해 스트림하면 안 된다 (2026-07-07, 강한 지적).

**Why:** 제품 원칙 — 서사는 모델의 몫이다(Claude Code 방식: NARRATE류 프롬프트 지시에 따라 모델이 tool call 전 한 줄을 직접 스트리밍). 하드코딩 문장은 스트리밍 메시지도 아니고 언어·맥락 대응도 가짜다. 침묵 구간의 진행 표시는 이미 프론트엔드가 담당한다(생각중 애니메이션, "..." 애니메이션, "N초 동안 생각함" 등) — 백엔드가 중복 공급할 이유가 없다.

**How to apply:** 모델이 서사를 생략하는 문제는 (a) 프롬프트 지시 강화, (b) 프론트 affordance로 풀고, 런타임 텍스트 합성으로 풀지 말 것. 런타임이 만들어도 되는 것은 구조화된 이벤트/메타데이터(plan_updated, tool_call 카드 등)까지다 — 자연어 문장은 금지. 관련: [[rice-plan-execute-react-architecture]], [[system-authority-and-architecture-principles]]
