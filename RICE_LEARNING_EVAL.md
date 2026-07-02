# RICE Learning Eval Design

> 작성일: 2026-06-22 (개정: 2026-06-26) · 상태: **확정 (결정 완료)**
> 범위: 제품 thesis "쓸수록 똑똑해짐"을 **정량적으로 측정·증명**하는 방법 — 측정 지표, cold/warm 실험 설계, eval harness, in-app "Learning Impact" 가시화(디자인 데모 surface), 데이터 계약, 데모 스크립트
> 관계: [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md)는 *무엇이 학습되는가*(skill·memory·patch)를 정의한다. 이 문서는 *학습이 일어났음을 어떻게 측정·증명하는가*를 정의한다. patch 메커니즘은 [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md) 5.2, SSE 이벤트는 [RICE_CONTRACTS.md](RICE_CONTRACTS.md) 1.3, 화면 매핑은 [RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md) 4장.

---

## 1. 목적 & 측정 철학

RICE의 단 하나의 핵심 주장은 **"쓸수록 사용자에게 맞춰 똑똑해진다"**([RICE_PRODUCT_IDEA.md](RICE_PRODUCT_IDEA.md))이다. 지금까지 이 주장의 증거는 **정성적**이다 — SSE 이벤트(8종, [RICE_CONTRACTS.md](RICE_CONTRACTS.md) 1.3)로 `skill_patch_proposed`(신규 생성은 `from_version=0`)·`memory_written`·`skill_patched`가 화면에 흐르는 것을 *보여준다*. 그러나 "스킬이 생성되는 것이 보인다"는 "스킬 덕분에 실제로 더 잘/빨리 한다"와 다르다.

이 문서는 그 간극을 닫는다: **동일한 과제를 학습 전(cold) vs 학습 후(warm)로 실행해, 더 적은 turn·tool call로 더 높은 성공률에 도달함을 숫자로 증명**한다.

설계 원칙:
- **하나의 정량 지표가 데모 설득력을 좌우한다.** 화려한 시각화보다 정직한 before/after 숫자 하나가 강하다.
- **재현 가능해야 한다.** 실제 인프라(실 K8s 등)에 의존하면 데모가 깨진다 → mock 환경 위에서 결정적으로 동작.
- **측정은 곧 contribution의 증거다.** 연구 수준을 표방한다면, 이 ablation 결과가 "학습이 성능을 개선한다"는 주장의 근거다.
- **UI 미완성 단계에도 적용한다.** 디자인 데모에는 *데이터 계약(JSON)을 고정한 패널*을 시드 수치로 먼저 넣고, 런타임 완성 후 eval harness의 실측치로 drop-in 교체한다(6장).

---

## 2. 측정 지표 (Metrics)

| 지표 | 정의 | 기대 방향 | 출처 |
|------|------|:--------:|------|
| `turns_to_completion` | 과제 완료까지 사용자↔에이전트 왕복(turn) 수 | ↓ | 세션 로그 |
| `tool_calls` | 과제당 tool 호출 총수 | ↓ | `tool_call` SSE 집계 |
| `success` | 과제 성공 여부 (성공 체크 함수, 4장) | ↑ | 성공 체크 |
| `skill_hit` | 관련 스킬이 **실제로 주입**됐는지 (warm 기대=true) | ↑ | prefetch 로그 |
| `latency` / `tokens` | 벽시계 시간 / 토큰 소비 (선택) | ↓ | provider 응답 |

**`skill_hit`이 중요한 이유 (이전 설계 리뷰와 연결).** 검색(retrieval)은 이 시스템의 무른 중심이다(FTS5 키워드 한계). warm 조건인데 `skill_hit=false`라면 — 스킬은 있지만 **검색이 못 끌어온 것**이다. `skill_hit`을 별도 지표로 두면 "학습 자체의 실패"와 "검색의 실패"를 **분리 진단**할 수 있다. (검색을 임베딩으로 정밀화하면 이 지표가 직접 개선된다 — RUNTIME_DESIGN 3장 후속.)

`turns`·`tool_calls`·`success`가 **데모용 3대 지표**다. 나머지는 부록.

---

## 3. 실험 설계 (Cold vs Warm Ablation)

