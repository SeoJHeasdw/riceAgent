# RICE Agent Execution — 요구사항이 들어오면 RICE가 일하는 방법

> **이 문서가 소유하는 것**: 사용자 요구사항 수신부터 최종 답변까지의 **실행 모델** —
> 의도 파악, plan-and-execute × ReAct, 컨텍스트 관리, 도구 이식/사용, 서브에이전트,
> 그리고 실행이 학습 루프(스킬/메모리)로 이어지는 접점.
>
> 인접 문서와의 경계: 턴의 REST/SSE 계약은 [RICE_CONTRACTS.md](RICE_CONTRACTS.md),
> 모듈 배치와 의존 규칙은 [RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md),
> 학습 루프 자체는 [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md),
> 런타임 전반 설계는 [RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md)가 정본이다.
> 이 문서는 그 조각들이 **한 턴 안에서 어떻게 맞물리는가**를 정본으로 기술한다.

★ **제품 결정 (2026-07-04)**: RICE의 메인 모델은 로컬 LLM(Ollama 등)이 될 수 있다.
따라서 실행 모델은 **토큰 절약이 아니라 수행 품질을 최적화**한다 — 계획 수립에
LLM을 한 번 더 부르고, 매 반복 계획 상태 전체를 프롬프트에 다시 실으며, 계획이
크면 반복 예산을 늘린다. 로컬 모델의 약점(긴 멀티스텝 작업에서 목표 상실, 관찰
후 재평가 생략)을 **구조**로 보정하는 것이 이 설계의 존재 이유다.

---

## 1. 한 턴의 전체 파이프라인

`POST /sessions/{id}/messages` → `Orchestrator.run_turn()` (agent/orchestrator.py).
오케스트레이터는 **얇은 조율자**다: 순서만 소유하고, 각 단계의 행동은 소유
모듈에 있다. 모든 모듈은 `interfaces.py` Protocol 뒤에 있고 배선은
`server/composition_root.py`가 한다.

```
1. PERSIST    사용자 메시지를 transcript(state.db)에 기록
2. PREFETCH   learning.prefetch → 주입할 스킬 + 메모리 + 스킬 생성 가이드
3. ASSEMBLE   도구 카탈로그 조립(builtin + connector + plugin, 예산 초과 시 bridge로 폴딩)
              시스템 프롬프트 합성(base ⊕ 스킬/메모리 ⊕ plugin 스킬) → TurnContext
4. RUN        AgentLoop.run — 이 문서의 §3 (plan-and-execute × ReAct)
              4a. PLAN     의도 파악·계획 수립 (복합 질의만; plan_updated SSE)
              4b. EXECUTE  LLM 호출 ↔ 도구 실행 루프 (ReAct; HITL 승인 포함)
5. PERSIST    루프가 만든 모든 메시지(도구 라운드 포함)를 transcript에 기록
6. OBSERVE    learning.observe → 메모리 기록 등 값싼 효과
7. SURFACE    학습 효과를 SSE로 서피싱(스킬 제안은 HITL 승인 카드)
8. DONE       스트림 종료 (실패 시에도 error → done)
```

턴이 끝난 뒤 클라이언트가 별도 논블로킹 호출로 세션 제목 생성(`/title`)과 게이트된
스킬/메모리 리뷰(`/learning-review`)를 트리거한다 — LLM 호출이 메인 스트림의
`done`을 지연시키지 않는다는 원칙.

---

## 2. 의도 파악 — 모든 질의가 계획을 요구하지 않는다

**모듈**: `agent/planner.py::TurnPlanner`, 프롬프트는 `prompts/planning.py`.

턴 시작 시 플래너가 LLM을 **1회** 호출해 최신 요청을 해석한다. 하나의 호출이
분류(triage)와 계획 수립을 겸한다:

- **단순 질의** (인사, 단일 질문/조회, 한 번의 도구 호출로 끝나는 요청, 직전 답변의
  후속 질문) → `{"plan_needed": false}` — 몇 토큰으로 끝나고, 루프는 계획 없이
  이전과 동일한 순수 ReAct로 진행한다. 분류 전용 호출을 따로 두지 않는 이유:
  같은 정보로 출력 길이만 달라지는데 왕복을 두 번 낼 이유가 없다.
