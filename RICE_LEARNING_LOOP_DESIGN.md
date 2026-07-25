# RICE Learning Loop Design

> 작성일: 2026-06-18 (개정: 2026-06-26, **2026-07-02 대개정 — Hermes learning_loop 이식**, **2026-07-03 v3 — Self-Improvement 리모델**, **2026-07-08 — 소스 드리프트 동기화: 관련도 기반 스킬 선별·검증 게이트·빌트인 스킬·메모리 격리 반영**) · 상태: **확정 (결정 완료)**
> 범위: RICE의 핵심 차별점인 learning loop의 **자체 스펙** — SKILL.md 포맷, usage 텔레메트리, 생성 트리거, **개선(patch)**, curator 규칙+LLM 통합, memory/user modeling, 안전장치(시크릿 스크러빙)
> 관계: [HERMES_LEARNING_LOOP_REFERENCE.md](HERMES_LEARNING_LOOP_REFERENCE.md)는 *참조*(Hermes 코드 분석), 이 문서는 *RICE의 결정*이다. 런타임 통합은 [RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) 3·4장, 모듈 위치는 [RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md) `learning/`. **학습이 실제로 일어났음을 측정·증명하는 방법**은 [RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md).
>
> **2026-07-02 대개정 요지**: 이 문서의 초판(§4·§5)이 서술한 "시스템 프롬프트 가이드로 LLM이 자율 판단" 트리거는 실제로는 **정규식/키워드 매칭** 중심으로 구현돼 있었다(스킬 생성 본문도 `final_message` 일부를 잘라 쓰는 수준). 재사용 가치 판단과 patch 본문 저작을 LLM 리뷰 경로로 옮기기 위해 학습 루프를 재설계했다 — Hermes의 `background_review.py`/`curator.py`를 참조해 RICE의 HITL 불변식(스킬은 항상 사람 승인 후 저장, §5.2.1)에 맞게 이식. 아래 §4·§5·§8은 전면 재작성됐다.
>
> **2026-07-03 v3 (Self-Improvement 리모델) 요지**: v2까지의 루프는 제안을 만들 수 있었지만 **자기 결정 이력을 학습 입력으로 사용하지 못했다**. 제안→결정이 in-memory 파킹에만 존재해 재시작·타임아웃과 함께 소실됐고, 거부된 제안이 반복 재등장할 수 있었으며(§5.2.4의 "(향후) 반복 거부 시 하향"이 미구현), 리뷰는 프론트 호출 의존 + 단일 세션 시야였다. 또한 메인 에이전트에는 "SKILL.md로 저장하라"는 지시만 있고 실제 저장 제안 도구가 없었다. v3는 **Learning Ledger**(제안/결정 영속 감사 로그 → 반복 거부 쿨다운 + 프롬프트 회피 목록)와 **Suggestion Inbox**(파킹 밖에서 살아남는 영속 제안함 — 타임아웃 이동 + 발굴 수신)를 중심으로 리모델했다. 신규: §9 (Ledger·Inbox·Discovery), §4/§5.2.2의 T5(미드턴 에이전트 발의), §6의 정규식 추출 제거 + 메모리 삭제.

---

## 1. Learning Loop 개요

```
경험(대화)  →  축적(skill 생성 / memory 기록)  →  재사용(다음 턴 주입)  →  개선(patch · §5.2)
   ▲                                                                              │
   └──────────────────────────────────────────────────────────────────────────┘
```

- 학습 효과는 **SSE 이벤트**(`memory_written`, `skill_patch_proposed`, `skill_patched` 등, [RICE_CONTRACTS.md](RICE_CONTRACTS.md) 1.3)로 ChatScreen에 가시화된다 — "쓸수록 똑똑해짐"을 *증명*하는 것이 데모 목표.
- 저장소: `~/.rice/skills/*.md` + `.usage.json` 사이드카, `state.db`(memories, FTS5).
- **두 개의 SSE 스트림.** 메인 턴(`POST /sessions/{id}/messages`)은 즉시·저비용 처리만 수행한다: 주입된 스킬의 use 카운터를 갱신하고, 대화 중 `propose_skill`로 스테이징된 T5 제안을 같은 턴의 `skill_patch_proposed` 카드로 서피싱한다. 메모리 작성과 사후 스킬 생성/개선처럼 판단이 필요한 작업은 턴이 끝난 뒤 **별도 스트림** `POST /sessions/{id}/learning-review`에서 처리한다(§4·§5.2·§6). LLM 호출이 메인 턴의 `done`을 지연시키지 않게 하기 위한 의도적 분리다.

> **용어 — "개선"과 "전이"는 다르다.** 루프의 "개선" 화살표는 스킬 **본문 내용을 고치고 version을 올리는 patch**(§5.2, 신규 생성도 이 화살표에 통합됨 — §4)를 뜻한다. 이는 스킬 **상태(state: active/stale/archived)** 를 바꾸는 **전이(transition, §5.1)** 와 별개 관심사다. 전이는 freshness·생명주기 유지(curator), patch는 품질·정확성 개선이다. 두 개를 한 화살표로 뭉뚱그리지 않는다.

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
source: session:abc123 | plugin:wxo-expert | user | curator:consolidate | builtin  # 출처
state: active                         # active | stale | archived
absorbed_into: null                   # curator 통합으로 archived됐으면 흡수한 umbrella 이름 (§5.1)
---

