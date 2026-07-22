# RICE Product Idea

> 작성일: 2026-06-10 (개정: **2026-07-22 — 제품 철학 재정렬**: Agent 중심 정의를 업무 자산화 중심으로 전환)
> 목적: 팀원들과 공유하기 위한 RICE 제품 컨셉 문서

---

## 1. 제품 개요

RICE는 **사용자가 일하며 만든 업무 방식을 재사용 가능한 자산으로 축적하고, 검증된 자산을 조직 실행으로 확장하는 AI 업무 시스템**이다.

RICE가 남기려는 것은 일회성 Agent 응답이 아니라, 다음 업무에서도 다시 사용할 수 있는 **검토 가능하고 개선 가능한 업무 방식**이다. Agent는 업무를 수행하는 인터페이스이고, Skill은 업무 자산의 기술 단위이며, Learning Loop는 자산 후보의 발견·재사용·개선을 돕는 내부 엔진이다.

**핵심 차별점:**
- 반복·교정이 나타난 업무에서 재사용할 업무 방식(Skill) 후보를 제안
- 사용자가 승인한 업무 절차만 개인 업무 자산으로 생성·변경
- 세션을 넘어 자산을 재사용하고 버전·이력·성과 근거를 축적
- 개인 업무 자산과 조직 전문성을 공유·배포하는 내부 마켓 생태계

**제품 개념의 위계:**

| 층위 | RICE에서의 역할 |
|------|-----------------|
| 제품 가치 | 업무 방식의 자산화와 재사용 |
| 사용자 접점 | Agent 기반 업무 수행 |
| 자산 단위 | Skill(재사용 가능한 업무 절차) |
| 축적 엔진 | Learning Loop(후보 발견·재사용·개선 제안) |
| 통제 원칙 | 사용자 승인·편집·거부·롤백 |
| 조직 확장 | 개인 자산 공유 + 조직 전문성 배포 |

**아키텍처 개요:**
- **2개 백엔드 구조**: rice-runtime (로컬) + rice-platform (원격)
- **콘텐츠 저장소**: rice-marketplace sibling repo를 rice-platform이 마운트해 skill/plugin artifact source로 사용
- **rice-runtime 내부**: 모듈화된 모놀리스 (Protocol 기반 인터페이스)
- **배포 형태**: Electron 앱 + PyInstaller 단일 바이너리

---

## 2. 해결하려는 문제

### 2.1 세션 간 컨텍스트 단절
- 이전 작업 내용이 다음 세션에 이어지지 않음
- 반복 작업도 매번 새로 설명 필요

### 2.2 업무 방식의 자산화 부재
- 한 번 수행한 복잡한 작업이 다음에 재사용되지 않음
- 반복 과정에서 정립된 절차와 교정 내용이 검토 가능한 형태로 남지 않음

### 2.3 조직 내 지식 분산
- 개인이 만든 유용한 스킬(반복 작업 워크플로우)이 공유되지 않음
- 솔루션팀의 전문성이 필요한 사람에게 전달되지 않음

---

## 3. 핵심 설계 원칙

### 3.1 업무 자산화 루프 (Learning Loop)

업무 수행 → 자산 후보 제안 → 사용자 승인 → 개인 업무 자산 → 재사용·성과 측정 → 개선 또는 공유의 순환 구조

- 시스템은 반복과 교정을 발견하고, 자산 초안을 만들고, 사용 근거를 측정한다.
- 사용자는 어떤 업무 절차를 자산으로 만들고 변경할지 결정한다.
- 승인된 Skill은 이후 관련 작업에서 재사용되고, 실제 사용 결과가 다음 개선 제안의 근거가 된다.
- **AI는 제안하고, 사람은 자산화한다.** 자동화의 대상은 발견·초안·측정이며, 자산 생성·변경의 최종 결정은 사용자에게 있다.

