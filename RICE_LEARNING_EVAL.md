# RICE Learning Eval Design

> 작성일: 2026-06-22 (개정: 2026-06-26, **2026-07-08 — 소스 드리프트 동기화**: `/eval/impact` 실배선 반영, mock 메커니즘 정정, §9 negative eval 신규, **2026-07-19 — §10 candidate 승격 평가 하네스 신규**) · 상태: **확정 (결정 완료)**
> 범위: 제품 thesis "쓸수록 똑똑해짐"을 **정량적으로 측정·증명**하는 방법 — 측정 지표, cold/warm 실험 설계, eval harness, in-app "Learning Impact" 가시화(디자인 데모 surface), 데이터 계약, 데모 스크립트
> 관계: [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md)는 *무엇이 학습되는가*(skill·memory·patch)를 정의한다. 이 문서는 *학습이 일어났음을 어떻게 측정·증명하는가*를 정의한다. patch 메커니즘은 [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md) 5.2, SSE 이벤트는 [RICE_CONTRACTS.md](RICE_CONTRACTS.md) 1.3, 화면 매핑은 [RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md) 4장.

---

## 1. 목적 & 측정 철학

RICE의 단 하나의 핵심 주장은 **"쓸수록 사용자에게 맞춰 똑똑해진다"**([RICE_PRODUCT_IDEA.md](RICE_PRODUCT_IDEA.md))이다. 지금까지 이 주장의 증거는 **정성적**이다 — SSE 이벤트(9종, [RICE_CONTRACTS.md](RICE_CONTRACTS.md) 1.3)로 `skill_patch_proposed`(신규 생성은 `from_version=0`)·`memory_written`·`skill_patched`가 화면에 흐르는 것을 *보여준다*. 그러나 "스킬이 생성되는 것이 보인다"는 "스킬 덕분에 실제로 더 잘/빨리 한다"와 다르다.

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
| T-C | "배포 오류(ImagePullBackOff) 트러블슈팅" | `deploy-k8s-service`(실제 `eval/tasks/t_c_debug.yaml` 기준. `deployment-expert` plugin은 현재 eval task에 사용되지 않음) | 정답 진단 단계 시퀀스 도달 |

**성공 체크는 결정적 mock 기반으로 구현한다.** 실제 부수효과(실 배포) 대신 **mock tool의 호출 시퀀스/최종 상태**로 판정한다(예: 정해진 인자·순서로 호출됐는가, 최종 상태가 기대값인가). LLM-judge(자유 서술 채점)는 결정성을 해치므로 **보조 수단으로만** 쓴다. 각 task의 `success_check`는 이 결정적 술어로 작성한다.

> **메커니즘 정정(2026-07-08)**: 현재 eval task는 mock builtin tool이 아니라 `eval/scripted_provider.py::ScriptedToolProvider`를 통해 **LLM provider 응답을 결정적으로 재생**한다. 각 task YAML의 `script:` 블록(고정 tool-call 시퀀스)을 재생하고, 시스템 프롬프트에 `skill_marker` 문자열이 있는지로 cold/warm 분기를 결정한다. task YAML이 참조하는 도구명(`kubectl_apply`/`kubectl_rollout_status`/`kubectl_get_pods`/`kubectl_describe_pod`/`kubectl_create_secret`/`search_docs`)은 실제 builtin 카탈로그에 등록되지 않은 eval 전용 이름이며, 루프는 이를 "unknown tool" 결과로 처리하면서도 `tool_calls` 지표에는 집계한다. 현재 3개 태스크는 모두 scripted replay이며, `eval/results/latest.json`의 `model` 필드는 `"scripted"`다. 실제 provider 기반 task를 추가할 경우 아래 §5 "모델 고정" 원칙을 적용한다.

---

## 5. Eval Harness (engineering)

런타임 완성 후 위 숫자를 **실제로 산출**하는 스크립트. 3레포를 늘리지 않고 `rice-runtime` **내부 디렉토리**로 둔다([RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md)와 정렬, 일반 `tests/`와 별개).

