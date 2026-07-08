---
name: rice-title-gen-and-i18n-direction
description: RICE 제목 생성은 언어설정 미참조(모델이 대화 언어로 생성); 앱은 ko/en 2개국어 로컬앱 예정; 프롬프트는 rice_runtime/prompts/에 중앙화
metadata: 
  node_type: memory
  type: project
  originSessionId: e91e1f70-e19c-490e-8752-31dae47bbfa2
---

RICE 채팅 제목 생성(`rice_runtime/agent/titler.py`, 프롬프트는 `rice_runtime/prompts/title.py`)은 의도적으로 **언어 설정(config.language / UI locale)을 참조하지 않는다**. 제목 프롬프트가 "대화와 같은 언어로 작성"이라고 모델에 지시 → 한국어 대화면 한국어 제목, 영어 대화면 영어 제목이 자동으로 나온다.

**중요(아키텍처):** 제목 생성은 **턴 스트림 안에 두면 안 된다**. 한때 orchestrator 5b에서 답변 후 동기 await했더니 제목 LLM 호출이 `done`을 막아 채팅 종료 후 ~30초 멈춤이 생김. 현재는 **별도 엔드포인트 `POST /sessions/{id}/title`**(Container.titler가 처리) — 프론트가 첫 턴 `done` 후 비차단으로 호출하고, 사이드바 행 title만 in-place 갱신(목록 refetch 안 함). 새 세션은 보내는 즉시 사용자 메시지 40자 잘라 **낙관적**으로 사이드바에 추가 → LLM 제목 도착 시 교체. titler는 한 줄/60자에서 **조기 종료**해 수다스러운 모델도 지연 상한. echo fallback이면 제목 생성 skip(낙관적 제목 유지).

**Why:** 앱은 향후 **한국어<->영어 2개국어 로컬 애플리케이션**으로 만들 예정이라 locale 강제 주입은 불필요하고, 모델이 대화 언어를 따라가는 편이 단순·견고하다. (과거에 "병렬 LLM 제목생성 + 한글 하드코딩" 기능을 만들었다가 마음에 안 들어 drop했고, 이번에 위 방식으로 재구현함.)

**How to apply:** 제목/요약류 LLM 출력의 언어는 설정값으로 강제하지 말고 "대화 언어를 따르라"고 프롬프트에 지시. 추후 locale-강제 제목이 필요하면 titler에 language 파라미터를 추가(현재 미구현). model-facing 프롬프트는 `rice_runtime/prompts/`(core 동급 leaf, `compose_system_prompt`로 섹션 합성)에 모으기로 결정 — 라우트/모듈에 흩지 말 것. profile.py의 선호 템플릿은 아직 미이전(다음 후보).