> 여기서 "승인 기반"은 실행 가능한 업무 절차인 Skill의 생성·변경에 적용된다. 저위험 개인화 memory와 사용 텔레메트리는 자동 기록될 수 있으며 열람·삭제할 수 있다. 외부 콘텐츠에서 유래한 memory는 격리 후 승인한다. 따라서 제품 원칙은 "모든 학습의 사전 승인"이 아니라 **"업무 자산의 승인 기반 승격"**이다.

> Learning loop 진행은 실시간 SSE 이벤트로 화면에 가시화된다(이벤트 명세 정본은 [RICE_CONTRACTS.md](RICE_CONTRACTS.md) 1.3 — 개선(patch) 포함 9종(2026-07-04 `plan_updated` 추가), 생성은 2026-07-02부터 patch(from_version=0)로 통일).

### 3.2 User Modeling
사용자별 개인화
- 답변 스타일 학습 (간결형/상세형/기술형)
- 업무 컨텍스트 이해
- 선호 모델 및 도구 기억

### 3.3 Shared Intelligence
조직 내 지식 공유 플랫폼
- 개인 스킬 공유 (Skill Market)
- 솔루션팀 전문성 배포 (Plugin Market)
- Skill/Plugin은 자산의 **형태**, 개인/팀/조직은 자산의 **소유·거버넌스 수준**으로 구분
- 현재 데모는 게시·설치·배포를 다루며, 팀 검토와 조직 표준 승격은 제품화 단계의 거버넌스 범위

---

## 4. 주요 화면 구성 (현재 8개 + 로드맵, 2026-07-08 정정 — HomeScreen 누락돼 있었음)

### 4.0 HomeScreen → rice-runtime

개인화 홈 베이스 — 축적된 업무 자산과 재사용 효과를 로그인 직후 한눈에 보여주는 대시보드(2026-06-29 신규 추가, 이 문서 최초 반영).

**성격:**
- 앱 기본 랜딩 화면(다른 화면으로 가기 전 허브)
- 학습 통계·자주 쓰는 스킬·대기 중인 제안을 집계해 보여준다
- 전용 백엔드 엔드포인트를 신설하지 않고 기존 API(`/skills`, `/eval/impact` 등)를 재사용

**목적:**
업무 방식이 얼마나 축적·재사용되고 있는지를 화면 하나로 요약해, MySkillsScreen까지 들어가지 않아도 자산화의 효과를 체감하게 한다.

---

### 4.1 ChatScreen → rice-runtime
메인 작업 공간

**현재 구현/배선된 기능:**
- 대화 인터페이스
- 모델 선택
- 세션 히스토리 (페이지네이션 로드)
- 로컬 메모리/스킬 기반 응답
- 툴/플러그인 실행 결과 표시
- 응답 재생성(regenerate) · 복사
- LearningPanel 사이드 패널 및 학습 투명성 모달(`GET /learning/events`, `GET /memories`)

**로드맵/미구현:**
- 파일 첨부 / 멀티모달 입력 — 계약 설계는 있으나 `POST /sessions/{id}/attachments`와 `MessageIn.attachment_ids`가 아직 없다
- 대화 내보내기 / 공유 — runtime 라우트 없음

**특징:**
- Claude Desktop 스타일 벤치마킹
- Learning loop 기반 개인화
- 세션 간 컨텍스트 유지

---

### 4.2 SkillMarketScreen → rice-platform
개인 제작 스킬 공유 플랫폼

**성격:**
- 개인 창작물 중심
- 업무 자동화/반복 절차 워크플로우(스킬)
- 선언적 SKILL.md 스펙 (실행/외부 접근이 필요하면 Connector로 연동)

**기능:**
- 스킬 등록/검색/설치
- 버전 관리
- Blue Point 후원 (선택)

**목적:**
개인 생산성 스킬을 조직 자산으로 전환

---

### 4.3 PluginMarketScreen → rice-platform
솔루션팀 전문성 배포 플랫폼

**성격:**
- 조직/솔루션팀 제공물
- 도메인 전문성 패키지
- 특정 제품/영역 지식

