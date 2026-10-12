# RICE Runtime Design

> 작성일: 2026-06-12 (개정: 2026-07-08 — 소스 드리프트 동기화) · 상태: **확정 (결정 완료)**
> 범위: `rice-runtime`의 핵심 설계 + 패키징 + 보안 베이스라인
> 해소 대상(전건 확정): [RICE_REPO_STRATEGY.md](RICE_REPO_STRATEGY.md) 9장 #1, #3, #5, #6, #7
> 아키텍처 참조: [RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md)(모듈 구조 정본)

---

## 1. 벤치마크 차용 결정표

> Hermes 분석([HERMES_LEARNING_LOOP_REFERENCE.md](HERMES_LEARNING_LOOP_REFERENCE.md) · [HERMES_TOOL_SYSTEM_REFERENCE.md](HERMES_TOOL_SYSTEM_REFERENCE.md))과 Gemini CLI agentic architecture 분석(`context/ARCHITECTURE*.md`) 기준, 컴포넌트별로 차용/단순화/제외를 결정한다. 이 표가 runtime의 전체 스코프를 정의한다.

| 벤치마크 컴포넌트 | 결정 | 근거 |
|----------------|------|------|
| 대화 루프 (`run_conversation`) | ★ **차용** (구조만, 재구현) | learning loop의 뼈대. 12k줄 전체가 아니라 패턴만 |
| Skill Lifecycle (`skill_usage`) | ★ **차용** | RICE의 핵심 차별점. use/view/patch 카운터 + 상태 전이 |
| 스킬 생성 트리거 | ★ **차용 후 RICE식 재구성** | 현재 구현은 시스템 프롬프트 가이드만이 아니라 `ReviewGate`/`ReviewEngine` 사후 리뷰와 `propose_skill` 미드턴 도구를 함께 사용한다. 저장은 항상 `skill_patch_proposed` 승인 후 수행 |
| Curator | ★ **단순화** — 자동 전이(규칙)만 차용, LLM 리뷰는 수동 트리거 버튼으로 | 기본 7일 주기는 데모에서 절대 안 보임. UI에서 "스킬 정리" 버튼 → 즉시 실행이 데모에 유리 |
| Memory Manager | ★ **차용** (builtin 1개로 시작) | Hermes도 외부 provider 1개 제한. MVP는 builtin(로컬)만 |
| Honcho (외부 user modeling) | ★ **제외** | 원격 API 의존 — 사내 데모에 외부 SaaS 부적합. user modeling은 로컬 구현 (3장) |
| `execute_code` (PTC) → 실제 구현명 `terminal` | ✅ **차용했으나 경계는 원안보다 약하게 구현됨** — repo 단위 코드/명령 실행 도구로 제공 | Hermes도 코드를 생성·실행한다(스킬에 넣지 않을 뿐). 원안은 자식 프로세스+cwd 한정이었으나, 실제 `agent/builtins/terminal_tool.py`는 **의도적으로 "호스트 셸"**이라 cwd를 제한하지 않는다 — 실제 방어선은 승인 게이트뿐이다. 상세·격차는 5장 |
| Plugin 시스템 (`register(ctx)`) | ★ **차용** (artifact 스펙은 CONTRACTS 문서) | Plugin Market의 전제 조건 |
| Cron Scheduler | ★ **제외** | 데모 범위 밖 |
| Gateway (메시징) | ★ **제외** | RICE는 데스크톱 앱 단일 접점 |
| FTS5 세션 검색 | ★ **차용** | 세션 간 컨텍스트 유지 데모에 직접 기여, SQLite 내장이라 비용 낮음 |
| Tool registry + 조건부 노출 (`check_fn`) | ★ **차용** (단순화) | built-in/plugin/connector tool을 한 곳에서 모아 LLM 스키마로 조립하고 **연결 안 된 Connector tool은 스키마에서 제외**. Hermes의 30s TTL 캐시는 생략(단일 세션, 연결 상태 직접 조회) — 2.1장 |
| Toolset (플랫폼별 묶음) | ★ **제외** | 데스크톱 단일 접점이라 telegram/discord류 플랫폼 toolset 불필요 |
| Tool Search (progressive disclosure) | ★ **차용** (구현됨, 2026-07-07 관련도 스포트라이트로 개편) | MCP/plugin tool 스키마가 예산 초과 시 `tool_search`/`tool_describe`/`tool_call` 3개 bridge + 관련도 상위 도구 스포트라이트로 접는다 — 2.1장 |
| Webhook-safe toolset (인젝션 방어) | ★ **차용 (재해석)** | RICE엔 webhook 없으나 **Gmail 본문 = 신뢰불가 외부 콘텐츠** → 동일 방어 적용 — 10장 |
| Gemini CLI ContextManager | ★ **차용 (RICE식 단순화)** | durable transcript는 보존하고 LLM 요청 직전에 provider-facing context만 렌더링/압축/마스킹한다 — 2.2장 |
| Gemini CLI Scheduler | ★ **차용 (RICE식 단순화)** | HITL/권한은 기존 dispatcher가 유지하고, 안전한 독립 tool call만 병렬 batch로 실행한다 — 2.3장 |
| Sub-agent delegation | ★ **차용** | main agent가 `delegate_subagent` builtin으로 독립 분석/리서치/검토를 위임하고 다음 루프에서 결과를 종합한다 — 2.4장 |

위 차용표가 runtime 전체 스코프의 정본이다 — Honcho **제외**(외부 SaaS), Curator **수동 트리거** 포함, 코드/명령 실행 도구(`terminal`, 원안 명칭 `execute_code`) **포함**(실제 경계는 원안보다 약함, 5장). Hermes와 Gemini CLI를 기준으로 가깝게 벤치마킹하며, 추후 개선·확장은 본 표를 갱신하는 방식으로 추적한다.

---

## 2. 대화 루프

