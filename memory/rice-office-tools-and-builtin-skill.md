---
name: rice-office-tools-and-builtin-skill
description: "office 빌트인 도구(pptx/xlsx/docx) + 빌트인 스킬 계층 — 상세는 RICE_RUNTIME_DIRECTORY.md·RICE_LEARNING_LOOP_DESIGN.md 정본(2026-07-08 동기화), 여기는 확장 방법과 설계 근거만"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9cbbdc80-538b-45d2-991b-3a73707f09cf
---

office 빌트인 도구(create_presentation/create_workbook/create_document)와 빌트인 스킬 계층(예약 네임스페이스, `source: builtin`, business-planning.md)의 구조는 [RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md) `agent/builtins/office/`·`learning/skill/` 트리와 [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md) §2가 정본이다(2026-07-08 동기화) — 상세를 여기 재기술하지 않는다.

**Why(마켓 배포 대신 빌트인을 택한 근거 — 문서에 없는 결정 배경)**: 첫 실행부터 존재해야 스킬 개념의 교보재가 되고, 도구-스킬 버전이 앱 릴리스와 동행하며, 네트워크/설치 의존이 없다.

**How to apply**: 새 빌트인 스킬 = `builtin_skills/*.md` 추가만으로 끝(카탈로그 자동 로드, pyproject package-data 포함됨). 새 빌트인 도구 = `agent/builtins/` 모듈 추가 + `build_builtin_tools` 등록.

**2026-07-10 pptx v2(문서에 반영됨, 확장 시 주의점만)**: create_presentation은 원본 스펙을 `docProps/riceSpec.json`으로 임베드하고 edit_presentation이 그걸 읽어 슬라이드 단위 편집 후 재빌드한다 — pptx 산출 포맷을 바꿀 때 이 임베드 계약(SPEC_PART)을 깨면 편집 도구가 전부 "no embedded spec"으로 거절하게 된다. 새 레이아웃 추가 시 _RENDERERS + _guess_layout + office/__init__ 스키마 설명 3곳을 함께 갱신할 것(테스트: tests/test_pptx_v2.py).

관련: [[rice-plan-execute-react-architecture]], [[rice-connector-and-frontend-state]]