핵심은 **단일 변인 통제**다. 같은 task, 같은 모델, 같은 시스템 프롬프트. 차이는 오직 **학습 상태(누적 스킬/메모리)** 뿐이다.

```
              ┌─────────────────────────────────────────────┐
              │            동일 Task Suite (4장)             │
              └─────────────────────────────────────────────┘
                      │                          │
        ┌─────────────▼──────────┐   ┌──────────▼───────────────┐
        │  COLD                  │   │  WARM                     │
        │  빈 ~/.rice            │   │  스킬/메모리 축적 상태      │
        │  (스킬 0, 메모리 0)     │   │  (관련 스킬 + patch 반영)   │
        └─────────────┬──────────┘   └──────────┬───────────────┘
                      │  같은 모델·프롬프트·temp  │
                      ▼                          ▼
              turns / tool_calls / success / skill_hit
                      └──────────  델타(Δ)  ──────────┘
                  기대: turns↓  tool_calls↓  success↑  skill_hit ✗→✓
```

**조건 정의:**
- **COLD (baseline)**: 빈 프로파일(`~/.rice_eval_cold`). 스킬·메모리 없음. task suite 실행.
- **WARM (학습 후)**: 관련 스킬이 축적된(+patch까지 반영된) 프로파일. *동일* task suite 실행.

**WARM 프로파일 준비 — 두 방법:**

| 방법 | 설명 | 장점 | 단점 |
|------|------|------|------|
| A. 자연 워밍업 | 워밍업 시나리오를 **실제로 실행**해 스킬을 자연 생성/patch | 가장 정직 (loop 전체가 도는 증거) | 비결정적(LLM 변동), 데모마다 결과 흔들림 |
| B. 스냅샷 시드 | 미리 만든 `skills/` 스냅샷을 WARM 프로파일에 주입 | **재현성 높음**, 데모 안정 | 워밍업 과정 자체는 안 보임 |

**데모는 B(스냅샷)로 재현성 확보 + 부록으로 A를 1회 실측 첨부한다.** B로 "결과의 안정적 재현"을, A로 "스킬이 실제로 자연 생성된다"를 각각 책임진다. 따라서 `eval/profiles/warm/`은 스냅샷 시드를 정본으로 두고, 자연 워밍업(A) 1회 실측은 부록 결과로 첨부한다.

---

## 4. Task Suite (데모용)

각 task = `{ id, prompt, success_check, related_skill }`. 데모용 **3~5개**면 충분하다. use case에 맞춰 작성한다(아래는 예시 — 실제 시나리오로 교체).

| id | prompt(요지) | 관련 스킬 | success_check(예) |
|----|-------------|-----------|-------------------|
| T-A | "이 서비스 배포해줘" | `deploy-k8s-service` | mock 배포 tool이 올바른 순서/인자로 호출됨 |
| T-B | "프로젝트 테스트 돌려줘" | `run-tests` + pytest preference | mock test runner가 `-n 8`(교정 반영)로 호출됨 |
| T-C | "WXO 에러 X 트러블슈팅" | `wxo-expert` plugin | 정답 진단 단계 시퀀스 도달 |

**성공 체크는 결정적 mock 기반으로 구현한다.** 실제 부수효과(실 배포) 대신 **mock tool의 호출 시퀀스/최종 상태**로 판정한다(예: 정해진 인자·순서로 호출됐는가, 최종 상태가 기대값인가). LLM-judge(자유 서술 채점)는 결정성을 해치므로 **보조 수단으로만** 쓴다. 각 task의 `success_check`는 이 결정적 술어로 작성한다.

> mock tool은 built-in tool로 구현(코드 실행 sandbox 불필요, [RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) 5장). 결정적으로 응답하도록 고정한다(예: 정해진 입력 → 정해진 결과·실패).

---

## 5. Eval Harness (engineering)

런타임 완성 후 위 숫자를 **실제로 산출**하는 스크립트. 3레포를 늘리지 않고 `rice-runtime` **내부 디렉토리**로 둔다([RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md)와 정렬, 일반 `tests/`와 별개).