# 본문: 절차적 지식 (메모리에 넣지 않는 "방법")
1. ...
```

- ✅ frontmatter 필수: `name`, `version`, `description`, `state`. 선택: `tags`, `source`, `updated_at`, `absorbed_into`
- **version**은 단조 증가 정수. patch(개선) 시 +1, 신규 생성은 1부터 시작(§4). 이전 버전은 `~/.rice/skills/<name>/.history/v<N>.md`에 **보관**한다(롤백·diff 가능, §5.2.4 — 신규 생성은 보관할 이전 버전이 없어 history를 만들지 않는다).
- ★ `source`로 학습 생성(`session:<id>`)/​plugin 제공(`plugin:<id>`)/사용자 직접 작성(`user`)/curator 통합 산출(`curator:consolidate`)/**패키지 동봉(`builtin`, 2026-07-05 신규 — 아래)**을 구분 → MySkillsScreen에서 출처 배지 표시. `plugin:`으로 시작하는 스킬은 curator 통합(§5.1)·review 엔진(§4·§5.2) 양쪽에서 patch 대상 후보로 보지 않는다(plugin이 소유한 콘텐츠 — 학습 루프 밖).
- **빌트인 스킬(`source: builtin`, 2026-07-05 신규)**: 앱 패키지에 동봉된 스킬(`learning/skill/builtin_skills/*.md`, 현재 `business-planning.md` — 스킬 개념을 처음부터 보여주는 교보재 목적). 원본은 사용자 디스크에 복사되지 않는다 — 상태(활성/비활성)만 `~/.rice/skills/.builtin-states.json`에 영속화. **예약 네임스페이스**: 같은 이름의 사용자 생성·patch·delete·rollback은 전부 `LearningService._ensure_not_builtin()`에서 거부(422). 상태 전이도 `active`/`archived`(활성/비활성) 두 가지만 허용 — `stale` 없음(curator가 애초에 안 봄, §5.1). Curator·통합(consolidate)·patch 제안 어디에서도 후보로 보지 않는다는 점은 `plugin:` 출처와 같다.
- `absorbed_into`(2026-07-02 신규, Phase B): curator 통합으로 archived된 스킬이 어느 umbrella에 흡수됐는지 기록(순수 정리로 archived됐으면 `null`). 기존 파서가 미지 frontmatter 키를 관대하게 처리해서 순수 추가 — 마이그레이션 불필요.

---

## 3. Usage 텔레메트리 (.usage.json 사이드카)

각 스킬 옆에 `<name>.usage.json`. Hermes의 use/view/patch 패턴 차용.

```json
{
  "skill": "deploy-k8s-service",
  "use": 12,          // lifetime 관여(engagement) 턴 수 — 아래 정직화 참고
  "version_use": 4,   // 현재 version에서의 관여 턴 수 — friction의 같은 기간 분모
  "view": 3,          // 모델이 skill_view 로 온디맨드 로딩한 횟수 (UI 열람은 세지 않음)
  "injected": 30,     // 전문 자동 주입(노출) 횟수 — use 와 분리 (2026-07-11 신규)
  "patch": 2,         // 개선(본문 수정)된 횟수 → version과 동기
  "friction": 1,      // 현재 version 관여 턴에서 교정/실패가 함께 감지된 횟수
  "routing_miss": 4,  // 라우터가 못 맞췄는데 모델이 수동 로딩한 횟수 = 발견성 결함 (2026-07-11 신규)
  "tools": {"kubectl_apply": 5, "search_files": 2},  // 관측된 도구 호출 빈도(상한 40종, 2026-07-06 신규)
  "last_used_at": "2026-06-18T09:00:00Z",
  "created_at": "2026-06-10T00:00:00Z"
}
```

- **텔레메트리 정직화(2026-07-11)**: 이전엔 "주입 = use+1"이었고 `view`는 UI 상세
  열람까지 세었다. 그 결과 라우터 오탐 한 번이 `last_used_at`을 갱신해 curator의
  stale/archive 판단과 리뷰 카탈로그 정렬(use 상위)이 전부 오염됐고, MySkills 패널
  열람이 view를 부풀려(실측 view=152 vs use=26) 온디맨드 신호가 무의미해졌다. 이제:
  - `use` = **관여 근거**가 있는 턴에만 가산: ① 모델이 `skill_view`로 직접 로딩,
    ② 사용자가 스킬 이름을 명시적으로 언급, ③ 주입 본문이 호출을 지시한 도구
    (verifier의 `tool_call_refs`와 동일 규칙)가 이 턴에 실제 호출됨. `last_used_at`·
    observed `tools` ledger도 관여 턴에만 갱신된다.
  - `view` = 모델의 `skill_view` 경로 전용. `GET /skills/{id}`(UI)는 어떤 카운터도
    올리지 않는다.
  - `injected` = 전문 자동 주입(노출) 집계 — "주입은 됐지만 안 쓰인" 스킬(오탐)을
    `injected≫use` 패턴으로 식별할 수 있다.
  - `version_use`/`friction` = 현재 version 안에서 같은 기간을 갖는 관여/마찰
    카운터. 본문·번들 리소스 수정 또는 rollback으로 version이 오르면 둘 다 0부터
    다시 시작하고, `use`는 lifetime 누적을 유지한다.
  - `friction`/`routing_miss` = 리뷰 LLM에 실리는 실측 힌트(§5.2.2) — 각각 "절차를
    의심하라", "발견성(설명/태그)을 고쳐라"의 근거.
  - **발견성은 수리(routing_miss)만이 아니라 생성 시점에도 배선된다(2026-07-25)**:
    이전에는 `propose_skill_create`의 tags 설명이 "0-5 short tags."뿐이라 새 스킬이
    **영어 태그만 달고 태어났고**, routing_miss가 쌓여 리뷰가 돌 때까지 몇 번은 안
    걸렸다. 이제 리뷰(T1)·발굴(T4) 두 생성 경로가 `prompts/review.py::
    TAGS_PARAM_DESCRIPTION` 하나를 공유해 처음부터 한/영 트리거를 요구한다(두 경로가
    따로 표류하던 것이 결함의 원인이라 상수를 공유한다). 빌트인 스킬도 한국어 트리거
    태그를 싣는다 — 사용자가 못 고치는 스킬이라 학습 루프가 사후 수리할 수 없다.
- 카운터 증가 시점: `use`/`injected`/`friction`=턴 종료 observe, `view`/`routing_miss`=`skill_view`, `patch`=본문 수정(승인) 시 (§5.2.4)
- 상태 전이·curator 입력으로 사용 (§5.1). `use`·`patch`는 학습 측정 지표로도 사용된다([RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md) 2장).
- **설명 사이드카(별개 파일, 2026-07-06 신규)**: `usage.json`이 텔레메트리라면, `learning/skill/analysis.py`+`analyzer.py`가 만드는 `SkillAnalysisData`는 "이 스킬이 뭘 하는지" 사람이 읽을 설명이다(휴리스틱+LLM 1회, `GET /skills/{id}`가 lazy-load, `POST /skills/{id}/analysis/refresh`로 재생성). **UI 설명 전용 — 권한/실행 판단에는 절대 쓰이지 않는다.**

---

## 4. 스킬 생성 트리거

**생성은 patch와 동일한 제안→승인 파이프라인으로 통일된다(2026-07-02).** 별도의
`skill_created` 알림 이벤트는 없다 — 신규 스킬 제안은 "본문 없음(v0) → 새 본문(v1)"
patch로 표현되고(`from_version=0`), `skill_patch_proposed`가 diff 카드로 보여준다
(100%-add diff, §5.2.3). 사용자가 승인해야만 실제로 `~/.rice/skills/`에 쓰인다 —
초판이 "HITL 후 저장"이라 적어놓고 실제 구현은 즉시 저장+사후 통지였던 불일치를
이번 개정에서 닫았다.

**판단 주체는 LLM이다(§5.2와 동일 엔진).** 초판의 "시스템 프롬프트 가이드로 LLM이
자율 판단"은 실제로는 정규식 키워드 매칭이었고(스킬 이름은 `skill: <name>` 패턴
추출 또는 세션 ID fallback, 본문은 `final_message`를 그냥 500자 잘라 붙임) —
재사용 가치를 실제로 판단할 능력이 없었다. 이제 `learning/review/engine.py`의
`ReviewEngine`이 대화 전체(+ 기존 스킬 카탈로그 + 이미 아는 facts)를 보고 LLM
1회 호출로 판단한다: 새 클래스급(class-level) 절차인지, 기존 스킬을 patch해야
하는지(§5.2.2의 우선순위), 아니면 저장할 게 없는지.

**언제 실행되는가.** 메인 턴 안에서 즉시 실행되지 않는다 — LLM 판단은 느릴 수
있어 턴의 `done`을 지연시키면 안 된다(§1). 대신:
1. 메인 턴이 끝나면(`done`) 프론트가 **별도 스트림** `POST /sessions/{id}/learning-review`를
   논블로킹으로 호출한다(제목 생성과 같은 트리거 지점, 같은 이유).
2. 서버는 먼저 값싼 휴리스틱 게이트(`learning/review/gate.py::ReviewGate`)로
   "LLM을 부를 가치가 있는 턴인가"만 판단한다(내용 판단은 안 함) — `tool_calls_made`가
   임계값 이상이거나, 교정/실패/스킬화 의도 신호(§5.2.2와 동일 신호셋)가 있거나,
   세션당 일정 턴 수가 지나면(Hermes의 nudge-interval과 같은 목적의 폴백) 통과.
3. 게이트를 통과하면 `ReviewEngine`이 LLM 1회 호출로 제안을 산출하고, 오케스트레이터의
   `agent/effect_surfacing.py`(메인 턴과 공유하는 동일 로직)가 그 결과를 `skill_patch_proposed`
   (+ HITL) / `memory_written`으로 스트리밍한다.

**이름 규약**: kebab-case, **클래스 레벨**이어야 한다("k8s-service-deploy" ✓,
오늘 대화 하나에만 맞는 "fix-pr-482"·"debug-today" 류는 ✗ — 이미 존재하는
스킬이 커버하면 그걸 patch하고, 없을 때만 새로 만든다, §5.2.2 우선순위 참고).

---

## 5. Skill Lifecycle (전이) & 개선 (Patch)

이 장은 두 개의 별개 관심사를 다룬다. **5.1 = 상태 전이**(active/stale/archived, curator), **5.2 = 본문 개선**(patch, version++). §1 "용어" 참조.

### 5.1 상태 전이 (Lifecycle & Curator)

상태: `active → stale → archived` (RUNTIME_DESIGN 4장과 동일).

**전이 규칙(curator, `learning/curator/`)**:
- 자동(규칙): freshness 경과 기준 — ★ 데모 설정 stale 3일 / archive 7일
  - freshness = `max(last_used_at, updated_at, created_at)`. 실제 사용·본문 편집·생성·
    사용자의 명시적 재활성화가 시계를 리셋하며, curator 자체의 상태 전이는 리셋하지 않는다.
  - `active`와 `stale`은 모두 검색·인덱스·관련도 기반 전문 주입 대상이다. `stale`은
    비활성이 아니라 재확인 경고이며, 완주한 턴에서 실제 관여가 확인되면 자동으로
    `active`에 복귀한다. 단순 노출(injected)은 복귀나 `last_used_at` 갱신 근거가 아니다.
  - MySkills에서 사용자가 stale→active를 선택하면 freshness만 리셋한다. 실제 사용이
    아니므로 `use`와 `last_used_at`은 꾸미지 않는다.
  - `archived`와 수동 `inactive`(`enabled=false`)는 검색·주입 대상이 아니며, inactive는
    curator가 자동 전이하거나 다시 켜지 않는다.
  - ⚠️ 일(day) 단위는 짧은 데모에서 자동 전이가 **관찰되지 않음** → 시연은 수동 트리거로 한다
- 수동: ChatScreen/MySkills의 "스킬 정리" 버튼 → `POST /skills/curate` 즉시 실행 (데모 경로)
- 사용량 기반 전이는 **향후 고도화로 연기** — 지금은 시간 규칙(자동) + 수동 "스킬 정리"만.

**LLM 리뷰 — 통합(consolidation), 2026-07-02 구현 완료.** 초판은 이걸 "향후
고도화로 연기"라 적어뒀는데, 지금이 그 향후다. Hermes의 `curator.py`
`CURATOR_REVIEW_PROMPT`를 이식한 `learning/curator/consolidate.py::ConsolidationEngine`이
담당한다:

- **트리거**: 수동뿐(비용·가시성 — Hermes와 동일 판단). `POST /skills/curate?mode=consolidate`
  (기본은 `mode=rules`, 즉 지금까지의 시간 규칙 전이만 — 하위호환). 이 모드는 규칙
  전이를 **항상 먼저** 실행한 뒤(Hermes의 2단계 모델과 동일) LLM 통합 분석을 더한다.
- **후보**: `source`가 `plugin:`로 시작하지 않는 active+stale 스킬 전체(본문 포함
  — 중복 판단엔 설명만으론 부족). 후보 총 글자수가 예산(`curator_candidate_budget_chars`,
  기본 300K자)을 넘으면 use 하위 스킬부터 후보에서 제외.
- **판단 기준**(Hermes 원문에서 그대로 이식, 가장 값진 부분): "사람 관리자라면 이걸
  N개의 별도 스킬로 쓸까, N개 섹션이 있는 스킬 하나로 쓸까"가 병합 기준. `use=0`은
  병합/정리 어느 쪽 근거로도 쓰지 않는다(증거 없음이지 낮은 가치의 증거가 아님).
  삭제 없음 — 최대 파괴적 행위는 archive.
- **산출물**: umbrella 스킬 하나의 `PatchProposal`(신규 생성 또는 기존 스킬 patch,
  `trigger="T3"`) + 흡수되는 각 형제 스킬의 `ArchiveProposal`(`absorbed_into=<umbrella>`).
  RICE는 support-file(`references/`·`templates/`·`scripts/`) 개념이 없어(SKILL.md가
  단일 파일 포맷) Hermes의 3번째 통합 방법(demote-to-reference)은 포팅하지 않았다.
- **승인**: 다른 제안들과 달리 `IApprovalGate` 파킹이 없다 — `/skills/curate`는
  스트림이 아닌 일반 REST 호출(사용자가 명시적으로 누른 버튼)이라 파킹할 대상(정지된
  스트림)이 애초에 없다. 제안은 JSON 응답에 그대로 실려 프론트가 로컬에 들고
  있다가, 병합 제안은 `POST /skills/{id}/consolidate-decision`으로, 정리(prune)
  제안은 `POST /skills/{id}/archive-decision`으로 개별 승인/거부한다(§5.2.5).

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

**초판은 "시스템 프롬프트 가이드로 LLM이 자율 판단"이라 적었지만, 실제 구현은
정규식 키워드 매칭이었다**(`signals.py`의 교정/실패 신호 문자열 목록 — "그게
아니라", "안 돼" 등). 규칙 기반이 실패하는 지점은 뻔했다: 실제로 어떤 본문을
써야 하는지는 판단할 수 없다(그래서 v1은 기존 본문 뒤에 "## 보완 사항 (검토
필요)"만 덧붙였다). 지금은 **§4와 같은 `ReviewEngine`**이 판단한다 — 규칙
기반 신호 탐지 함수(`has_correction_signal`/`has_failure_signal` 등)는
버려지지 않고, "제안을 직접 만드는" 역할에서 "LLM을 부를 가치가 있는가"를
판단하는 게이트(§4의 `ReviewGate`) 역할로 재배치됐다.

- **T1a/T1b/T1c — 턴 중 교정/실패/보완 (주 트리거, 데모 핵심).** 이 세 값의
  의미는 초판과 같다 — (a) 사용자가 절차를 명시적으로 교정, (b) 절차의 tool
  call이 실패해 대안으로 성공, (c) 에이전트가 실행 중 누락 단계/낡은 값을
  스스로 발견. **다른 점: 이제 LLM 자신이 셋 중 무엇에 해당하는지 골라
  `trigger_reason` 파라미터로 self-classify한다**(`propose_skill_patch` 툴
  스키마). 정규식으로는 (c)를 애초에 판단할 수 없어 T1c는 한 번도 실사용되지
  않았는데, 이제 실제로 나올 수 있다.
- **T2 — 사용자 수동 편집 (MySkills).** 초판과 동일 — MySkillsScreen에서 본문
  직접 편집 → 저장. 가장 통제적. 제안 단계 없이 바로 적용(`PUT /skills/{id}`,
  SSE는 없음 — 애초에 스트림 밖의 일반 REST 편집).
- **T3 — curator 통합 (수동 "AI 통합 제안").** §5.1 참고. `ConsolidationEngine`이
  중복/유사 스킬 묶음을 하나의 umbrella로 합치는 patch를 제안한다.
- **T5 — 미드턴 에이전트 발의 (2026-07-03 신규, Self-Improvement v3).** 메인
  에이전트가 대화 **도중** `propose_skill` 빌트인 도구로 직접 발의한다("이걸
  스킬로 승격할까요?"). v2까지 SKILL_GUIDANCE는 "SKILL.md로 저장하라"고 지시만
  하고 도구가 없었다(실행 경로가 없는 지시였기 때문에 실제 저장으로 이어지지 않았다).
  도구는 파일에 닿지 않는다: `LearningService.stage_proposal()`에 세션별로
  스테이징하고, `observe()`가 턴 종료 때 드레인해 **같은 턴의 스트림**에서 기존
  HITL diff 카드로 서피싱한다(`skill_patch_proposed`, trigger="T5" — 생성/개선
  모두). 스팸 방어: 턴당 3건 상한, plugin 소유 스킬 거부, 중단(abort)된 턴의
  스테이징은 폐기. **반복 거부 쿨다운(§9)을 적용하지 않는 유일한 경로** —
  사용자가 대화에 현전해 카드를 바로 거부할 수 있고, 명시적 "스킬로 저장해줘"
  요청일 수 있기 때문(쿨다운은 무인 재제안을 막는 장치지 명시적 의도를 막는
  장치가 아니다).

- **실측 텔레메트리 힌트(2026-07-11 신규, v4 기간 정합).** 리뷰 프롬프트의
  patch 가능 스킬 헤딩에 `lifetime_use/current_version_use/
  current_version_friction/routing_miss`(§3) 한 줄이 붙는다(신호가 없으면 생략).
  시스템 프롬프트가 해석 규칙을 준다 — friction 높음 = "현재 version 관여 턴마다
  교정/실패가 겹쳤다, 절차를 의심하라"; routing_miss 높음 = "본문이 아니라
  발견성이 문제다, `new_description`/`new_tags`로 사용자가 실제로 쓰는 단어를
  추가하라". 리뷰가 대화 하나만 보던 맹점을 누적 실측이 보완한다.
- **빌트인 카탈로그 인지(2026-07-11 신규).** 리뷰/발굴 엔진의 카탈로그에 빌트인
  스킬이 "(builtin)" 라벨의 compact(패치 불가) 항목으로 실린다 — 이전엔 안 보여서
  빌트인이 이미 덮는 절차를 신규 스킬로 재발명하거나, 빌트인 예약 이름으로 create를
  제안해 **승인 시점에야 에러가 나는 막다른 카드**가 가능했다. 이제 예약 이름
  제안은 엔진에서 조기 드랍된다(fail-closed).

**신호 소스가 둘로 나뉜다는 점에 주의**: (1) 메인 턴 안에서 정규식이 감지하는
신호는 `ReviewGate.should_review()`의 **게이트 판단**에만 쓰인다(LLM을 부를지
결정) — 신호 자체가 제안이 되진 않는다. (2) 실제 trigger 분류(T1a/b/c)는
게이트를 통과한 뒤 LLM이 대화 전체를 읽고 스스로 내린다. 이 분리 덕에 "정규식이
놓친 미묘한 교정"도 LLM이 잡아낼 여지가 생긴다(게이트가 `tool_calls_made`나
누적 턴 수만으로도 통과할 수 있으므로).

#### 5.2.3 제안 산출물 & HITL 게이트

제안(T1a/b/c/T3, 그리고 §4의 신규 생성 제안 T4)은 다음을 산출한다:
- `proposed_body` — 개선된(또는 신규) **전체 본문**
- `changelog` — 한 줄 요약 (예: "pytest 병렬도 4→8, 사용자 교정 반영") — LLM이
  대화와 같은 언어로 작성한다(제목 생성과 같은 원칙, locale 강제 없음)
- `diff` — 현재 version 대비 (UI 렌더용). 신규 생성(T4)은 `build_diff("", body)` —
  100%-add diff로, "새 스킬 제안" 카드가 된다(별도 카드 타입 불필요, `from_version===0`이
  판별자)
- `trigger` — T1a/T1b/T1c/T3/T4 (감사용)
- `description`/`tags` — T4(신규 생성)에서만 의미 있음(생성 초기값)
- `new_description`/`new_tags` — **기존 스킬 patch의 발견성 메타데이터 변경**(2026-07-11
  신규, 선택). `routing_miss`(§3)가 쌓인 스킬은 본문이 아니라 색인 신호(설명/태그)가
  문제다 — 리뷰 LLM(`propose_skill_patch`의 동명 인자)과 미드턴 `propose_skill`
  (기존 스킬에 description/tags를 주면 변경 제안으로 해석)이 이 채널로 고친다.
  None이면 변경 없음, 현재 값과 같으면 빌더가 None으로 접는다. **메타데이터-only
  patch도 성립한다**(본문 diff가 비어도). diff 렌더러는 본문 전용이므로 변경 내용은
  `warnings`에 "설명 변경 포함: …" / "태그 변경 포함: …"으로 명시돼 카드에 보인다.
  승인 시 `apply_patch`가 본문과 함께 적용한다 — 발견성 개선이 다음 턴의 라우팅
  (§7 관련도 매칭)에 즉시 반영되는 **자기개선 되먹임 루프**가 이걸로 닫힌다.
- `warnings` — §8 가드가 시크릿을 마스킹했으면 그 라벨 목록("AWS access key 자동 마스킹됨" 등),
  없으면 빈 배열

게이트:
1. **`skill_patch_proposed` SSE 이벤트** (`approval_id` 포함 제어 이벤트, `tool_approval_required`과 **동일 parking·timeout·재연결 메커니즘** — 정본 [RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) 5.1, 계약 [RICE_CONTRACTS.md](RICE_CONTRACTS.md) 1.2·1.3) → 앱이 **diff 카드**(+ warnings 있으면 경고 배너) 표시. 메인 턴 스트림과 `/learning-review`(§4) 스트림 양쪽에서 같은 모양으로 나온다 — 둘 다 `agent/effect_surfacing.py`의 동일 함수를 거치기 때문.
2. 사용자 선택: **승인 / 거부 / 편집 후 승인**
3. 응답: **`POST /skills/{id}/patch`** (body 예: `{ "approval_id": "...", "decision": "approve|reject", "edited_body": "..." }`)

#### 5.2.4 적용 (승인 시)

```
1. (기존 스킬 patch만) 현재 본문 → ~/.rice/skills/<name>/.history/v<N>.md   (보관)
   (신규 생성=T4는 이 단계를 건너뛴다 — 보관할 이전 버전이 없다)
2. 새 본문 기록 — edited_body(사용자가 카드에서 직접 고쳤으면) > proposed_body
   (제안 자체의 본문) > 기존 본문(신규 생성이 아닌데 편집도 제안 본문도 없는
   퇴화 케이스) 순으로 우선한다. frontmatter version += 1(신규면 1로 시작), updated_at 갱신
3. §8 가드(크기 상한 + 시크릿 스크러빙) 통과 — 실패 시 적용 자체가 거부된다(ValidationError)
4. <name>.usage.json 의 patch += 1 (신규 생성은 예외 — "생성"은 "패치 횟수"가 아니므로 0-초기화만)
5. skill_patched SSE 이벤트 발행 → ChatScreen "스킬 개선됨 v{N+1}"(또는 "신규 스킬 v1") + MySkills 갱신
6. MySkillsScreen: 버전 배지, patch 카운터, .history에 이전 버전(기존 patch만 — 롤백 가능)
```

거부 시: 변경 없음 + **ledger에 `rejected` 기록**(§9 — 반복 거부 쿨다운의 원천).
**무응답(timeout, 기본 5분)은 2026-07-03부터 폐기가 아니라 제안함(suggestion inbox, §9)으로 이동**한다 — 무응답은 거부 의사표시가 아니라 자리 비움일 수 있다. 단 이 서버 이동은 5분 지연이 있어, 프론트는 파킹 즉시 제안함에 사본을 미러해 세션을 옮겨도 놓치지 않게 한다(안전망 — §9.2, 2026-07-14). 적용은 여전히 하지 않으므로(본문 불변) safe-default는 유지된다([RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) 5.1). 턴 중단(abort)로 인한 cancelled는 그대로 폐기. ~~(향후) 동일 스킬에 대한 반복 거부 시 제안 빈도를 하향한다~~ → **구현 완료(2026-07-03)**: 쿨다운 창(`review_rejection_cooldown_hours`, 기본 24h) 안에서 `review_max_rejections`(기본 2)회 이상 거부된 스킬은 리뷰/발굴이 제안을 만들지 않고(사후 억제), 최근 거부 목록이 리뷰 프롬프트에 실려 LLM이 애초에 재제안을 피한다(사전 회피) — §9.

#### 5.2.5 계약 변경 요약 (CONTRACTS 반영 필요)

이 메커니즘은 [RICE_CONTRACTS.md](RICE_CONTRACTS.md)의 계약을 **확장**한다:

| 종류 | 추가 | 비고 |
|------|------|------|
| SSE 이벤트 | ~~`skill_created`~~ | **2026-07-02 제거.** §4 참고 — 신규 생성이 patch(from_version=0)로 통일되며 별도 생성 이벤트가 사라졌다. 이후 2026-07-04에 `plan_updated`가 추가되어 현재 스트림 이벤트는 CONTRACTS §1.3의 9종이다. |
| SSE 제어 이벤트 | `skill_patch_proposed` (skill_id, changelog, diff, trigger, from_version, to_version, **proposed_body**, **warnings**) | `tool_approval_required`과 동일 폴링형 제어 이벤트. proposed_body/warnings는 2026-07-02 추가(견고성 — 프론트가 diff 재구성에 의존하던 것을 서버 원문으로 대체) |
| SSE 이벤트 | `skill_patched` (skill_id, version) | 적용 완료. version=1이면 신규 생성이었다는 뜻(프론트는 로컬 목록에 없으면 새로 추가해야 한다 — "없으면 그냥 무시" 버그 주의) |
| 엔드포인트 | `POST /skills/{id}/patch` | 제안 응답 (approve/reject/edited_body) |
| 엔드포인트 | **`POST /sessions/{id}/learning-review`** | **신규(§4).** SSE 스트림(메인 턴과 동일 이벤트 어휘). body: `{tool_calls_made}`. 메인 턴 `done` 직후 프론트가 논블로킹 호출 |
| 엔드포인트 | **`POST /sessions/{id}/learn`** | **신규(§5.3).** 명시적 "이 대화를 스킬로 저장". body: `{instruction?}` |
| 엔드포인트 | `POST /skills/curate` | **`mode` 쿼리 파라미터 추가**(`rules`\|`consolidate`, 기본 `rules` — 하위호환). consolidate는 §5.1 |
| 엔드포인트 | **`POST /skills/{id}/archive-decision`** | **신규(§5.1).** curator 정리(prune) 제안 승인/거부. body: `{approved, absorbed_into?}` |
| 엔드포인트 | **`POST /skills/{id}/consolidate-decision`** | **신규(§5.1).** curator 병합(umbrella) 제안 승인/거부 — `/patch`와 달리 approval_id 파킹 없음(§5.1 참고). body: `{approved, proposed_body?, description?, tags?}` |
| 엔드포인트 | `GET /skills/{id}/history` | 버전 히스토리 (diff·롤백 UI) |
| 엔드포인트 | `POST /skills/{id}/rollback` | 이전 버전 복구 |
| 엔드포인트 | **`GET /skills/suggestions`** | **신규(§9, 2026-07-03).** 영속 제안함 — 타임아웃 이동분 + 발굴 산출물 |
| 엔드포인트 | **`POST /skills/suggestions/{sid}/decision`** | **신규(§9).** 제안함 항목 승인/거부. body: `{approved, edited_body?}` |
| 엔드포인트 | **`POST /skills/discover`** | **신규(§9).** 크로스세션 발굴 수동 트리거 (LLM 1회, 결과는 제안함으로) |
| 엔드포인트 | **`GET /learning/events`** | **신규(§9).** 학습 감사 로그 (제안/결정/적용/메모리 이력) |
| 엔드포인트 | **`DELETE /memories/{id}`** | **신규(§6, 2026-07-03).** 메모리 사후 삭제(위생) |
| 빌트인 도구 | **`propose_skill`** | **신규(§5.2.2 T5).** 미드턴 스테이징 — 쓰기 권한 없음, HITL 카드는 동일 |

> `skill_patch_proposed`의 정지/재개·`approval_id`·timeout·재연결 복구(`GET /sessions/{id}/pending-approvals`)는 tool 승인과 **공유 메커니즘**이라 patch 전용 추가가 아니다 — 정본 [RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) 5.1. `/archive-decision`·`/consolidate-decision`은 **이 메커니즘을 쓰지 않는다** — 제안이 애초에 스트림 위에서 파킹된 적이 없기 때문(§5.1).

#### 5.2.6 데모 스크립트 (T1a 경로 — 가장 설득력)

```
1. 사용자가 반복 과제 수행(5+ tool calls) → 메인 턴 done → /learning-review가
   게이트 통과 → ReviewEngine이 스킬 v1 생성 제안 → 사용자 승인      [skill_patch_proposed(from_version=0) → skill_patched(v1)]
2. 다음 턴, 같은 과제 → 에이전트가 v1 주입해 사용                    [use++, "스킬 사용됨"]
3. 환경 변화로 한 단계 실패  /  또는 사용자가 "이제 X로 바뀌었어" 교정
4. done 후 /learning-review가 다시 게이트 통과 → LLM이 개선안 제안
   → diff 카드 (v1→v2, 빨강/초록, trigger는 LLM이 스스로 T1a/T1b/T1c 중 고름) [skill_patch_proposed]
5. 사용자 승인 → version 2, .history/v1.md 보관                     [skill_patched]
6. MySkills: v2 배지 + patch 카운터 1 + .history(롤백 가능)
```

이 시퀀스가 "쓸수록 똑똑해짐"을 **눈으로** 증명한다(정성적 증거). 같은 주장을 **숫자로** 증명하는 것은 [RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md)의 Learning Impact 패널이다.

### 5.3 명시적 학습 트리거 — "이 대화를 스킬로 저장" (2026-07-02 신규)

§4·§5.2가 자동 감지(게이트+LLM 판단)라면, 이건 사용자가 직접 "지금 이 대화를
스킬로 만들어줘"라고 요청하는 경로다(Hermes의 `/learn` 명령 이식, ChatScreen에
버튼으로 구현 — RICE는 슬래시커맨드로 스킬을 주입하지 않으므로 명령어가 아니라
버튼이 자연스럽다).

- **엔드포인트**: `POST /sessions/{id}/learn` (body: `{instruction?: string}` — 빈
  문자열이면 "방금 대화를 통째로 정리해줘"로 폴백)
- **게이트 없음**: §4의 `ReviewGate`를 거치지 않는다 — 명시적 요청 자체가 이미
  "할 가치가 있다"는 신호다. 항상 `ReviewEngine.learn()`을 실행한다.
- **엔진 재사용**: §4·§5.2와 **같은 `ReviewEngine`**, 더 지시적인 프롬프트
  (`LEARN_SYSTEM_PROMPT` — "아무것도 제안하지 않는 것"이 드문 경우여야 한다고
  명시)만 다르다. 결과는 동일하게 `skill_patch_proposed`(+HITL) 스트림으로 나온다.

---

## 6. Memory / User Modeling

> Honcho(외부 SaaS) 제외 → 로컬 builtin. RUNTIME_DESIGN 3장과 정렬.

| 종류 | 저장 | 작성 규칙 |
|------|------|-----------|
| facts | `state.db` | **선언적 사실만** ("Project uses pytest" ✓ / "Run tests with pytest -n 4" ✗ → 후자는 스킬로) |
| preferences | `state.db` | 답변 스타일(간결/상세/기술형), 선호 모델/도구 |
| 세션 히스토리 | `state.db` + FTS5 | 과거 대화 검색(session_search) |

- **prefetch 기본값(2026-07-07 정정 및 스킬 선별 방식 개편)**: 턴 시작에 **최근 facts**(`prefetch_recent_facts`, 기본 **8** — 기존 문서는 20으로 적었으나 실제 기본값은 8) + **쿼리 FTS 매칭 상위**(`prefetch_fts_top_k`, 기본 **5** — 기존 문서는 10) 메모리를 주입한다. **스킬은 더 이상 "예산 내 전체 주입"이 아니다** — 매 턴 관련도 스코어링(상세 §7 갱신)으로 상위만 자동 주입되고 나머지는 인덱스로만 실린다. `skill_hit`으로 매칭 실패를 진단하는 것은 여전히 유효([RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md) 2장).
- ★ memory 기록 시 `memory_written` SSE 이벤트 → 개인화 가시화
- **작성 주체 단일화 (2026-07-03, v3)**: `observe()`의 정규식 fact/preference
  추출(FACT_PREFIXES/PREF_PREFIXES 도입구 매칭)은 **제거됐다**. 리뷰 LLM의
  `propose_memory_write`와 이중 작성자였고, "항상"/"저는" 같은 느슨한 도입구는
  노이즈 메모리를 만들었다 — 메모리는 HITL 없이 매 턴 주입되는 가장 위생에
  민감한 경로다. 이제 기록 판단은 리뷰 LLM 하나뿐이고, `observe()`는 use++와
  T5 스테이징 드레인만 한다.
- **격리(quarantine, `review/engine.py` 소유, 이 문서 최초 반영)**: 외부 콘텐츠(connector/plugin/bridge 도구 결과)가 섞인 턴에서 나온 메모리는 `status: quarantined`로 저장되어 승인 전까지 prefetch·FTS 검색에 걸리지 않는다 — 프롬프트 인젝션이 메모리를 오염시켜 이후 턴에 조용히 재주입되는 경로를 막는 장치다(§8의 시크릿/위협 스캔과는 다른 방어선 — 이건 콘텐츠 자체가 아니라 **출처**로 판정한다). 판정 근거는 tool 메시지 `tool_source` metadata. 승인은 `POST /memories/{id}/approve`(CONTRACTS §6.7) → `active` 전환. 각 메모리의 `derived_from_external`/`evidence_message_ids` 필드가 판정 근거를 기록한다.
- **사후 삭제 (2026-07-03, v3)**: `DELETE /memories/{id}` + LearningPanel
  신호 행의 제거 버튼. 메모리는 승인 게이트가 없는 유일한 학습 경로라, 잘못
  들어간 항목이 "매 턴 영구 주입"되지 않으려면 사후 제거 수단이 필수다. 삭제는
  ledger에 `memory_deleted`로 남는다(§9).

---

## 7. Context 토큰 예산 & Prefix Caching

**총 컨텍스트 예산 = 270K 토큰**(모델 응답 20K 예약 포함). "토큰 사용량 < 정확도" 원칙 — 토큰을 더 쓰더라도 업무 처리 능력·정확도를 우선한다. 270K 전제는 대용량 컨텍스트(예: Claude 1M 컨텍스트 베타) 또는 그에 준하는 모델이며, on-prem(Ollama) 모델은 모델별 한도에 맞춰 같은 우선순위로 비례 축소한다.

| 구획 | 예산(목표) | 비고 |
|------|----------:|------|
| 시스템 프롬프트(base + 스킬 생성/patch 가이드) | ~8K | 고정 |
| 도구 정의(built-in tools, `execute_code` 포함) | ~12K | |
| MCP 도구(Connector 노출 tool) | ~15K | 연결 수 비례 (MVP: Gmail) |
| 규칙(rules) | ~5K | |
| **스킬(자동 주입 상위 + 인덱스)** | **작음 — 상위 1개 본문 + 짧은 인덱스** | 2026-07-11 v2: 점수 임계(3.0)에 **strong-hit 게이트**(name/tag 신호 ≥2 또는 명시적 언급)가 추가됐고, 개수 상한은 `skill_max_auto_inject`(기본 **1**)로 내려갔다 — 태그 한 개 스치는 오탐이 본문 ~1K 토큰을 끌고 들어오던 것을 차단. 한 번 주입된 스킬은 `skill_sticky_turns`(기본 2) 동안 재매칭 없이 유지된다(후속 지시가 라우터에 안 걸려도 태스크 중간에 절차가 증발하지 않게). 인덱스는 관련도순·상한 30행+오버플로 안내(`learning/skill/relevance.py`, `learning/service.py::prefetch`) |
| 메모리(facts 8 + FTS 상위 5, §6 실 기본값) | ~10K | §6 |
| 메시지(대화 히스토리) | ~160K (잔여) | 초과 시 오래된 턴부터 요약/절단 |
| **모델 응답(출력 예약)** | **20K** | |
| **합계** | **270K** | |

- **스킬 선별은 매 턴 관련도 매칭이 주경로다(2026-07-07 개편, 2026-07-11 정밀도 v2)**: `core/lexical.py`의 결정론적 사다리(exact→substring→char-bigram fuzzy→ko→en 의도어 확장)로 스코어링해, **명시적 언급이거나 (점수 ≥3.0 AND name/tag strong-hit ≥2)** 인 상위 `skill_max_auto_inject`(기본 1)개만 본문 그대로 자동 주입하고 나머지는 이름+한줄 설명의 인덱스(관련도순, 30행 상한)만 실린다. 주입된 스킬은 `skill_sticky_turns`(기본 2) 동안 점착 유지된다. 모델이 인덱스에서 필요를 느끼면 `skill_view` builtin으로 온디맨드 로드한다(같은 턴 반복 조회에는 "already loaded" note가 실려 소형 모델의 중복 로딩 루프를 끊는다). 라우터가 못 맞춘 수동 로딩은 `routing_miss`로 계측돼(§3) 리뷰 LLM의 발견성 개선(`new_description`/`new_tags` patch)으로 되먹임된다. prefix caching(Claude `cache_control`) 이점은 상위 스킬·인덱스가 매 턴 반복되는 한 유지된다. Ollama는 caching 미지원 → 컨텍스트 재사용으로 대체([RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) 6장).
- **매 턴이 매칭에 의존하므로 retrieval 정확도가 그만큼 더 중요해졌다** — 예산 초과 시에만 작동하던 안전장치가 아니라 상시 경로가 됐다는 것이 이전 서술과의 핵심 차이. `skill_hit`으로 매칭 실패를 계속 진단한다([RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md) 2장). 시멘틱(임베딩) 매칭은 2026-07-07 적대적 리뷰에서 최종 기각됐다(로컬 8GB 최소사양 트레이드오프).
- **MCP 도구는 연결된 Connector 것만 주입**한다(미연결 Connector tool은 스키마에서 제외 — [RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) 2.1). MCP 예산(~15K) 초과 시엔 bridge 3종 + 관련도 스포트라이트로 접는다(**구현 완료** — 기존 문서의 "Phase 2" 항목은 2026-07-01에 착수했고, 2026-07-07에 관련도 스포트라이트 방식으로 개편됐다. [RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) 2.1).
- **서브에이전트 컨텍스트 격리**: 서브에이전트 실행은 **새 대화를 여는 것과 동일하게** 독립된 컨텍스트(별도 270K 윈도)에서 작업시켜 부모 대화의 컨텍스트 오염을 막는다. 토큰 사용량은 늘지만 정확도를 우선하는 위 원칙과 일관된다.

> 학습 측정(eval)은 [RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md) 참조.

---

## 8. 안전장치 — 크기 제한 & 시크릿 스크러빙 (2026-07-02 신규)

LLM이 스킬/메모리 본문을 직접 저작하게 되면서(§4·§5.2·§5.3) 생긴 새 위협 모델
하나를 막는다: **"LLM이 대화에서 본 시크릿(API 키, 토큰)을 스킬/메모리 본문에
무심코 옮겨 적어, 그게 매 턴 시스템 프롬프트에 다시 주입되는 사고."** (**2026-07-03
갱신, 2026-07-08 문서 정정**: 이 절이 애초에 "포팅 안 한다"고 선을 그었던 Hermes의
더 넓은 위협 분류체계 — exfiltration/injection/persistence/network/obfuscation/
traversal — 는 실제로는 이후 `learning/skill/guard.py::scan_risks()`로 **포팅됐다**.
아래 "다루지 않은 것"을 정정한다.)

**두 가지 체크** (`learning/skill/guard.py`):
- **크기 상한**(`skill_max_body_chars`, 기본 20,000자) — 폭주한 LLM 응답에 대한 마지막 방어선. 넘으면 `ValidationError`로 거부(제안 자체가 버려짐 — §5.2.4).
- **시크릿 스크러빙** — AWS 키(`AKIA...`)·Anthropic/OpenAI형 API 키(`sk-...`)·GitHub/Slack/Google 토큰·Bearer 토큰·JWT·PEM 개인키 헤더 등 보수적인 패턴 목록을 `[REDACTED:<라벨>]`로 치환. 오탐(정상적인 "API 키를 설정하세요" 같은 설명문)이 적도록 실제 시크릿 형태만 매칭한다.

**적용 지점 — 두 곳, 서로 다른 이유로:**
- **스킬**: `LearningService.apply_patch`/`create_skill`/`update_skill` 세 곳
  (파일 쓰기 직전). `SkillStore` 자체는 순수 I/O 레이어로 남겨둔다(검증 로직을
  스토리지 계층에 섞지 않음). 이 덕분에 **사용자가 직접 작성**하는 `create_skill`/
  `update_skill` 경로도 같이 보호받는다 — 이전엔 이 두 경로에 크기/시크릿
  검증이 전혀 없었다(이번에 같이 닫힌 기존 gap).
- **메모리**: `MemoryStore.add()` 내부(저장소 경계 자체). 스킬과 다른 이유:
  메모리는 **HITL 승인이 없고**(§6), 승인 전 diff 검토 없이 저장 즉시 다음 턴
  주입 후보가 된다. 현재 메모리 작성 판단은 `ReviewEngine`의 `propose_memory_write`
  경로로 단일화됐지만, 저장소 경계에서 스크러빙을 적용하면 현재·향후 호출부가
  검증을 누락할 수 없다.

**제안 단계에서도 미리 적용**: `ReviewEngine`/`ConsolidationEngine`은 `PatchProposal`을
만들 때 이미 스크러빙을 거친 본문을 `proposed_body`에 담고, 발견한 라벨을
`warnings`에 실어 승인 카드에 경고로 보여준다(§5.2.3) — 그래서 사용자는 승인
시점에 "이 제안에서 시크릿이 자동으로 지워졌다"는 걸 알 수 있다. `apply_patch`가
쓰기 직전에 **다시** 스크러빙하는 것은 방어적 이중 체크(이미 클린한 본문에
대해선 아무 일도 안 함)다.

**실제로 포팅된 것(2026-07-03, 위 정정)**: `learning/skill/guard.py::scan_risks()`가 Hermes THREAT_PATTERNS에 준하는 탐지를 구현한다 — 프롬프트 인젝션(한/영)·시스템 프롬프트 override·hidden-instruction·자격증명 유출·파괴적 쉘(`rm -rf`/`mkfs`/`dd`/fork bomb)·원격 실행(`curl|sh`)·네트워크 리스너(`nc -l`)·역쉘(`/dev/tcp/`, `bash -i`)·SSH 키 영속성(`authorized_keys`)·zero-width 은닉 문자. 적용 경로는 두 갈래: 스킬 쪽은 `proposals.py::_build_warnings`(제안의 `warnings`에 실림, §5.2.3), 메모리 쪽은 `review/engine.py::_memory_effect`(메모리는 HITL이 없으므로 fail-closed로 즉시 차단 — §6의 격리(quarantine)와는 별개 방어선, 이건 콘텐츠 내용으로 판정하고 격리는 출처로 판정한다).

**여전히 다루지 않은 것(의도적, 범위 밖)**: `skill_preprocessing.py`의 inline shell 치환(스킬 로드 시점에 쉘 커맨드 실행)은 포팅하지 않는다 — prefetch 시점에 임의 커맨드가 실행되는 구조 자체가 RICE의 로컬-신뢰 모델에서도 과도한 위험이다.

**스킬 승격 검증(품질 게이트) — 별도 관심사, 2026-07-07 신규**: 위 두 체크(크기·시크릿)는 안전 문제를 막지만, "이 스킬 본문이 구조적으로 쓸만한가"는 다른 질문이다. `learning/skill/verifier.py::verify_candidate()`가 이를 결정론적 lint 사다리로 검사한다 — **LLM 재채점 없음**(같은 로컬 LLM이 자기가 쓴 걸 재검토해봐야 지연만 늘고 신호는 안 는다는 판단, `core/lexical.py`의 "시멘틱 매칭 기각"과 같은 결의 근거).
- **fail(반려, 객관적 무결성만)**: 빈 본문(8자 미만), 코드펜스 미폐합, 본문이 SKILL.md frontmatter로 시작.
- **warn(경고, 판단 항목 — 반려는 아님)**: 본문 60자 미만, 번호/불릿/헤딩/펜스가 하나도 없음(no-steps), 문장이 끊긴 채 끝남, TODO/TBD/placeholder 토큰, 백틱으로 언급한 도구가 실제 카탈로그에 없음(unknown-tool), 설명 누락(생성만), 기존 스킬과 근접중복(Jaccard≥0.6, 생성만), 패치가 원본을 30% 미만으로 줄임(원본 400자 이상일 때만).
- **3중 관문**: ① `stage_proposal`(T5 미드턴) — fail 시 스테이징 자체를 거부하고 사유를 도구 결과로 모델에 돌려줘 같은 턴 재제안을 유도. ② `_screen_proposals` — review/learn/sweep/discovery가 만든 LLM 제안 전부의 단일 관문, fail은 드롭 + ledger `verifier_rejected` 기록(§9.1). ③ `apply_patch`/`create_skill`/`update_skill` 직전 — 사용자가 카드에서 직접 고친 `edited_body`까지 포함해 최종 관문(우회 불가).
- warn은 새 채널을 만들지 않는다 — 기존 `PatchProposal.warnings`(§5.2.3, 시크릿 스크러빙 경고와 같은 필드)에 함께 실린다.
- 도구 존재 확인은 `server/composition_root.py`의 `_known_tool_names()`(builtin+connector+plugin+bridge+가상 빌트인 전체를 late binding으로 조회)가 근거다.
- rollback·마켓 설치 경로는 의도적으로 이 게이트를 적용하지 않는다(이미 신뢰된 콘텐츠거나 되돌리는 동작이라 새 검증이 필요 없다는 판단).

---

## 9. Learning Ledger & Suggestion Inbox — 루프의 메타학습 (2026-07-03 신규, v3)

v2까지의 루프는 "무엇을 배울까"는 잘 판단했지만 **"내 제안이 어떻게 받아들여졌나"를
기억하지 못했다.** 제안→결정은 in-memory 파킹(`IApprovalGate`)에만 존재해
재시작·타임아웃과 함께 증발했고, 그 결과: 거부된 제안이 다음 리뷰에서 그대로
재등장할 수 있었고, 타임아웃된 제안(= 자리 비움)은 소멸했으며, 시스템의 학습
행동 전체가 감사 불가능했다. v3는 이 구멍을 state.db의 두 영속 테이블
(`learning_events`, `skill_suggestions`)로 닫는다. 구현:
`learning/ledger.py::LearningLedger`.

### 9.1 learning_events — append-only 감사 로그

모든 제안/결정/적용이 기록된다: `proposed`(리뷰·learn·T5·발굴), `approved`/
`rejected`/`timeout`(HITL 결정 — `effect_surfacing`이 `resolve_proposal`로 위임),
`memory_written`/`memory_deleted`, `discovery_run`, `suggestion_added`.
제안함 결정에는 별도 `suggestion_decided` kind를 만들지 않는다. `LearningLedger.decide_suggestion()`은 inbox 행의 상태만 갱신하고, `LearningService.decide_suggestion()`이 일반 `approved`/`rejected` 이벤트를 기록하면서 `detail.suggestion_id`로 출처를 연결한다. 이 외 실제로 쓰이는 kind(**2026-07-08 문서 반영, 기존 문서 누락**):
**`memory_quarantined`/`memory_blocked`/`memory_approved`**(§6 격리),
**`conflict`**(낙관적 동시성 충돌), **`context_rendered`**(AGENT_EXECUTION §4의
렌더 감사), **`analysis_refreshed`**(스킬 설명 사이드카 갱신),
**`verifier_rejected`**(검증 게이트 반려, §8). `GET /learning/events`로 노출 —
"루프가 실제로 무엇을 배우고 무엇이 기각됐는가"의 단일 원천(편집가능·감사가능·
롤백 원칙의 감사 축). 기록은 전부 best-effort — 감사 실패가 턴을 깨지 않는다
(usage 카운터와 동일 원칙).

**메타학습 두 겹** (같은 데이터, 다른 방어선):
1. **사전(프롬프트) 회피** — 최근 거부 목록(`recent_rejections`)이 리뷰·발굴
   프롬프트에 "Recently REJECTED proposals (do NOT re-propose)" 블록으로 실린다.
2. **사후(엔진) 억제** — 그래도 나온 제안 중 쿨다운 창(`review_rejection_cooldown_hours`,
   기본 24h) 안에서 `review_max_rejections`(기본 2)회 이상 거부된 skill_id의
   제안은 엔진이 버린다(fail-closed). T5(미드턴)만 예외 — §5.2.2 참고.

### 9.2 skill_suggestions — 영속 제안함(inbox)

파킹(스트림) 밖에서 살아남는 제안의 수신처. **서버가 영속하는** 유입원 둘:
- **타임아웃 이동** (§5.2.4) — 무응답 제안은 폐기 대신 여기로.
- **발굴(discovery)** (§9.3) — 애초에 파킹할 스트림이 없는 백그라운드 산출물.

payload는 SSE `skill_patch_proposed`와 **같은 어휘**(approval_id 제외) — 프론트가
파킹 카드와 제안함 카드를 동일 렌더러로 그린다. 결정은
`POST /skills/suggestions/{sid}/decision`(승인 시 create/patch → `apply_patch`,
archive → `decide_archive`로 수렴, 거부는 ledger에 기록되어 쿨다운 입력으로 사용된다).
위생: 같은 skill_id+kind의 pending 중복은 멱등 스킵, pending 총량 상한
(`suggestion_pending_cap`, 기본 30). UI: LearningPanel "제안함" 섹션(실배선 —
이전의 목업 proposals와 별개).

**프론트 라이브 미러(안전망, 2026-07-14).** 위 둘은 서버가 채우는 영속 유입원이라
파킹 제안은 서버 타임아웃(5분) 전까지 inbox에 없다 — 그동안 제안은 채팅 스트림의
diff 카드에만 있어, 사용자가 세션을 옮기면 놓칠 수 있다. 그래서 **프론트는
`skill_patch_proposed`를 받는 즉시 제안함에도 클라이언트 사본을 미러**한다(발생
세션 무관). 서버 계약·`skill_suggestions` 테이블은 불변 — 미러는 순수 프론트
표시/결정 레이어다:
- 미러 항목만 `approval_id`를 실어(서버 row는 없음) 구분한다. 결정은 파킹
  엔드포인트 `POST /skills/{id}/patch`로 라우팅하고, 서버 inbox row는 종전대로
  `POST /skills/suggestions/{sid}/decision`.
- 채팅 diff 카드 ↔ 제안함 미러는 서로 정리한다: 한쪽에서 결정하면 반대쪽을
  제거/걷어낸다(이중 표시·이중 결정 방지).
- 5분 뒤 서버가 타임아웃분을 실제 inbox row로 올리면, 프론트가 같은 키
  (`skill_id`+버전쌍)로 미러를 서버 row로 대체한다(`GET /skills/suggestions` 병합).
  이미 파킹이 만료된 미러를 결정하면 실패하고 재조회로 자가치유한다.
- 재연결 복구(`GET /sessions/{id}/pending-approvals`)로 되살아난 아직-파킹
  제안도 같은 방식으로 미러한다.

seam: rice-app `LearningContext`(`mirrorParkedProposal`/`dropParkedMirror`/
`resolvedApprovals`) + `ChatScreen`. 채팅 카드 **세션 격리 불변식**: 모든
`handleSkillPatchProposed` 호출은 캡처된 sessionId를 넘긴다(폴백이면 세션 혼선 — learn 경로 버그였고 2026-07-14 수정).

### 9.3 크로스세션 발굴 (DiscoveryEngine)

단일 세션 리뷰(§4)가 구조적으로 못 보는 것: **세션 경계를 넘는 반복**("이 사람은
매주 같은 리포트를 처음부터 다시 시킨다" — 각 세션 안에서는 '한 번'일 뿐이므로).
`learning/discovery.py::DiscoveryEngine`이 최근 세션들의 압축 다이제스트
(`prompts/discovery.py::build_session_digest` — 세션당 `discovery_digest_chars`,
tool 결과는 이름만)를 놓고 LLM 1회 호출로 2회+ 반복 패턴을 신규 스킬로 제안한다.

- **생성만 제안** (`propose_skill_create`만 노출) — 다이제스트는 압축본이라 기존
  본문을 안전하게 고칠 근거가 못 된다(§4의 "본문을 못 봤으면 patch 금지"를 도구
  수준에서 강제). 기존 스킬명 재발명·억제 대상은 엔진이 버린다. 실행당 최대 2건.
- **트리거**: 수동 `POST /skills/discover`(LearningPanel "패턴 발굴" 버튼) +
  백그라운드 `discovery_interval_seconds`(기본 **0=off** — LLM 비용) —
  `CuratorScheduler`가 curator 규칙 전이와 같은 루프에서 주기 관리.
- **산출물은 전부 제안함으로** — 백그라운드에서 사용자를 실시간 방해하지 않는다.

### 9.4 v3에서 의도적으로 안 한 것

- **스킬 효과성 점수(helped/hurt)** — use++는 주입 횟수지 유효성이 아니다. 주입된
  턴의 교정 신호와 상관 짓는 자동 감점은 오탐 위험이 커서 보류 — ledger가 이미
  원천 데이터를 쌓고 있으므로 향후 순수 추가로 가능.
- **메모리 HITL 게이트** — 메모리를 제안함에 태우는 안은 기각. 메모리는 저비용·
  고빈도라 카드 승인이 UX를 압도한다. 대신 작성 주체 단일화(§6) + 사후 삭제 +
  ledger 기록으로 통제.
- **제안함 항목의 자동 만료** — pending cap으로 충분. 시간 기반 expired 전이는
  curator에 붙일 수 있는 향후 순수 추가.

---

## 10. 폐루프 고도화 1차 — 인과 보존·안전 경계 확장·실패 보존 (2026-07-18 신규, v4)

적대 리뷰(업계 최고수준 모델의 심층 분석) 결론: 루프의 저작·승인·감사 축은
탄탄하지만 **경험→귀속→평가→승격의 폐루프가 중간에서 끊긴다.** 이 절은 그 지적
중 즉시 구조로 닫을 수 있는 것들을 반영한 변경의 정본이다(회귀 테스트:
`tests/test_learning_hardening_v4.py`).

### 10.1 evidence watermark — 감사 가능한 구간 고정 + 카탈로그 우선순위 hint

리뷰 잡(`learning_jobs`)에 **evidence(JSON)** 를
부착한다: 관찰 구간의 메시지 워터마크(`from_message_id`/`through_message_id`),
그 구간에서 주입/조회/관여된 스킬과 **실제 주입·조회 시점의 버전 목록**,
버전별 friction 턴 수(`friction_by_skill_version`).
`observe()`가 턴 종료 시점에 고정하고(리뷰 시점의 전역 스냅샷으로는 재구성
불가 — 그 사이 스킬이 패치된다), 잡 병합 시 from 보존·through 갱신·버전
합집합으로 합성된다(`LearningLedger._merge_evidence`).

**소비**는 세 군데다. (1) `from..through` inclusive 구간만 gate와 review engine에
동일하게 전달해 이후 대화가 과거 잡에 섞이지 않게 한다(경계가 없거나 역전되면
전체 history로 넓히지 않고 잡을 failed 처리; evidence가 없는 구버전 잡만 전체
history 허용). (2) 병합된 `turns`를 nudge gate에 반영한다. (3) evidence 스킬을
관여>조회>주입 순으로 `ReviewEngine._build_catalog(priority_skill_ids)`에 넘겨
전문(full-body) 예산을 이번 구간의 스킬에 먼저 배정한다. 이전엔 누적 사용량
순에서 잘리면 방금 사용된 저사용 스킬이 빠졌다.

이것은 **완전한 LearningEpisode가 아니다.** 버전/마찰 맵은 감사와 후보 우선순위
hint이며, 과거 버전 본문·도구 결과·성공 판정과 baseline 비교까지 재현하지는
못한다. 잡에 붙인 이유는 잡이 "미리뷰 구간"과 같은 수명을 갖기 때문이다.

### 10.2 안전 경계 확장 — "본문"이 아니라 "모델에 닿는 모든 데이터"

§8의 경계가 본문에만 걸려 있던 세 구멍을 닫았다:

- **memory secret 재유출(P0)**: `_memory_effect`가 store가 반환한 **정제본
  (`item.content`)** 만 ledger detail·SSE(`MemoryWritten.content`)에 싣는다.
  이전엔 memories 테이블에는 `[REDACTED]`가 저장되는데 learning_events와
  스트림으로는 원문 시크릿이 그대로 샜다. `memory_blocked` 차단 기록도 scrub
  후 저장.
- **metadata scrub/scan**: description/tags/changelog(create·patch·consolidate
  전부)도 시크릿 scrub + risk scan 대상이다(`proposals.py::sanitize_metadata`).
  description은 승인 후 매 턴 시스템 프롬프트의 스킬 인덱스에 들어가므로
  본문과 동일한 위협 표면이다. 최종 서비스 쓰기 경계에서도 다시 scrub·길이/개수
  상한을 적용하고, 승인 카드(SSE·재연결·제안함·통합 화면)는 실제 저장될 metadata와
  warning/diff/본문을 숨기지 않는다.
- **quarantine 격리 완결**: 격리 메모리는 리뷰 프롬프트의 "Already-known
  facts"로 재공급되지 않는다(`_recent_fact_strings`가 active만). 격리는 출처
  기반 방어인데, 기지사실 재공급은 그 방어를 우회해 다음 리뷰어의 전제를
  오염시켰다.
- **plugin 소유권 경계**: store에 남은 legacy `source=plugin:*` 행도 review
  카탈로그에서는 compact/read-only로만 보이며 patch/edit/delete/archive/rollback/
  state 변경과 시간 규칙 curator 전이를 서비스 최종 경계에서 거부한다.

### 10.3 리뷰 실패의 보존 — done이 아니라 retryable pending

리뷰 LLM 호출 실패(provider 장애, context 한도)는 이제 `ReviewLLMError`로
구분된다 — "제안 없음"(정상 `[]`)과 "판단 못 함"(장애)은 다른 사건이다.
`service.review()`/`sweep`은 이를 받아 잡을 **pending으로 되돌리고**(attempts+=1,
`0016`), `review_job_max_attempts`(기본 3) 도달 시에만 failed로 마감한다.
재시도 잡은 이미 gate를 통과했으므로 gate를 다시 평가하지 않으며, 성공한 뒤에만
nudge 카운터를 초기화한다. claim은 오래된 잡 우선이고 retry/finish는 `running`
상태에서만 원자적으로 전이한다. 재시도는 다음 POST 또는 스케줄러 sweep이
자연히 집어간다. `/learn`(명시적
사용자 요청)은 SSE error로 정직하게 실패를 보여준다(이전: 조용한 빈 결과).

**context 예산의 CJK 정합**: `review/context.py::_clip`의 1토큰=4자 고정 환산을
제거하고 `core/tokens.py` estimator로 역산+검증한다(§7과 동일 원칙). 이전엔
한국어 세션에서 24K 명목 예산이 실제 ~96K 토큰이 되어 리뷰 호출이 provider
한도에서 터졌고, 그 실패가 §10.3 이전엔 done으로 삼켜져 한국어 사용자의 학습
기회가 체계적으로 소실됐다.

**한 요청 예산**: history와 catalog를 각각 24K로 잡지 않는다. system prompt,
메시지, skill catalog, known facts, tool schema를 합친 입력 전체에 하나의 상한을
적용하고 review 출력 상한도 provider에 명시한다. Ollama는 live `num_ctx`에서
입력+출력+추정 여유를 매 호출 역산하므로 설정 화면에서 모델을 바꿔도 시작 시점의
낡은 예산을 쓰지 않는다.

### 10.4 귀속 정밀도 — friction 버전 스코핑 & 신호 오탐 가드

- **friction 버전 스코핑**: `version_use`와 `friction`을 현재 version 카운터로
  두고, 승인 patch뿐 아니라 직접 본문/번들 편집과 rollback을 포함한 모든 version
  전이에서 함께 리셋한다(`UsageStore.increment_patch`). 잡 evidence에는 관찰 당시
  `skill_id/version`별 friction을 남긴다. 다만 이것만으로 baseline-vs-candidate
  성능을 증명하지는 않는다(§10.5).
- **signals.py 오탐 가드**: 부정문("오류/에러/실패 (+가/는/도) 없-",
  "no tests failed"/"without errors"/"0 failed")과 첨가 표현("뿐(만) 아니라")은
  신호가 아니다. friction과 리뷰 게이트가 이 신호를 먹으므로 재현율보다
  정밀도가 우선이다. 한국어는 고정폭 look-around, 영어는 부정 구문 사전 제거
  방식(부정어와 신호 사이에 단어가 끼는 언어 구조 차이).
- **consolidation 순서 보장**: archive proposal이 가진 umbrella의 목표
  `version + canonical body SHA-256`이 승인 시점의 active/enabled target과 정확히
  같을 때만 흡수를 허용한다. self/inactive/plugin target도 거부한다. 단순 존재
  확인만으로는 기존 umbrella patch 미승인을 구분할 수 없던 구멍을 닫았고,
  umbrella가 먼저 적용되지 않았거나 그 뒤 다시 바뀌면 409로 원본을 보존한다.

### 10.5 이번에 안 한 것 (후속 과제, 우선순위 순)

- ~~**candidate 상태 + baseline-vs-candidate 평가**~~ — **2026-07-19 구현 완료,
  §10.6.** 평가 하네스 재설계(시뮬레이션 롤아웃 + LLM 심판)를 포함하며, 방법론
  정본은 [RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md) §10.
- ~~**완전한 LearningEpisode**~~ — **2026-07-19 완결.** 두 단계로 이행됐다:
  (1) 도구 전사(이름/인자/성패/결과, scrub·상한 정제)가 `learning/episode.py` 로
  실체화되어 잡 evidence(`tool_transcript`)와 `PatchProposal.tool_transcript` 에
  실린다 — candidate 평가의 trace-grounded replay(EVAL §10.2)의 원천.
  (2) **독립 엔티티화 + 판정**(`learning_episodes`, migration 0018): 턴 1개 =
  에피소드 1행. `outcome`(success/corrected/failure/aborted — signals.py 의
  결정적 신호 기반, corrected 가 "사용자 수정" 축이고 aborted > corrected >
  failure > success 우선순위) + `judgment`(신호 근거·도구 호출 수·관여 스킬·
  발화 발췌 — 판정이 휴리스틱임을 근거와 함께 정직하게 노출) + `episode`(전사·
  스킬 버전 맵). aborted 턴도 기록된다 — 중단도 판정이며, 기록은 관찰일 뿐
  학습 반영이 아니라 fail-closed(제안 폐기·잡 미생성)와 충돌하지 않는다.
  리뷰 잡 evidence 의 `episode_ids` 가 병합을 넘어 턴 단위 기록을 가리키고
  (인과 링크), `GET /learning/episodes` 로 조회한다. 보존은
  `learning_episode_cap`(기본 500, 0=off) 상한의 최근 우선 — 장기 감사는
  learning_events 몫.
- **proposal group 트랜잭션** — consolidation의 umbrella+archive들을 하나의
  승인 단위로 묶는 것. §10.4의 순서 가드가 데이터 손실은 이미 막으므로,
  UI/계약 변경이 필요한 그룹화는 후속 순수 추가.

### 10.6 candidate 승격 게이트 — baseline-vs-candidate 평가 (2026-07-19, §10.5 1번 이행)

> 코드: `learning/candidate/`(store·rollout·harness), `prompts/candidate_eval.py`,
> 영속 테이블 `skill_candidates`. 평가 방법론 정본은
> [RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md) §10 — 여기는 루프 통합만 기술.

지금까지 루프는 안전한 제안·승인·적용과 인과 증거 보존(§10.1)까지는 견고했지만
"자가개선된 스킬이 실제로 더 좋아졌다"를 자동으로 증명하지 못했다. 이 절이 그
간극을 닫는다: **승인 = 동의 게이트(사용자), 평가 = 증거 게이트(시스템)**.

**흐름** (`candidate_eval_enabled=True`일 때 — 기본 꺼짐, 아래 "기본값" 참고):

```
HITL 승인(카드 resolve_proposal / 제안함 decide_suggestion)
  → apply_patch 대신 candidate 격리 (skill_candidates, 스킬 파일 불변)
     · baseline 워터마크 고정: version + body SHA-256 (§10.4 순서 가드와 동일 원칙)
     · SSE `skill_candidate_staged` (skill_patched 가 아니다 — "적용됨"은 거짓)
  → 백그라운드 평가 (스케줄러 candidate_eval_interval_seconds 틱, 또는
     POST /skills/candidates/{id}/evaluate 수동)
     · held-out(제안을 낳은 구간의 실제 사용자 발화 = PatchProposal.seed_prompts)
       + paraphrase 과제에서 baseline vs candidate 2-arm 비교 (EVAL §10)
  → 판정: 회귀 조건(성공률·비용·심판 일관 열세) 위반 없음 → promoted
     (apply_patch 실행 — 검증 harness·낙관적 동시성·history·usage 전부 기존 경로)
     | 위반 → discarded (스킬 불변, 리포트 보존)
     | baseline 이동 감지 → expired (직접 편집·rollback 이 이긴다)
     | 인프라 실패 → attempts 재시도, 상한 도달 시 failed (후보 보존)
```

**설계 결정과 근거**:

- **평가는 승인 뒤에 온다** (§10.5 문구 그대로 "즉시 active로 승격하지 않고 …
  통과 시 승격"). 카드 앞에 두면 LLM 호출 다수(과제 생성 + 2-arm 롤아웃 + 심판)
  가 미드턴 카드를 분 단위로 지연시킨다. 사용자가 이미 동의한 변경이므로 이
  게이트의 일은 **개선 증명이 아니라 회귀 방지**다 — tie 는 통과.
- **사용자가 항상 이긴다**: `POST /skills/candidates/{id}/decision` 으로 평가와
  무관하게 즉시 승격/폐기할 수 있다(pending/failed 에서). provider 오프라인·평가
  실패의 탈출구이자 "스킬은 사용자 소유" 원칙의 유지.
- **실패 구분**(§10.3 원칙 재적용): 회귀 검출(discarded)은 정상 판정, provider/
  infra 실패(`CandidateEvalError`)는 재시도 계약. 이전 eval 처럼 실패가 조용히
  삼켜지지 않는다. 부팅 시 running 고아는 pending 복구(recover_boot_state).
- **같은 스킬의 미결 후보는 1개**(409 `skill.candidate_pending`): 후보가 줄서면
  어느 baseline 과 비교했는지가 흐려진다. 제안함 pending 은 유지되므로 기존
  후보 결정 후 다시 승인하면 된다.
- **기본값은 꺼짐**: 평가 1건 = LLM 호출 다수로, discovery 를 기본 꺼짐으로 둔
  것과 같은 비용 논리. 켜기 전까지 승인 경로는 바이트 단위로 기존과 동일하다
  (순수 추가). consolidation 병합 승인은 이 게이트를 타지 않는다 — §10.4 의
  version+hash 순서 가드가 이미 그 경로의 안전을 소유한다.
- **ledger 이벤트**: `candidate_staged/promoted/discarded/expired/eval_failed` —
  폐기도 리포트와 함께 남는다(증거 없는 폐기는 증발과 같다).

**커버리지·증거력 보강 (2026-07-19 2차)**: 평가의 두 약점을 같은 날 닫았다 —
(1) **trace-grounded replay**: 제안을 낳은 구간의 실제 도구 전사
(`learning/episode.py`, LearningEpisode 발판)를 롤아웃이 재생해, 중립 승인
시뮬레이션이 못 보던 "도구 결과 내용에 따른 분기(진단→대응)"를 록화된 현실의
범위 안에서 관찰한다. (2) **심판 양방향 일관성 + 증거 강도 블록**: 같은 pairing
을 좌우 스왑으로 두 번 심판해 불일치를 tie 로 접고(위치 편향의 표본 단위 상쇄),
리포트가 `evidence`(합의율·재생율·표본 수·분산)로 판정의 신뢰도 자체를 계량한다.
상세는 EVAL §10.2-10.3.