```
rice-runtime/
└── eval/                          # 학습 측정 하네스 (tests/와 분리)
    ├── tasks/                     # task 정의 (YAML: prompt + success_check + related_skill + script)
    │   ├── t_a_deploy.yaml
    │   ├── t_b_tests.yaml
    │   └── t_c_debug.yaml
    ├── scripted_provider.py       # ★ 결정론 재생 프로바이더(§4 정정 참고) — 현재 태스크 3종 전부가 이걸 씀
    ├── negative.py                # ★ 안전성 negative eval(N1~N6, §9 신규)
    ├── profiles/
    │   └── warm/                  # 스냅샷 시드(방법 B). cold는 checked-in 디렉토리가 아니라 실행마다 tempfile로 생성
    ├── runner.py                  # cold/warm 프로파일로 suite 실행 → 메트릭 수집
    ├── metrics.py                 # turns/tool_calls/success/skill_hit 집계
    ├── report.py                  # results JSON(6장 계약) + 사람이 읽는 요약 생성
    └── results/                   # 타임스탬프별 결과(cold/warm/delta) + negative-latest.json
```

**runner 흐름:**
```
1. COLD: 실행마다 빈 임시 프로파일(tempfile, prefix=rice-eval-cold-) 생성
         → suite의 각 task를 N회 실행 → metrics 기록
2. WARM 준비: 방법 B(스냅샷) 또는 A(워밍업 실행)로 ~/.rice_eval_warm 구성
3. WARM: 동일 suite를 N회 실행 → metrics 기록
4. report: cold vs warm 델타 산출 → results/<ts>.json (6장 스키마) + 요약 텍스트
```

**측정의 함정과 처리 (정직성):**
- **LLM 비결정성** → 각 task **N회** 반복, **중앙값/평균 + 분산** 보고. 현재 runner 기본값은 `runs_per_task=3`이며, 데모용 seed fixture는 5회 예시를 사용한다. `temperature`는 낮게 고정.
- **모델 고정** → cold/warm 동일 provider·model(예: `claude-sonnet-4-6`). 모델이 다르면 ablation이 오염된다.
- **성공 결정성** → 4장의 mock 기반 체크. LLM-judge는 보조.
- **분리 진단** → `skill_hit`으로 "학습 적용 여부" 자체를 확인. warm·skill_hit=false면 검색 문제로 切り分け.
- **공정성** → cold/warm 모두 동일 max_iterations([RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) 2장, 기본 25), 동일 tool 집합.

> 이 하네스는 **데모 직전 1회 실행이 아니라 회귀 도구**로도 쓴다: 프롬프트·검색·patch 로직을 바꿨을 때 학습 효과가 퇴행하지 않았는지 CI에서 확인(향후).

---

## 6. In-app "Learning Impact" 가시화 (디자인 데모 surface)

디자인 데모에 들어가는 **눈에 보이는** 부분. 5장 harness가 산출한 JSON을 읽어 before/after를 렌더한다.

### 6.1 위치

**MySkillsScreen 내 패널.** 별도 화면을 추가하지 않고 정량 증거를 차별점 화면에 자연 배치한다. (검토했던 대안: ChatScreen 사이드 배지 — 공간 협소 / 새 화면 신설 — 화면 구조 변경 부담.)

> **구현 상태**: 이 패널은 프론트엔드에 **이미 구현됨** — [`LearningImpactPanel.tsx`](rice-app/src/components/workspace/LearningImpactPanel.tsx)가 MySkillsScreen 안에서 "Learning Impact" 탭으로 렌더된다([MySkillsScreen.tsx](rice-app/src/screens/MySkillsScreen.tsx) — `showImpact` 토글). 6.3 스키마(`ImpactReport`)에만 의존하므로 현재 시드 fixture가 추후 harness 실측치로 drop-in 교체된다.

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

아래 JSON은 `eval/results/demo-v1-seed.json` 형태의 데모 seed 예시다. 실제 harness 기본 실행(`eval/runner.py::main`)은 `runs_per_task=3`으로 `eval/results/latest.json`을 생성한다.

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

