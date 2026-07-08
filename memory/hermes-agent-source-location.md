---
name: hermes-agent-source-location
description: Hermes Agent 소스 코드 위치와 탐색 시 주의사항 (RICE의 learning loop 참조 모델)
metadata: 
  node_type: memory
  type: project
  originSessionId: 939e8f17-9946-42a8-a0fc-15b3167f5bf3
---

Hermes Agent 소스는 `/Users/jaehoseo/Desktop/vswrk/bob/hermes-agent`에 있다 (런타임 데이터 홈인 `~/.hermes`와 별개). RICE의 HERMES_LEARNING_LOOP_REFERENCE.md가 분석한 대상 코드베이스다.

**Why:** RICE 설계 검증 시 Hermes 동작을 소스에서 직접 확인할 수 있다. 사용자가 2026-06-11에 위치를 알려줌.

**How to apply:** 폴더가 매우 크므로 사용자 지시대로 필요한 파일만 표적 grep/read 할 것 — 전체 탐색 금지. 핵심 파일: agent/curator.py, agent/memory_manager.py, agent/prompt_builder.py, tools/code_execution_tool.py, run_agent.py(~12k줄), gateway/run.py(~19k줄).
