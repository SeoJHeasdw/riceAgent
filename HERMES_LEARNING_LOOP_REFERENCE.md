# Hermes Agent Learning Loop Architecture

> Hermes Agent의 "쓸수록 나에게 맞춰 학습·진화하는" 메커니즘 코드 분석 참조 문서.

---

## 목차

1. [개요](#개요)
2. [핵심 컴포넌트](#핵심-컴포넌트)
3. [Tool vs Skill 차이](#tool-vs-skill-차이)
4. [Learning Loop 흐름](#learning-loop-흐름)
5. [오케스트레이션 계층](#오케스트레이션-계층)
6. [Tool 생성 메커니즘](#tool-생성-메커니즘)
7. [데이터 저장 방식](#데이터-저장-방식)
8. [실제 코드 위치](#실제-코드-위치)

---

## 개요

Hermes Agent의 "쓸수록 나에게 맞춰 학습·진화"는 **5개 축**으로 구현된다:

| 축 | 주체 | 타이밍 | 지속성 |
|----|------|--------|--------|
| **1. Skill 자율 생성/패치** | LLM (프롬프트 가이드 기반) | 턴 중 즉시 | 영구 (SKILL.md) |
| **2. Background Review 포크** | daemon 스레드 AIAgent | 매 턴 종료 후 | 영구 (메모리+스킬) |
| **3. Built-in Memory (MEMORY.md/USER.md)** | memory 도구 | 즉시 (다음 세션 반영) | 영구 (Markdown) |
| **4. External Memory Provider** | MemoryManager → 플러그인 | 매 턴 백그라운드 sync | 원격/로컬 DB |
| **5. Curator 자동 스킬 정리** | 백그라운드 스레드 | 7일 주기 opportunistic | 스킬 아카이브/통합 |

**핵심 설계 원칙**: 프롬프트 캐시를 절대 깨지 않는다. 모든 학습 쓰기는 **현재 세션의 시스템 프롬프트를 건드리지 않고** 다음 세션에 반영된다 (단, Background Review 포크는 메모리/스킬 파일을 디스크에 쓰되 현재 대화에 영향 없음).

---

## 핵심 컴포넌트

### 1. Skill Lifecycle System

#### 1.1 Skill Usage Telemetry (`tools/skill_usage.py`)

**위치**: `tools/skill_usage.py` (900줄)

**역할**: 스킬 사용 추적 및 생애주기 상태 관리

**핵심 기능**:
- `~/.hermes/skills/.usage.json`에 자동 기록
- 카운터: `use_count`, `view_count`, `patch_count`
- 타임스탬프: `last_used_at`, `last_viewed_at`, `last_patched_at`
- Provenance 추적: `created_by: "agent"` 마커

**상태 전이**:
```
active → stale (N일 미사용) → archived (M일 미사용)
```

**주요 함수**:
```python
bump_use(skill_name)           # 스킬 사용 시 자동 호출
bump_view(skill_name)          # 스킬 조회 시
bump_patch(skill_name)         # 스킬 수정 시
mark_agent_created(skill_name) # 에이전트 생성 마킹
set_state(skill_name, state)   # 상태 전이
archive_skill(skill_name)      # 아카이브로 이동
restore_skill(skill_name)      # 복원
```

#### 1.2 Curator (자동 스킬 관리)

**위치**: `agent/curator.py` (1,916줄)

**역할**: 주기적 스킬 검토 및 생애주기 관리

**두 단계 프로세스**:

1. **자동 전환 (규칙 기반, LLM 없음)** — 기본 동작:
```python
def apply_automatic_transitions(now: Optional[datetime] = None):
    """사용 패턴 기반 자동 상태 전이"""
    # stale_after_days, archive_after_days 설정값 기반
    # 핀된 스킬은 제외
```

2. **LLM 통합 패스 (독립 AIAgent 포크)** — **기본 OFF** (`DEFAULT_CONSOLIDATE = False`):
```python
def _run_llm_review(prompt: str):
    """별도 AIAgent 생성하여 스킬 검토"""
    review_agent = AIAgent(
        max_iterations=9999,
        platform="curator",
        skip_memory=True,
    )
    # 중복 스킬 통합, 품질 개선 제안
```

> ⚠️ **중요**: LLM 통합 패스는 **기본적으로 실행되지 않음**.
> 활성화 방법:
> - `config.yaml`에 `curator.consolidate: true` 설정, 또는
> - CLI에서 `hermes curator run --consolidate` 명시적 실행
> 기본 동작은 결정론적 규칙 기반 비활성 프루닝(자동 전환)만 수행.

**Curator 상태 파일**: `~/.hermes/skills/.curator_state`
- `last_run_at`, `paused`, `run_count` 등 저장
- 최초 실행 시 `last_run_at`를 현재 시각으로 seeding → 설치 직후 바로 실행되지 않도록 설계

**실행 방식**:
- 백그라운드 스레드 (비동기)
- 사용자 대화 방해 안 함
- 설정: `curator.enabled`, `curator.interval_hours`, `curator.consolidate`
- 기본 주기: 7일 (`DEFAULT_INTERVAL_HOURS = 24 * 7`)
- 추가 게이트: 유휴 2시간 이상일 때만 (`DEFAULT_MIN_IDLE_HOURS = 2`)
- 상태 전이 기본값: stale 30일, archive 90일
- 트리거: `maybe_run_curator()` 호출 시 interval/idle/enabled 조건을 검사하는 **기회적(opportunistic) 실행**
- Gateway에서도 실행됨 (`gateway/run.py`에서 `maybe_run_curator()` 호출)

#### 1.3 Curator Backup System

**위치**: `agent/curator_backup.py`

**역할**: 모든 변경 전 자동 스냅샷

**특징**:
- tar.gz 형식
- `~/.hermes/skills/.curator_backups/` 저장
- 롤백 가능

### 2. Memory System

#### 2.1 Memory Manager

**위치**: `agent/memory_manager.py` (1,081줄)

**역할**: 여러 메모리 제공자 통합 관리

**핵심 메서드**:
```python
class MemoryManager:
    def sync_all(user_content, assistant_content, session_id, messages)
        """매 대화 턴마다 메모리 동기화 — ThreadPoolExecutor(max_workers=1)로 백그라운드 실행
        (개별 provider ABC의 메서드명은 sync_turn()이며, MemoryManager는 내부에서 provider.sync_turn()을 호출)"""
    
    def prefetch_all(query, session_id)
        """다음 턴 전에 관련 컨텍스트 미리 로드"""
    
    def on_session_switch(new_session_id, parent_session_id, reset, rewound)
        """세션 전환 시 메모리 상태 유지"""
    
    def on_delegation(task, result, child_session_id)
        """서브에이전트 작업 결과 기록"""
```

> 💡 **백그라운드 실행 설계**: `sync_all()`은 `ThreadPoolExecutor(max_workers=1)`로 백그라운드 실행됨.
> 싱글 워커로 순서 보장(turn N이 turn N+1보다 먼저 도착), 느린 provider가 메인 루프를 블록하지 않음.

**라이프사이클 훅**:
- `on_turn_start` - 턴 시작
- `on_session_end` - 세션 종료
- `on_pre_compress` - 컨텍스트 압축 전
- `on_memory_write` - 메모리 쓰기 시

**Provider 정책**:
- builtin provider는 **항상** 첫 번째로 등록
- 외부 provider는 **동시에 1개만 허용** — 두 번째 등록 시도는 경고와 함께 거부됨
- 선택은 `memory.provider` 설정으로
- 한 provider의 실패가 다른 provider를 막지 않음 (failure isolation)

#### 2.2 Memory Tool 파일 구조 (`tools/memory_tool.py`)

**두 가지 파일**로 메모리를 관리:

| 파일 | 역할 | 내용 |
|------|------|------|
| `MEMORY.md` | 에이전트 개인 메모 | 학습한 패턴, 작업 컨텍스트, 기술적 메모 |
| `USER.md` | 사용자 프로파일 | 사용자 선호도, 스타일, 배경 정보 |

**세션 내 동작**:
```
세션 시작: MEMORY.md + USER.md → frozen snapshot → 시스템 프롬프트에 주입
세션 중: memory_write() → 디스크에 즉시 저장 (시스템 프롬프트는 변경 안 함)
다음 세션: 변경된 내용이 새 snapshot으로 시스템 프롬프트에 반영
```

> ⚠️ **캐시 보호 설계**: 세션 중 메모리 쓰기는 디스크에만 저장되고 시스템 프롬프트를 즉시 변경하지 않음.
> 이는 프롬프트 캐시를 보호하기 위한 의도적 설계. 변경 사항은 **다음 세션**부터 반영됨.

#### 2.3 Honcho Plugin (Cross-session User Modeling)

**위치**: `plugins/memory/honcho/__init__.py`

**역할**: AI-native 크로스 세션 사용자 모델링

**4가지 도구**:
```python
honcho_profile    # 사용자 peer card (이름, 역할, 선호도, 스타일)
honcho_search     # 과거 대화 시맨틱 검색
honcho_reasoning  # 자연어 질문으로 사용자 패턴 분석
honcho_context    # 현재 세션 전체 컨텍스트
```

**Dialectic Q&A**: LLM 기반 추론으로 사용자 행동 패턴 학습

### 3. Background Review 포크

**위치**: `agent/background_review.py`

**역할**: 매 턴 종료 후 daemon 스레드로 포크된 AIAgent가 대화를 검토하여 메모리/스킬 저장 여부 판단

**동작 방식**:
```
응답 반환 직후
  ↓
daemon 스레드 포크 (메인 루프 블록 없음)
  ↓
AIAgent 대화 스냅샷 replay
  ↓
3가지 리뷰 프롬프트 중 선택:
  - _MEMORY_REVIEW_PROMPT  → 메모리만 검토
  - _SKILL_REVIEW_PROMPT   → 스킬만 검토
  - _COMBINED_REVIEW_PROMPT → 메모리 + 스킬 동시 검토
  ↓
저장할 게 있으면 memory_write() 또는 skill_manage() 호출
```

**캐시 최적화**:
- 같은 모델: warm cache 재사용 (대화 prefix 공유)
- 다른 모델: digest 압축 → cold-write 최소화

### 4. Cron Scheduler (자동 실행)

**위치**: `cron/scheduler.py` (3,042줄)

**역할**: 스케줄 기반 작업 실행

**특징**:
- 작업당 독립 AIAgent
- 멀티 플랫폼 전달 (Telegram, Discord 등)
- 3분 하드 인터럽트 (무한 루프 방지)
- `skip_memory=True` 기본값 — 크론 세션에서는 메모리 프로바이더 실행 안 함

**스킬·컨텍스트 통합 기능**:
- `skills` 필드: 특정 스킬을 해당 job에서만 로드
- `context_from`: 이전 job의 마지막 출력을 다음 job의 프롬프트에 체인
- `workdir`: 특정 디렉토리에서 실행하고 해당 `AGENTS.md` / `CLAUDE.md` 로드

---

## Tool vs Skill 차이

### Built-in Tool (실행 가능한 함수)

**본질**: Python으로 구현된 실행 가능한 함수

**위치**: `tools/*.py`

**예시**:
```python
# tools/web_tools.py
def web_search_tool(query: str, limit: int = 5) -> str:
    """실제 웹 검색 수행"""
    results = search_api.search(query, limit)
    return json.dumps(results)

registry.register(
    name="web_search",
    schema=WEB_SEARCH_SCHEMA,
    handler=web_search_tool,
)
```

**특징**:
- ✅ 즉시 실행 가능
- ✅ LLM이 tool_calls로 직접 호출
- ✅ 항상 사용 가능 (코드베이스에 내장)
- ❌ 절차적 지식 없음

**Built-in Tools 목록** (`toolsets.py`):
```python
_HERMES_CORE_TOOLS = [
    "web_search", "web_extract",
    "terminal", "process",
    "read_file", "write_file", "patch", "search_files",
    "vision_analyze", "image_generate",
    "skills_list", "skill_view", "skill_manage",
    "browser_navigate", "browser_snapshot", "browser_click",
    "text_to_speech",
    "todo", "memory",
    "session_search",
    "clarify",
    "execute_code", "delegate_task",
    "cronjob",
    "send_message",
    # ... 등
]
```

### Skill (절차적 지식 문서)

**본질**: 마크다운 문서 (SKILL.md)

**위치**: `~/.hermes/skills/*/SKILL.md`

**예시**:
```markdown
# skills/github/github-code-review/SKILL.md
---
name: github-code-review
description: "Review PRs: diffs, inline comments via gh or REST."
---

## PR Review Workflow (End-to-End)

### Step 1: Set up environment
source gh-env.sh

### Step 2: Gather PR context
gh pr view 123
gh pr diff 123 --name-only

### Step 3: Check out the PR locally
git fetch origin pull/123/head:pr-123
git checkout pr-123

### Step 4: Read the diff
git diff main...HEAD

### Step 5: Run automated checks
pytest
ruff check .

### Step 6: Apply review checklist
- Correctness
- Security
- Code Quality
...
```

**특징**:
- ❌ 실행 불가능 (문서일 뿐)
- ✅ 절차적 가이드 ("어떻게 할지")
- ✅ 도구 조합 방법
- ✅ 베스트 프랙티스, 함정, 검증 방법

**스킬 주입 방식**:

> ❌ 스킬은 시스템 프롬프트에 직접 주입되지 않음
> ✅ 슬래시 커맨드 방식으로 on-demand 주입

```python
# agent/skill_commands.py
def scan_skill_commands():
    """~/.hermes/skills/ 스캔 → /skill-name 슬래시 커맨드 등록"""

def build_skill_invocation_message(skill_name):
    """사용자가 /skill-name 입력 시 SKILL.md 전체를 user 메시지로 주입"""
```

**실제 흐름**:
```
1. 시작 시: scan_skill_commands() → 각 스킬을 /skill-name 커맨드로 등록
2. 사용자: /github-code-review 입력
3. build_skill_invocation_message() → SKILL.md 전체 내용을 user 메시지로 전송
4. 스킬 내용이 프롬프트 캐시를 깨지 않고 user 턴에 주입됨
```

> 💡 **설계 의도**: 시스템 프롬프트에 미리 주입하면 로드된 스킬마다 캐시가 무효화됨.
> user 메시지로 주입함으로써 **프롬프트 캐시(stable prefix) 보호**.

**스킬 생성 트리거**: 프로그램적 감지 로직은 없고, **시스템 프롬프트의 명시적 가이드라인**으로 LLM이 자율 판단한다.

`agent/prompt_builder.py`의 `SKILLS_GUIDANCE`:
> "After completing a complex task (**5+ tool calls**), fixing a tricky error, or discovering a non-trivial workflow, save the approach as a skill with skill_manage so you can reuse it next time."

추가로 스킬 사용 중 내용이 낡았으면 **묻지 않고 즉시 patch**하라는 지시도 포함:
> "When using a skill and finding it outdated, incomplete, or wrong, patch it immediately with skill_manage(action='patch') — don't wait to be asked."

**Skill vs Plugin 선택 기준**: 생성 주체로 구분된다.
- **Skill**: 에이전트가 자율 생성/패치 (시스템 프롬프트에 생성 가이드 존재, Curator 관리 대상)
- **Plugin**: 사람(사용자/개발자)이 직접 작성하는 확장 포인트

### 비교표

| 측면 | Built-in Tool | Skill |
|------|--------------|-------|
| **본질** | 실행 가능한 함수 | 절차적 지식 문서 |
| **형태** | Python 코드 | Markdown 문서 |
| **위치** | `tools/*.py` | `skills/*/SKILL.md` |
| **LLM 접근** | tool_calls로 직접 호출 | `/skill-name` 입력 시 user 메시지로 주입 |
| **역할** | "무엇을 할 수 있는가" | "어떻게 해야 하는가" |
| **생성** | 개발자가 코드로 구현 | 에이전트가 경험에서 학습 |
| **진화** | 코드 업데이트 필요 | 자동으로 개선/통합 |
| **Curator 관리** | ❌ | ✅ |

---

## Learning Loop 흐름

### 전체 흐름도

```
1. 사용자 프롬프트
   ↓
2. AIAgent.run_conversation()
   ↓
3. 시스템 프롬프트 구성 (3계층)
   - stable:   정체성 + 도구 가이드 + 스킬 인덱스     ← 세션 내 불변 (캐시 보호)
   - context:  AGENTS.md, .cursorrules               ← 세션 내 불변
   - volatile: MEMORY.md + USER.md 스냅샷, 타임스탬프  ← 세션마다 갱신
   ↓
4. Memory prefetch (관련 컨텍스트 로드)
   ↓
5. LLM 추론 + Tool 호출
   ↓
6. Tool 실행 (handle_function_call)
   ↓
7. skill_usage.bump_use() 자동 호출 (텔레메트리)
   ↓
8. Memory sync (턴 종료 시) — 백그라운드 스레드
   ↓
9. 응답 반환
   ↓
10. (백그라운드 daemon) Background Review 포크
    - 대화 스냅샷을 replay하며 메모리/스킬 저장 검토
    - 같은 모델: warm cache 재사용
    - 다른 모델: digest 압축 → cold-write 최소화
   ↓
11. (백그라운드) Curator 주기적 실행
    - 규칙 기반 자동 상태 전이 (항상)
    - LLM 통합 패스 (consolidate=true 일 때만)
```

### 시나리오별 상세 흐름

#### 시나리오 A: 기존 도구 사용 (웹검색)

```
사용자: "웹검색해서 최근 이란 전쟁 추이 알려줘"
   ↓
AIAgent.run_conversation()
   ↓
memory_manager.prefetch_all("이란 전쟁")
   ↓
LLM: "web_search 도구를 사용하겠습니다"
   ↓
handle_function_call("web_search", {"query": "이란 전쟁 추이"})
   ↓
tools/web_tools.py의 web_search_tool() 실행
   ↓
결과 반환 → 사용자에게 응답
   ↓
memory_manager.sync_all(user_msg, assistant_response)
   ↓
끝 (스킬 생성 없음 - web_search는 이미 built-in)
```

#### 시나리오 B: 복잡한 작업 → 스킬 생성

```
사용자: "GitHub PR #123 리뷰해줘"
   ↓
AIAgent: 여러 도구 조합 사용
  - terminal("gh pr view 123")
  - terminal("gh pr diff 123")
  - terminal("git fetch origin pull/123/head:pr-123")
  - read_file(변경된 파일들)
  - terminal("pytest")
  - terminal("gh pr review 123 --request-changes ...")
   ↓
응답 완료
   ↓
(선택적) LLM 판단: "이 패턴 자주 쓸 것 같은데?"
   ↓
skill_manage(action="create", name="github-pr-review", content="...")
   ↓
~/.hermes/skills/github-pr-review/SKILL.md 생성
   ↓
skill_usage.mark_agent_created("github-pr-review")
   ↓
다음 세션에서:
  - scan_skill_commands()가 /github-pr-review 슬래시 커맨드 등록
  - 사용자가 /github-pr-review 입력 시 SKILL.md → user 메시지로 주입
  - LLM이 문서화된 절차 따라서 더 빠르고 정확하게 수행
```

---

## 오케스트레이션 계층

### 계층 구조

```
┌─────────────────────────────────────────────────────────┐
│ 최상위: 사용자 인터페이스 레이어                              │
├─────────────────────────────────────────────────────────┤
│ • CLI (cli.py)                                          │
│ • Gateway (gateway/run.py) - 메시징 플랫폼               │
│ • Cron Scheduler (cron/scheduler.py) - 자동 실행         │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│ 중간: AIAgent 오케스트레이터 (run_agent.py)                │
├─────────────────────────────────────────────────────────┤
│ • run_conversation() - 메인 대화 루프                     │
│ • Memory Manager 통합                                    │
│ • Tool 실행 조율                                         │
│ • 스트리밍/콜백 관리                                       │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│ 하위: 실행 엔진들                                          │
├─────────────────────────────────────────────────────────┤
│ • model_tools.py - 도구 디스패치                          │
│ • agent/memory_manager.py - 메모리 동기화                 │
│ • agent/curator.py - 스킬 생애주기 관리                   │
│ • tools/skill_usage.py - 텔레메트리 수집                  │
└─────────────────────────────────────────────────────────┘
```

### 주체별 역할

| 작업 유형 | 주체 | Plan 수립자 | 실행 방식 |
|---------|------|-----------|---------|
| 사용자 대화 | AIAgent | LLM (도구 호출 체인) | 동기 루프 |
| 메모리 동기화 | Memory Manager | 규칙 기반 (매 턴) | 자동 훅 |
| 스킬 텔레메트리 | skill_usage | 규칙 기반 (도구 사용 시) | 자동 훅 |
| 스킬 생애주기 | Curator | 규칙 + LLM 하이브리드 | 백그라운드 스레드 |
| Cron 작업 | Scheduler | 시간 기반 트리거 | 독립 프로세스 |

**핵심**: 중앙 집중식 플래너가 아닌 **분산 자율 시스템**

### AIAgent 핵심 루프

**위치**: `run_agent.py` (~5,577줄)

```python
class AIAgent:
    def run_conversation(self, user_message: str, ...):
        """메인 대화 루프"""
        
        # 1. 메모리 prefetch
        context = self._memory_manager.prefetch_all(user_message)
        
        # 2. 시스템 프롬프트 구성 (스킬 포함)
        system_prompt = self._build_system_prompt()
        
        # 3. 도구 호출 루프
        while api_call_count < max_iterations:
            if self._interrupt_requested:
                break
            
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tool_schemas
            )
            
            if response.tool_calls:
                for tool_call in response.tool_calls:
                    result = handle_function_call(
                        tool_call.name,
                        tool_call.args,
                        task_id
                    )
                    messages.append(tool_result_message(result))
                api_call_count += 1
            else:
                # 최종 응답
                break
        
        # 4. 메모리 동기화
        self._memory_manager.sync_all(user_msg, assistant_response)
        
        return response
```

---

## Tool 생성 메커니즘

### 3가지 방법

#### 1. `execute_code` - 프로그래밍 방식 도구 생성 (PTC)

**위치**: `tools/code_execution_tool.py` (~1,848줄)

**원리**: 
1. LLM이 Python 스크립트 작성
2. 스크립트 내에서 `hermes_tools` 모듈로 built-in 도구 호출
3. 샌드박스에서 안전하게 실행
4. stdout만 LLM에게 반환

**아키텍처**:
```
Parent Process (AIAgent)
   ↓
   생성: hermes_tools.py (RPC 스텁)
   ↓
   Unix Domain Socket 또는 File-based RPC
   ↓
Child Process (Sandbox)
   ↓
   import hermes_tools as ht
   result = ht.web_search("query")
   ↓
   RPC → Parent → 실제 도구 실행 → 결과 반환
```

**허용된 도구** (샌드박스 내):
```python
SANDBOX_ALLOWED_TOOLS = frozenset([
    "web_search",
    "web_extract",
    "read_file",
    "write_file",
    "search_files",
    "patch",
    "terminal",
])
```

**제한값**:
- 타임아웃: 기본 5분 (`DEFAULT_TIMEOUT = 300`)
- 최대 도구 호출: 50회 (`DEFAULT_MAX_TOOL_CALLS = 50`)
- stdout 50KB / stderr 10KB 캡
- 환경 변수 스크러빙 (allowlist 기반, API 키 등 시크릿 차단)

**백엔드별 격리 수준**:
| 백엔드 | 격리 수준 |
|--------|----------|
| local (자식 프로세스) | **파일시스템/네트워크 격리 없음** — env 스크럽 + 도구 제한만 |
| docker / singularity / modal / daytona | 컨테이너 격리 + 리소스 제한 (기본 CPU 1, 메모리 5120MB, 디스크 51200MB) |

**특징**:
- ✅ 런타임에 새 도구 생성
- ✅ 기존 도구 조합
- ✅ 즉시 실행
- ❌ 일회성 (디스크 저장 안 됨)

#### 2. Skill 내 스크립트 - 재사용 가능한 도구

**원리**:
1. `skill_manage(action="write_file", file_path="scripts/tool.py", ...)`
2. `~/.hermes/skills/<skill>/scripts/` 디렉토리에 저장
3. `terminal("python scripts/tool.py ...")` 로 호출

**특징**:
- ✅ 영구 저장 (디스크)
- ✅ 버전 관리 가능 (Git)
- ✅ Curator 관리 대상
- ✅ 세션 간 재사용
- ❌ Built-in처럼 tool_calls로 직접 호출 불가

#### 3. Plugin System - 진짜 새 도구 등록

**위치**: `~/.hermes/plugins/<name>/`

**구조**:
```
~/.hermes/plugins/my-tool/
├── plugin.yaml
└── __init__.py
```

```python
# __init__.py
def register(ctx):
    def my_custom_tool(param1: str, param2: int) -> str:
        result = do_something(param1, param2)
        return json.dumps({"result": result})
    
    ctx.register_tool(
        name="my_custom_tool",
        schema={
            "name": "my_custom_tool",
            "description": "Does something custom",
            "parameters": {
                "type": "object",
                "properties": {
                    "param1": {"type": "string"},
                    "param2": {"type": "integer"}
                }
            }
        },
        handler=my_custom_tool
    )
```

**특징**:
- ✅ Built-in과 동등
- ✅ LLM이 tool_calls로 직접 호출
- ✅ 영구 등록 (재시작 후에도 사용)
- ✅ 완전한 통합

### 비교표

| 방법 | 생성 방식 | 지속성 | 사용 방식 | Built-in 동등 |
|------|----------|--------|----------|--------------|
| **execute_code** | 런타임 Python 코드 | 일회성 | 즉시 실행 | ❌ |
| **Skill 스크립트** | skill_manage로 저장 | 영구 | terminal 호출 | ❌ |
| **Plugin** | 사용자 직접 작성 | 영구 | tool_calls 호출 | ✅ |

---

## 데이터 저장 방식

### 1. 세션 데이터 (Session Data)

**저장소: SQLite (`~/.hermes/state.db`)**

```python
# 위치: hermes_state.py
DEFAULT_DB_PATH = get_hermes_home() / "state.db"
SCHEMA_VERSION = 16

# 주요 테이블
sessions:
  - id, source, user_id, model, system_prompt
  - parent_session_id (압축 시 세션 체인)
  - started_at, ended_at, message_count
  - input_tokens, output_tokens, cache_read_tokens
  - title, archived

messages:
  - session_id, role, content, tool_calls
  - timestamp, token_count, reasoning
  - active (soft delete 지원)

# FTS5 전문 검색
messages_fts: FTS5 가상 테이블 (content 전문 검색)
messages_fts_trigram: FTS5 trigram (CJK 언어 지원)
```

**특징:**
- **WAL 모드**: 동시 읽기 + 단일 쓰기 (게이트웨이 멀티플랫폼 지원)
- **FTS5 검색**: 모든 세션 메시지에 대한 빠른 전문 검색
- **압축 체인**: `parent_session_id`로 압축된 세션 연결
- **프로필 격리**: 각 프로필은 독립적인 `state.db` 보유

### 2. 스킬 메타데이터 (Skill Metadata)

**저장소: JSON (`~/.hermes/skills/.usage.json`)**

```python
# 위치: tools/skill_usage.py
_usage_file() -> Path:
    return get_hermes_home() / "skills" / ".usage.json"

# 구조 (스킬 이름을 키로 사용)
{
  "skill_name": {
    "use_count": 5,
    "view_count": 12,
    "patch_count": 3,
    "last_used_at": "2026-06-10T07:30:00Z",
    "last_viewed_at": "2026-06-10T07:25:00Z",
    "last_patched_at": "2026-06-09T15:00:00Z",
    "created_at": "2026-06-01T10:00:00Z",
    "created_by": "agent",  # "agent" | "user" | "bundled" | "hub"
    "state": "active",      # "active" | "stale" | "archived"
    "pinned": false
  }
}
```

**특징:**
- **사이드카 파일**: SKILL.md와 분리된 운영 텔레메트리
- **원자적 쓰기**: tempfile + os.replace 패턴
- **파일 락**: fcntl (Unix) / msvcrt (Windows)로 프로세스 간 동기화
- **Best-effort**: 실패해도 툴 호출은 계속 진행

**보조 파일들:**
```
~/.hermes/skills/
  .usage.json           # 메인 텔레메트리
  .usage.json.lock      # 락 파일
  .bundled_manifest     # 번들 스킬 목록 (name:hash)
  .curator_suppressed   # Curator가 제거한 빌트인 스킬 목록
  .archive/             # 아카이브된 스킬 디렉토리
  .hub/lock.json        # Hub 설치 스킬 목록
```

### 3. 유저 모델링 데이터 (User Modeling - Honcho)

**저장소: Honcho API (원격) + 로컬 캐시**

```python
# 위치: plugins/memory/honcho/

# Honcho는 자체 백엔드에 데이터 저장 (벡터DB 아님!)
# - PostgreSQL: 구조화된 데이터 (세션, 메시지, 메타모르포시스)
# - 임베딩: 의미 검색용 (프로바이더는 Honcho 서버가 선택)

# 로컬 설정 파일
~/.hermes/honcho.json  또는  ~/.honcho/config.json

# 구조
{
  "api_key": "...",
  "baseUrl": "https://api.honcho.dev",  # 또는 self-hosted
  "hosts": {
    "hermes": {
      "workspace": "my-workspace",
      "peer_name": "user",
      "ai_peer": "hermes"
    }
  }
}
```

**Honcho 데이터 모델:**
- **Workspace**: 최상위 격리 단위
- **Peer**: 사용자/AI 엔티티 (user, hermes, ...)
- **Session**: 대화 세션
- **Message**: 세션 내 메시지 (user/assistant)
- **Metamorphosis**: 사용자에 대한 지속적 사실 (conclusions)
- **Dialectic**: Q&A 기반 컨텍스트 추출

**로컬 캐시:**
```python
# HonchoSessionManager (plugins/memory/honcho/session.py)
self._cache: dict[str, HonchoSession]  # 세션 키 → 세션 객체
self._peers_cache: dict[str, Any]      # Peer ID 캐시
self._sessions_cache: dict[str, Any]   # Honcho 세션 ID 캐시
self._context_cache: dict[str, dict]   # Prefetch 결과 캐시
```

### 4. 기타 메모리 프로바이더

| 프로바이더 | 저장 방식 | 위치 |
|-----------|----------|------|
| **Honcho** | PostgreSQL + 임베딩 (원격) | Honcho 서버 |
| **Mem0** | 벡터DB (Qdrant/Chroma/...) | 설정에 따라 로컬/원격 |
| **Supermemory** | 자체 백엔드 | 원격 API |
| **Hindsight** | SQLite + 임베딩 | `~/.hermes/hindsight/` |
| **Holographic** | 파일 기반 | `~/.hermes/holographic/` |
| **OpenViking** | 벡터DB | 설정에 따라 |

### 5. 설정 파일 (Configuration)

```
~/.hermes/
  config.yaml          # 메인 설정 (모델, 에이전트, 디스플레이 등)
  .env                 # API 키만 (시크릿)
  honcho.json          # Honcho 설정
  auth.json            # OAuth 토큰 (GitHub, Google 등)
```

### 6. 로그 파일 (Logs)

```
~/.hermes/logs/
  agent.log            # INFO+ 레벨
  errors.log           # WARNING+ 레벨
  gateway.log          # 게이트웨이 전용
```

### 저장 방식 요약

| 데이터 유형 | 저장소 | 형식 | 특징 |
|------------|--------|------|------|
| **세션 히스토리** | SQLite | `state.db` | WAL, FTS5, 압축 체인 |
| **스킬 텔레메트리** | JSON | `.usage.json` | 원자적 쓰기, 파일 락 |
| **유저 모델링** | Honcho API | PostgreSQL + 임베딩 | 원격, 세션 간 지속 |
| **스킬 콘텐츠** | Markdown | `SKILL.md` | 버전 관리 가능 |
| **설정** | YAML/JSON | `config.yaml`, `.env` | 프로필별 격리 |
| **로그** | 텍스트 | `logs/*.log` | 로테이션 지원 |

**핵심 설계 원칙:**
1. **프로필 격리**: 모든 데이터는 `get_hermes_home()` 기준
2. **동시성 안전**: SQLite WAL, 파일 락, 스레드 안전 캐시
3. **검색 최적화**: FTS5로 빠른 전문 검색
4. **원자적 업데이트**: tempfile + rename 패턴
5. **Best-effort 텔레메트리**: 실패해도 메인 기능은 계속

---

## 실제 코드 위치

### 핵심 파일

| 파일 | 줄 수 | 역할 |
|------|------|------|
| `run_agent.py` | ~5,577 | AIAgent 클래스, 메인 대화 루프 |
| `model_tools.py` | ~1,255 | 도구 오케스트레이션, 디스패치 |
| `tools/registry.py` | ~150 | 도구 등록 시스템 |
| `toolsets.py` | ~100 | 도구셋 정의 |
| `tools/skill_usage.py` | 900 | 스킬 텔레메트리 |
| `agent/curator.py` | 1,916 | 스킬 생애주기 관리 |
| `agent/memory_manager.py` | 1,081 | 메모리 통합 관리 |
| `tools/code_execution_tool.py` | ~1,848 | PTC (execute_code) |
| `cron/scheduler.py` | 3,042 | 스케줄 작업 실행 |
| `gateway/run.py` | ~18,239 | 메시징 게이트웨이 |
| `cli.py` | ~15,530 | CLI 인터페이스 |

### 디렉토리 구조

```
hermes-agent/
├── run_agent.py          # AIAgent 클래스
├── model_tools.py        # 도구 오케스트레이션
├── toolsets.py           # 도구셋 정의
├── cli.py                # CLI 인터페이스
├── agent/                # 에이전트 내부
│   ├── curator.py        # 스킬 생애주기
│   ├── memory_manager.py # 메모리 관리
│   └── prompt_builder.py # 프롬프트 구성
├── tools/                # 도구 구현
│   ├── registry.py       # 도구 등록
│   ├── skill_usage.py    # 텔레메트리
│   ├── code_execution_tool.py  # PTC
│   ├── web_tools.py      # 웹 도구
│   └── ...
├── skills/               # 번들 스킬
│   ├── github/
│   │   └── github-code-review/
│   │       └── SKILL.md
│   └── ...
├── plugins/              # 플러그인
│   ├── memory/
│   │   └── honcho/
│   └── ...
├── gateway/              # 메시징 게이트웨이
│   └── run.py
└── cron/                 # 스케줄러
    └── scheduler.py
```