```
rice-runtime/
└── eval/                          # 학습 측정 하네스 (tests/와 분리)
    ├── tasks/                     # task 정의 (YAML: prompt + success_check + related_skill)
    │   ├── t_a_deploy.yaml
    │   └── ...
    ├── profiles/
    │   ├── cold/                  # 빈 ~/.rice 시드 (스킬 0)
    │   └── warm/                  # 스냅샷 시드 (스킬 + patch 반영) — 방법 B
    ├── runner.py                  # cold/warm 프로파일로 suite 실행 → 메트릭 수집
    ├── metrics.py                 # turns/tool_calls/success/skill_hit 집계
    ├── report.py                  # results JSON(6장 계약) + 사람이 읽는 요약 생성
    └── results/                   # 타임스탬프별 결과 (cold/warm/delta)
```

**runner 흐름:**
```
1. COLD: profiles/cold → 격리된 ~/.rice_eval_cold 로 runtime 세션 기동
         → suite의 각 task를 N회 실행 → metrics 기록
2. WARM 준비: 방법 B(스냅샷) 또는 A(워밍업 실행)로 ~/.rice_eval_warm 구성
3. WARM: 동일 suite를 N회 실행 → metrics 기록
4. report: cold vs warm 델타 산출 → results/<ts>.json (6장 스키마) + 요약 텍스트
```

**측정의 함정과 처리 (정직성):**
- **LLM 비결정성** → 각 task **N회(권장 3~5)** 반복, **중앙값/평균 + 분산** 보고. `temperature`는 낮게 고정.
- **모델 고정** → cold/warm 동일 provider·model(예: `claude-sonnet-4-6`). 모델이 다르면 ablation이 오염된다.
- **성공 결정성** → 4장의 mock 기반 체크. LLM-judge는 보조.
- **분리 진단** → `skill_hit`으로 "학습 적용 여부" 자체를 확인. warm·skill_hit=false면 검색 문제로 切り分け.
- **공정성** → cold/warm 모두 동일 max_iterations([RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) 2장, 기본 25), 동일 tool 집합.

> 이 하네스는 **데모 직전 1회 실행이 아니라 회귀 도구**로도 쓴다: 프롬프트·검색·patch 로직을 바꿨을 때 학습 효과가 퇴행하지 않았는지 CI에서 확인(향후).

---

## 6. In-app "Learning Impact" 가시화 (디자인 데모 surface)

디자인 데모에 들어가는 **눈에 보이는** 부분. 5장 harness가 산출한 JSON을 읽어 before/after를 렌더한다.

### 6.1 위치

**MySkillsScreen 내 패널.** 7화면 구조를 유지하면서 정량 증거를 차별점 화면에 자연 배치한다. (검토했던 대안: ChatScreen 사이드 배지 — 공간 협소 / 새 8번째 화면 신설 — 화면 구조 변경 부담.)

> **구현 상태**: 이 패널은 프론트엔드에 **이미 구현됨** — [`LearningImpactPanel.tsx`](rice-app/src/components/carbon/LearningImpactPanel.tsx)가 MySkillsScreen 안에서 "Learning Impact" 탭으로 렌더된다([MySkillsScreen.tsx](rice-app/src/screens/MySkillsScreen.tsx) — `showImpact` 토글). 6.3 스키마(`ImpactReport`)에만 의존하므로 현재 시드 fixture가 추후 harness 실측치로 drop-in 교체된다.

### 6.2 패널 와이어프레임 (개념)

```
┌─ Learning Impact ─────────────────────────────────┐
│  같은 작업, 학습 전 vs 후   (suite: demo-v1, n=5)   │
│                                                    │
│  Turns         ▓▓▓▓▓ 5   →   ▓▓ 2      (-60%)      │
│  Tool calls    ▓▓▓▓▓▓▓▓ 8 →  ▓▓▓ 3     (-62%)      │
│  Success       80%        →  100%      (+20pt)     │
│  관련 스킬 주입   ✗         →   ✓ deploy-k8s @v2     │
│                                                    │
│  [ 과제별 보기 ]                 [ eval 다시 실행 ]   │
└────────────────────────────────────────────────────┘
```