- **구현 상태**: `GET /eval/impact`는 `server/routes/eval_impact.py`에 구현되어 있으며, 최신 `ImpactReport`를 `$RICE_EVAL_RESULTS/latest.json` → `~/.rice/eval/results/latest.json` → repo 내 `eval/results/latest.json` 순서로 조회해 반환한다.
- 프론트(`MySkillsScreen`)는 이 엔드포인트를 직접 호출한다. 번들된 시드(`IMPACT_REPORT`)는 fetch 실패 시의 폴백이며, `LearningImpactPanel`은 `demo={!impact}` 플래그로 데모 데이터와 평가 suite 실측치를 구분한다.
- 현재 계약 경로는 `/eval/impact`다. 초기 설계에서 검토했던 `/eval/runs`는 채택되지 않았으며, 최종 경로는 [RICE_CONTRACTS.md](RICE_CONTRACTS.md)가 정본이다.

---

## 7. 계약/문서 변경 요약 (ripple)

이 문서를 채택하면 다음이 수반된다(단일 출처 원칙 유지 — [README.md](README.md) 중복 방지 규칙):

| 대상 문서 | 변경 |
|-----------|------|
| [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md) | 이미 반영: 5.2 patch(개선) 절, `use`·`patch`가 측정 지표임 명시(§3) |
| [RICE_CONTRACTS.md](RICE_CONTRACTS.md) | `GET /eval/impact` 계약 반영(`/eval/runs`는 채택되지 않음) |
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

---

## 9. Negative (안전성) Eval — "배우면 안 되는 것을 배우지 않았는가" (신규, 이 문서 최초 반영 — 2026-07-08)

§1-8은 전부 **긍정적 학습 효과**(더 잘·빨리 함)를 증명하는 축이다. `eval/negative.py`는 반대 축을 결정론적으로 검증하는 별도 하네스다 — 모듈 자체 docstring 표현을 빌리면 "배우면 안 되는 것을 배우지 않는다"는 걸 증명한다. 결과는 `eval/results/negative-latest.json`.

여섯 개 화이트박스 체크(코드 경로를 직접 assert — LLM 판단 없음):

| id | 검증 대상 |
|----|-----------|
| N1 | 시크릿이 스킬/메모리 본문에 영속되지 않음(LEARNING_LOOP §8 스크러빙) |
| N2 | 프롬프트 인젝션이 스킬 제안에서 플래그됨(`warnings`, `guard.py::scan_risks` — LEARNING_LOOP §8) |
| N3 | 인젝션이 섞인 메모리가 격리(quarantine)됨(LEARNING_LOOP §6) |
| N4 | 반복 거부된 제안에 쿨다운이 걸림(LEARNING_LOOP §9.1) |
| N5 | stale patch(낙관적 동시성 충돌)가 감지됨 |
| N6 | 외부 콘텐츠 파생 메모리가 승인 전엔 주입되지 않고, 승인 후엔 정상 주입됨(격리와 같은 축의 왕복 확인) |

이 6종은 이미 통과하는 상태로 하네스에 들어와 있다(신규 기능이 아니라 §1-8과 마찬가지로 이미 구현된 방어선의 **회귀 방지 축**). §1의 "측정은 곧 contribution의 증거다" 원칙을 안전성 쪽에도 적용한 것 — 정량적 학습 효과만 자랑하고 안전장치는 말로만 있다고 하지 않기 위함이다.

---

## 10. Candidate 승격 평가 하네스 — "이 patch가 실제로 더 낫다"의 자동 증명 (2026-07-19 신규)

§1-8의 cold/warm 하네스는 **suite 수준의 데모/회귀 도구**다: 결정적이지만, task가 고정 YAML이고 `scripted_provider.py`가 marker 유무로 각본을 고르는 구조라 "스킬이 *모델의* 성능을 개선했다"의 증거가 못 된다(LEARNING_LOOP §10.5의 리뷰 지적). 이 절의 하네스는 다른 질문에 답한다 — **"방금 승인된 이 특정 patch/신규 스킬이, 이 사용자의 실제 모델에서, 기존 버전보다 회귀 없이 동작하는가"** — 그리고 그 답이 승격/폐기를 자동으로 결정한다(루프 통합은 LEARNING_LOOP §10.6이 정본, 여기는 평가 방법론).