- **복합 질의** (여러 행동이 서로를 딛고 서는 요청 — 수집→분석→종합→전달) →
  `{"plan_needed": true, "goal": …, "steps": […]}` — 해석된 의도(goal) 한 문장과
  2~`plan_max_steps`(기본 8)개의 단계. 각 단계는 **어느 도구로 무엇을 만들며
  성공 기준이 무엇인지**(detail)까지 적게 지시된다.

플래너 프롬프트에는 최근 대화(도구 원문 제외), **이 턴에 실제로 쓸 수 있는 도구
카탈로그**(승인 문구는 걸러냄 — 계획에 무관한 되새김 연료), 주입된 스킬 이름이
실린다 — 계획이 현실의 도구에 접지(grounding)되게 하기 위함이다. goal/단계 제목은
대화 언어를 따른다(언어 설정 비배선 — 제목 생성과 같은 원칙).

**소형 모델 안정화 (2026-07-04)**: qwen 9b급 로컬 모델이 플래너에서 무한 thinking
루프("Wait, I need to check…" 반복)에 빠지는 실측 사고 후 세 겹으로 재설계했다.

1. **thinking 억제 + 디코딩 제약**: 플래너 호출은 `think=False` +
   `response_schema`(JSON 스키마, Ollama `format`)로 나간다. JSON 준수는 프롬프트의
   "ONLY JSON, no code fence" 같은 부정형 지시(소형 모델 검증-루프의 1순위 연료)가
   아니라 grammar가 보증한다. 저온(0.2)×thinking 조합은 qwen 계열의 문서화된
   무한 반복 조건 — thinking을 끄면 성립하지 않는다.
2. **디코딩 수준 토큰 상한**: `max_tokens`(num_predict)로 출력 자체가 bounded —
   폭주 생성은 벽시계 타임아웃이 아니라 샘플러가 자른다.
3. **thinking 상한 fast-fail**: per-call think를 표현 못 하는 프로바이더가 thinking을
   흘리면 6,000자에서 끊고 계획 없이 진행한다(타임아웃까지 태우지 않는다).

프롬프트 자체도 다이어트했다: delegate_subagent 팬아웃 조건문(실행 단계 관심사),
propose_skill 마지막-단계 규칙(§3.5의 완료 유도와 중복)을 제거 — 판단 규칙 하나가
소형 모델에겐 되새김질할 체크리스트 항목이다.

**fail-soft 불변식**: 플래너의 어떤 실패(파싱 불가, 타임아웃 `planner_timeout_seconds`,
프로바이더 에러, 1단계짜리 사이비 계획, thinking 폭주)도 턴을 깨지 않는다 — 계획
없이 진행할 뿐이다. echo 프로바이더(모델 미설정)는 플래너를 건너뛴다.

---

## 3. Plan-and-Execute × ReAct — 실행 모델의 핵심

**모듈**: `agent/loop.py::AgentLoop` + `agent/planner.py::PlanState`.
계약 타입: `core/types.py::TurnPlan/PlanStep` (불변 스냅샷, revision 단조 증가).

두 패턴의 역할 분담: **Plan-and-execute가 뼈대**(무엇을 어떤 순서로), **ReAct가
관절**(각 행동 후 관찰을 계획에 반영). 어느 한쪽만으로는 부족하다 — 계획 없는
ReAct는 긴 작업에서 표류하고, 재평가 없는 계획 실행은 첫 관찰이 계획과 어긋나는
순간 무너진다.

### 3.1 계획은 매 반복 프롬프트에 다시 실린다 (스냅샷 프롬프팅)

계획이 있는 동안 **매 LLM 호출**의 시스템 프롬프트 끝에 살아있는 계획 블록이
렌더된다(`prompts/planning.py::render_plan_block`):

```
## Active Plan (you MUST work this plan)
Goal: …
Plan revision: 3
Steps:
  [v] s1. 폴더 구조 파악 (note: 42 files, React+TS)
  [>] s2. 핵심 파일 병렬 분석 — delegate_subagent로 팬아웃
  [ ] s3. 분석 결과 종합
  [ ] s4. Gmail로 발송 — 승인 카드 예상
  [ ] s5. 절차를 스킬로 제안 (propose_skill)

How to work this plan (ReAct): …(재평가 지침)
```

