# RICE Learning Loop Design

> 작성일: 2026-06-18 (개정: 2026-06-26) · 상태: **확정 (결정 완료)**
> 범위: RICE의 핵심 차별점인 learning loop의 **자체 스펙** — SKILL.md 포맷, usage 텔레메트리, 생성 트리거, **개선(patch)**, curator 규칙, memory/user modeling
> 관계: [HERMES_LEARNING_LOOP_REFERENCE.md](HERMES_LEARNING_LOOP_REFERENCE.md)는 *참조*(Hermes 코드 분석), 이 문서는 *RICE의 결정*이다. 런타임 통합은 [RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) 3·4장, 모듈 위치는 [RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md) `learning/`. **학습이 실제로 일어났음을 측정·증명하는 방법**은 [RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md)(신규).

---

## 1. Learning Loop 개요

```
경험(대화)  →  축적(skill 생성 / memory 기록)  →  재사용(다음 턴 주입)  →  개선(patch · §5.2)
   ▲                                                                              │
   └──────────────────────────────────────────────────────────────────────────┘
```

- 모든 단계는 **SSE 이벤트**(`skill_created`, `memory_written`, `skill_patch_proposed`, `skill_patched` 등, [RICE_CONTRACTS.md](RICE_CONTRACTS.md) 1.3)로 ChatScreen에 실시간 가시화된다 — "쓸수록 똑똑해짐"을 *증명*하는 것이 데모 목표.
- 저장소: `~/.rice/skills/*.md` + `.usage.json` 사이드카, `state.db`(memories, FTS5).

> **용어 — "개선"과 "전이"는 다르다.** 루프의 "개선" 화살표는 스킬 **본문 내용을 고치고 version을 올리는 patch**(§5.2)를 뜻한다. 이는 스킬 **상태(state: active/stale/archived)** 를 바꾸는 **전이(transition, §5.1)** 와 별개 관심사다. 전이는 freshness·생명주기 유지(curator), patch는 품질·정확성 개선이다. 두 개를 한 화살표로 뭉뚱그리지 않는다.

---

## 2. SKILL.md 포맷 (정본)

스킬은 마켓 artifact의 `skills/`와 **동일 포맷**이라 로컬 생성 스킬과 plugin 제공 스킬이 호환된다([RICE_CONTRACTS.md](RICE_CONTRACTS.md) 2.2).

```markdown
---
name: deploy-k8s-service              # kebab-case, 고유
version: 3                            # 정수, patch 시 +1 (MySkillsScreen 버전 표시)
tags: [kubernetes, deploy, ci]        # 마켓/필터/검색용
description: K8s 서비스 배포 절차       # 한 줄 요약 (목록·prefetch 매칭에 사용)
created_at: 2026-06-18T10:00:00Z
updated_at: 2026-06-20T09:00:00Z      # patch 시 갱신 (생성 시 = created_at)
source: session:abc123 | plugin:wxo-expert   # 출처 (학습 생성 vs plugin)
state: active                         # active | stale | archived
---

# 본문: 절차적 지식 (메모리에 넣지 않는 "방법")
1. ...
```

- ✅ frontmatter 필수: `name`, `version`, `description`, `state`. 선택: `tags`, `source`, `updated_at`
- **version**은 단조 증가 정수. patch(개선) 시 +1. 이전 버전은 `~/.rice/skills/<name>/.history/v<N>.md`에 **보관**한다(롤백·diff 가능, §5.2.4).
- ★ `source`로 학습 생성/​plugin 제공을 구분 → MySkillsScreen에서 출처 배지 표시

---

## 3. Usage 텔레메트리 (.usage.json 사이드카)

각 스킬 옆에 `<name>.usage.json`. Hermes의 use/view/patch 패턴 차용.

```json
{
  "skill": "deploy-k8s-service",
  "use": 12,          // 시스템 프롬프트에 주입되어 실제 작업에 사용된 횟수
  "view": 3,          // 사용자가 MySkills에서 상세 열람한 횟수
  "patch": 2,         // 개선(본문 수정)된 횟수 → version과 동기
  "last_used_at": "2026-06-18T09:00:00Z",
  "created_at": "2026-06-10T00:00:00Z"
}
```

- 카운터 증가 시점: `use`=턴 종료 시 주입된 스킬에 가산, `view`=`GET /skills/{id}`, `patch`=본문 수정(승인) 시 (§5.2.4)
- 상태 전이·curator 입력으로 사용 (§5.1). `use`·`patch`는 학습 측정 지표로도 사용된다([RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md) 2장).

