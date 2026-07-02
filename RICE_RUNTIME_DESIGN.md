# RICE Runtime Design

> 작성일: 2026-06-12 (개정: 2026-06-26) · 상태: **확정 (결정 완료)**
> 범위: `rice-runtime`의 핵심 설계 + 패키징 + 보안 베이스라인
> 해소 대상(전건 확정): [RICE_REPO_STRATEGY.md](RICE_REPO_STRATEGY.md) 9장 #1, #3, #5, #6, #7
> 아키텍처 참조: [RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md)(모듈 구조 정본)

---

## 1. Hermes 차용 결정표

> Hermes 분석([HERMES_LEARNING_LOOP_REFERENCE.md](HERMES_LEARNING_LOOP_REFERENCE.md) · [HERMES_TOOL_SYSTEM_REFERENCE.md](HERMES_TOOL_SYSTEM_REFERENCE.md)) 기준, 컴포넌트별로 차용/단순화/제외를 결정한다. 이 표가 runtime의 전체 스코프를 정의한다.

| Hermes 컴포넌트 | 결정 | 근거 |
|----------------|------|------|
| 대화 루프 (`run_conversation`) | ★ **차용** (구조만, 재구현) | learning loop의 뼈대. 12k줄 전체가 아니라 패턴만 |
| Skill Lifecycle (`skill_usage`) | ★ **차용** | RICE의 핵심 차별점. use/view/patch 카운터 + 상태 전이 |
| 스킬 생성 트리거 | ★ **차용** (프롬프트 가이드 방식) | Hermes 검증: 별도 로직 없이 시스템 프롬프트 가이드로 충분히 동작 ("5+ tool calls 작업 후 저장") |
| Curator | ★ **단순화** — 자동 전이(규칙)만 차용, LLM 리뷰는 수동 트리거 버튼으로 | 기본 7일 주기는 데모에서 절대 안 보임. UI에서 "스킬 정리" 버튼 → 즉시 실행이 데모에 유리 |
| Memory Manager | ★ **차용** (builtin 1개로 시작) | Hermes도 외부 provider 1개 제한. MVP는 builtin(로컬)만 |
| Honcho (외부 user modeling) | ★ **제외** | 원격 API 의존 — IBM 내부 데모에 외부 SaaS 부적합. user modeling은 로컬 구현 (3장) |
| `execute_code` (PTC) | ✅ **차용 (포함)** — repo 단위 코드 실행 도구로 제공 | Hermes도 코드를 생성·실행한다(스킬에 넣지 않을 뿐). RICE는 **repo 단위**로 작업하므로 에이전트가 생성한 코드를 작업 repo 안에서 실행하는 것이 자연스럽다. 실행 경계는 5장(자식 프로세스 + repo 작업 디렉토리 한정 + env 스크럽) |
| Plugin 시스템 (`register(ctx)`) | ★ **차용** (artifact 스펙은 CONTRACTS 문서) | Plugin Market의 전제 조건 |
| Cron Scheduler | ★ **제외** | 데모 범위 밖 |
| Gateway (메시징) | ★ **제외** | RICE는 데스크톱 앱 단일 접점 |
| FTS5 세션 검색 | ★ **차용** | 세션 간 컨텍스트 유지 데모에 직접 기여, SQLite 내장이라 비용 낮음 |
| Tool registry + 조건부 노출 (`check_fn`) | ★ **차용** (단순화) | built-in/plugin/connector tool을 한 곳에서 모아 LLM 스키마로 조립하고 **연결 안 된 Connector tool은 스키마에서 제외**. Hermes의 30s TTL 캐시는 생략(단일 세션, 연결 상태 직접 조회) — 2.1장 |
| Toolset (플랫폼별 묶음) | ★ **제외** | 데스크톱 단일 접점이라 telegram/discord류 플랫폼 toolset 불필요 |
| Tool Search (progressive disclosure) | ★ **연기 (Phase 2)** | MCP tool이 토큰 예산 초과 시 `tool_search/describe/call`로 접기. MVP는 Connector 1개라 미발생 — 11장 |
| Webhook-safe toolset (인젝션 방어) | ★ **차용 (재해석)** | RICE엔 webhook 없으나 **Gmail 본문 = 신뢰불가 외부 콘텐츠** → 동일 방어 적용 — 10장 |