**예시:**
- WXO 전문가 플러그인
- 법률 플러그인
- 제품별 트러블슈팅 플러그인

**효과:**
에이전트에 특정 도메인 전문성 추가
- 제품 기능 정확한 설명
- 실무적 트러블슈팅
- 도메인 컨텍스트 이해

---

### 4.4 MySkillsScreen → rice-runtime
개인 업무 자산의 기술 단위인 Skill을 보고 관리하는 화면

**성격:**
- 업무 과정에서 제안·승인된 자산(생성된 Skill) 가시화 — RICE의 핵심 차별점이 눈에 보이는 화면
- 학습으로 생성된 skill + Skill Market에서 설치한 skill + 설치된 plugin이 제공하는 skill을 함께 표시(출처 구분)
- 상태(active / stale / archived)와 사용 통계 확인
- 스킬 보관/활성화, 수동 "스킬 정리" 트리거
- 스킬 **버전 배지·개선(patch) 이력**과 롤백 — 개선 제안 diff 카드 승인 흐름은 [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md) 5.2
- **Learning Impact 패널** — 같은 과제를 자산 재사용 전(cold) vs 후(warm)로 돌린 정량 비교(turns·tool calls·success·관련 스킬 주입)로 **업무 자산의 재사용 효과**를 숫자로 증명하는 surface. **MySkillsScreen 내 패널**로 구현됨. 측정 방법·데이터 계약은 [RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md)

---

### 4.5 MyPluginsScreen → rice-runtime
설치한 Plugin을 관리하는 화면

**성격:**
- 설치된 Plugin 목록과 활성화/비활성화/삭제
- Plugin이 요구하는 외부 연결(`requires.connectors`) 권한 확인

---

### 4.6 ConnectorsScreen → rice-runtime
외부 서비스 연결(Connector)을 관리하는 화면

**성격:**
- 연결된 외부 서비스 목록, 새 연결 추가(OAuth), 삭제, 상태 확인

**목적:**
- Plugin이 외부 서비스(Google Drive, Notion 등)에 접근할 때 필요한 권한 관리
- Plugin manifest의 `requires.connectors`와 연동, OAuth 2.0 기반 인증

---

### 4.7 SettingsScreen → rice-runtime
모델·일반 설정 화면

**성격:**
- 모델 설정(provider, model_id, temperature 등)
- API 키 관리(→ OS 키체인 저장)
- 일반 설정(언어, 테마, **글꼴(font)** 등)

---

### 4.8 향후 추가 검토 화면 (로드맵)

> 설계는 UI의 상위집합이다. 아래는 컨셉상 필요하나 현재 8개 화면엔 없는 것 — 디자인 개선·기능 추가 후보로 추적한다.

- **Skill 작성·게시(Authoring/Publish)**: "개인 스킬 공유"의 진입점. 사용자가 skill(SKILL.md)을 만들어 Skill Market에 publish(`POST /artifacts`). **UI는 존재한다**(`SkillAuthorModal`·`SkillPublishDialog`, SkillMarketScreen/MySkillsScreen에 배선됨 — 2026-07-08 확인). 현재는 로컬 React state만 갱신하고 실제 `POST /artifacts` 호출까지는 아직 연결되지 않았다. 남은 일은 UI 신설이 아니라 백엔드 연동이다. (설치된 skill 관리는 MySkillsScreen이 담당 — Skill↔Plugin 관리는 MySkills/MyPlugins로 이미 대칭)
- **세션 관리 보강**: 세션 이름변경 · FTS5 검색 · 보관(archive).

> 각 화면의 엔드포인트 계약은 [RICE_CONTRACTS.md](RICE_CONTRACTS.md), 화면→백엔드→모듈 매핑은 [RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md) 4장을 참조한다.

---

## 5. Skill vs Plugin 구분