---

## 4. 스킬 생성 트리거

✅ **별도 분류 로직 없이 시스템 프롬프트 가이드로 처리**(Hermes 검증 #1). 대화 루프가 시스템 프롬프트에 가이드 문구를 주입한다.

가이드 요지(프롬프트에 삽입):
> "여러 단계(예: 5+ tool calls)를 거쳐 재사용 가치가 있는 절차를 완료했다면, `SKILL.md`로 저장하라. 선언적 사실이 아니라 **반복 가능한 방법**만 스킬로 만든다."

- 생성 시 `skill_created` SSE 이벤트 발행 → ChatScreen에 "스킬 생성됨" 표시(learning loop 가시화 핵심)
- 스킬은 **사용자 확인(HITL) 후 저장**한다 — 생성 제안을 사용자가 승인해야 저장되며, 이후 MySkillsScreen에서 관리(편집/보관/삭제).

---

## 5. Skill Lifecycle (전이) & 개선 (Patch)

이 장은 두 개의 별개 관심사를 다룬다. **5.1 = 상태 전이**(active/stale/archived, curator), **5.2 = 본문 개선**(patch, version++). §1 "용어" 참조.

### 5.1 상태 전이 (Lifecycle & Curator)

상태: `active → stale → archived` (RUNTIME_DESIGN 4장과 동일).

**전이 규칙(curator, `learning/curator/`)**:
- 자동(규칙): `last_used_at` 경과 기준 — ★ 데모 설정 stale 3일 / archive 7일
  - ⚠️ 일(day) 단위는 짧은 데모에서 자동 전이가 **관찰되지 않음** → 시연은 수동 트리거로 한다
- 수동: ChatScreen/MySkills의 "스킬 정리" 버튼 → `POST /skills/curate` 즉시 실행 (데모 경로)
- 사용량 기반 전이는 **향후 고도화로 연기** — 지금은 시간 규칙(자동) + 수동 "스킬 정리"만.

**LLM 리뷰(선택)**: Hermes의 LLM 기반 큐레이션은 수동 트리거 시에만(비용·데모 가시성). 중복 스킬 병합 제안 등 → 5.2.2의 T3 patch 제안으로 연결된다.

### 5.2 Skill 개선 (Patch) — 데모 핵심 경로

> §1 루프의 "개선" 화살표 = 이 절. 스킬 **본문 수정 + version++**. 상태 전이(5.1)와 별개.
> 이전까지 이 화살표는 "누가/무엇이 본문을 고치는가"가 미정이었다. 이 절이 그것을 닫는다.

#### 5.2.1 원칙 — 생성과 동일하게 "제안 → HITL 승인 → 적용"

스킬 생성이 사용자 승인(HITL, §4) 후 저장되듯, **개선도 제안 → 사용자 승인 → 적용**으로 일관한다. 에이전트가 본문을 *무단 자동 수정하지 않는다*.

자율성 스펙트럼에서 ★ 선택 근거:

| 옵션 | 설명 | 판단 |
|------|------|------|
| 완전 자율 | 에이전트가 조용히 본문 수정 | ❌ RICE의 편집가능·감사가능·롤백 원칙 위반, 스킬 오염 위험 |
| ★ **제안 + 승인** | diff 카드로 제안 → 사용자 승인/거부/편집 | 생성과 일관, 감사 가능, **diff가 강력한 데모 비주얼** |
| 수동 only | MySkills 직접 편집만 | "쓸수록 개선"을 증명하기엔 너무 약함 |

#### 5.2.2 트리거 — 무엇이 patch 제안을 일으키는가

**별도 분류 로직 없이 시스템 프롬프트 가이드로 처리**(생성 트리거 §4와 동일 방식, Hermes 검증 #1과 일관).

- **T1 — 턴 중 교정/실패 (주 트리거, 데모 핵심).** 주입된 스킬을 사용하던 중 다음 중 하나가 발생:
  - (a) 사용자가 절차를 **명시적으로 교정** (예: "그 명령은 이제 `-n 8`이야")
  - (b) 절차의 tool call이 **실패** → 에이전트가 대안으로 성공
  - (c) 에이전트가 실행 중 **누락 단계/낡은 값**을 발견

  가이드 문구(프롬프트 삽입):
  > "주입된 스킬을 따르다가 절차가 틀렸거나 단계가 빠졌거나 값이 낡았음을 발견하면 — 특히 사용자가 교정했거나 절차 실패 후 우회로 성공했다면 — 개선된 **전체 본문**과 **한 줄 changelog**를 `skill_patch`로 제안하라. 사소한 표현 변경만으로는 제안하지 않는다."

- **T2 — 사용자 수동 편집 (MySkills).** MySkillsScreen에서 본문 직접 편집 → 저장. 가장 통제적. 사용자가 작성자이므로 **제안 단계 없이 바로 적용**(5.2.4의 1~5 동일, SSE는 `skill_patched`만).

- **T3 — 정리 시 리뷰 (수동 "스킬 정리").** curator의 LLM 리뷰(수동 트리거 시에만 — 5.1의 LLM 리뷰 정책과 일관)가 중복 스킬 병합/개선을 **제안**. 배치성. 동일하게 HITL 승인.

#### 5.2.3 제안 산출물 & HITL 게이트

T1/T3 제안은 다음을 산출한다:
- `proposed_body` — 개선된 **전체 본문**
- `changelog` — 한 줄 요약 (예: "pytest 병렬도 4→8, 사용자 교정 반영")
- `diff` — 현재 version 대비 (UI 렌더용)
- `trigger` — T1a/T1b/T1c/T3 (감사용)

게이트:
1. **`skill_patch_proposed` SSE 이벤트** (`approval_id` 포함 제어 이벤트, `tool_approval_required`과 **동일 parking·timeout·재연결 메커니즘** — 정본 [RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) 5.1, 계약 [RICE_CONTRACTS.md](RICE_CONTRACTS.md) 1.2·1.3) → 앱이 **diff 카드** 표시
2. 사용자 선택: **승인 / 거부 / 편집 후 승인**
3. 응답: **`POST /skills/{id}/patch`** (body 예: `{ "decision": "approve|reject", "edited_body": "..." }`)

#### 5.2.4 적용 (승인 시)

```
1. 현재 본문 → ~/.rice/skills/<name>/.history/v<N>.md   (보관 — §2와 동일)
2. 새 본문 기록, frontmatter version += 1, updated_at 갱신
3. <name>.usage.json 의 patch += 1                      (§3)
4. skill_patched SSE 이벤트 발행 → ChatScreen "스킬 개선됨 v{N+1}" + MySkills 갱신
5. MySkillsScreen: 버전 배지 v{N+1}, patch 카운터 +1, .history에 이전 버전(롤백 가능)
```

거부 시: 변경 없음. **무응답(timeout, 기본 5분)도 거부로 간주** — 본문 불변, 제안 폐기(안전 기본값, [RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) 5.1). (향후) 동일 스킬에 대한 반복 거부 시 제안 빈도를 하향한다.

#### 5.2.5 계약 변경 요약 (CONTRACTS 반영 필요)

이 메커니즘은 [RICE_CONTRACTS.md](RICE_CONTRACTS.md)의 계약을 **확장**한다 — 골격 단계이므로 명시적으로 추가한다:

| 종류 | 추가 | 비고 |
|------|------|------|
| SSE 제어 이벤트 | `skill_patch_proposed` (skill_id, changelog, diff, trigger) | `tool_approval_required`과 동일 폴링형 제어 이벤트 |
| SSE 이벤트 | `skill_patched` (skill_id, version) | 적용 완료. ★ 8종에 추가(→9종) 권장 (skill_created에 patch 플래그를 끼우는 것보다 명확) |
| 엔드포인트 | `POST /skills/{id}/patch` | 제안 응답 (approve/reject/edited_body) |
| 엔드포인트 | `GET /skills/{id}/history` | 버전 히스토리 (diff·롤백 UI) |
| 엔드포인트 | `POST /skills/{id}/rollback` | 이전 버전 복구 (선택) |

> `skill_patch_proposed`의 정지/재개·`approval_id`·timeout·재연결 복구(`GET /sessions/{id}/pending-approvals`)는 tool 승인과 **공유 메커니즘**이라 patch 전용 추가가 아니다 — 정본 [RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) 5.1.

→ 위 patch 메커니즘(제안 → HITL 승인 → 적용 + T1/T2/T3 트리거) 및 계약 추가(`skill_patch_proposed`·`skill_patched`·`/skills/{id}/patch|history|rollback`)가 정본이다. CONTRACTS 1.2·1.3에 반영 완료.

#### 5.2.6 데모 스크립트 (T1a 경로 — 가장 설득력)

```
1. 사용자가 반복 과제 수행 → 에이전트가 스킬 v1 생성(HITL 승인)   [skill_created]
2. 다음 턴, 같은 과제 → 에이전트가 v1 주입해 사용              [use++, "스킬 사용됨"]
3. 환경 변화로 한 단계 실패  /  또는 사용자가 "이제 X로 바뀌었어" 교정
4. 에이전트가 개선안 제안 → diff 카드 (v1→v2, 빨강/초록)       [skill_patch_proposed]
5. 사용자 승인 → version 2, .history/v1.md 보관               [skill_patched]
6. MySkills: v2 배지 + patch 카운터 1 + .history(롤백 가능)
```

이 시퀀스가 "쓸수록 똑똑해짐"을 **눈으로** 증명한다(정성적 증거). 같은 주장을 **숫자로** 증명하는 것은 [RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md)의 Learning Impact 패널이다.

---

## 6. Memory / User Modeling

> Honcho(외부 SaaS) 제외 → 로컬 builtin. RUNTIME_DESIGN 3장과 정렬.

| 종류 | 저장 | 작성 규칙 |
|------|------|-----------|
| facts | `state.db` | **선언적 사실만** ("Project uses pytest" ✓ / "Run tests with pytest -n 4" ✗ → 후자는 스킬로) |
| preferences | `state.db` | 답변 스타일(간결/상세/기술형), 선호 모델/도구 |
| 세션 히스토리 | `state.db` + FTS5 | 과거 대화 검색(session_search) |

- **prefetch 기본값**: 턴 시작에 **최근 facts N=20** + **쿼리 FTS 매칭 상위 K=10(메모리)** + **active 스킬 전부 주입**(스킬 예산 내, §7). N·K는 `config.yaml`로 노출. 스킬은 예산 초과 시에만 FTS 랭킹으로 선별 — 그 구간이 retrieval 취약점이며([RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) 3장), `skill_hit`으로 진단한다([RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md) 2장)
- ★ memory 기록 시 `memory_written` SSE 이벤트 → 개인화 가시화

---

## 7. Context 토큰 예산 & Prefix Caching

**총 컨텍스트 예산 = 270K 토큰**(모델 응답 20K 예약 포함). "토큰 사용량 < 정확도" 원칙 — 토큰을 더 쓰더라도 업무 처리 능력·정확도를 우선한다. 270K 전제는 대용량 컨텍스트(예: Claude 1M 컨텍스트 베타) 또는 그에 준하는 모델이며, on-prem(Ollama) 모델은 모델별 한도에 맞춰 같은 우선순위로 비례 축소한다.

| 구획 | 예산(목표) | 비고 |
|------|----------:|------|
| 시스템 프롬프트(base + 스킬 생성/patch 가이드) | ~8K | 고정 |
| 도구 정의(built-in tools, `execute_code` 포함) | ~12K | |
| MCP 도구(Connector 노출 tool) | ~15K | 연결 수 비례 (MVP: Gmail) |
| 규칙(rules) | ~5K | |
| **스킬(active 주입)** | **~40K (상한)** | 초과 시 `state=active` + `use` 상위 우선, 나머지는 description만 색인 |
| 메모리(facts N=20 + FTS K=10) | ~10K | §6 |
| 메시지(대화 히스토리) | ~160K (잔여) | 초과 시 오래된 턴부터 요약/절단 |
| **모델 응답(출력 예약)** | **20K** | |
| **합계** | **270K** | |

- 활성 스킬은 시스템 프롬프트에 전체 주입 + prefix caching 의존(Claude `cache_control`, Hermes 검증 #6). Ollama는 caching 미지원 → 컨텍스트 재사용으로 대체([RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) 6장).
- **스킬 예산(~40K) 초과 구간에서만 검색이 스킬을 선별**하므로 retrieval 취약성이 노출된다(MVP 대응·진단: [RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) 3장 + `skill_hit`).
- **MCP 도구는 연결된 Connector 것만 주입**한다(미연결 Connector tool은 스키마에서 제외 — [RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) 2.1). MCP 예산(~15K) 초과 시 progressive disclosure로 접는 것은 Phase 2([RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) 11장).
- **서브에이전트 컨텍스트 격리**: 서브에이전트 실행은 **새 대화를 여는 것과 동일하게** 독립된 컨텍스트(별도 270K 윈도)에서 작업시켜 부모 대화의 컨텍스트 오염을 막는다. 토큰 사용량은 늘지만 정확도를 우선하는 위 원칙과 일관된다.

> 학습 측정(eval)은 [RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md) 참조.