대화 중반에 계획을 한 번 말하고 잊게 두지 않는다. 로컬 LLM은 히스토리 중간의
계획을 놓친다 — 시스템 프롬프트의 현재 상태가 유일한 진실이 되게 한다. 이
블록은 컨텍스트 렌더링(§4)의 예산 계산에 포함되므로 안전하다.

### 3.2 `update_plan` — 관찰을 계획에 되먹이는 통로

계획이 있는 동안만 광고되는 가상 빌트인. 모델은 단계를 시작할 때(in_progress),
끝낼 때(done + 결과 한 줄 note), 막혔을 때(failed), 관찰이 계획을 바꿀 때
(add_steps / skip)마다 호출한다.

- **루프가 직접 가로챈다** — dispatcher를 타지 않는다. 계획은 턴-로컬 상태라
  외부 실행도 승인도 영속 부작용도 없다. 대신 `tool_call`/`tool_result` SSE는
  그대로 방출해 감사 가능성을 유지한다.
- 적용은 관용적이다: 모르는 step_id·불량 status는 경고로 강등하고 나머지는
  적용한다 — 로컬 LLM의 부분적으로 틀린 호출이 진행 자체를 막지 않는다.
- 갱신마다 revision이 오르고 전체 스냅샷이 `plan_updated` SSE로 흐른다.
- 전 단계가 종결(done/failed/skipped)되면 자동으로 `status: "completed"`.

### 3.3 Drift guard — 재평가를 잊는 모델을 구조가 붙잡는다

`plan_drift_guard_rounds`(기본 4)회의 도구 라운드가 `update_plan` 없이 지나가면,
다음 반복의 계획 블록에 **REASSESS NOW** 지시가 붙는다: 끝난 단계를 done으로
기록하거나 관찰이 계획을 바꿨으면 수정하라, 오토파일럿으로 도구만 부르지 마라.
ReAct의 "관찰→추론" halves를 프롬프트 선의에 맡기지 않고 결정론적으로 강제하는
장치다. 같은 원칙으로, 계획 블록의 상시 지침에 "같은 도구가 두 번 연속 실패하면
세 번째는 다르게 하라"가 포함된다.

### 3.4 반복 예산은 계획 크기에 비례한다

계획 없는 턴은 기존 `learning.max_iterations`(기본 25) 그대로. 계획이 있으면
`12 + 6×단계수`와 기존 상한 중 큰 쪽을 취하되 `agent.plan_max_iterations`(기본 60)
로 캡한다. `add_steps`로 계획이 자라면 예산도 함께 자란다(매 반복 재계산).
5단계 실작업이 25라운드 상한에 걸려 중간에 죽는 것보다, 상한을 계획에 비례시키는
것이 "일을 잘하는 서비스"다.

### 3.5 완료 → 스킬 승격 유도

전 단계가 종결되고 이 턴의 도구 호출이 3회 이상이면, 계획 블록이 최종 답변 전에
`propose_skill` 호출을 유도한다. 완료된 계획(goal + 단계 + 무엇이 통했는지 note)은
그 자체로 SKILL.md의 뼈대다. 제안은 스테이징일 뿐이며 실제 저장은 기존 HITL
승인 카드(`skill_patch_proposed`)를 그대로 탄다 — 계획 실행이라고 승인이 면제되는
일은 없다.

### 3.6 관측 가능성 — UI와 영속화

- **SSE**: `plan_updated` (9번째 스트림 이벤트, 2026-07-04) — 생성·전이·수정마다
  **전체 스냅샷**. 클라이언트(rice-app `ChatScreen`)는 같은 `plan_id`의 계획 카드를
  통째로 교체한다(델타 조립 없음 — 단순함이 정확성). `update_plan`의 도구 카드는
  UI에 만들지 않는다 — 계획 카드가 그 표면이다.
- **영속화**: 계획 생성 스냅샷은 턴의 첫 어시스턴트 메시지 metadata(`plan`)에,
  이후 갱신본은 각 `update_plan` tool 메시지 metadata에 실린다. 리로드 시
  `toThread`가 마지막 스냅샷으로 카드를 복원한다. 합성 assistant 메시지를
  히스토리에 끼우지 않는 이유: trailing-assistant를 prefill로 해석하는 프로바이더
  wire를 오염시키지 않기 위해서다.