| 구분 | Skill Market | Plugin Market |
|------|-------------|---------------|
| 제공자 | 개인 | 솔루션팀/조직 |
| 성격 | 워크플로우(프롬프트 절차) | 전문성 패키지 (Skills + Connector) |
| 목적 | 업무 자동화/반복 절차 | 도메인 지식 확장 + 외부 연동 |
| 비유 | 재사용하는 작업 절차 | 머리에 추가되는 전문성 |

> 원자적 실행 도구(tool-call)·외부 서비스 접근은 별도 마켓 단위가 아니라 **Connector(MCP) 레이어**가 제공한다. Plugin은 필요한 Connector를 함께 요구(`requires.connectors`)한다.

> **자산 형태와 조직 성숙도는 별개 축이다.** Skill이 곧 개인용이고 Plugin이 곧 조직용인 것은 아니다. Skill/Plugin은 무엇을 배포하는지를 구분하고, 개인/팀/조직은 누가 소유·검토·적용하는지를 구분한다. 현재 데모의 공개/게시 상태를 넘어 `personal → shared → team_standard → org_standard`와 같은 공식 승격 모델은 권한·거버넌스와 함께 제품화 범위에서 정의한다.

---

## 6. 제품 목표

RICE는 다음 3가지를 결합한다:

1. **업무 방식의 자산화**: 반복 작업과 교정을 검토 가능한 Skill로 전환
2. **통제 가능한 재사용과 개선**: 승인된 자산을 다시 적용하고 성과 근거로 개선
3. **조직 실행으로의 확장**: 개인 업무 자산 공유 + 조직 전문성 배포

개인화된 Agent는 이 세 목표를 사용자가 자연어로 이용하게 하는 실행 인터페이스다.

---

## 7. 데모 핵심 포인트

### Show and Prove 목표
1. 업무 수행에서 재사용 가능한 자산 후보가 제안되고, 사용자 승인 후 Skill로 남는 과정
2. 승인된 자산을 다시 사용했을 때의 개선 효과 — **정성**(라이브 SSE·개선 diff 카드)과 **정량**(Learning Impact 패널: 같은 과제 cold→warm에서 turns·tool calls↓, success↑)을 함께 보인다. 측정 설계는 [RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md)
3. 개인 업무 자산과 조직 전문성의 설치·공유·실행
4. 세션 간 컨텍스트 유지와 개인화된 응답

### 현재 범위
- 권한/거버넌스/SSO는 제외
- Show and prove 목적의 데모
- 제품화 여부는 추후 판단

---

## 8. 아키텍처 요약

### 8.1 백엔드 구조
RICE는 **2개 백엔드** 구조를 채택한다:

1. **rice-runtime** (로컬 프로세스)
   - 단일 프로세스 내 모듈화된 모놀리스
   - Protocol 기반 인터페이스로 모듈 간 결합도 완화
   - composition_root.py가 유일한 DI 진입점

2. **rice-platform** (원격 서버)
   - Skill Market + Plugin Market 통합
   - artifact_type으로 구분 (skill | plugin)

보조 콘텐츠 저장소로 sibling repo `rice-marketplace`가 있다. 이는 skill/plugin artifact 원본을 보관하는 저장소이며, `rice-platform`이 `RICE_ARTIFACTS_DIR`/`RICE_PLUGIN_MARKETPLACES`로 마운트해 서빙한다. API를 제공하는 별도 백엔드는 아니다.

### 8.2 rice-runtime 내부 모듈

server / agent / execution / learning / connector / core / prompts의 7개 모듈로 구성된 모듈화 모놀리스다. `prompts`는 model-facing 프롬프트 조립 전용 leaf 모듈이다(2026-07-08 문서 반영). 상세 디렉토리 트리·모듈 책임·핵심 불변식(직접 import 금지, composition_root 단일 와이어링)은 [RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md)(정본)를 참조한다.

---

## 9. 한 줄 정의

**일할수록 나의 업무 방식이 검토 가능한 자산으로 남고, 검증된 자산이 조직의 실행력으로 확장되는 IBM 내부용 AI 업무 시스템**