### 10.1 설계 — 무엇이 §1-8과 다른가

| 축 | cold/warm 하네스 (§3-5) | candidate 하네스 (이 절) |
|----|------------------------|--------------------------|
| 질문 | "학습이 쌓이면 좋아지는가" (suite) | "이 한 건의 변경이 회귀 없는가" (per-candidate) |
| task | 고정 YAML 3종 | **held-out**(제안을 낳은 구간의 실제 사용자 발화, `PatchProposal.seed_prompts`) + **paraphrase**(LLM 재표현 — 원문 암기 과적합 방지) |
| provider | scripted 각본 재생 | **실제 배선된 provider** (메인 턴과 동일 — 더 싼 모델로 대체하면 결과가 배포 환경을 대표하지 못한다) |
| 성공 판정 | mock tool 시퀀스 | 결정적 지표(도구 시퀀스·비용) + **rubric 기반 LLM 심판** |
| 산출 | ImpactReport (데모 패널) | 승격/폐기 판정 + 영속 리포트 (`skill_candidates.report`) |

**단일 변인 통제**(§3의 원칙 유지): 두 arm(baseline/candidate)은 같은 provider·같은 scaffold 프롬프트·같은 도구 표면(두 본문의 참조 도구 **합집합** — 한쪽만 아는 도구가 있으면 표면 차이가 스킬 차이와 교락된다)을 보고, 다른 것은 **주입된 스킬 블록 하나**다(실전 주입 렌더러 `_render_skills`를 그대로 태워 배포 표면과 동일). 신규 생성 후보는 baseline arm이 스킬 없이 돈다 — §3 cold/warm과 같은 축.

### 10.2 시뮬레이션 롤아웃 — trace-grounded replay (2026-07-19 보강)

`learning/candidate/rollout.py`: 메인 루프를 흉내낸 bounded 미니 루프(기본 5스텝)를 돌리되 **도구는 절대 실제로 실행하지 않는다**. 도구 결과는 두 단계로 답한다:

1. **재생(grounded)**: 제안을 낳은 실제 세션의 **도구 전사**(`learning/episode.py` — 도구 이름/인자/성패/결과의 scrub·상한 정제본, LearningEpisode 발판)에서 같은 이름의 기록을 **기록 순서대로 소진**하며 그 결과를 그대로 돌려준다. 전사의 결과는 모델이 실제 세션에서 봤던 렌더 그대로이므로, **도구 결과 내용에 따라 갈리는 분기(진단→대응)가 록화된 현실의 범위 안에서 관찰된다** — 초판 시뮬레이션이 커버하지 못했던 환경 반응 축의 개방. 같은 이름의 기록을 다 쓰면 마지막 기록을 반복(읽기형 도구의 안정 상태 가정). 전사의 원천: 리뷰 엔진이 제안 시점에 리뷰 구간에서 추출(`PatchProposal.tool_transcript`), T5 미드턴 제안은 턴 종료 드레인에서 부착, 리뷰 잡 evidence(`tool_transcript` 필드, 병합 시 최근 우선 cap 20)에도 감사용으로 영속.
2. **중립 승인(ungrounded)**: 전사에 없는 도구는 동일한 중립 승인 결과.

**한계를 계량한다**(리포트 `mode: "simulated_rollout"` + `evidence.grounded_tool_result_rate`): 재생율 0%의 평가와 90%의 평가는 같은 verdict라도 증거력이 다르다 — 그 차이를 리포트가 숨기지 않는다. 전사 밖의 미록화 분기는 여전히 관찰 불가(완전한 LearningEpisode의 잔여 몫). 얻는 것: (1) 부작용 제로, (2) 결정성 — 재생은 기록 순서 기반이라 환경 노이즈 없음, (3) 공정성 — 두 arm이 같은 전사·같은 도구 표면(본문 참조 ∪ 전사 이름 합집합)을 본다.