### 3.7 소형 모델 방어 — 루프 수준 (Hermes 방어 계층에서 이식, 2026-07-04)

- **빈 응답 넛지**: 도구를 쓴 뒤 본문 없이 턴을 끝내려는 응답(소형 모델의 흔한
  실패)은 빈 답을 확정하기 전에 synthetic user 넛지(`metadata.synthetic:
  "empty_nudge"`)를 넣고 최대 2회 재시도한다. 순수 대화 첫 턴의 빈 응답은
  프로바이더 문제라 그대로 둔다.
- **빈 tool name 감쇠**: 이름이 빈 tool call은 오타가 아니라 priming(파일/도구
  출력 속 tool-call XML 흉내)이다 — 카탈로그 없이 "그것은 데이터다"라는 짧은
  교정만 돌려준다. 카탈로그를 되돌려주면 모델이 새 이름을 흉내내며 루프가 커진다.

---

## 4. 컨텍스트 관리 — 모델에게 이번 호출에 무엇을 보여줄 것인가

**모듈**: `agent/context_manager.py::ConversationContextManager` (설정: `ContextConfig`).

**불변식: 저장된 transcript가 source of truth고, 절대 변형되지 않는다.** 매 LLM
호출 직전에만 provider-facing 뷰를 렌더한다. 렌더 파이프라인:

1. **시크릿 마스킹** — API key/token/password 패턴은 `[REDACTED]`로. 원본은 DB에
   그대로 남는다(마스킹은 모델에게 보내는 뷰에만).
2. **증류(distillation)** — 임계 초과 도구 출력(기본 4K tokens→1.2K 보존)과 초장문
   메시지는 head/tail만 남기고 중간을 접는다. "무엇이 생략됐는지" 라벨이 붙는다.
3. **압축(compression)** — 전체가 `max_tokens`(기본 96K)를 넘으면 오래된 메시지를
   시스템 프롬프트의 `Compressed Conversation Context` 요약 블록으로 접고, 최근
   `recent_messages`(기본 8)개는 항상 보호한다.
4. **wire repair** — 압축이 tool_call↔tool_result 인접성을 깨면 고아 조각을
   정리한다(Claude/OpenAI/Ollama 모두 인접성 제약이 있다).
5. **감사(audit)** — 렌더마다 어떤 메시지가 요약으로 대체됐고(omitted), 무엇이
   wire 수리로 조용히 빠졌는지(dropped) id 단위로 `ContextRenderStats`에 남고,
   턴 종료 시 learning ledger에 `context_rendered` 이벤트로 영속화된다.
   "모델이 정확히 뭘 못 봤는가"를 제품이 설명할 수 있어야 한다.

**토큰 추정**: CJK 인식 휴리스틱(`core/tokens.py`)으로 시작해, 프로바이더가
보고하는 실측 input_tokens로 세션 내내 보정된다(`TokenCalibrator`). 컨텍스트 길이
초과 에러가 나면 예산을 0.45배로 줄여 1회 재렌더-재시도한다.

**계층 분리**: 이 계층은 학습 루프와 무관하다. 압축은 "이번 호출에 무엇을
보여줄지"의 문제고, 스킬/메모리는 learning 모듈의 승인 기반 자기개선 문제다.

---

## 5. 도구 시스템 — 이식(등록)과 사용

**모듈**: `agent/tool_dispatch.py` (스키마 조립·라우팅·HITL), `agent/tool_scheduler.py`
(병렬화), `agent/builtins/` (내장 도구).

### 5.1 도구의 4가지 출처와 이식 방법

| source | 무엇 | 이식 방법 |
|---|---|---|
| `builtin` | 프로세스 내 도구: read/write/search_files, terminal, vision_analyze, delegate_subagent, propose_skill | `agent/builtins/`에 모듈 추가 → `build_builtin_tools()`에 등록(한 곳). 핸들러는 `async (dict) -> object`, 위험하면 `requires_approval=True`, 세션 문맥이 필요하면 `needs_session=True` 마커 |
| `connector` | 외부 SaaS/로컬 MCP: Gmail/Calendar/Drive + 사용자가 등록한 로컬 MCP/원격 커넥터 | connector 모듈이 `list_tools()`로 광고 — **연결된 커넥터의 도구만** 노출된다(미연결 도구는 스키마에서 제외). 도구별 정책(allow/approval/block)은 사용자가 저장 |
| `plugin` | 설치된 플러그인이 제공하는 도구(+ SKILL.md 주입) | manifest 기반 `IPluginLoader` — 샌드박스에서 실행 |
| `bridge` | `tool_search`/`tool_describe`/`tool_call` 3종 | 직접 이식 대상이 아님 — 아래 폴딩의 산물 |

