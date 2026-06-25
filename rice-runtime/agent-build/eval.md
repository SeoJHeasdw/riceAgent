# 작업: eval — 학습 측정 하네스 (cold/warm · Learning Impact)

> 먼저 `CONVENTIONS.md`를 읽었다고 가정한다.
> **정본: `../RICE_LEARNING_EVAL.md` 전체.** 출력 데이터 계약은 §6.3.
> 이건 다른 모듈이 끝난 뒤 마지막에 한다(전 모듈에 의존).

## 목표

"쓸수록 똑똑해진다"를 **숫자로 증명**하는 하네스. 같은 태스크를 **cold**(빈
`~/.rice`)와 **warm**(학습 스냅샷 시드)에서 돌려 turns/tool_calls/success/skill_hit를
비교하고, UI의 Learning Impact 패널이 읽는 결과 JSON을 생성한다.

트리(RUNTIME_DIRECTORY §2) — **`src/`가 아니라 레포 루트의 `eval/`**:
```
eval/
  tasks/                    # task 정의: prompt + success_check + related_skill
  profiles/cold/            # 빈 ~/.rice
  profiles/warm/            # 스냅샷 시드(방법 B — EVAL 참조)
  runner.py                 # suite 실행
  metrics.py                # turns/tool_calls/success/skill_hit 집계
  report.py                 # → results JSON
  results/                  # 타임스탬프별 cold/warm/delta (UI fixture 소스)
```

## 출력 데이터 계약 (정본 EVAL §6.3, 미러: `rice-app/src/lib/types.ts`의 `ImpactReport`)

`results/latest.json`은 **정확히 이 모양**이어야 한다(앱이 정적 fixture로 읽음 —
CONTRACTS §1.3, 새 엔드포인트 만들지 마라):

```jsonc
{
  "task_suite": "string",
  "model": "string",
  "runs_per_task": 3,
  "generated_at": "ISO-8601",
  "tasks": [
    {
      "id": "string", "title": "string",
      "cold": { "turns": 0, "tool_calls": 0, "success_rate": 0.0, "skill_hit": false, "skills_used": [] },
      "warm": { "turns": 0, "tool_calls": 0, "success_rate": 0.0, "skill_hit": true,  "skills_used": [] },
      "delta": { "turns_pct": 0.0, "tool_calls_pct": 0.0, "success_pp": 0.0 }
    }
  ],
  "aggregate": { "turns_pct": 0.0, "tool_calls_pct": 0.0, "success_pp": 0.0, "skill_hit_rate": 0.0 }
}
```
필드 의미·계산식은 EVAL §6.3을 따른다. `*_pct`는 cold 대비 warm 개선율,
`success_pp`는 퍼센트포인트, `skill_hit_rate`는 warm에서 관련 스킬이 끌려온 비율.

## 핵심 개념 (정본 EVAL)
- **cold vs warm ablation:** 동일 태스크·동일 모델, `~/.rice` 상태만 다르게.
  cold=빈 상태, warm=관련 스킬/메모리가 시드된 상태. 차이가 "학습 효과".
- **skill_hit:** warm에서 관련 스킬이 **실제로 prefetch에 끌려왔는가**(끌려왔으면
  true). warm인데 skill_hit=false면 "학습 실패"가 아니라 "검색 실패"(retrieval
  취약점 진단 — RUNTIME_DESIGN §3). 이 분리 진단이 이 하네스의 중요한 가치다.
- **runs_per_task:** 변동성 완화를 위해 태스크당 N회 반복 평균.

## 실행 방법 (runner.py)
- 런타임을 **in-process로 구동**: `build_container(paths=RicePaths.from_env(<프로파일
  경로>))`로 격리된 `~/.rice`를 가리키고, `agent.run_turn(...)`을 직접 호출해 SSE
  이벤트를 수집(서버/HTTP 불필요). cold는 빈 임시 디렉토리, warm은 시드된 디렉토리.
- 측정: 한 태스크 = 성공할 때까지(또는 상한)의 turn 수, tool_call 수, 성공여부
  (`success_check`), prefetch에 관련 스킬이 들어갔는지(skill_hit).
- 모델: 결정성/비용을 위해 기본은 실제 provider 대신 **시나리오 모킹 또는 저비용
  설정**을 권장(EVAL 참조). 실제 모델로 돌릴 수 있게 옵션화하되, 기본 실행이
  네트워크/키 없이도 의미 있는 결과를 내도록 하라(애매하면 NOTE).

## tasks/ 와 profiles/
- `tasks/`에 task 3~5개(README의 다음 액션 §5). 각 task = `{id, title, prompt,
  success_check, related_skill}`. 형식은 EVAL의 task 정의를 따른다(JSON/YAML 택1, NOTE).
- `profiles/warm/`은 "방법 B = 스냅샷 시드"(EVAL): 관련 스킬 .md + 메모리를 미리
  심어둔 `~/.rice` 스냅샷. `profiles/cold/`는 비어 있음.

## 수용 기준 (Acceptance)
1. `python3.13 eval/runner.py`(또는 동등)가 cold·warm을 돌려 `results/<ts>.json` +
   `results/latest.json`을 위 스키마로 생성.
2. 생성된 JSON이 `ImpactReport` 스키마를 만족(필드/타입 일치) — 검증 코드 포함.
3. 최소 1개 태스크에서 warm이 cold보다 turns/tool_calls 적게 나오는 시나리오를
   재현(시드의 효과가 숫자로 보임).
4. skill_hit이 cold=false, warm=true로 분리 측정됨.
5. 격리: 실제 `~/.rice`를 건드리지 않음(전부 임시/프로파일 경로).

## 하지 말 것
- 결과 JSON 스키마를 임의 변경 금지(앱이 묶여 있음 — types.ts `ImpactReport`).
- 새 런타임 엔드포인트 추가 금지(MVP는 정적 fixture).
- `src/rice_runtime/` 안에 eval 코드를 넣지 마라(eval은 별도 최상위 폴더, tests와 분리).
- 런타임 모듈의 **구체 구현** import 금지 — 공개 진입점은 `build_container` +
  `agent.run_turn`(= public surface). 그 외엔 interfaces.