### 10.3 심판과 판정 — 회귀 방지 게이트

- **rubric**: 과제 생성 호출이 seed에서 2-4개의 관찰 가능한 성공 기준을 도출한다(스킬/버전 언급 금지 — 과제 성공을 기술하지, 스킬 사용을 기술하지 않는다). 생성 실패 시 held-out만으로 강등(+기록), seed가 아예 없으면 infra 실패로 재시도.
- **pairwise 심판 + 양방향 일관성**(2026-07-19 보강): 과제마다 두 전사를 A/B로 **은닉**하고 rubric 충족 수를 센다. `candidate_eval_judge_consistency`(기본 켜짐)면 같은 pairing을 **좌우를 바꿔 두 번** 심판한다: 두 방향 모두 같은 쪽 우세일 때만 승패, 불일치는 tie — LLM 심판의 최대 노이즈원(위치 편향)을 표본 평균이 아니라 **표본 단위에서 상쇄**한다(항상 A를 고르는 극단 편향 심판도 전 pairing tie로 접혀 가짜 회귀/가짜 승리를 못 만든다 — 테스트로 고정). 충족률은 두 방향 평균, 합의율은 `evidence.judge_agreement_rate`로 리포트에 남는다. 구조화 출력(response_schema·think=False·temp 0.1 — 소형 모델 안정화 레시피).
- **판정 = 회귀 조건 검사** (LEARNING_LOOP §10.5 문구의 "성공률·비용·회귀 조건"):
  - `success_regression`: candidate rubric 충족률 평균 < baseline
  - `cost_regression`: 출력량이 상대 허용율(기본 +50%) **과** 절대 하한(+2,000자)을 모두 초과 — 상대만 쓰면 짧은 출력에서 몇십 자 차이가 오탐된다
  - `judge_preference_regression`: 심판이 일관되게 baseline 우세(candidate 무승·유패)
  - 위반 0건 → promote. **tie는 통과다** — 사용자가 이미 동의한 변경이므로 이 게이트의 일은 개선 증명이 아니라 회귀 방지이고, 폐기 쪽에 보수 편향을 걸면 정상 개선이 심판 노이즈로 계속 좌초한다.
- **실패 구분**(LEARNING_LOOP §10.3 원칙): provider/infra 실패는 `CandidateEvalError` → attempts 재시도(기본 2회), 소진 시 failed로 **보존**(수동 결정 가능). 회귀 검출은 정상 판정(discarded)이며 리포트와 함께 남는다.
- **증거 강도 블록**(2026-07-19 보강, `report.evidence`): 판정만이 아니라 그 판정을 얼마나 믿을 수 있는지도 리포트의 일부다 — `pairings`, held-out/paraphrase 과제 수, `judge_agreement_rate`(양방향 합의율), `grounded_tool_result_rate`(전사 재생율), `tool_calls_observed`, arm별 `success_stddev`. 프론트/사용자는 promote라는 결론과 그 결론의 표본 크기·합의도·근거율을 함께 본다.

### 10.4 노브·API (요약 — wire 정본은 CONTRACTS)

`LearningConfig.candidate_eval_*`: `enabled`(기본 **False** — 평가 1건=LLM 호출 다수, discovery와 같은 비용 논리로 기본 꺼짐; 꺼진 동안 승인 경로는 기존과 동일), `interval_seconds`(스케줄러 드레인, 기본 120), `paraphrase_tasks`/`max_tasks`/`runs_per_task`/`max_steps`, `cost_tolerance`, `max_attempts`, `output_max_tokens`.

`GET /skills/candidates` · `POST /skills/candidates/{id}/evaluate` · `POST /skills/candidates/{id}/decision` + SSE `skill_candidate_staged`. 테스트는 `tests/test_candidate_eval.py`(결정적 fake provider — 테스트 더블이 각본을 연기하는 것과, 출하 하네스가 실제 provider 행동을 관찰하는 것은 별개다).