위 차용표가 runtime 전체 스코프의 정본이다 — Honcho **제외**(외부 SaaS), Curator **수동 트리거** 포함, `execute_code` **포함**(repo 단위 코드 실행 도구, sandbox 경계는 5장). Hermes를 기준으로 가깝게 벤치마킹하며, 추후 개선·확장은 본 표를 갱신하는 방식으로 추적한다.

---

## 2. 대화 루프

```
요청 수신 (로컬 API, CONTRACTS 문서의 계약)
 → memory prefetch (관련 메모리/스킬 컨텍스트 로드)
 → 시스템 프롬프트 구성 (활성 스킬 주입 + 스킬 생성 가이드)
 → LLM 호출 루프 (tool_calls 처리, max_iterations 제한)
 → 스트리밍 응답 (SSE/WS로 앱에 전달)
 → 턴 종료: memory sync + skill usage 텔레메트리
```

- ★ max_iterations 기본값: 25 (Hermes는 사실상 무제한이나 데모 앱은 폭주 방지 필요)
- ★ 스킬 주입: Hermes처럼 시스템 프롬프트에 활성 스킬 전체 주입 + prefix caching 의존 (Hermes 검증 #6: 명시적 무효화 없이 자연 재캐시로 충분)

동시 세션은 **1개**(단일 활성 대화, 프로토타입)로 한다.

### 2.1 Tool 노출·조립 (스키마 게이팅)

> 출처: [HERMES_TOOL_SYSTEM_REFERENCE.md](HERMES_TOOL_SYSTEM_REFERENCE.md) §2·§3. Hermes의 registry + `check_fn` + toolset 구조에서 RICE에 필요한 부분만 차용.

매 턴 시스템 프롬프트를 구성할 때 LLM에 넘길 tool 스키마는 **한 곳에서 모아 거른다**(Hermes `get_tool_definitions()` 대응). 책임 모듈은 `agent/tool_dispatch.py`([RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md) #2) — 호출 라우팅뿐 아니라 **스키마 조립 + 가용성 필터**를 소유한다.

- **수집 대상**: built-in tool + 활성 plugin이 요구하는 tool + 연결된 Connector(MCP) tool
- **조건부 노출 (`check_fn` 차용·단순화)**: tool은 **사용 가능할 때만** 스키마에 포함한다.
  - **연결 안 된 Connector의 tool은 스키마에서 제외** — 예: Gmail 미연결이면 `gmail_*` tool을 LLM에 노출하지 않는다(못 쓸 도구로 인한 토큰 낭비·환각성 호출 방지).
  - Hermes의 30s TTL 캐시는 **생략** — RICE는 단일 활성 세션이라 턴 시작에 연결 상태를 직접 조회하면 충분.
- **노출 ≠ 실행**: 이 게이팅은 *노출*(스키마 포함 여부)이다. 9.4장 Permission 검증·5장 실행 승인(HITL)은 *실행*(호출 직전) 단계로, 서로 **별개의 방어선**이다.
- **플랫폼별 toolset은 제외**: Hermes의 `hermes-telegram`류 묶음은 데스크톱 단일 접점인 RICE엔 불필요.
- **예산 초과 대응은 Phase 2**: MCP tool이 토큰 예산을 넘으면 progressive disclosure로 접는다(11장). MVP는 Connector 1개라 미발생.

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

**검색(retrieval)은 FTS5만 — 알려진 한계와 MVP 대응.** 임베딩/시맨틱 검색은 제외한다(on-prem LLM 리소스 부담 + 패키징 복잡도 회피). 다만 FTS5 키워드 매칭이 이 시스템의 **무른 중심**임을 명시한다: 사용자 표현이 스킬/메모리의 어휘와 어긋나면 관련 항목이 **있어도 안 끌려온다**(= `skill_hit=false`) → "쓸수록 똑똑해짐"이 조용히 실패한다. **MVP는 retrieval을 견고화하지 않고, 위험을 *제한·진단·격리*한다:**

- **스킬은 검색에 거의 의존하지 않는다 — inject-all-active-under-budget.** 활성 스킬은 (토큰 예산 내라면) 시스템 프롬프트에 **전부 주입**한다(2장 · [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md) 7장). 데모 규모에선 활성 스킬 수가 적어 *랭킹/검색이 필요 없다.* 검색이 실제로 스킬을 **선별**하는 경우는 **예산 초과 시뿐**이고, 그때만 FTS5 랭킹의 취약성이 드러난다.
- **FTS5 색인 범위 = frontmatter(name·tags·description)까지** — 본문 키워드뿐 아니라 한 줄 description·tags로 매칭(LEARNING_LOOP 2장: description은 "prefetch 매칭용"). 메모리·세션 히스토리도 FTS5 전문.
- **`skill_hit`으로 분리 진단** — warm인데 `skill_hit=false`면 "학습 실패"가 아니라 "검색 실패"다([RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md) 2장). 데모 전 이 지표로 retrieval 구멍을 사전 점검한다.
- **업그레이드 솔기 (향후, MVP 밖)** — 검색 정밀화(임베딩/시맨틱)는 `learning/memory/search.py` + `ILearningService` 인터페이스 **뒤에서 교체** 가능하게 둔다([RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md) 핵심 불변식). 계약·UI 변경 없이 `skill_hit`만 끌어올리는 단일 솔기다.

---

## 4. Skill Lifecycle

- 스킬 형식: ★ Hermes와 동일한 `SKILL.md` + frontmatter (name, version, tags, description — 상세 스키마는 [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md), 마켓 artifact와 호환되도록 CONTRACTS 문서와 정렬)
- 생성 트리거: 시스템 프롬프트 가이드 (Hermes 검증 #1 문구 차용)
- 텔레메트리: use/view/patch 카운터 + 사이드카 JSON (Hermes 검증과 동일 패턴)
- 상태 전이(transition): active → stale → archived — **stale 3일 / archive 7일** (설정으로 노출)
  - ⚠️ 일(day) 단위 주기로 설정하면 짧은 데모에서는 자동 전이를 확인할 수 없음 → 시연은 수동 "스킬 정리"로 한다
- Curator: 자동 전이(규칙) + 수동 "스킬 정리" 트리거
- **개선(patch)** — 상태 전이와 **별개 관심사**다. 주입된 스킬의 절차가 틀리거나 낡으면 에이전트가 **개선안을 제안** → 사용자 승인(HITL) 후 본문 교체 + version++ + 이전 버전 `~/.rice/skills/<name>/.history/v<N>.md` 보관(롤백·diff). **무단 자동 수정 없음.** 메커니즘 정본은 [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md) 5.2, 승인 전송은 5장(HITL 승인 메커니즘), 계약(`skill_patch_proposed`·`skill_patched`·`/skills/{id}/patch|history|rollback`)은 [RICE_CONTRACTS.md](RICE_CONTRACTS.md) 1.2·1.3.

---

## 5. 코드 실행과 Sandbox 경계 (Connector · Plugin)

> Hermes 검증 #4의 핵심 교훈: **local 백엔드는 파일시스템/네트워크 격리가 없다.** 마켓에서 받은 코드를 그대로 local 실행하면 안 됨.
>
> 전제: 마켓 artifact 중 **skill(SKILL.md)은 선언적**이라 코드 실행이 없어 sandbox 대상이 아니다. 실제 코드 실행/외부 접근은 ① 에이전트의 **`execute_code`**, ② **Connector(MCP) 레이어와 plugin이 포함한 connector 코드**에서 발생하며, 이 장은 그 경계를 정의한다.

| 실행 대상 | 신뢰 수준 | ★ 실행 방식 |
|----------|----------|------------|
| built-in tool | 우리가 작성 | 프로세스 내 직접 실행 |
| **`execute_code` (에이전트 생성 코드)** | 에이전트 생성 · repo 범위 | **자식 프로세스 + 작업 repo 디렉토리로 cwd 한정 + env 스크럽 + 타임아웃** (Hermes PTC 수준, "repo 단위" 작업 전제) |
| skill (SKILL.md) | 선언적 | 코드 실행 없음 → sandbox 대상 아님 |
| 사용자 본인이 만든 Connector·plugin 코드 | 본인 책임 | 자식 프로세스 + env 스크럽 (Hermes local 수준) |
| **마켓에서 설치한 plugin·Connector(MCP 서버) 코드** | 타인 코드 | 자식 프로세스 + env 스크럽 (데모 한정 신뢰) |

**`execute_code` 실행 경계**: 에이전트가 생성한 코드는 **현재 작업 repo를 cwd로 고정**한 자식 프로세스에서 실행한다 — 파일 접근이 작업 repo로 자연 제한되고, env는 스크럽(키체인 시크릿·토큰 미노출), 실행은 타임아웃으로 끊는다. "repo 단위 작업"이라는 전제가 곧 격리 경계다(Claude Code/Hermes PTC와 동일 발상). 위험 동작(파일 삭제·네트워크·외부 명령)은 아래 tool 실행 승인(HITL) 대상이다. 제품화 시 Docker 컨테이너(CPU/메모리/네트워크 제한)로의 승격은 ADR로 기록.

**실행 방식**: 마켓에서 설치한 plugin·Connector(MCP) 코드는 **자식 프로세스 + env 스크럽**으로 실행하고, "데모 한정, 신뢰된 사내 artifact만"을 전제로 둔다 — 데모 대상이 IBM 내부 큐레이션된 artifact이기 때문. 제품화 시 Docker 컨테이너(CPU/메모리 제한)로 전환은 ADR로 기록.

> 검토했던 대안: Docker 실행(안전하나 사용자 PC에 Docker 요구 → 데모 제약), 선언적만 허용(가장 안전 — skill은 이미 선언적이나, Connector(MCP) 실행이 막혀 외부 연동 데모 약화).

**Tool 실행 승인 (human-in-the-loop)**: Connector(MCP)·plugin이 제공하는 tool, 그리고 `execute_code`의 위험 동작(파일 삭제·네트워크·외부 명령) 실행 전, 사용자 승인을 받는다. 승인 요청은 SSE `tool_approval_required` 이벤트로 보내고 앱이 `POST /sessions/{id}/tool-approval`로 응답한다(CONTRACTS 1.2). 승인 **UI 구현은 개발 영역**이며, 본 문서는 승인 **시점(실행 직전)·실행 경계(위 표)** 를 정의한다. 전송 메커니즘(아래 5.1)은 스킬 개선 승인(`skill_patch_proposed`, LEARNING_LOOP 5.2)과 **동일**하다.

### 5.1 HITL 승인 메커니즘 — parking · timeout · 방치 · 재연결

> 적용 대상: ① tool 실행 승인(`tool_approval_required`), ② 스킬 개선 승인(`skill_patch_proposed`). 둘 다 "에이전트 턴 **도중** 사용자 결정을 기다린다"는 동일 문제 → **단일 메커니즘**으로 처리한다.

**전송 방식 (재검토 완료 — SSE+POST 고수).** SSE는 단방향, 승인은 요청-응답이라 *이 한 케이스만* 보면 WebSocket이 더 자연스럽다는 지적이 있었다. 그러나 (a) 승인은 저빈도·단발 상호작용이고, (b) WS는 전 구간 구현·재연결 비용을 올리며, (c) 아래 parking 설계로 SSE+POST의 함정이 닫힌다 → **SSE+POST 유지**(전송 결정 CONTRACTS 1.2와 일관). 대신 함정(timeout·방치·재연결)을 **명시 설계**한다 — 미설계 시 데모에서 가장 잘 깨지는 지점이기 때문.

- **parking (턴 정지·재개).**
  1. 승인 지점에서 런타임이 고유 **`approval_id`** 를 만들어 SSE 제어 이벤트에 실어 보낸다.
  2. 에이전트 루프는 `ApprovalRegistry`(`server/approval.py`)에 `approval_id → asyncio.Future`를 등록하고 그 Future를 `await` — 스레드 블로킹이 아닌 **async 일시중단**(다른 I/O는 계속).
  3. 앱이 `POST /sessions/{id}/tool-approval` 또는 `POST /skills/{id}/patch`를 **`approval_id` 포함**해 보내면, 핸들러가 Future를 resolve(approve/reject/edited_body) → 루프 재개.
- **timeout (방치 방지).** 각 Future에 **기본 5분**(설정값) 타임아웃. 만료 시 **안전 기본값 = 거부/취소**로 resolve:
  - tool 승인 만료 → 위험 tool **실행 안 함**, 루프엔 "미승인으로 건너뜀" 주입(또는 턴 종료)
  - skill patch 만료 → **적용 안 함**(본문 불변), 제안 폐기
- **방치(abandonment)·중복.** 세션 `POST /sessions/{id}/abort` 또는 `POST /shutdown` 시 해당 세션의 **대기 Future 전부 취소**로 resolve + 정리(고아 방지). 이미 resolve/만료된 `approval_id`로 **늦게/중복** 도착한 POST → **409/410 idempotent no-op**. 레지스트리는 인메모리로 충분 — 단일 활성 세션(2장)이라 동시 대기 승인은 소수이고, 런타임 재시작 시 소실돼도 무방(데모용).
- **SSE 끊김·재연결 상관.** 승인 상태는 **SSE 연결이 아니라 레지스트리(서버측)에 산다** → 대기 중 SSE가 끊겨도 루프는 계속 `await`(Future는 연결과 독립), POST는 새 연결로 와도 된다. 재연결 시 앱이 놓친 프롬프트를 복구하도록 **`GET /sessions/{id}/pending-approvals`**(대기 중 `{approval_id, kind, payload}` 목록) → 카드 재렌더. 제어 이벤트 재전송 대신 **폴링형 복구**(SSE+POST 패턴과 일관).

→ 계약 반영(CONTRACTS 1.2): 제어 이벤트에 **`approval_id`** 추가 · 승인 POST 본문에 `approval_id` 포함 · **`GET /sessions/{id}/pending-approvals`** 추가 · 늦은/중복 POST = 409/410. 모듈 위치는 `server/approval.py`([RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md)).

---

## 6. 모델 프로바이더 & 키 보관 (REPO_STRATEGY #3 해소)

서비스 형태 관점으로 2개 provider를 지원한다 ("모델 선택" UI가 SaaS/on-prem을 토글 — 제품 기능):

| 형태 | Provider | 모델 (프로토타입) | 접속/인증 |
|------|----------|-------------------|-----------|
| **SaaS** | Claude (Anthropic API) | Claude Sonnet 4.5 | API 키 → OS 키체인, prompt caching 지원 |
| **on-prem** | Ollama (로컬 모델) | Qwen3.6-27B / Qwen3.5-9B (MLX) / Qwen3.5-9B | `base_url` (키 불필요), 사내·오프라인 |

> 모델은 프로토타입 기준. OpenAI 호환 게이트웨이 추상화는 Phase 2. (※ Claude 최신은 Sonnet 4.6(`claude-sonnet-4-6`) — 필요 시 교체)

**Provider 차이 정규화** — `ILLMProvider` Protocol이 다음을 흡수한다:
- **tool calling**: Claude native `tool_use` ↔ Ollama function-calling(모델별 상이·미지원 가능) → 공통 tool_call 표현으로 정규화, 미지원 모델은 프롬프트 기반 폴백
- **streaming**: 각 provider 스트림 → 공통 **SSE 이벤트**(8종, 2026-07-02 정정 — `skill_created` 제거, 명세 정본 [RICE_CONTRACTS.md](RICE_CONTRACTS.md) 1.3)로 변환
- **prompt caching**: Claude `cache_control`(스킬 주입 비용 상쇄, Hermes 검증 #6) ↔ Ollama 미지원 → keep-alive·컨텍스트 재사용으로 대체

- ★ 키 보관: SaaS(Claude) 키만 OS 키체인 (macOS Keychain / Windows Credential Manager), Electron `safeStorage` 활용. `.env` 평문 금지. on-prem(Ollama)은 키 없음

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
attachments(id, message_id, kind, path, created_at)                 -- 첨부/멀티모달 (MVP: kind ∈ {xlsx, png})

-- Connector 관련 테이블 (신규)
connectors(
  id TEXT PRIMARY KEY,
  connector_type TEXT NOT NULL,  -- 'gmail' (프로토타입); 향후 'google_drive' 등
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

**목적**: Plugin이 외부 서비스에 접근할 수 있도록 OAuth 기반 연결 관리. 프로토타입은 **Gmail 커넥터 하나만 진지하게 구현**하고, 아래 9.2 OAuth 흐름을 정본으로 한다. 그 외 서비스(Google Drive·Slack·Microsoft 등)는 동일 OAuth 골격에 connector_type만 추가하면 되는 **확장 포인트로만** 두고 이번 데모에서는 구현하지 않는다.

**구성 요소**:
- Connector Manager (connector/manager.py)
- OAuth Flow Handler (connector/oauth/)
- Credential Storage (connector/credential/keychain.py)
- Permission System (connector/permission/checker.py)

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
   
5. Connector 상태 업데이트 → "connected"
```

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
5. **신뢰불가 외부 콘텐츠 격리 (프롬프트 인젝션 방어)**: Connector로 가져온 **외부 콘텐츠(예: Gmail 메일 본문)는 신뢰할 수 없다** — 공격자가 메일에 심은 지시문이 에이전트를 탈취해 위험 tool(`execute_code`·terminal·파일 삭제·네트워크)을 호출하게 만들 수 있다(Hermes webhook-safe toolset의 교훈, [HERMES_TOOL_SYSTEM_REFERENCE.md](HERMES_TOOL_SYSTEM_REFERENCE.md) §6.3 재해석). 대응: (a) 외부 콘텐츠는 **데이터로 명시 구분**(지시문으로 취급하지 않음), (b) 위험 tool 실행은 5장 HITL 승인을 **반드시** 거치게 함, (c) 향후 외부 콘텐츠 주도 턴에서 위험 tool **노출 자체를 축소**(restricted toolset). MVP는 (a)+(b)로 최소 방어, (c)는 11장 백로그.

---

## 11. 향후 확장 (UI 로드맵 대응)

> 설계는 UI 상위집합이다. 현재 프로토타입 UI에 있으나 MVP 범위가 미정인 것 + 아직 UI에 없는 확장 후보를 추적한다.

- **멀티모달/첨부 처리 (MVP 포함)**: ChatScreen 파일 첨부 → **xlsx·png만** 첨부 저장(`~/.rice/attachments/`) + provider 멀티모달 전달. 업로드는 `POST /sessions/{id}/attachments`(선업로드) 후 메시지가 `attachment_id`로 참조 (CONTRACTS 1.1·1.3)
- **음성 입력(STT)**: ChatScreen 마이크 UI → Web Speech 또는 로컬 STT (이번 개발 **제외**, 향후 확장)
- **Skill 작성·게시**: 사용자 제작 skill(SKILL.md)을 platform에 publish(`POST /artifacts`) — 게시 UI 신설 (설치된 skill 관리는 MySkillsScreen이 이미 담당)
- **Connector 제공 도구 가시화**: 연결된 Connector(MCP)가 노출하는 원자적 tool 목록을 ConnectorsScreen에서 확인
- **artifact 디지털 서명**: 10장 Phase 2 분석 항목
- **세션 검색·관리 보강**: FTS5 기반 세션 검색 UI, 이름변경/보관
- **Tool Search (progressive disclosure)**: 설치 plugin/Connector가 늘어 MCP tool 스키마가 토큰 예산([RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md) 7장 MCP ~15K)을 넘으면, core tool은 유지하고 비-core tool을 `tool_search`/`tool_describe`/`tool_call` 3개 브릿지 도구로 접는다([HERMES_TOOL_SYSTEM_REFERENCE.md](HERMES_TOOL_SYSTEM_REFERENCE.md) §6.4). MVP는 Connector 1개라 미발생 — Phase 2 솔기.
- **MCP 동적 갱신 (Phase 2)**: MCP 어댑터 구현 시, 서버가 `notifications/tools/list_changed`를 보내면 해당 서버 tool을 전부 deregister 후 재등록(nuke-and-repave, [HERMES_TOOL_SYSTEM_REFERENCE.md](HERMES_TOOL_SYSTEM_REFERENCE.md) §7.3).
- **외부 콘텐츠 주도 턴의 restricted toolset**: 10장-5의 (c) — 신뢰불가 외부 콘텐츠가 주도하는 턴에서 위험 tool 노출을 축소.

---

> 모듈 구조의 **핵심 불변식**과 화면→모듈 매핑은 [RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md)(정본)를, 전체 구현 우선순위(다음 액션)는 [README.md](README.md)를 참조한다.