```
요청 수신 (로컬 API, CONTRACTS 문서의 계약)
 → learning prefetch (관련 메모리 + 관련도 상위 스킬 + 스킬 인덱스 로드)
 → 시스템 프롬프트 구성 (base + 스킬/메모리 + plugin 스킬 인덱스)
 → 의도 파악·계획 수립 (TurnPlanner LLM 1회 — 복합 질의만 TurnPlan,
    단순 질의는 계획 없이 통과; plan_updated SSE) … 2026-07-04, AGENT_EXECUTION §3
 → 컨텍스트 렌더링 (히스토리 압축/요약/마스킹, 최근 턴 보호)
 → LLM 호출 루프 (tool_calls 처리, max_iterations 제한
    — 계획이 있으면 매 반복 계획 상태를 프롬프트에 렌더 + update_plan
      인터셉트로 단계 전이/수정(ReAct 재평가) + drift guard + 완료 시
      propose_skill 유도)
 → ToolScheduler (안전한 독립 tool 병렬화, 승인/파괴적 tool 순차)
 → 스트리밍 응답 (HTTP SSE로 앱에 전달)
 → 턴 종료: skill usage 텔레메트리 + T5 스킬 제안 드레인
 → post-turn: /learning-review에서 메모리 작성 및 스킬 생성/개선 제안 처리
```

- ★ max_iterations 기본값: 25 (Hermes는 사실상 무제한이나 데모 앱은 폭주 방지 필요). 계획이 있는 턴은 단계 수에 비례해 `agent.plan_max_iterations`(기본 60)까지 늘어난다 — 로컬 LLM 메인 배포에서 토큰 절약보다 수행 품질 우선이라는 제품 결정. 계획 실행의 정본은 [RICE_AGENT_EXECUTION.md](RICE_AGENT_EXECUTION.md). `agent` 섹션 전체 6개 노브(planning_enabled·planner_recent_messages·planner_timeout_seconds·plan_max_steps·plan_max_iterations·plan_drift_guard_rounds)는 AGENT_EXECUTION §8 표가 정본.
- ★ 스킬 주입(2026-07-07 개편): 활성 스킬 **전체**를 예산 내 주입하던 방식은 폐기됐다. 지금은 매 턴 관련도 점수 상위(기본 임계 3.0·최대 2개)만 본문 그대로 자동 주입되고, 나머지는 이름+한줄 설명 인덱스로만 실려 모델이 필요하면 온디맨드로 불러온다. prefix caching 이점은 상위 스킬·인덱스가 매 턴 반복되는 한 유지된다. 상세는 [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md) 정본.
- ★ 컨텍스트 관리는 **저장된 세션 히스토리 원본을 바꾸지 않는다**. `ConversationContextManager`가 매 LLM 호출 직전에만 provider-facing view를 만든다.
- ★ 스킬 생성/개선은 계속 HITL 경로만 탄다. `propose_skill`은 스테이징만 하고, 실제 저장은 `skill_patch_proposed` 승인 뒤에만 일어난다.

동시 세션은 **1개**(단일 활성 대화, 프로토타입)로 한다.

### 2.1 Tool 노출·조립 (스키마 게이팅)

> 출처: [HERMES_TOOL_SYSTEM_REFERENCE.md](HERMES_TOOL_SYSTEM_REFERENCE.md) §2·§3. Hermes의 registry + `check_fn` + toolset 구조에서 RICE에 필요한 부분만 차용.

