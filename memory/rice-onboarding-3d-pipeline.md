---
name: rice-onboarding-3d-pipeline
description: RICE 온보딩 Learning Core 재설계 — 벤치마크 목표·팔레트 규칙(앱 테마 준수)·레이아웃 함정·3D 소싱
metadata: 
  node_type: memory
  type: project
  originSessionId: 041e1a28-e244-4b41-95dc-b672c20f733b
---

RICE 온보딩(rice-app)은 단일 3D 상태 오브젝트 **Learning Core**(명품 시계 무브먼트형) 중심의 5스텝(약속→원리→확장→신뢰→시작)으로 재설계 중. 파일: `rice-app/src/components/carbon/onboarding/`의 OnboardingFlow.tsx(셸)·steps.tsx·scenes.tsx·LearningCore.tsx·mockData.ts. 현재 코어는 **인앱 SVG 플레이스홀더**(교체 seam=LearningCore.tsx, 중앙에 이미지 슬롯 주석).

**벤치마크(정본 목표) — 반드시 먼저 볼 것:** `screenshoot/onboarding/benchmark/`
- `3D_exam3`(=Step2 목표): **좌 플랫 플로우(대화→패턴→제안→승인→재사용) + 우 프리미엄 다이얼/노브, 나란히.**
- `3D_exam2`(=Step3 목표): **중앙 코어 + 서로 다른 3D 모듈 3개(스킬 칩·플러그인 큐브더미·커넥터 포트)를 빛나는 케이블로 연결 + 실제 앱 아이콘(Drive·Sheets·Mail·Cal).**
- `3D_exam1`: 화려한 Step2 대안(채팅→입자→중앙 HUD 코어+궤도).
- `Conceptual Reference1~3`: 톤(명품 시계 매크로·시네마틱 다크).

**Why(2026-07-09 실패에서 배움):**
- ❌ **팔레트를 네이비(#0D0F12/#24262B/#1677FF)로 오버라이드하지 말 것.** chatScreen과 단절돼 "다른 앱"처럼 보임. **앱 Carbon 팔레트를 그대로 따를 것**: base #161616 · panel #262626/#393939 · interactive #0f62fe · success #42be65 · skill #4589ff · plugin #a56eff · connector #08bdba. 온보딩만 국소 변경 금지, 전역도 뒤엎지 말 것.
- ❌ **사진형 3D를 플랫 UI 위에 하우징 없이 띄우면 "따로 논다"**(blob). 코어를 중앙에 놓고 노드/카드를 겹치게 하지 말 것 → exam3처럼 **좌우 분리** + 어떤 3D 이미지든 **리세스 하우징**에 앉혀 통합.
- 코어 안 "RICE" 글자는 **UI 오버레이라 허용**. 텍스트 금지는 *Higgsfield 생성 에셋*에만.

**How to apply(3D 소싱 결정: Higgsfield 타깃 재도전):** 중앙 blob 아닌 **슬롯용 히어로만** 생성 — exam3 노브 + exam2 모듈(칩·큐브·포트), **크리스프·밝게·투명배경**(지난 결과는 murky/어두워 실패). 검증된 레시피: 스틸=**Recraft V4.1**(`background_color`+`colors`로 브랜드 lock, count4·2k≈8크레딧), 루프=**Kling v3.0 pro**(`start=end` seamless, sound off), `remove_background`. 잔액 max 플랜(~1860), `get_cost` 프리플라이트. 생성 전 벤치마크 대비 검증 후 배선. [[user-runs-rendering]](실행/스크린샷은 사용자) · [[no-hardcoded-user-facing-text]].
