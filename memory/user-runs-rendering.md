---
name: user-runs-rendering
description: 사용자가 모든 작업에서 렌더링/실행을 직접 함 — 띄우지 말 것
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 14d9befa-1104-427f-8fce-240910d6fc70
---

모든 프롬프트·모든 작업에서 앱 렌더링(실행/스크린샷 확인)은 사용자가 직접 한다.

**Why:** 사용자가 명시적으로 "렌더링은 내가 진행할꺼야"라고 지시함.

**How to apply:** 코드 변경 후 타입체크/빌드 검증까지만 하고, dev 서버 실행이나 스크린샷 검증을 자처하거나 제안하지 말 것. 실행 방법 자체는 repo 루트 README.md의 "로컬 실행" 절에 정리돼 있음(그쪽이 최신 출처 — 여기 따로 베끼지 않음).
