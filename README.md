# RICE 설계 문서

> RICE: 사용자 경험을 축적하며 학습·진화하는 개인 에이전트 + 내부 마켓 생태계 (IBM 내부용 데스크톱 앱)
> Claude Desktop · Hermes Agent를 벤치마킹한 애플리케이션

## 문서 구성 (읽는 순서)

| 순서 | 문서 | 성격 | 상태 |
|------|------|------|------|
| 1 | [RICE_PRODUCT_IDEA.md](RICE_PRODUCT_IDEA.md) | 제품 컨셉 (source of truth) | ✅ 안정 |
| 2 | [RICE_REPO_STRATEGY.md](RICE_REPO_STRATEGY.md) | 레포 구조 + 레포 간 계약 원칙 | ✅ 안정 |
| 3 | [RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md) | rice-runtime 모듈 구조 (정본) | ✅ 안정 |
| 4 | [RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) | 런타임 설계 + 패키징 + 보안 | ✅ 확정 |
| 5 | [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md) | learning loop 자체 스펙 (SKILL.md·usage·생성·개선patch·curator·memory) | ✅ 확정 |
| 6 | [RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md) | 학습 측정·증명 (cold/warm ablation·eval harness·Learning Impact 패널) | ✅ 확정 |
| 7 | [RICE_CONTRACTS.md](RICE_CONTRACTS.md) | app↔runtime API + artifact + platform API | ✅ 확정 |
| 8 | [RICE_PLATFORM_DESIGN.md](RICE_PLATFORM_DESIGN.md) | rice-platform 설계 (catalog·registry·publish) | ✅ 확정 (Phase 2) |
| 참조 | [HERMES_LEARNING_LOOP_REFERENCE.md](HERMES_LEARNING_LOOP_REFERENCE.md) | Hermes Agent 코드 분석 (설계 문서 아님) | ✅ 검증 완료 |

## 문서 간 역할 규칙 (중복 방지)

- 제품 차원 정의(화면 컨셉, 범위)는 **PRODUCT_IDEA**에만 쓴다. (엔드포인트·계약은 쓰지 않는다)
- 레포 구조와 분리 전략은 **REPO_STRATEGY**에만 쓴다.
- 모듈 구조·디렉토리·핵심 불변식·화면→모듈 매핑은 **RUNTIME_DIRECTORY**가 단일 출처다.
- SSE 이벤트·엔드포인트·artifact manifest 계약은 **CONTRACTS**가 단일 출처다.
- learning loop 자체 스펙(SKILL.md 포맷·usage·생성·개선patch·curator·memory)은 **LEARNING_LOOP_DESIGN**이 단일 출처다.
- 학습이 일어났음의 **측정·증명**(지표·cold/warm ablation·eval harness·Learning Impact 패널 데이터 계약)은 **LEARNING_EVAL**이 단일 출처다.
- rice-platform 설계(catalog·registry·publish)는 **PLATFORM_DESIGN**이 단일 출처다.
- 모든 설계 결정은 확정되어 각 문서 본문에 평문으로 반영돼 있다 (별도 결정 등록부 없음).
- **HERMES_LEARNING_LOOP_REFERENCE**는 읽기 전용 참조 자료다.

## 현재 상태 (2026-06-26)

- 단계: 아키텍처 확정 — 3레포 + rice-runtime 모듈화 모놀리스
- 결정됨:
  - 레포 3개: `rice-app` (Electron) / `rice-runtime` (로컬) / `rice-platform` (원격)
  - 2개 백엔드 구조(로컬 runtime + 원격 platform), 7개 화면 매핑 검증
  - rice-runtime 내부 6모듈(server·agent·execution·learning·connector·core) — Protocol 인터페이스 + composition_root.py DI
  - Connector Management 설계 반영 (RUNTIME_DESIGN 9장, CONTRACTS 4장)
  - **MVP 스코프 20건 확정**(2026-06-18): provider(Claude Sonnet 4.5 + Ollama Qwen), 단일 세션, FTS5만, PyInstaller, 자동업데이트 제외, Gmail 커넥터, SSE, STT 제외, 자체 artifact 스펙, 스킬 버전 히스토리·HITL 저장, platform 버전/검수/스키마, stale 3일·archive 7일, 마켓 실행=자식프로세스(데모 신뢰), tool 승인=SSE 이벤트+엔드포인트, mock 카탈로그 — 각 설계 문서 본문에 반영
  - **데모 핵심 3건 설계 반영**(2026-06-22): ① 스킬 **개선(patch)** = 제안→HITL승인→적용(version++·.history, LEARNING_LOOP 5.2) ② 학습 **측정**(cold/warm ablation·eval harness·Learning Impact 패널, LEARNING_EVAL 신규) ③ HITL **승인 parking·timeout·재연결** 메커니즘(SSE+POST 고수, RUNTIME_DESIGN 5.1) — 검색(retrieval)은 MVP에서 견고화하지 않고 `skill_hit`으로 진단(RUNTIME_DESIGN 3장)
- 설계 확정(2026-06-26): Hermes 차용표, `execute_code` 포함(repo 단위 코드 실행), Gmail 단일 커넥터, 엔드포인트 초안, 첨부 xlsx·png 선업로드, manifest/레이아웃, 270K 토큰 예산·서브에이전트 격리, 개선(patch), eval 패널(MySkillsScreen 구현)·WARM 스냅샷·결정적 성공체크 — 전부 각 설계 문서 본문에 반영.
- 다음 액션:
  1. composition_root.py 구현 + 모듈 간 Protocol 인터페이스 정의 (ILearningService, IConnectorService, IAgent 등)
  2. PyInstaller 패키징 스파이크 (1~2일)
  3. Connector(Gmail) OAuth 흐름 구현 (RUNTIME_DESIGN 9장) + `execute_code` 실행 경계(RUNTIME_DESIGN 5장) + 첨부 파이프라인(CONTRACTS 1.3)
  4. 개선(patch) diff 카드 + Learning Impact 패널은 프론트 선구현 완료 → eval harness 골격(`rice-runtime/eval/`) + task 3~5개로 실측치 교체