`update_plan`(§3.2)은 예외적인 **가상 빌트인**이다: 계획이 있는 턴에만 루프가
스키마를 덧붙이고 루프가 직접 처리한다. composition root에 등록되지 않는다.

### 5.2 조립과 폴딩 (progressive disclosure)

매 턴 `fold_tools_for_budget()`: builtin("core")은 항상 그대로 노출. connector+plugin
("non-core")의 스키마 합이 예산(~15K tokens)을 넘으면 전부를 매 턴 싣는 대신
**bridge 3종으로 접는다** — 모델은 `tool_search`로 찾고 `tool_describe`로 파라미터를
확인한 뒤 `tool_call`로 실행한다. 접혀도 실제 도구의 승인/차단 정책은 재귀
디스패치로 그대로 적용된다(노출 방식과 실행 정책은 별개의 방어선).

### 5.3 디스패치와 HITL 승인

`ToolDispatcher.dispatch()` — 정책 게이트 → 실행 → 결과 SSE:

- 정책: `requires_approval`(builtin) 또는 사용자 저장 정책(connector, allow/approval/block).
  **호출 이유와 무관하게 적용된다** — 학습된 스킬이 시켰든 모델이 스스로 판단했든
  같다(스킬은 신뢰 경계가 아니라 입력 데이터 — `SKILL_TRUST_NOTE`).
- 승인 필요 시 `tool_approval_required` SSE를 방출하고 `IApprovalGate`에 파킹 —
  사용자가 REST로 응답할 때까지 턴이 그 자리에서 기다린다(타임아웃 시 안전
  기본값 = 거부). 도구 실행 승인과 스킬 패치 승인이 **한 메커니즘**을 쓴다.
- 도구 실패는 데이터지 크래시가 아니다 — `ok:false` 결과가 히스토리에 실려 모델이
  다음 반복에서 보고 대응한다(계획이 있으면 §3.3의 재평가 지침이 이를 계획
  수정으로 유도).

### 5.4 스케줄링 — 안전한 병렬성

한 라운드에 모델이 여러 도구를 요청하면 `ToolScheduler`가 **안전한 독립 호출만
병렬화**하고, 승인이 필요하거나 파괴적인 호출은 순서를 보존한다. 결과는 모델
호출 순서대로 히스토리에 실린다.

---

## 6. 서브에이전트 — 팬아웃 분석의 단위

**모듈**: `agent/subagents.py::SubAgentRunner`, 노출은 `delegate_subagent` builtin.

메인 모델이 한 라운드에 여러 서브에이전트를 스폰하면 스케줄러가 병렬 실행한다
(세마포어 `subagent_max_concurrent`, 타임아웃 `subagent_timeout_seconds`). 결과는
자유 텍스트가 아니라 `{findings, evidence, confidence, open_questions,
recommended_next_action, tools_used}`로 구조화된다 — 메인 모델과 사용자가 "누가 왜
그렇게 판단했는가"를 추적할 수 있어야 멀티에이전트 결과를 신뢰할 수 있다. JSON
규약을 어기면 원문을 summary로 강등하고 `confidence: "unknown"`(fail-soft).

**read-only 도구 미니 ReAct 루프 (2026-07-04)**: 서브에이전트는 read-only 빌트인
(`read_file`/`list_dir`/`search_files`/`fetch_url` — `builtins.build_subagent_toolset`)
을 직접 부르며 최대 `subagent_max_tool_rounds`(기본 6) 도구 라운드를 돈다. 이전의
"제공된 컨텍스트만 요약" 워커는 컨텍스트 격리 효과가 없었다 — 메인이 파일 원문을
읽어 넘겨야 했으므로 오염은 메인에서 이미 발생한 뒤였다. 이제 워커가 직접 읽고,
메인은 구조화 결과만 본다. 부작용 있는 도구(쓰기/셸/재위임)는 주지 않는다 — 워커는
승인 게이트 밖에서 돈다. 라운드 소진 시 도구 없는 최종 호출로 보고를 강제하고,
호출당 thinking 16,000자 상한으로 추론 폭주를 끊는다(플래너와 같은 fast-fail).

