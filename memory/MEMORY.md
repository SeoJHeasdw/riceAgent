# Memory Index

- [시스템 권한 및 아키텍처 원칙](system-authority-and-architecture-principles.md) — 백엔드/프론트엔드 최고 권한(파괴적 행위 허용, 의존성 업그레이드 포함), 백엔드 캡슐화·모듈화, 프론트엔드 컴포넌트 분리 및 UI/UX 경험 최우선
- [Hermes Agent 소스 위치](hermes-agent-source-location.md) — /Users/jaehoseo/Desktop/vswrk/bob/hermes-agent, 표적 탐색만 허용
- [렌더링은 사용자가 직접](user-runs-rendering.md) — 모든 작업에서 실행/스크린샷은 사용자 담당, 자처/제안 금지
- [사용자용 텍스트 하드코딩 금지](no-hardcoded-user-facing-text.md) — 서사는 모델 스트리밍 몫(NARRATE 프롬프트), 진행 표시는 프론트 담당; 런타임의 자연어 문장 합성 금지
- [제목생성·i18n 방향](rice-title-gen-and-i18n-direction.md) — 제목은 언어설정 미참조(모델이 대화 언어로); ko/en 2개국어 예정; 프롬프트는 prompts/에 중앙화
- [Connector·프론트 상태](rice-connector-and-frontend-state.md) — Gmail/Calendar/Drive+로컬MCP+커스텀원격 전부 실배선, 정본은 RICE_RUNTIME_DESIGN.md §9 등(2026-07-08 동기화); memory는 PKCE 미구현 등 잔여 제약만
- [Plan-Execute×ReAct 실행 모델](rice-plan-execute-react-architecture.md) — 정본은 RICE_AGENT_EXECUTION.md(2026-07-10 2차: sub-agent v2 스킬/도구 주입+scratch 아티팩트, fetch_url v2, 루프 종료 계약 §3.8까지 동기화); 잔여는 사용자 config의 구버전 planner_timeout=240만
- [소형 모델 안정화](rice-small-model-stabilization.md) — qwen 9b 무한루프 방지 조치, 정본은 RICE_AGENT_EXECUTION.md; memory는 코드 트랩(utility provider 20토큰 truncation)·사용자 config.yaml의 planner_timeout=240 잔존만
- [Office 도구·빌트인 스킬](rice-office-tools-and-builtin-skill.md) — 정본은 RICE_RUNTIME_DIRECTORY.md·RICE_LEARNING_LOOP_DESIGN.md; memory는 빌트인 채택 근거+확장 방법(How to apply)+pptx v2 임베드 스펙 계약(SPEC_PART) 주의점만
- [온보딩 3D 파이프라인](rice-onboarding-3d-pipeline.md) — Learning Core 5스텝 재설계; 벤치마크(screenshoot/onboarding/benchmark: exam3=Step2, exam2=Step3); 팔레트는 앱 Carbon 준수(네이비 금지); 3D는 Higgsfield 타깃 재도전(슬롯용·크리스프); seam=LearningCore.tsx