매 턴 시스템 프롬프트를 구성할 때 LLM에 넘길 tool 스키마는 **한 곳에서 모아 거른다**(Hermes `get_tool_definitions()` 대응). 책임 모듈은 `agent/tool_dispatch.py`([RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md) #2) — 호출 라우팅뿐 아니라 **스키마 조립 + 가용성 필터**를 소유한다.

- **수집 대상**: built-in tool + 활성 plugin이 요구하는 tool + 연결된 Connector(MCP) tool
- **builtin 도구군(2026-07 확장)**: 파일/검색/터미널/비전/웹 기본 도구 외에 **office 문서 생성**(`create_presentation`/`create_workbook`/`create_document`, stdlib-only OOXML 라이터, 승인 필요) builtin이 추가됐다 — 전체 목록은 [RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md) `agent/builtins/` 트리가 정본.
- **조건부 노출 (`check_fn` 차용·단순화)**: tool은 **사용 가능할 때만** 스키마에 포함한다.
  - **연결 안 된 Connector의 tool은 스키마에서 제외** — 예: Gmail 미연결이면 `gmail_*` tool을 LLM에 노출하지 않는다(못 쓸 도구로 인한 토큰 낭비·환각성 호출 방지).
  - Hermes의 30s TTL 캐시는 **생략** — RICE는 단일 활성 세션이라 턴 시작에 연결 상태를 직접 조회하면 충분.
- **노출 ≠ 실행**: 이 게이팅은 *노출*(스키마 포함 여부)이다. 9.4장 Permission 검증·5장 실행 승인(HITL)은 *실행*(호출 직전) 단계로, 서로 **별개의 방어선**이다.
- **플랫폼별 toolset은 제외**: Hermes의 `hermes-telegram`류 묶음은 데스크톱 단일 접점인 RICE엔 불필요.
- **예산 초과 대응 구현됨(2026-07-07 개편)**: MCP/plugin tool 스키마가 토큰 예산(~15K tokens)을 넘으면 core builtin은 그대로 노출하고, non-core tool은 `tool_search`/`tool_describe`/`tool_call` bridge 3종으로 접는다 — 단 **접힌 도구 중 이번 턴 사용자 메시지와 관련도가 높은 상위 도구(기본 최대 8개·4K 토큰)는 bridge 옆에 스키마 그대로 함께 노출**된다(순수 전량 폴딩은 폐기 — 소형 모델이 `tool_search` 호출을 스스로 안 배우면 도구 자체를 못 쓰는 실패가 잦았다). 관련도 스코어링은 결정론적 매칭 사다리(`core/lexical.py`)를 쓴다 — 시멘틱 임베딩은 로컬 8GB 최소사양 트레이드오프상 의도적으로 채택하지 않았다(2026-07-07 적대적 리뷰 최종 결론). 노출 방식이 접혀도 실제 실행 시 권한/HITL은 그대로 적용된다. 상세는 [RICE_AGENT_EXECUTION.md](RICE_AGENT_EXECUTION.md) §5.2가 정본.

### 2.2 Agentic Context Management

> 출처: Gemini CLI `ContextManager`/`PipelineOrchestrator`의 핵심 아이디어를 RICE 런타임에 맞게 재구현한다. 전체 graph pipeline을 복제하지 않고, 로컬 데스크톱 agent에 필요한 provider-facing 렌더링 계층만 둔다.

**책임 모듈**: `agent/context_manager.py` (`ConversationContextManager`)

- **source of truth는 세션 transcript**다. `SessionManager.history()`의 원본 메시지는 유지하고, LLM 호출 직전에만 렌더링된 사본을 만든다.
- **긴 히스토리 압축**: `ContextConfig.max_tokens`/`retained_tokens`를 넘으면 오래된 메시지를 system prompt의 `Compressed Conversation Context` 요약으로 접고 최근 메시지는 보호한다.
- **tool output distillation**: 큰 tool 결과는 모델에 다시 보낼 때 요약/절단한다. 단, DB에 저장된 tool result 원본은 유지한다.
- **시크릿 마스킹**: token/API key/password류 문자열은 provider-facing context에서 `[REDACTED]` 처리한다.
- **provider wire 안전성**: 렌더링 결과가 tool message로 시작하지 않게 정리해 Claude/OpenAI/Ollama의 tool_result adjacency 제약을 깨지 않도록 한다.
- **설정**: `core/config.py::ContextConfig`가 `enabled`, `max_tokens`, `retained_tokens`, `recent_messages`, `subagent_history_messages`, tool/message distillation threshold, `subagent_timeout_seconds`(기본 300)·`subagent_max_concurrent`(기본 3)·`subagent_max_tool_rounds`(기본 6 — sub-agent read-only 미니 ReAct 루프 상한, 2026-07-04 추가. 이전엔 `approval_timeout_seconds`를 임시로 빌려 쓰다 분리)를 가진다. `/settings`는 `context` 섹션을 조회/갱신한다.

이 계층은 learning loop와 분리된다. 컨텍스트 압축은 "모델에게 이번 호출에 무엇을 보여줄지"의 문제이고, 스킬/메모리 생성은 `learning` 모듈의 승인 기반 자기개선 문제다.

### 2.3 ToolScheduler

**책임 모듈**: `agent/tool_scheduler.py`

Gemini CLI Scheduler의 상태 머신 전체를 옮기지는 않는다. RICE는 이미 `ToolDispatcher`가 tool lookup, connector/plugin routing, permission, HITL parking을 담당하므로, Scheduler는 다음 조율만 맡는다.

- 한 assistant tool-use round 안에서 **독립적이고 승인 불필요한 tool call**은 병렬 batch로 실행한다.
- `requires_approval`, `wait_for_previous=true`, `terminal`, `write_file`, `propose_skill` 같은 순서/효과가 중요한 tool은 순차 실행한다. `tool_call` bridge는 스케줄러가 접힌 대상의 `parallel_safe`를 볼 수 없으므로 wrapper 자체를 보수적으로 순차 실행한다.
- 일반 도구의 SSE `tool_call`/`tool_result`는 실행 중 그대로 흘린다. 단, `tool_call` bridge는 하나의 논리 실행으로 정규화해 내부 `:bridged` envelope를 숨기고, abort 없이 결과까지 도달하면 outer id를 공유하는 요청 대상 이름·인자의 call/result 한 쌍만 공개한다(승인 제어 이벤트는 전달). provider용 LLM history에는 모델이 낸 wrapper를 보존하고 원래 tool call 순서대로 result를 붙인다. 상세 계약은 [RICE_AGENT_EXECUTION.md](RICE_AGENT_EXECUTION.md) §5.2와 [SSE event schema](rice-runtime/contracts/sse-events.schema.json)가 정본이다.
- abort 신호가 오면 대기/실행 중인 batch를 취소하고 safe error result를 남긴다.

### 2.4 Sub-agent Delegation

**책임 모듈**: `agent/subagents.py`, `agent/builtins/subagent_tools.py`

`delegate_subagent`는 main agent가 독립적인 분석/리서치/검토 작업을 하위 agent에게 맡길 수 있게 하는 builtin tool이다.

- sub-agent는 같은 provider를 사용하며, 격리 미니 toolset(`read_file`/`list_dir`/`search_files`/`fetch_url` + learning 배선 시 `skill_search`/`skill_view` + thread 격리 `write_scratch`)을 받는다. 메인 모델이 `tools` 화이트리스트로 표면을 더 좁힐 수 있다. 일반 쓰기·셸·재위임 등 외부 효과는 main agent의 기존 tool/HITL 경로로만 수행하며, `write_scratch`는 `.subagent/<thread>/` 격리 영역 밖으로는 절대 쓰지 못한다(사용자 가시 영역 승격은 메인의 승인 게이트 도구 몫).
- sub-agent는 최근 대화 컨텍스트를 렌더링된 view로 받고, `{findings, evidence, confidence, open_questions, recommended_next_action, tools_used, artifacts}` 구조화 결과를 반환한다 — `artifacts`는 워커가 실제 남긴 scratch 파일 경로들(러너 관찰 기반)이고 메인이 `read_file`로 읽는다. 실행 모델 상세는 AGENT_EXECUTION §6 정본.
- main agent가 여러 `delegate_subagent` 호출을 한 round에 내면 ToolScheduler가 안전하게 병렬화하고, 다음 LLM iteration에서 main agent가 결과를 종합한다.
- 이 기능은 learning loop를 우회하지 않는다. sub-agent 결과가 스킬화될 가치가 있으면 main agent가 기존 `propose_skill` → `skill_patch_proposed` 승인 경로를 사용한다.

---

## 3. Memory / User Modeling (builtin)

> Honcho 제외에 따라 로컬 구현. RICE의 "개인화" 데모를 책임지는 부분.

| 메모리 종류 | 저장 | 용도 |
|------------|------|------|
| 사실 메모리 (facts) | SQLite | "사용자는 K8s 환경 사용" 같은 선언적 사실 |
| 선호 프로필 (preferences) | SQLite | 답변 스타일(간결/상세), 선호 모델/도구 |
| 세션 히스토리 | SQLite + FTS5 | 과거 대화 검색 (session_search) |

- ★ Hermes의 메모리 작성 가이드 차용: 선언적 사실만 저장, 절차는 스킬로 ("Project uses pytest" ✓ / "Run tests with pytest -n 4" ✗)
- ★ prefetch 전략: 턴 시작 시 최근 사실 N개 + 쿼리 FTS 매칭 상위 K개 주입

**검색(retrieval)은 결정론적 키워드/lexical 매칭만 — 임베딩 없음, 알려진 한계와 대응.** 임베딩/시맨틱 검색은 제외한다(on-prem·8GB 최소사양 로컬 제품에 안 맞는 트레이드오프 — 2026-07-07 적대적 리뷰 최종 결론, `core/lexical.py` 정본 근거). 사용자 표현이 스킬/메모리의 어휘와 어긋나면 관련 항목이 **있어도 안 끌려온다**(= `skill_hit=false`) → "쓸수록 똑똑해짐"이 조용히 실패할 수 있다. **MVP는 retrieval을 완전히 견고화하지 않고, 위험을 *제한·진단·격리*한다:**

- **매칭 엔진은 대상별로 다르다(2026-07-07 개편 — 스킬 쪽 알고리즘이 바뀌었다).** 메모리(`learning/memory/store.py`)·세션 히스토리는 FTS5 전문검색을 그대로 쓴다. **스킬은 더 이상 FTS5가 아니라** `core/lexical.py`(exact→substring→char-bigram fuzzy→ko→en 의도어 확장)로 매칭하고, 상위 소수만 자동 본문 주입한다(위 2장 갱신 참고) — "예산 초과 시에만 작동하는 구간형 안전장치"가 아니라 매 턴 작동하는 주경로가 됐다는 점이 이전 서술과의 핵심 차이다. frontmatter(name·tags·description)가 매칭 재료인 것은 공통.
- **`skill_hit`으로 분리 진단** — warm인데 `skill_hit=false`면 "학습 실패"가 아니라 "매칭 실패"다([RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md) 2장). 데모 전 이 지표로 매칭 구멍을 사전 점검한다.
- **업그레이드 솔기 — 사실상 닫힘**: 검색 정밀화(임베딩/시맨틱)로의 교체는 인터페이스상(`learning/memory/search.py`+`learning/skill/relevance.py`+`ILearningService`) 여전히 가능하지만, 채택 여부는 이미 **기각 결정**이 나 있다(위 참고) — "향후 후보"가 아니라 "의도적으로 안 감" 쪽에 가깝다.

---

## 4. Skill Lifecycle

- 스킬 형식: ★ Hermes와 동일한 `SKILL.md` + frontmatter (name, version, tags, description — 상세 스키마는 [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md), 마켓 artifact와 호환되도록 CONTRACTS 문서와 정렬)
- 생성 트리거: `ReviewGate`/`ReviewEngine` 사후 리뷰, 명시적 `/learn`, 미드턴 `propose_skill` 도구. 시스템 프롬프트 가이드는 모델 행동을 유도하지만 저장 경로 자체는 승인 기반 도구/리뷰 파이프라인이 담당한다.
- 텔레메트리: use/view/patch 카운터 + 사이드카 JSON (Hermes 검증과 동일 패턴)
- 상태 전이(transition): active → stale → archived — **stale 3일 / archive 7일** (설정으로 노출)
  - `stale`은 경고가 붙은 사용 가능 상태로 검색·관련도 주입을 계속하며, 실제 관여 시
    active로 자동 복귀한다. 사용자의 수동 재활성화도 freshness 시계만 리셋한다(use는 불변).
    archived 및 수동 inactive만 검색·주입에서 제외한다.
  - ⚠️ 일(day) 단위 주기로 설정하면 짧은 데모에서는 자동 전이를 확인할 수 없음 → 시연은 수동 "스킬 정리"로 한다
- Curator: 자동 전이(규칙) + 수동 "스킬 정리" 트리거
- **개선(patch)** — 상태 전이와 **별개 관심사**다. 주입된 스킬의 절차가 틀리거나 낡으면 에이전트가 **개선안을 제안** → 사용자 승인(HITL) 후 본문 교체 + version++ + 이전 버전 `~/.rice/skills/<name>/.history/v<N>.md` 보관(롤백·diff). **무단 자동 수정 없음.** 메커니즘 정본은 [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md) 5.2, 승인 전송은 5장(HITL 승인 메커니즘), 계약(`skill_patch_proposed`·`skill_patched`·`/skills/{id}/patch|history|rollback`)은 [RICE_CONTRACTS.md](RICE_CONTRACTS.md) 1.2·1.3.
- **저작 판단(review)과 세션 간 발견(discovery)은 별도 서브시스템이다(2026-07-02~03 추가, 이 문서 최초 반영)**: 스킬/메모리를 "만들 가치가 있는가"는 `learning/review/`(ReviewGate가 값싼 휴리스틱으로 LLM 호출 여부만 판단 → ReviewEngine이 실제 LLM 리뷰 1회 호출, 쓰기 권한 없이 제안만)가 맡고, 세션을 넘나드는 패턴 발견은 `learning/discovery.py`(최소 2세션, 생성 전용)가 맡는다. 둘 다 `learning/ledger.py`(append-only 감사로그 + 재시작 생존하는 제안 인박스)에 기록되고, `server/scheduler.py`가 주기적으로 트리거한다("Self-Improvement v3"). 상세 스펙은 [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md) 정본.
- **스킬 승격 전 결정론 검증 게이트(2026-07-07 추가)**: `learning/skill/verifier.py` — LLM 재채점 없이 lint 사다리로 스킬 생성/패치를 3중 관문(스테이징·LLM 제안 스크리닝·최종 디스크 쓰기)에서 검사한다(빈 본문·코드펜스 미폐합 등은 반려, thin-body·unknown-tool 등은 경고만). 상세는 LEARNING_LOOP_DESIGN 정본.

---

## 5. 코드 실행과 Sandbox 경계 (Connector · Plugin)

> Hermes 검증 #4의 핵심 교훈: **local 백엔드는 파일시스템/네트워크 격리가 없다.** 마켓에서 받은 코드를 그대로 local 실행하면 안 됨.
>
> 전제: 마켓 artifact 중 **skill(SKILL.md)은 선언적**이라 코드 실행이 없어 sandbox 대상이 아니다. 실제 코드 실행/외부 접근은 ① 에이전트의 **`terminal`** builtin(이 문서 초판 명칭은 `execute_code`였으나 구현된 도구명·경계 모두 다르다 — 아래), ② **Connector(MCP) 레이어와 plugin이 포함한 connector 코드**에서 발생하며, 이 장은 그 경계를 정의한다.

| 실행 대상 | 신뢰 수준 | ★ 실행 방식 (실측, 2026-07-08 동기화) |
|----------|----------|------------|
| built-in tool(파일 read/write/search 등) | 우리가 작성 | 프로세스 내 직접 실행 |
| **`terminal` builtin(에이전트가 만든 셸 명령 실행)** | 에이전트 생성 · **호스트 셸** | `agent/builtins/terminal_tool.py`의 실제 설계: cwd 제한도 별도 자식-프로세스 격리도 **없는 호스트 셸 직접 실행**(코드 주석: "cwd 제한은 방어가 아니다"). **유일한 실제 방어선은 `requires_approval=True` HITL 승인** — env는 allowlist가 아니라 알려진 자격증명 키 ~20종 **denylist**로만 거른다 |
| skill (SKILL.md) | 선언적 | 코드 실행 없음 → sandbox 대상 아님 |
| plugin이 선언한 자체 실행 도구(`entrypoint.tools`) | 마켓 설치 코드 | `execution/sandbox/`(`ChildProcessSandbox`) — **자식 프로세스 + env allowlist(10개 무해 키만 통과) + RLIMIT_CPU/AS + timeout+kill**. 아래 원래 설계대로 실제 격리를 갖춘 유일한 경로 |
| Connector(MCP 서버) 코드 | 원격 프로세스(RICE 프로세스 밖) | RICE는 MCP 프로토콜(HTTP/stdio)로만 통신 — 코드 자체를 RICE가 실행하지 않는다(로컬 stdio MCP는 사용자가 `.mcp.json`에 등록한 커맨드를 spawn) |

**알려진 격차(코드 변경 필요)**: 위 표의 `execute_code` 행이 원래 정의했던 "자식 프로세스+작업 repo cwd 한정+env 스크럽+타임아웃"은 `terminal`에는 **채택되지 않았다** — `terminal_tool.py` 자체 코드 주석이 이를 명시적으로 인정한다. 이번 동기화는 문서를 실제 구현에 맞춰 정정한다. 원안 수준의 격리(자식 프로세스+cwd+env allowlist)로 강화할지, 승인 게이트만으로 충분하다고 볼지는 별도 제품/보안 판단 대상이다.

**실행 방식**: 마켓에서 설치한 plugin·Connector(MCP) 코드는 **자식 프로세스 + env 스크럽**으로 실행하고, "데모 한정, 신뢰된 사내 artifact만"을 전제로 둔다 — 데모 대상이 조직 내부에서 큐레이션된 artifact이기 때문. 제품화 시 Docker 컨테이너(CPU/메모리 제한)로 전환은 ADR로 기록.

> 검토했던 대안: Docker 실행(안전하나 사용자 PC에 Docker 요구 → 데모 제약), 선언적만 허용(가장 안전 — skill은 이미 선언적이나, Connector(MCP) 실행이 막혀 외부 연동 데모 약화).

**Tool 실행 승인 (human-in-the-loop)**: Connector(MCP)·plugin이 제공하는 tool, 그리고 `terminal`(위 격차 때문에 모든 호출이 승인 대상 — 유일한 방어선이다) 실행 전, 사용자 승인을 받는다. 승인 요청은 SSE `tool_approval_required` 이벤트로 보내고 앱이 `POST /sessions/{id}/tool-approval`로 응답한다(CONTRACTS 1.2). 승인 **UI 구현은 개발 영역**이며, 본 문서는 승인 **시점(실행 직전)·실행 경계(위 표)** 를 정의한다. 전송 메커니즘(아래 5.1)은 스킬 개선 승인(`skill_patch_proposed`, LEARNING_LOOP 5.2)과 **동일**하다.

### 5.1 HITL 승인 메커니즘 — parking · timeout · 방치 · 재연결

> 적용 대상: ① tool 실행 승인(`tool_approval_required`), ② 스킬 개선 승인(`skill_patch_proposed`). 둘 다 "에이전트 턴 **도중** 사용자 결정을 기다린다"는 동일 문제 → **단일 메커니즘**으로 처리한다.

**전송 방식 (재검토 완료 — SSE+POST 고수).** SSE는 단방향, 승인은 요청-응답이라 *이 한 케이스만* 보면 WebSocket이 더 자연스럽다는 지적이 있었다. 그러나 (a) 승인은 저빈도·단발 상호작용이고, (b) WS는 전 구간 구현·재연결 비용을 올리며, (c) 아래 parking 설계로 SSE+POST의 함정이 닫힌다 → **SSE+POST 유지**(전송 결정 CONTRACTS 1.2와 일관). 대신 함정(timeout·방치·재연결)을 **명시 설계**한다 — 미설계 시 데모에서 가장 잘 깨지는 지점이기 때문.

- **parking (턴 정지·재개).**
  1. 승인 지점에서 런타임이 고유 **`approval_id`** 를 만들어 SSE 제어 이벤트에 실어 보낸다.
  2. 에이전트 루프는 `ApprovalRegistry`(`server/approval.py`)에 `approval_id → asyncio.Future`를 등록하고 그 Future를 `await` — 스레드 블로킹이 아닌 **async 일시중단**(다른 I/O는 계속).
  3. 앱이 `POST /sessions/{id}/tool-approval` 또는 `POST /skills/{id}/patch`를 **`approval_id` 포함**해 보내면, 핸들러가 Future를 resolve(approve/reject/edited_body) → 루프 재개.
- **timeout (방치 방지).** 각 Future에 **기본 5분**(설정값) 타임아웃. 만료 시 **안전 기본값 = 거부/취소**로 resolve:
  - tool 승인 만료 → 위험 tool **실행 안 함**, 루프엔 "미승인으로 건너뜀" 주입(또는 턴 종료)
  - skill patch 만료 → **적용 안 함**(본문 불변), `timeout`/`disconnected`이면 제안함(`/skills/suggestions`)으로 보존
- **방치(abandonment)·중복.** 세션 `POST /sessions/{id}/abort` 또는 `POST /shutdown` 시 해당 세션의 **대기 Future 전부 취소**로 resolve + 정리(고아 방지). 이미 resolve/만료된 `approval_id`로 **늦게/중복** 도착한 POST → **409/410 idempotent no-op**. 레지스트리는 인메모리로 충분 — 단일 활성 세션(2장)이라 동시 대기 승인은 소수이고, 런타임 재시작 시 소실돼도 무방(데모용).
- **SSE 끊김·재연결 상관.** 승인 상태는 **SSE 연결이 아니라 레지스트리(서버측)에 산다** → 대기 중 SSE가 끊겨도 루프는 계속 `await`(Future는 연결과 독립), POST는 새 연결로 와도 된다. 재연결 시 앱이 놓친 프롬프트를 복구하도록 **`GET /sessions/{id}/pending-approvals`**(대기 중 `{approval_id, kind, payload}` 목록) → 카드 재렌더. 제어 이벤트 재전송 대신 **폴링형 복구**(SSE+POST 패턴과 일관).

→ 계약 반영(CONTRACTS 1.2): 제어 이벤트에 **`approval_id`** 추가 · 승인 POST 본문에 `approval_id` 포함 · **`GET /sessions/{id}/pending-approvals`** 추가 · 늦은/중복 POST = 409/410. 모듈 위치는 `server/approval.py`([RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md)).

---

## 6. 모델 프로바이더 & 키 보관 (REPO_STRATEGY #3 해소)

서비스 형태 관점으로 **4개 provider**를 지원한다("모델 선택" UI가 조합을 다룬다) — 애초 "Phase 2"로 미뤄뒀던 OpenAI 호환 게이트웨이 추상화는 이미 구현·병합됐다:

| 형태 | Provider | 모델 (프로토타입) | 접속/인증 |
|------|----------|-------------------|-----------|
| **SaaS** | Claude (Anthropic API) | Claude Sonnet 4.5 | API 키 → OS 키체인, prompt caching 지원 |
| **on-prem** | Ollama (로컬 모델) — **메인 대상** | Qwen3.6-27B / Qwen3.5-9B (MLX) / gpt-oss:20b | `base_url` (키 불필요), 사내·오프라인 |
| **SaaS** | OpenAI 호환(`agent/providers/openai.py`) | 사용자 등록 모델(`registered_models`) | API 키 → OS 키체인 |
| **폴백** | echo | 모델 미설정 시 | 키 불필요, 플래너 등 LLM 호출 스킵 |

> 모델명은 프로토타입 기준이며 수시 교체된다 — 고정값으로 읽지 말 것. (※ Claude 최신은 Sonnet 4.6(`claude-sonnet-4-6`) — 필요 시 교체)

**Provider 차이 정규화** — `ILLMProvider` Protocol이 다음을 흡수한다:
- **tool calling**: Claude native `tool_use` ↔ Ollama function-calling(모델별 상이·미지원 가능) → 공통 tool_call 표현으로 정규화, 미지원 모델은 프롬프트 기반 폴백
- **streaming**: 각 provider 스트림 → 공통 **SSE 이벤트**(9종, 2026-07-04 `plan_updated` 추가 — 명세 정본 [RICE_CONTRACTS.md](RICE_CONTRACTS.md) 1.3)로 변환
- **prompt caching**: Claude `cache_control`(스킬 주입 비용 상쇄, Hermes 검증 #6) ↔ Ollama 미지원 → keep-alive·컨텍스트 재사용으로 대체

- ★ 키 보관: SaaS provider API 키는 OS 키체인(macOS Keychain / Windows Credential Manager)에 저장한다. `.env` 평문 저장은 금지한다. on-prem(Ollama)은 키가 없다.

---

## 7. 로컬 데이터 모델 (SQLite 초안)

```
~/.rice/
  state.db        # 세션/메시지/메모리 (WAL 모드)
  skills/         # SKILL.md 디렉토리 + .usage.json 사이드카 (학습 생성 + 마켓 설치 skill)
  plugins/        # 설치된 plugin (skills + 요구 Connector)
  connectors/     # Connector(MCP) 연결 메타 (credential은 OS 키체인)
  config.yaml     # 설정
  logs/
```

```sql
-- state.db 핵심 테이블 (초안)
sessions(id, title, model, started_at, ended_at, archived)
messages(id, session_id, role, content, tool_calls, ts)  + FTS5
memories(id, kind, content, created_at, source_session_id)
installed_artifacts(id, type, name, version, source, installed_at)  -- type: skill|plugin (MySkills / MyPlugins)
attachments(id, message_id, kind, path, created_at)                 -- 설계 초안 — 2026-07-08 기준 업로드 API·MessageIn 필드 모두 미구현(CONTRACTS 정본, 11장 참고)

-- Connector 관련 테이블
connectors(
  id TEXT PRIMARY KEY,
  connector_type TEXT NOT NULL,  -- 실제 구현(2026-07 기준): 'gmail'|'calendar'|'drive'|'local:<name>'|'custom_<slug>'
  display_name TEXT,
  status TEXT,                    -- 'connected', 'disconnected', 'error'
  connected_at TIMESTAMP,
  last_verified_at TIMESTAMP
)

connector_credentials(
  connector_id TEXT PRIMARY KEY,
  -- 실제 credential은 OS 키체인에 저장, 이 테이블은 메타데이터만
  token_expires_at TIMESTAMP,
  scopes TEXT,                    -- JSON array
  FOREIGN KEY (connector_id) REFERENCES connectors(id)
)
```

> 스키마는 CONTRACTS 문서의 artifact manifest와 `installed_artifacts`를 정렬해 유지한다.

---

## 8. 패키징/배포 (REPO_STRATEGY #1 해소)

### 8.0 배포 형태 (3 배포물)

| 배포물 | 형태 | 위치/엔드포인트 |
|--------|------|-----------------|
| `rice-app` | Electron 앱 (.app / .exe) — React UI + runtime supervisor | 사용자 PC |
| `rice-runtime` | PyInstaller 단일 바이너리 | `~/.rice/bin/rice-runtime`, 데이터 `~/.rice/`, 실행 `rice-runtime --port {동적 할당} --token {1회용}` |
| `rice-platform` | Docker 컨테이너 (FastAPI) | 클라우드(AWS/GCP), `https://platform.rice.app` |

> 포트는 고정값이 아니라 Electron이 가용 포트를 골라 주입한다(CONTRACTS 문서 1.1). `rice-app`이 `rice-runtime` 자식 프로세스의 spawn/health/종료를 책임진다.

### 8.1 런타임 배포 방식

Python 런타임은 **PyInstaller 단일 바이너리**로 배포한다 (아래 비교, 1~2일 스파이크로 의존성 검증):

| 방식 | 장점 | 단점 |
|------|------|------|
| ★ **PyInstaller 단일 바이너리** | 사용자 PC에 Python 불필요, Electron이 사이드카로 spawn하기 단순 | 빌드 파이프라인 초기 비용, 네이티브 의존성 이슈 가능 |
| 임베디드 Python 동봉 | 유연함 | 용량 큼, Windows/macOS 분기 복잡 |
| uv 런타임 설치 | 개발 중 편함 | 사용자 머신에 설치 행위 필요 — 데모 배포에 부적합 |

- ★ 권장 진행: **1~2일 스파이크**로 PyInstaller가 의존성(SQLite/FTS5, HTTP 서버)과 충돌 없는지 검증 후 확정
- ★ Electron 배포: electron-builder, 코드사인은 데모 단계 생략(macOS는 ad-hoc sign + 안내)
- 자동 업데이트는 **제외**한다

---

## 9. Connector Management (신규 추가)

> ConnectorsScreen 지원을 위한 외부 서비스 연결 관리

### 9.1 개요

**목적**: Plugin/에이전트가 외부 서비스에 접근할 수 있도록 OAuth 기반 연결 관리. 아래 9.2 OAuth 흐름이 정본이며, 실제 구현은 Gmail 하나를 훨씬 넘어섰다(2026-07-01): **Google 계열 3종(Gmail·Calendar·Drive, 같은 OAuth client 공유) + 사용자가 등록하는 로컬 MCP(stdio/http, `.mcp.json`) + 커스텀 원격 MCP 커넥터(OAuth 자동 디스커버리, RFC 9728/8414/7591)**까지 구현이 확장됐다. 나머지(Slack·Microsoft 등)는 여전히 동일 OAuth 골격에 connector_type 디스크립터만 추가하면 되는 확장 포인트로 남아 있다. 모듈 트리는 [RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md) `connector/`가, 엔드포인트는 [RICE_CONTRACTS.md](RICE_CONTRACTS.md)가 정본.

**구성 요소**:
- Connector Manager(connector/manager.py) + Service 파사드(connector/service.py — list_tools/invoke/permission/update-check 수렴점)
- OAuth Flow Handler(connector/oauth/) + 자동 디스커버리(connector/discovery.py) + 커스텀 등록(connector/custom_registry.py)
- 원격 MCP 클라이언트(connector/mcp/) + 로컬 MCP(connector/local/)
- Credential Storage (connector/credential/keychain.py)
- Permission System (connector/permission/checker.py) + 도구별 allow/approval/block 정책

### 9.2 OAuth 2.0 흐름

```
1. POST /connectors { connector_type: "gmail" }
   → OAuth URL 생성 (state 토큰 포함)
   
2. 앱이 시스템 브라우저로 OAuth URL 열기
   → 사용자 인증 및 권한 승인
   
3. Redirect → GET /connectors/oauth/callback?code=...&state=...
   → state 검증 후 authorization code로 access_token 교환
   
4. Credential 저장 (OS 키체인)
   → access_token, refresh_token, expires_at
   
5. Connector 상태 업데이트 → "verifying"
   → tools/list 동기화 + 안전한 read-only 도구 1회 실제 호출(health probe)
   
6. probe 성공 → "connected" / 실패 → "error" + error_code/error_detail 영속화
   → POST /connectors/{id}/verify 로 재연결 없이 재검증 가능
```

**실행 검증이 필요한 이유(2026-07-16)**: Google MCP 서버(gmailmcp 등)는 `tools/list`가
IAM 없이 성공하지만 `tools/call`은 (a) 프로젝트에 `mcp.tools.call` 권한
(`roles/mcp.toolUser`), (b) `x-goog-user-project` 헤더(quota project — client_secret
JSON의 project_id 또는 client_id 숫자 접두에서 파생해 런타임이 자동 주입),
(c) Workspace Developer Preview 접근을 추가로 요구한다. 그래서 "도구 목록을
받아왔다"는 연결 성공의 증거가 아니며, health probe(gmail=list_labels,
calendar=list_calendars)가 통과해야 connected로 확정한다. 진단 코드
(`connectors.mcp_service_permission_denied` / `mcp_service_disabled` /
`mcp_scope_insufficient` 등)는 migration 0015의 error_code/error_detail 컬럼에
저장되고 UI가 복구 안내로 변환한다. OAuth 동의화면이 테스트(미게시) 단계인 것
자체는 차단 사유가 아니다(테스트 사용자 허용).

### 9.3 Credential 저장

- **저장소**: OS 키체인 (macOS Keychain / Windows Credential Manager)
- **라이브러리**: `keyring` Python 패키지
- **키 형식**: `rice.connector.{connector_id}.{credential_type}`
- **자동 갱신**: Runtime이 백그라운드에서 refresh_token으로 갱신

### 9.4 Permission 검증

Plugin manifest의 `requires.connectors` 필드를 기반으로 검증한다. 필드는 중첩 형태 `requires.connectors`로 통일한다(CONTRACTS 문서 2.1과 동일). 값은 **connector_type 목록**(예: `"gmail"`)이며, 인스턴스 식별자 `connector_id`와 혼동하지 않는다.

```python
# connector/permission/checker.py
def check_plugin_permission(plugin_id: str, connector_type: str) -> bool:
    manifest = load_plugin_manifest(plugin_id)
    required = manifest.get("requires", {}).get("connectors", [])  # connector_type 목록
    return connector_type in required
```

**검증 시점**:
1. Plugin 설치 시: UI에 필요한 Connector 목록 표시
2. Plugin 활성화 시: 모든 필요한 Connector 연결 확인
3. Tool 실행 시: Runtime이 접근 권한 검증

---

## 10. 보안 베이스라인

1. **로컬 API 인증**: 런타임 spawn 시 일회성 토큰 발급 → 앱만 접근 가능 (포트 스캔하는 타 프로세스 차단)
2. **시크릿**: OS 키체인 (6장, 9.3장), 로그에 키/토큰 출력 금지
3. **마켓 artifact 실행 경계**: 5장 결정에 따름 + artifact 체크섬 검증 (CONTRACTS 문서)
   - **Phase 2 백로그 (MVP 밖, 결정 아님)**: 체크섬만으로는 위변조 방지가 약함 → 디지털 서명(GPG/X.509) + platform 측 서명 키 관리 + runtime 측 신뢰 체인 검증 (우선순위 🟢 낮음, 담당: 보안 팀 + Platform 팀)
4. **Connector Credential**: OS 키체인 저장, 메모리에서 즉시 삭제, 로그 금지
5. **신뢰불가 외부 콘텐츠 격리 (프롬프트 인젝션 방어)**: Connector로 가져온 **외부 콘텐츠(예: Gmail 메일 본문)는 신뢰할 수 없다** — 공격자가 메일에 심은 지시문이 에이전트를 탈취해 위험 tool(`terminal`·파일 삭제·네트워크)을 호출하게 만들 수 있다(Hermes webhook-safe toolset의 교훈, [HERMES_TOOL_SYSTEM_REFERENCE.md](HERMES_TOOL_SYSTEM_REFERENCE.md) §6.3 재해석). 대응: (a) 외부 콘텐츠는 **데이터로 명시 구분**(지시문으로 취급하지 않음), (b) 위험 tool 실행은 5장 HITL 승인을 **반드시** 거치게 함, (c) 향후 외부 콘텐츠 주도 턴에서 위험 tool **노출 자체를 축소**(restricted toolset). MVP는 (a)+(b)로 최소 방어, (c)는 11장 백로그.

---

## 11. 향후 확장 (UI 로드맵 대응)

> 설계는 UI 상위집합이다. 현재 프로토타입 UI에 있으나 MVP 범위가 미정인 것 + 아직 UI에 없는 확장 후보를 추적한다.

- **멀티모달/첨부 처리 (설계 확정, 구현은 아직 — 2026-07-08 확인)**: ChatScreen 파일 첨부 → **xlsx·png만** 첨부 저장(`~/.rice/attachments/`) + provider 멀티모달 전달이 **설계**이지만, `POST /sessions/{id}/attachments` 라우트도 `MessageIn.attachment_ids` 필드도 코드에 없다(CONTRACTS 정본 — 프론트 `sse.ts` 주석도 동일하게 미래형으로 남아 있음). 착수 시 CONTRACTS 1.1·1.3과 함께 갱신할 것
- **Skill 작성·게시**: 사용자 제작 skill(SKILL.md)을 platform에 publish(`POST /artifacts`) — **UI는 이미 존재한다**(`SkillAuthorModal`·`SkillPublishDialog`, SkillMarketScreen/MySkillsScreen에 배선됨, PRODUCT_IDEA 정본)지만 로컬 React state만 갱신하고 실제 `POST /artifacts` 호출은 아직 없다(2026-07-08 확인) — 남은 일은 UI 신설이 아니라 백엔드 연동
- **Connector 제공 도구 가시화**: 연결된 Connector(MCP)가 노출하는 원자적 tool 목록을 ConnectorsScreen에서 확인
- **artifact 디지털 서명**: 10장 Phase 2 분석 항목
- **세션 검색·관리 보강**: FTS5 기반 세션 검색 UI, 이름변경/보관
- **Tool Search UI/가시화 보강**: runtime은 이미 plugin/Connector tool schema 예산 초과 시 `tool_search`/`tool_describe`/`tool_call` bridge로 접는다. 향후 UI에서 "접힌 도구 카탈로그"와 검색 결과를 개발자/고급 사용자에게 보여줄 수 있다.
- **MCP 동적 갱신 (Phase 2)**: MCP 어댑터 구현 시, 서버가 `notifications/tools/list_changed`를 보내면 해당 서버 tool을 전부 deregister 후 재등록(nuke-and-repave, [HERMES_TOOL_SYSTEM_REFERENCE.md](HERMES_TOOL_SYSTEM_REFERENCE.md) §7.3).
- **외부 콘텐츠 주도 턴의 restricted toolset**: 10장-5의 (c) — 신뢰불가 외부 콘텐츠가 주도하는 턴에서 위험 tool 노출을 축소.

---

> 모듈 구조의 **핵심 불변식**과 화면→모듈 매핑은 [RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md)(정본)를, 전체 구현 우선순위(다음 액션)는 [README.md](README.md)를 참조한다.
