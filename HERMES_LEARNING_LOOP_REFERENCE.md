# Hermes Agent Learning Loop Architecture (참조 분석)

> **작성일**: 2026-06-10  
> **목적**: Hermes Agent의 "쓸수록 나에게 맞춰 학습·진화하는" 메커니즘 상세 분석
>
> **⚠️ 문서 성격**: 이 문서는 **RICE의 설계 문서가 아니라**, RICE의 learning loop 설계에 참조할 외부 시스템(Hermes Agent)의 코드 분석 문서다. Hermes의 어떤 구조를 RICE에 차용/단순화할지는 [RICE_REPO_STRATEGY.md](RICE_REPO_STRATEGY.md)의 9장 미결정 사항에서 별도로 결정한다.

---

## 목차

1. [개요](#개요)
2. [핵심 컴포넌트](#핵심-컴포넌트)
3. [Tool vs Skill 차이](#tool-vs-skill-차이)
4. [Learning Loop 흐름](#learning-loop-흐름)
5. [오케스트레이션 계층](#오케스트레이션-계층)
6. [Tool 생성 메커니즘](#tool-생성-메커니즘)
7. [실제 코드 위치](#실제-코드-위치)
8. [확인 필요 사항 (해소됨)](#확인-필요-사항-2026-06-11-소스-검증으로-전부-해소)

---

## 개요

Hermes Agent는 3개의 핵심 시스템으로 "경험에서 학습하고 진화"하는 기능을 구현:

1. **Skill Lifecycle System** - 경험에서 스킬 생성 → 사용하며 개선
2. **Memory System** - 세션을 넘나드는 user modeling
3. **Tool Creation System** - 런타임에 새 도구 생성

---

## 핵심 컴포넌트

### 1. Skill Lifecycle System

#### 1.1 Skill Usage Telemetry (`tools/skill_usage.py`)

**위치**: `tools/skill_usage.py` (852줄)

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

**검증됨**: ✅ 코드 확인 완료

#### 1.2 Curator (자동 스킬 관리)

**위치**: `agent/curator.py` (1844줄)

**역할**: 주기적 스킬 검토 및 생애주기 관리

**두 단계 프로세스**:

1. **자동 전환 (규칙 기반, LLM 없음)**:
```python
def apply_automatic_transitions(now: Optional[datetime] = None):
    """사용 패턴 기반 자동 상태 전이"""
    # stale_after_days, archive_after_days 설정값 기반
    # 핀된 스킬은 제외
```

2. **LLM 리뷰 (독립 AIAgent 포크)**:
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

**실행 방식**:
- 백그라운드 스레드 (비동기)
- 사용자 대화 방해 안 함
- 설정: `curator.enabled`, `curator.interval_hours`

**검증됨**: ✅ 코드 확인 완료

#### 1.3 Curator Backup System

**위치**: `agent/curator_backup.py`

**역할**: 모든 변경 전 자동 스냅샷

**특징**:
- tar.gz 형식
- `~/.hermes/skills/.curator_backups/` 저장
- 롤백 가능

**검증됨**: ✅ 파일 존재 확인 (내용 미확인)

### 2. Memory System

#### 2.1 Memory Manager

**위치**: `agent/memory_manager.py` (653줄)

**역할**: 여러 메모리 제공자 통합 관리

**핵심 메서드**:
```python
class MemoryManager:
    def sync_turn(user_content, assistant_content, session_id, messages)
        """매 대화 턴마다 메모리 동기화"""
    
    def prefetch_all(query, session_id)
        """다음 턴 전에 관련 컨텍스트 미리 로드"""
    
    def on_session_switch(new_session_id, parent_session_id, reset, rewound)
        """세션 전환 시 메모리 상태 유지"""
    
    def on_delegation(task, result, child_session_id)
        """서브에이전트 작업 결과 기록"""
```

**라이프사이클 훅**:
- `on_turn_start` - 턴 시작
- `on_session_end` - 세션 종료
- `on_pre_compress` - 컨텍스트 압축 전
- `on_memory_write` - 메모리 쓰기 시

**검증됨**: ✅ 코드 확인 완료

#### 2.2 Honcho Plugin (Cross-session User Modeling)

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

**검증됨**: ✅ 코드 확인 완료

### 3. Cron Scheduler (자동 실행)

**위치**: `cron/scheduler.py` (2216줄)

**역할**: 스케줄 기반 작업 실행

**특징**:
- 작업당 독립 AIAgent
- 멀티 플랫폼 전달 (Telegram, Discord 등)
- 3분 하드 인터럽트 (무한 루프 방지)

**검증됨**: ✅ 코드 확인 완료

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

**검증됨**: ✅ 코드 확인 완료

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

**시스템 프롬프트 주입**:
```python
# agent/prompt_builder.py
def build_skills_system_prompt():
    """활성화된 스킬들을 시스템 프롬프트에 주입"""
    # SKILL.md 내용을 읽어서 프롬프트에 포함
```

**검증됨**: ✅ 코드 확인 완료

### 비교표

| 측면 | Built-in Tool | Skill |
|------|--------------|-------|
| **본질** | 실행 가능한 함수 | 절차적 지식 문서 |
| **형태** | Python 코드 | Markdown 문서 |
| **위치** | `tools/*.py` | `skills/*/SKILL.md` |
| **LLM 접근** | tool_calls로 직접 호출 | 시스템 프롬프트에 주입 |
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
3. Memory prefetch (관련 컨텍스트 로드)
   ↓
4. LLM 추론 + Tool 호출
   ↓
5. Tool 실행 (handle_function_call)
   ↓
6. skill_usage.bump_use() 자동 호출 (텔레메트리)
   ↓
7. Memory sync (턴 종료 시)
   ↓
8. 응답 반환
   ↓
9. (백그라운드) Curator 주기적 실행
   - 자동 상태 전이
   - LLM 리뷰 (중복 통합, 품질 개선)
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
memory_manager.sync_turn(user_msg, assistant_response)
   ↓
끝 (스킬 생성 없음 - web_search는 이미 built-in)
```

**검증됨**: ✅ 흐름 확인 완료

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
  - 스킬 자동 로드 (시스템 프롬프트에 주입)
  - LLM이 문서화된 절차 따라서 더 빠르고 정확하게 수행
```

**확인 필요**: ⚠️ LLM이 언제 스킬 생성을 판단하는지 정확한 트리거 조건

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

**검증됨**: ✅ 아키텍처 확인 완료

### AIAgent 핵심 루프

**위치**: `run_agent.py` (약 12,000줄)

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
        self._memory_manager.sync_turn(user_msg, assistant_response)
        
        return response
```

**검증됨**: ✅ 코드 확인 완료

---

## Tool 생성 메커니즘

### 3가지 방법

#### 1. `execute_code` - 프로그래밍 방식 도구 생성 (PTC)

**위치**: `tools/code_execution_tool.py` (약 2,000줄)

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

**예시**:
```python
execute_code(code="""
import hermes_tools as ht
import json

def analyze_pr(pr_number):
    # PR 정보 가져오기
    pr_info = ht.terminal(f"gh pr view {pr_number} --json files")
    files = json.loads(pr_info)['files']
    
    # 각 파일 분석
    issues = []
    for file in files:
        content = ht.read_file(file['path'])
        if 'password' in content.lower():
            issues.append(f"{file['path']}: 하드코딩된 비밀번호")
    
    return {"issues": issues, "file_count": len(files)}

result = analyze_pr(123)
print(json.dumps(result))
""")
```

**특징**:
- ✅ 런타임에 새 도구 생성
- ✅ 기존 도구 조합
- ✅ 즉시 실행
- ✅ 재사용 가능 (같은 코드 재실행)
- ❌ 일회성 (디스크 저장 안 됨)

**검증됨**: ✅ 코드 확인 완료

#### 2. Skill 내 스크립트 - 재사용 가능한 도구

**원리**:
1. `skill_manage(action="write_file", file_path="scripts/tool.py", ...)`
2. `~/.hermes/skills/<skill>/scripts/` 디렉토리에 저장
3. `terminal("python scripts/tool.py ...")` 로 호출

**예시**:
```markdown
# skills/my-analyzer/SKILL.md
---
name: my-analyzer
---

## Scripts

### scripts/analyze.py
```python
#!/usr/bin/env python3
import sys, json, subprocess

def analyze_codebase(path):
    result = subprocess.run(['ruff', 'check', path], capture_output=True)
    issues = parse_ruff_output(result.stdout)
    return {"issues": issues}

if __name__ == "__main__":
    result = analyze_codebase(sys.argv[1])
    print(json.dumps(result))
```

## Usage
```bash
python ~/.hermes/skills/my-analyzer/scripts/analyze.py /path/to/code
```
```

**특징**:
- ✅ 영구 저장 (디스크)
- ✅ 버전 관리 가능 (Git)
- ✅ Curator 관리 대상
- ✅ 세션 간 재사용
- ❌ Built-in처럼 tool_calls로 직접 호출 불가

**검증됨**: ✅ 패턴 확인 완료 (실제 예시는 미확인)

#### 3. Plugin System - 진짜 새 도구 등록

**위치**: `~/.hermes/plugins/<name>/`

**구조**:
```
~/.hermes/plugins/my-tool/
├── plugin.yaml
└── __init__.py
```

**예시**:
```yaml
# plugin.yaml
name: my-custom-tool
version: 1.0.0
description: Custom tool for X
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

**검증됨**: ✅ 플러그인 시스템 확인 완료

### 비교표

| 방법 | 생성 방식 | 지속성 | 사용 방식 | Built-in 동등 |
|------|----------|--------|----------|--------------|
| **execute_code** | 런타임 Python 코드 | 일회성 | 즉시 실행 | ❌ |
| **Skill 스크립트** | skill_manage로 저장 | 영구 | terminal 호출 | ❌ |
| **Plugin** | 사용자 직접 작성 | 영구 | tool_calls 호출 | ✅ |

---

## 실제 코드 위치

### 핵심 파일

| 파일 | 줄 수 | 역할 |
|------|------|------|
| `run_agent.py` | ~12,000 | AIAgent 클래스, 메인 대화 루프 |
| `model_tools.py` | ~150 | 도구 오케스트레이션, 디스패치 |
| `tools/registry.py` | ~150 | 도구 등록 시스템 |
| `toolsets.py` | ~100 | 도구셋 정의 |
| `tools/skill_usage.py` | 852 | 스킬 텔레메트리 |
| `agent/curator.py` | 1,844 | 스킬 생애주기 관리 |
| `agent/memory_manager.py` | 653 | 메모리 통합 관리 |
| `tools/code_execution_tool.py` | ~2,000 | PTC (execute_code) |
| `cron/scheduler.py` | 2,216 | 스케줄 작업 실행 |
| `gateway/run.py` | ~2,000+ | 메시징 게이트웨이 |
| `cli.py` | ~11,000 | CLI 인터페이스 |

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

---
## 데이터 저장 방식 (Data Storage Architecture)

### 1. 세션 데이터 (Session Data)

**저장소: SQLite (`~/.hermes/state.db`)**

```python
# 위치: hermes_state.py
DEFAULT_DB_PATH = get_hermes_home() / "state.db"
SCHEMA_VERSION = 14

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

**각 프로바이더는 독자적인 저장 방식 사용:**

| 프로바이더 | 저장 방식 | 위치 |
|-----------|----------|------|
| **Honcho** | PostgreSQL + 임베딩 (원격) | Honcho 서버 |
| **Mem0** | 벡터DB (Qdrant/Chroma/...) | 설정에 따라 로컬/원격 |
| **Supermemory** | 자체 백엔드 | 원격 API |
| **Hindsight** | SQLite + 임베딩 | `~/.hermes/hindsight/` |
| **Holographic** | 파일 기반 | `~/.hermes/holographic/` |
| **OpenViking** | 벡터DB | 설정에 따라 |

### 5. 설정 파일 (Configuration)

**저장소: YAML + JSON**

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


## 확인 필요 사항 (2026-06-11 소스 검증으로 전부 해소)

> 아래 6개 항목은 `hermes-agent` 소스 직접 확인으로 해소됨.

### 1. 스킬 생성 트리거 조건 ✅

**답**: 프로그램적 감지 로직은 없고, **시스템 프롬프트의 명시적 가이드라인**으로 LLM이 자율 판단한다.

`agent/prompt_builder.py`의 `SKILLS_GUIDANCE`:
> "After completing a complex task (**5+ tool calls**), fixing a tricky error, or discovering a non-trivial workflow, save the approach as a skill with skill_manage so you can reuse it next time."

추가로 스킬 사용 중 내용이 낡았으면 **묻지 않고 즉시 patch**하라는 지시도 포함:
> "When using a skill and finding it outdated, incomplete, or wrong, patch it immediately with skill_manage(action='patch') — don't wait to be asked."

- 사용자 명시 요청 불필요 (에이전트 자율)
- 반복 작업 감지 같은 별도 로직 없음 — 순수 프롬프트 기반

### 2. Curator 실행 주기 ✅

**답** (`agent/curator.py`):
- 기본 주기: `DEFAULT_INTERVAL_HOURS = 24 * 7` (**7일**)
- 추가 게이트: `DEFAULT_MIN_IDLE_HOURS = 2` (사용자 유휴 2시간 이상일 때만)
- 상태 전이 기본값: stale 30일, archive 90일
- 트리거: 타이머가 아니라 `maybe_run_curator()` 호출 시점에 내부적으로 interval/idle/enabled 조건을 검사하는 **기회적(opportunistic) 실행**
- Gateway에서도 실행됨 (`gateway/run.py`에서 `maybe_run_curator()` 호출 확인)

### 3. Memory Provider 선택 ✅

**답** (`agent/memory_manager.py`):
- builtin provider는 **항상** 첫 번째로 등록
- 외부 provider는 **동시에 1개만 허용** — 두 번째 등록 시도는 경고와 함께 거부됨
- 선택은 `memory.provider` 설정으로
- 한 provider의 실패가 다른 provider를 막지 않음 (failure isolation)

### 4. execute_code 보안 ✅

**답** (`tools/code_execution_tool.py`): 보안 수준은 **백엔드에 따라 다름**.

공통 제한:
- 환경 변수 스크러빙 (allowlist 기반, API 키 등 시크릿 차단)
- 샌드박스 내 도구 7개로 제한: `web_search`, `web_extract`, `read_file`, `write_file`, `search_files`, `patch`, `terminal`
- 타임아웃 기본 5분, 툴 호출 최대 50회, stdout 50KB / stderr 10KB 캡

백엔드별:
| 백엔드 | 격리 수준 |
|--------|----------|
| local (자식 프로세스) | **파일시스템/네트워크 격리 없음** — env 스크럽 + 도구 제한만 |
| docker / singularity / modal / daytona | 컨테이너 격리 + 리소스 제한 (기본 CPU 1, 메모리 5120MB, 디스크 51200MB) |

→ RICE 시사점: 마켓에서 받은 코드를 실행한다면 local 백엔드 수준으로는 부족하고, 컨테이너 백엔드에 준하는 경계가 필요.

### 5. Skill vs Plugin 선택 기준 ✅

**답**: 공식 가이드라인 문서는 없음. 다만 코드 구조상 역할이 명확히 갈림:
- **Skill**: 에이전트가 자율 생성/패치 (시스템 프롬프트에 생성 가이드 존재, Curator 관리 대상)
- **Plugin**: 사람(사용자/개발자)이 직접 작성하는 확장 포인트 (에이전트에게 plugin 생성 가이드는 프롬프트에 없음)

즉 기존 추측("Skill = 절차 지식, Plugin = 새 기능/외부 통합")이 맞으며, 생성 주체로도 구분된다: **Skill은 에이전트가, Plugin은 사람이 만든다.**

### 6. 프롬프트 캐싱 ✅

**답**:
- 스킬은 시스템 프롬프트에 주입되고, 시스템 프롬프트 전체가 **prefix caching** 대상
- 설계 의도가 코드 주석에 명시됨: "token cost is paid once at install and then amortised across all sessions via prefix caching"
- `anthropic_prompt_cache_policy()` (`agent/agent_runtime_helpers.py`)가 프로바이더별로 `cache_control` 마커 적용 여부와 배치 레이아웃(native vs envelope)을 결정
- 별도의 명시적 캐시 무효화 전략은 없음 — 스킬 변경 시 프롬프트 prefix가 바뀌어 **자연스럽게 캐시 미스 → 재캐시**되는 구조 (변경이 드물다는 전제)

---

## 결론

Hermes Agent의 학습·진화 메커니즘은 다음 3가지 축으로 구현됨:

1. **Skill Lifecycle** - 경험을 문서화하고 자동으로 관리
2. **Memory System** - 사용자 패턴을 학습하고 세션 간 유지
3. **Tool Creation** - 런타임에 새 도구를 프로그래밍으로 생성

핵심은 **분산 자율 시스템**:
- 중앙 플래너 없음
- 각 컴포넌트가 자율적으로 판단
- LLM은 전술적 도구 선택
- 규칙 엔진은 전략적 생애주기 관리

**검증 상태**:
- ✅ 핵심 코드 확인 완료
- ✅ "확인 필요 사항" 6개 항목 전부 소스 검증으로 해소 (2026-06-11)

---

**문서 버전**: 1.1  
**최종 업데이트**: 2026-06-11  
**작성자**: 코드 분석 기반 정리