- 막대는 cold(회색) vs warm(강조색) 비교. 델타는 화살표·퍼센트.
- `관련 스킬 주입` 행이 검색 품질을 노출 — warm에서 `✓ + 버전`이 떠야 "patch까지 반영된 스킬이 실제로 쓰였다".
- `[과제별 보기]` → task별(T-A/T-B/T-C) 분해. `[eval 다시 실행]` → harness 트리거(데모 후반, 선택).

### 6.3 데이터 계약 (results JSON → UI)

UI는 이 스키마에만 의존한다. 디자인 단계엔 **시드 fixture**로, 런타임 완성 후엔 harness 실측으로 동일 스키마를 채운다(drop-in).

```json
{
  "task_suite": "demo-v1",
  "model": "claude-sonnet-4-6",
  "runs_per_task": 5,
  "generated_at": "2026-06-22T10:00:00Z",
  "tasks": [
    {
      "id": "T-A",
      "title": "K8s 서비스 배포",
      "cold": { "turns": 5, "tool_calls": 8, "success_rate": 0.8, "skill_hit": false },
      "warm": { "turns": 2, "tool_calls": 3, "success_rate": 1.0, "skill_hit": true,
                "skills_used": ["deploy-k8s-service@2"] },
      "delta": { "turns_pct": -0.60, "tool_calls_pct": -0.62, "success_pp": 0.20 }
    }
  ],
  "aggregate": {
    "turns_pct": -0.55, "tool_calls_pct": -0.58, "success_pp": 0.17, "skill_hit_rate": 1.0
  }
}
```

> `@2`는 patch로 v2까지 개선된 스킬이 사용됐음을 뜻한다 — [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md) 5.2와 연결(정량+정성이 같은 스킬을 가리킴).

### 6.4 노출 경로

- 데모 단계(★): 런타임이 `eval/results/latest.json`을 **정적 fixture**로 노출하거나, 앱이 번들한 fixture를 읽는다. 엔드포인트 불필요.
- 향후: `GET /eval/runs`, `GET /eval/runs/{id}` (선택, [RICE_CONTRACTS.md](RICE_CONTRACTS.md)에 추가). MVP는 fixture로 충분.

---

## 7. 계약/문서 변경 요약 (ripple)

이 문서를 채택하면 다음이 수반된다(단일 출처 원칙 유지 — [README.md](README.md) 중복 방지 규칙):

| 대상 문서 | 변경 |
|-----------|------|
| [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md) | 이미 반영: 5.2 patch(개선) 절, `use`·`patch`가 측정 지표임 명시(§3) |
| [RICE_CONTRACTS.md](RICE_CONTRACTS.md) | (선택) `GET /eval/runs`·`/eval/runs/{id}` — **MVP는 fixture로 대체 가능** |
| [RICE_PRODUCT_IDEA.md](RICE_PRODUCT_IDEA.md) | MySkillsScreen에 "Learning Impact" 패널 추가(4.4) |
| [RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md) | `eval/` 디렉토리 추가(`tests/`와 별개, 5장 트리) |
| [README.md](README.md) | 문서 목록에 본 문서 추가, 단일 출처 규칙에 "학습 측정 = LEARNING_EVAL" 한 줄 |

---

## 8. 데모 스크립트 (eval — 정량)

```
1. "RICE가 정말 학습하는지 숫자로 보자" → MySkills > Learning Impact 패널 열기
2. 비교 카드: 같은 과제 T-A
     Turns 5→2,  Tool calls 8→3,  Success 80→100%,  관련 스킬 주입 ✗→✓(deploy-k8s @v2)
3. (개발자 청중) "이건 harness가 cold/warm 두 번 실제로 돌린 결과" → results JSON / report 노출
4. (연결) ChatScreen 라이브로 그 warm 경로 재현: 스킬 주입 → 적은 turn으로 완료
        → 5.2 patch 데모(diff 카드 승인)와 이어 붙이면, "개선된 v2 스킬이 숫자를 만든다"가 한 흐름
```

→ **정량(패널) + 정성(라이브 SSE·diff 카드)** 이 동일 주장을 두 각도에서 증명한다. 비개발자에겐 패널의 막대·퍼센트가, 개발자에겐 JSON·반복 횟수·분산이 각각 설득력을 가진다.
