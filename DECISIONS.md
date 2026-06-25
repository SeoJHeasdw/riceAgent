# RICE 결정 등록부 (열린 결정만)

> 목적: **아직 미결정인 항목만** 추적한다. 확정된 결정은 각 설계 문서 본문에 **평문으로 반영**되며 여기서 제거된다.
> ID는 출처 문서 기준. 현재 미결 **11건**.

---

## 1. Runtime ([RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md))

| ID | 항목 | 추천/메모 | 기한 |
|----|------|-----------|------|
| RD 1-A | Hermes 차용 결정표 승인 | Honcho 제외·Curator 수동 트리거 포함 승인 | 수 |
| RD 1-B | `execute_code`(PTC) 포함 | Phase 2로 연기 | 다음 주 |
| RD 9-A ≡ CT 4-A | Connector(Gmail) OAuth 흐름 상세 | 한쪽 확정 시 양쪽 닫힘 | Phase 1 중간 |

## 2. Contracts ([RICE_CONTRACTS.md](RICE_CONTRACTS.md))

| ID | 항목 | 추천/메모 |
|----|------|-----------|
| CT 1-B | 엔드포인트 초안 승인 | OpenAPI YAML 상세화 전 |
| CT 1-C | 첨부/멀티모달 입력 방식 | multipart vs 사전업로드 + MVP 범위 |
| CT 2-B | manifest/레이아웃 승인 | — |

## 3. Learning Loop ([RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md))

| ID | 항목 | 추천/메모 |
|----|------|-----------|
| LL-4 | prefetch N·K + context 토큰 예산 | 주입 상한·우선순위 |
| LL-5 | Skill 개선(patch) 메커니즘 | ★ 제안→HITL승인→적용, 트리거 T1(교정/실패)·T2(수동편집)·T3(정리 리뷰). 계약 추가: SSE `skill_patch_proposed`·`skill_patched`(8종→9종) + `POST /skills/{id}/patch`·`GET /skills/{id}/history`·`POST /skills/{id}/rollback`. CONTRACTS 1.2·1.3에 반영, 승인 필요 (LEARNING_LOOP 5.2.5) |

## 4. Learning Eval ([RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md))

| ID | 항목 | 추천/메모 |
|----|------|-----------|
| LE-1 | Learning Impact 패널 **위치** | ★ MySkillsScreen 내 패널 (7화면 유지) vs 새 화면 신설 (EVAL 6.1) |
| LE-2 | WARM 프로파일 **준비 방식** | ★ B(스냅샷, 재현성) + 부록 A(자연 워밍업) 1회 실측 (EVAL 3) |
| LE-3 | **성공 체크** 구현 | ★ mock tool 호출 시퀀스/최종 상태(결정적), LLM-judge 보조 (EVAL 4) |

> Platform: 미결정 없음.

---

## 5. 즉시 실행 액션 (결정과 별개로 착수)

1. composition_root.py 구현 + 모듈 간 Protocol 인터페이스 정의 (ILearningService, IConnectorService, IAgent 등)
2. PyInstaller 패키징 스파이크 (1~2일)
3. Connector(Gmail) OAuth 흐름 상세 설계 (RD 9-A / CT 4-A)
4. Skill 개선(patch) diff 카드 + Learning Impact 패널 컴포넌트 — 데이터 계약(EVAL 6.3) 기준 시드 fixture로 선구현 (디자인 데모용)
5. eval harness 골격(`rice-runtime/eval/`) + task suite 3~5개 정의 (EVAL 4·5)