---

## 7. 실행 → 학습의 접점

실행 모델은 세 지점에서 학습 루프에 접속한다(상세는 LEARNING_LOOP 문서):

1. **주입(턴 시작)**: prefetch가 활성 스킬 전체(예산 내)와 관련 메모리를 시스템
   프롬프트에 싣는다. 플러그인 SKILL.md도 같은 형식으로 실린다.
2. **미드턴 제안**: `propose_skill` builtin — 스테이징만 하고, 턴 종료 시
   `skill_patch_proposed` HITL 카드로 서피싱된다(생성도 from_version=0의 패치로
   통일). §3.5의 계획 완료 유도가 이 경로의 주 트리거 중 하나다.
3. **사후 관찰**: `learning.observe`(값싼 효과) + 게이트된 `/learning-review`(LLM
   리뷰). 계획 턴의 update_plan 호출·note들은 히스토리에 남아 리뷰 LLM이 "무엇이
   통했는지"를 읽는 상질의 원료가 된다.

메모리 오염 방지: 외부 콘텐츠(connector/plugin/bridge 도구 결과)가 섞인 턴에서
나온 메모리는 격리(quarantined)되어 승인 전 주입되지 않는다. 각 tool 메시지의
`tool_source` metadata가 그 판정 근거다.

---

## 8. 설정 요약 (`config.yaml`의 `agent` 섹션, PUT /settings로 갱신 가능)

| 키 | 기본 | 의미 |
|---|---|---|
| `planning_enabled` | `true` | false면 플래너 없이 순수 ReAct(이전 동작) |
| `planner_recent_messages` | 12 | 플래너에 보여줄 최근 대화 수 |
| `planner_timeout_seconds` | 90 | 플래너 호출 타임아웃(초과 시 계획 없이 진행) |
| `plan_max_steps` | 8 | 계획 단계 수 상한(프롬프트 지시; 코드 하드캡 12) |
| `plan_max_iterations` | 60 | 계획 턴의 반복 예산 상한(§3.4) |
| `plan_drift_guard_rounds` | 4 | 이 횟수의 무보고 도구 라운드 후 재평가 강제(§3.3) |

---

## 9. 워크스루: "해당 폴더의 프론트코드를 분석해서 분석 결과를 gmail로 보내줘"

1. **PREFETCH/ASSEMBLE** — 관련 스킬·메모리 주입, 도구 조립: `read_file`/`search_files`
   (builtin), `delegate_subagent`, `propose_skill`, Gmail 커넥터 도구(연결돼 있으므로 노출).
2. **PLAN** — 플래너가 복합 질의로 판정, 계획 생성(`plan_updated` → 앱에 계획 카드):
   s1 폴더 구조 파악(search_files) → s2 핵심 파일 병렬 분석(delegate_subagent 팬아웃)
   → s3 종합 정리 → s4 Gmail 발송(승인 예상) → s5 절차 스킬화(propose_skill).
3. **EXECUTE** — s1: search_files/read_file로 구조 파악, `update_plan`(s1 done, note).
   s2: 서브에이전트 3개 병렬 스폰(컴포넌트/상태관리/스타일 각각), 구조화 결과 종합,
   `update_plan`. s3: 분석 리포트 작성. s4: Gmail 발송 도구 호출 →
   `tool_approval_required` 카드 → 사용자 승인 → 발송, `update_plan`(s4 done).
   중간에 관찰이 어긋나면(예: 폴더가 모노레포) add_steps/skip으로 계획을 수정하고,
   4라운드 무보고 시 drift guard가 재평가를 강제한다.
4. **완료** — 전 단계 done → 계획 카드 "완료", propose_skill 유도 →
   `frontend-analyze-and-mail` 스킬 제안 카드(diff) → 사용자 승인 시 저장.
   다음에 같은 부류의 요청이 오면 이 스킬이 주입되어 더 적은 시행착오로 실행된다.
