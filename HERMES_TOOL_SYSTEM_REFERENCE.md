# Hermes Agent Tool System (참조 분석)

> Skill이 절차적 지식 문서라면, Tool은 LLM이 직접 호출하는 실행 가능한 함수다.  
> 이 문서는 Tool이 어디에 있고, 어떻게 등록·관리·노출되는지를 코드 기반으로 정리한 참조 문서다.

---

## 목차

1. [핵심 파일 구조](#1-핵심-파일-구조)
2. [Tool 등록 메커니즘](#2-tool-등록-메커니즘)
3. [Toolset 시스템](#3-toolset-시스템)
4. [Tool 노출 흐름 (LLM까지의 경로)](#4-tool-노출-흐름-llm까지의-경로)
5. [전체 Built-in Tool 목록](#5-전체-built-in-tool-목록)
6. [특수 Tool 카테고리](#6-특수-tool-카테고리)
7. [MCP Tool 통합](#7-mcp-tool-통합)
8. [Plugin Tool 통합](#8-plugin-tool-통합)
9. [Tool vs Skill — 설계 철학](#9-tool-vs-skill--설계-철학)
10. [Tool 추가 방법 (치트시트)](#10-tool-추가-방법-치트시트)

---

## 1. 핵심 파일 구조

```
tools/                    ← 모든 built-in tool 구현 위치
├── registry.py           ← 싱글턴 ToolRegistry — 모든 Tool의 중앙 저장소
├── terminal_tool.py      ← terminal, process
├── web_tools.py          ← web_search, web_extract
├── file_tools.py         ← read_file, write_file, patch, search_files
├── browser_tool.py       ← browser_navigate, browser_snapshot, ...
├── memory_tool.py        ← memory (MEMORY.md + USER.md 관리)
├── skill_manager_tool.py ← skill_manage, skills_list, skill_view
├── delegate_tool.py      ← delegate_task (서브에이전트 스폰)
├── code_execution_tool.py← execute_code (Python-in-Python 샌드박스)
├── vision_tools.py       ← vision_analyze, video_analyze
├── todo_tool.py          ← todo (에이전트 루프에서 인터셉트)
├── session_search_tool.py← session_search
├── clarify_tool.py       ← clarify
├── cronjob_tools.py      ← cronjob
├── mcp_tool.py           ← MCP 서버 연결 & 동적 등록
├── homeassistant_tool.py ← ha_* (HASS_TOKEN 게이팅)
├── kanban_tools.py       ← kanban_* (HERMES_KANBAN_TASK 게이팅)
├── discord_tool.py       ← discord, discord_admin
├── image_generation_tool.py ← image_generate
├── tts_tool.py           ← text_to_speech
├── computer_use_tool.py  ← computer_use (cua-driver 게이팅)
└── ...

toolsets.py               ← 플랫폼별 Tool 묶음(Toolset) 정의
model_tools.py            ← 오케스트레이션 레이어 — 발견·필터링·LLM 스키마 반환
```

---

## 2. Tool 등록 메커니즘

### 2.1 자동 발견 (Auto-Discovery)

`model_tools.py`가 임포트될 때 모듈 레벨에서 자동 발견이 실행된다.

```python
# model_tools.py — 모듈 레벨
discover_builtin_tools()   # tools/*.py를 AST로 스캔 → 각 파일 임포트
```

`discover_builtin_tools()`는 `tools/*.py` 파일을 **AST 정적 분석**으로 스캔해,
최상위 레벨에 `registry.register(...)` 호출이 있는 파일만 임포트한다.

```python
# tools/registry.py
def _module_registers_tools(module_path: Path) -> bool:
    """모듈 본문(body)에 registry.register() 최상위 호출이 있는지 확인"""
    # 함수 내부의 register() 호출은 무시 → 의도치 않은 임포트 방지
```

> 💡 **핵심**: `tools/` 디렉토리에 새 파일을 만들고 최상위에 `registry.register()`를 넣으면
> 자동으로 발견된다. import 목록을 수동으로 관리하지 않아도 된다.

### 2.2 `registry.register()` 호출 패턴

각 tool 파일은 모듈 최하단에서 자신을 등록한다.

```python
# tools/web_tools.py (패턴 예시)
from tools.registry import registry

def check_web_requirements() -> bool:
    """web_search가 사용 가능한지 확인 (API 키 체크 등)"""
    return bool(os.getenv("EXA_API_KEY") or os.getenv("TAVILY_API_KEY") or ...)

def web_search_tool(query: str, ...) -> str:
    # ... 구현 ...
    return json.dumps({"results": [...]})

registry.register(
    name="web_search",              # 도구 이름 (LLM이 호출하는 이름)
    toolset="web",                  # 소속 툴셋
    schema={
        "name": "web_search",
        "description": "Search the web...",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "..."},
            },
            "required": ["query"],
        },
    },
    handler=lambda args, **kw: web_search_tool(
        query=args.get("query", ""), task_id=kw.get("task_id")
    ),
    check_fn=check_web_requirements,  # None이면 항상 활성
    requires_env=["EXA_API_KEY"],     # 설정 UI에 표시용 (실제 게이팅은 check_fn)
    emoji="🔍",
)
```

### 2.3 `ToolEntry` 구조

```python
class ToolEntry:
    name: str                    # 도구 이름
    toolset: str                 # 소속 툴셋
    schema: dict                 # OpenAI 포맷 스키마
    handler: Callable            # 실행 함수
    check_fn: Callable | None    # 가용성 체크 (None = 항상 활성)
    requires_env: list           # 필요한 환경변수 (UI 표시용)
    is_async: bool               # async 핸들러 여부 (자동 브리징)
    emoji: str                   # 스피너 UI 표시용 이모지
    max_result_size_chars: int   # 결과 잘라내기 한계 (기본: budget_config.py)
    dynamic_schema_overrides: Callable | None  # 런타임 스키마 수정
```

### 2.4 `check_fn` — 조건부 Tool 노출

`check_fn`은 Tool이 LLM 스키마에 포함될지 결정하는 가용성 검사 함수다.

```python
# 예: Home Assistant — HASS_TOKEN 환경변수가 있을 때만 노출
def check_ha_requirements() -> bool:
    return bool(os.getenv("HASS_TOKEN"))

registry.register("ha_list_entities", "homeassistant", ..., check_fn=check_ha_requirements)
```

**체크 결과는 30초 TTL로 캐시된다.**  
→ `hermes tools`로 설정 변경 시 다음 API 호출에서 자동 반영 (즉각 무효화 불필요).

### 2.5 `dynamic_schema_overrides` — 런타임 스키마 수정

스키마 일부를 런타임 설정에 따라 동적으로 변경할 때 사용.

```python
# 예: delegate_task — 현재 설정의 max_concurrent_children 값을 스키마 설명에 반영
registry.register(
    "delegate_task", ...,
    dynamic_schema_overrides=lambda: _build_delegate_overrides(),
)
```

`get_tool_definitions()` 호출 시마다 오버라이드가 병합되어 반환된다.

---

## 3. Toolset 시스템

### 3.1 Toolset이란

Tool을 **플랫폼·시나리오별로 묶는 명명된 그룹**이다.  
LLM에는 Toolset 단위로 Tool이 노출되거나 차단된다.

```
Toolset "web"        → [web_search, web_extract]
Toolset "terminal"   → [terminal, process]
Toolset "hermes-cli" → _HERMES_CORE_TOOLS (전체 기본 묶음)
```

### 3.2 `_HERMES_CORE_TOOLS` — 플랫폼 공통 기본 묶음

```python
# toolsets.py
_HERMES_CORE_TOOLS = [
    # 웹
    "web_search", "web_extract",
    # 터미널
    "terminal", "process", "read_terminal",
    # 파일
    "read_file", "write_file", "patch", "search_files",
    # 비전·이미지
    "vision_analyze", "image_generate",
    # 스킬 관리
    "skills_list", "skill_view", "skill_manage",
    # 브라우저 자동화
    "browser_navigate", "browser_snapshot", "browser_click",
    "browser_type", "browser_scroll", "browser_back",
    "browser_press", "browser_get_images",
    "browser_vision", "browser_console", "browser_cdp", "browser_dialog",
    # TTS
    "text_to_speech",
    # 플래닝·메모리
    "todo", "memory",
    # 세션 검색
    "session_search",
    # 질의
    "clarify",
    # 코드 실행·위임
    "execute_code", "delegate_task",
    # 크론잡
    "cronjob",
    # Home Assistant (HASS_TOKEN 게이팅)
    "ha_list_entities", "ha_get_state", "ha_list_services", "ha_call_service",
    # Kanban (HERMES_KANBAN_TASK 게이팅)
    "kanban_show", "kanban_list", "kanban_complete", "kanban_block",
    "kanban_heartbeat", "kanban_comment", "kanban_create", "kanban_link", "kanban_unblock",
    # Computer Use (cua-driver 게이팅)
    "computer_use",
]
```

> `hermes-cli`, `hermes-telegram`, `hermes-slack` 등 대부분의 플랫폼 툴셋이 이 목록을 그대로 사용.

### 3.3 플랫폼별 툴셋 목록

| 툴셋 이름 | 설명 |
|-----------|------|
| `hermes-cli` | 기본 CLI (= `_HERMES_CORE_TOOLS`) |
| `hermes-cron` | 크론 실행 (= `_HERMES_CORE_TOOLS`) |
| `hermes-telegram` | 텔레그램 봇 |
| `hermes-discord` | 디스코드 봇 (`+ discord, discord_admin`) |
| `hermes-slack` | 슬랙 봇 |
| `hermes-whatsapp` | WhatsApp 봇 |
| `hermes-signal` | Signal 봇 |
| `hermes-matrix` | Matrix 봇 |
| `hermes-mattermost` | Mattermost 봇 |
| `hermes-email` | 이메일 봇 |
| `hermes-sms` | SMS 봇 (Twilio) |
| `hermes-bluebubbles` | iMessage 봇 |
| `hermes-feishu` | 飞书/Lark 봇 (`+ feishu_doc_read` 등) |
| `hermes-dingtalk` | DingTalk 봇 |
| `hermes-wecom` | WeCom 봇 |
| `hermes-weixin` | WeChat 봇 |
| `hermes-qqbot` | QQ 봇 |
| `hermes-yuanbao` | 元宝 봇 (`+ yb_*` 도구) |
| `hermes-webhook` | Webhook 수신 (`_HERMES_WEBHOOK_SAFE_TOOLS`만 — 프롬프트 인젝션 방어) |
| `hermes-acp` | VS Code/Zed/JetBrains 에디터 통합 |
| `hermes-api-server` | OpenAI 호환 HTTP API |
| `coding` | 코딩 집중 툴셋 (posture — `agent/coding_context.py`에서 자동 선택) |

### 3.4 시나리오별 단위 툴셋

| 툴셋 | Tool |
|------|------|
| `web` | `web_search`, `web_extract` |
| `search` | `web_search` |
| `terminal` | `terminal`, `process` |
| `file` | `read_file`, `write_file`, `patch`, `search_files` |
| `browser` | 브라우저 도구 전체 + `web_search` |
| `vision` | `vision_analyze` |
| `image_gen` | `image_generate` |
| `video` | `video_analyze` |
| `video_gen` | `video_generate` |
| `computer_use` | `computer_use` |
| `skills` | `skills_list`, `skill_view`, `skill_manage` |
| `memory` | `memory` |
| `todo` | `todo` |
| `clarify` | `clarify` |
| `session_search` | `session_search` |
| `code_execution` | `execute_code` |
| `delegation` | `delegate_task` |
| `cronjob` | `cronjob` |
| `tts` | `text_to_speech` |
| `moa` | `mixture_of_agents` |
| `homeassistant` | `ha_*` |
| `kanban` | `kanban_*` |
| `discord` | `discord` |
| `discord_admin` | `discord_admin` |
| `spotify` | `spotify_*` |
| `feishu_doc` | `feishu_doc_read` |
| `feishu_drive` | `feishu_drive_*` |
| `yuanbao` | `yb_*` |
| `x_search` | `x_search` (xAI/슈퍼그록 게이팅) |
| `safe` | `web_search`, `web_extract`, `vision_analyze`, `image_generate` |
| `debugging` | `terminal`, `process` + web + file |

### 3.5 툴셋 활성화·비활성화

```bash
hermes tools              # curses UI로 토글
```

또는 `config.yaml`:
```yaml
tools:
  telegram:
    enabled: ["web", "file"]   # 이것만 활성
    disabled: ["terminal"]     # 이것만 비활성
```

> ⚠️ **`hermes-*` 번들을 `disabled_toolsets`에 넣으면 core tools 보호 로직이 동작함.**  
> `bundle_non_core_tools()`가 `_HERMES_CORE_TOOLS`와 겹치는 부분을 제거해 core는 유지.

---

## 4. Tool 노출 흐름 (LLM까지의 경로)

```
AIAgent.__init__()
  │
  ├─ get_tool_definitions(enabled_toolsets, disabled_toolsets)
  │    │
  │    ├─ 1. toolsets.resolve_toolset()  → 활성 툴 이름 집합 수집
  │    │      (enabled_toolsets 기준, 없으면 전체)
  │    │
  │    ├─ 2. disabled_toolsets 빼기
  │    │      hermes-* 번들은 non-core 부분만 제거
  │    │
  │    ├─ 3. registry.get_definitions(tools_set)
  │    │      check_fn TTL-캐시 (30s) 통과한 도구만 포함
  │    │
  │    ├─ 4. 동적 스키마 후처리
  │    │      execute_code → 실제 가용 sandbox 도구 목록 반영
  │    │      discord/discord_admin → 봇 권한 기반 스키마 교체
  │    │      browser_navigate → web_search 없으면 설명에서 참조 제거
  │    │
  │    ├─ 5. schema_sanitizer → llama.cpp 호환성 정규화
  │    │
  │    └─ 6. Tool Search 조립 (MCP/플러그인 도구 과다 시)
  │           비-core 도구를 tool_search/describe/call 3개로 접기
  │           (context window 대비 임계값 초과 시)
  │
  └─ LLM API 호출에 tool_definitions 전달
```

**메모이제이션**: `quiet_mode=True`일 때 `_tool_defs_cache`(최대 8개 LRU)에 캐시.  
캐시 키: `(enabled_toolsets, disabled_toolsets, registry._generation, config_mtime, HERMES_KANBAN_TASK)`

---

## 5. 전체 Built-in Tool 목록

### 웹·검색

| Tool | 파일 | 게이팅 | 설명 |
|------|------|--------|------|
| `web_search` | `tools/web_tools.py` | API 키 필요 (Exa/Firecrawl/Tavily/Parallel 중 하나) | 웹 검색 |
| `web_extract` | `tools/web_tools.py` | 동일 | URL 내용 추출 |
| `x_search` | `tools/x_search_tool.py` | xAI 크레덴셜 또는 XAI_API_KEY | X(트위터) 검색 |

### 터미널·프로세스

| Tool | 파일 | 게이팅 | 설명 |
|------|------|--------|------|
| `terminal` | `tools/terminal_tool.py` | 없음 (local), Docker/Modal 조건부 | 명령어 실행 (local/docker/modal/ssh/daytona) |
| `process` | `tools/terminal_tool.py` | 없음 | 백그라운드 프로세스 관리 |
| `read_terminal` | `tools/read_terminal_tool.py` | `HERMES_DESKTOP` env | 데스크톱 앱 임베디드 터미널 읽기 |

### 파일

| Tool | 파일 | 게이팅 | 설명 |
|------|------|--------|------|
| `read_file` | `tools/file_tools.py` | 없음 | 파일 읽기 (에이전트 루프에서 보안 체크) |
| `write_file` | `tools/file_tools.py` | 없음 | 파일 쓰기 |
| `patch` | `tools/file_tools.py` | 없음 | 파일 패치 (fuzzy 매칭) |
| `search_files` | `tools/file_tools.py` | 없음 | 파일 내용·이름 검색 (에이전트 루프에서 인터셉트) |

### 브라우저 자동화

| Tool | 파일 | 게이팅 | 설명 |
|------|------|--------|------|
| `browser_navigate` | `tools/browser_tool.py` | playwright 설치 | URL 탐색 |
| `browser_snapshot` | `tools/browser_tool.py` | 동일 | 페이지 스냅샷 (접근성 트리) |
| `browser_click` | `tools/browser_tool.py` | 동일 | 클릭 |
| `browser_type` | `tools/browser_tool.py` | 동일 | 텍스트 입력 |
| `browser_scroll` | `tools/browser_tool.py` | 동일 | 스크롤 |
| `browser_back` | `tools/browser_tool.py` | 동일 | 뒤로 가기 |
| `browser_press` | `tools/browser_tool.py` | 동일 | 키 입력 |
| `browser_get_images` | `tools/browser_tool.py` | 동일 | 이미지 수집 |
| `browser_vision` | `tools/browser_tool.py` | 동일 | 스크린샷 + 비전 분석 |
| `browser_console` | `tools/browser_tool.py` | 동일 | 콘솔 로그 읽기 |
| `browser_cdp` | `tools/browser_cdp_tool.py` | 동일 | Chrome DevTools Protocol |
| `browser_dialog` | `tools/browser_dialog_tool.py` | 동일 | 대화상자 처리 |

### 비전·미디어

| Tool | 파일 | 게이팅 | 설명 |
|------|------|--------|------|
| `vision_analyze` | `tools/vision_tools.py` | 비전 지원 모델 | 이미지 분석 |
| `video_analyze` | `tools/vision_tools.py` | 비디오 지원 모델 | 비디오 분석 (기본 OFF) |
| `image_generate` | `tools/image_generation_tool.py` | 이미지 생성 프로바이더 설정 | 이미지 생성 |
| `video_generate` | `tools/video_generation_tool.py` | 비디오 생성 프로바이더 설정 | 비디오 생성 (기본 OFF) |
| `text_to_speech` | `tools/tts_tool.py` | Edge TTS (무료)/ElevenLabs/OpenAI/xAI | TTS |

### 스킬 관리

| Tool | 파일 | 게이팅 | 설명 |
|------|------|--------|------|
| `skills_list` | `tools/skill_manager_tool.py` | 없음 | 스킬 목록 조회 |
| `skill_view` | `tools/skill_manager_tool.py` | 없음 | 스킬 내용 조회 |
| `skill_manage` | `tools/skill_manager_tool.py` | 없음 | 스킬 생성·수정·삭제 |

### 플래닝·메모리·세션

| Tool | 파일 | 게이팅 | 에이전트 루프 인터셉트 | 설명 |
|------|------|--------|----------------------|------|
| `todo` | `tools/todo_tool.py` | 없음 | ✅ | 할일 관리 |
| `memory` | `tools/memory_tool.py` | 없음 | ✅ | MEMORY.md + USER.md 읽기·쓰기 |
| `session_search` | `tools/session_search_tool.py` | 없음 | ✅ | 과거 세션 시맨틱 검색 |
| `clarify` | `tools/clarify_tool.py` | 없음 | - | 사용자에게 질문 |

### 코드 실행·위임

| Tool | 파일 | 게이팅 | 에이전트 루프 인터셉트 | 설명 |
|------|------|--------|----------------------|------|
| `execute_code` | `tools/code_execution_tool.py` | 없음 | - | Python 코드 실행 (샌드박스) |
| `delegate_task` | `tools/delegate_tool.py` | 없음 | ✅ | 서브에이전트 스폰 |

### 스케줄링

| Tool | 파일 | 설명 |
|------|------|------|
| `cronjob` | `tools/cronjob_tools.py` | 크론잡 생성·관리 |

### Home Assistant (서비스 게이팅)

| Tool | 게이팅 조건 |
|------|------------|
| `ha_list_entities` | `HASS_TOKEN` 환경변수 |
| `ha_get_state` | 동일 |
| `ha_list_services` | 동일 |
| `ha_call_service` | 동일 (추가 보안: `shell_command` 등 차단 도메인 목록) |

### Kanban (서비스 게이팅)

| Tool | 게이팅 조건 |
|------|------------|
| `kanban_show` | `HERMES_KANBAN_TASK` env 또는 kanban 툴셋 명시 활성화 |
| `kanban_list` | 동일 |
| `kanban_complete` | 동일 |
| `kanban_block` | 동일 |
| `kanban_heartbeat` | 동일 |
| `kanban_comment` | 동일 |
| `kanban_create` | 동일 |
| `kanban_link` | 동일 |
| `kanban_unblock` | 동일 |

### 플랫폼별 Tool

| Tool | 플랫폼 | 파일 |
|------|--------|------|
| `discord` | Discord | `tools/discord_tool.py` |
| `discord_admin` | Discord | `tools/discord_tool.py` |
| `yb_query_group_info` | 元宝 | `tools/yuanbao_tools.py` |
| `yb_query_group_members` | 元宝 | `tools/yuanbao_tools.py` |
| `yb_send_dm` | 元宝 | `tools/yuanbao_tools.py` |
| `yb_search_sticker` | 元宝 | `tools/yuanbao_tools.py` |
| `yb_send_sticker` | 元宝 | `tools/yuanbao_tools.py` |
| `feishu_doc_read` | 飞书 | `tools/feishu_doc_tool.py` |
| `feishu_drive_*` | 飞书 | `tools/feishu_drive_tool.py` |
| `spotify_*` | Spotify | (plugins/spotify) |

### 컴퓨터 제어

| Tool | 파일 | 게이팅 |
|------|------|--------|
| `computer_use` | `tools/computer_use_tool.py` | cua-driver 설치 여부 |

### 기타·특수

| Tool | 파일 | 설명 |
|------|------|------|
| `mixture_of_agents` | `tools/mixture_of_agents_tool.py` | MoA 추론 (기본 OFF) |
| `tool_search` | `tools/tool_search.py` | MCP/플러그인 도구 over-limit 시 동적 등장 |
| `tool_describe` | `tools/tool_search.py` | 동일 |
| `tool_call` | `tools/tool_search.py` | 동일 |

---

## 6. 특수 Tool 카테고리

### 6.1 에이전트 루프 인터셉트 도구

일부 Tool은 `model_tools.handle_function_call()`에 도달하기 전에
`run_agent.py`에서 **인터셉트**되어 에이전트 레벨 상태를 직접 사용한다.

```python
# model_tools.py
_AGENT_LOOP_TOOLS = {"todo", "memory", "session_search", "delegate_task"}
```

레지스트리에 스키마는 등록되어 있지만, 실제 실행은 `run_agent.py`가 담당.

### 6.2 check_fn 없음 = 항상 포함

`check_fn=None`인 도구는 툴셋에 들어있기만 하면 항상 LLM에 노출된다.  
대부분의 core 도구가 이에 해당 (`terminal`, `read_file`, `web_search` 등은 API 키 체크).

### 6.3 Webhook 안전 툴셋

신뢰할 수 없는 외부 콘텐츠(PR 제목, 댓글 등)가 트리거하는 Webhook은
프롬프트 인젝션 방어를 위해 의도적으로 제한된 툴셋을 사용한다.

```python
_HERMES_WEBHOOK_SAFE_TOOLS = ["web_search", "web_extract", "vision_analyze", "clarify"]
```

terminal, file, execute_code 등 로컬 실행 도구는 제외.

### 6.4 Tool Search (Progressive Disclosure)

MCP + 플러그인 도구가 많아서 context window 대비 임계값(기본 10%)을 초과하면,
core 도구는 유지하되 비-core 도구들을 3개의 브릿지 도구로 접는다.

```
[tool_search]   → 이름/설명 기반 도구 검색
[tool_describe] → 특정 도구의 전체 스키마 조회
[tool_call]     → 찾은 도구 실제 호출
```

LLM이 도구를 쓰고 싶을 때 먼저 `tool_search`로 찾고, `tool_describe`로 확인하고,
`tool_call`로 실행하는 방식.

---

## 7. MCP Tool 통합

### 7.1 설정

`~/.hermes/config.yaml`의 `mcp_servers` 섹션:

```yaml
mcp_servers:
  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    timeout: 120
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "ghp_..."
    supports_parallel_tool_calls: true
  remote_api:
    url: "https://my-mcp-server.example.com/mcp"
    headers:
      Authorization: "Bearer sk-..."
```

### 7.2 동작 방식

```
config.yaml mcp_servers 읽기
  ↓
각 서버에 stdio/HTTP/SSE 연결
  ↓
서버의 tool 목록 조회 (tools/list)
  ↓
각 tool을 registry.register("mcp-<server>-<tool>", toolset="mcp-<server>", ...)로 등록
  ↓
이후 일반 built-in tool처럼 동작
```

**지연 발견**: 각 진입점(CLI, Gateway, TUI, ACP)이 시작 시 명시적으로 `discover_mcp_tools()` 호출.  
(과거에는 모듈 레벨에서 자동 실행했으나 Gateway 이벤트 루프 블록 문제로 분리됨.)

### 7.3 동적 갱신

MCP 서버가 `notifications/tools/list_changed` 이벤트를 보내면
기존 해당 서버의 tool을 전부 `deregister()` 후 재등록 (nuke-and-repave).

---

## 8. Plugin Tool 통합

### 8.1 플러그인 Tool 등록

플러그인은 `ctx.register_tool()`을 통해 Tool을 등록한다.  
core 파일(`model_tools.py`, `toolsets.py` 등)을 수정하지 않아도 됨.

```python
# ~/.hermes/plugins/my_plugin/__init__.py
def register(ctx):
    def my_custom_tool(args, **kwargs) -> str:
        return json.dumps({"result": "..."})

    ctx.register_tool(
        name="my_custom_tool",
        description="Does something custom",
        parameters={...},
        handler=my_custom_tool,
        toolset="my_plugin",
    )
```

### 8.2 발견 타이밍

```python
# model_tools.py — 모듈 레벨
discover_plugins()   # hermes_cli/plugins.py
```

`discover_builtin_tools()` 직후에 플러그인도 발견되므로,
`model_tools.py`가 임포트되는 시점 이후에는 플러그인 tool이 레지스트리에 등록되어 있다.

> ⚠️ **발견 타이밍 주의**: `model_tools.py`를 임포트하지 않은 코드 경로에서
> 플러그인 상태를 읽으려면 `discover_plugins()`를 직접 호출해야 한다 (멱등).

---

## 9. Tool vs Skill — 설계 철학

### 왜 Skill에 Tool을 삽입하지 않나

Hermes의 핵심 설계 원칙: **코어는 좁게, 기능은 가장자리에**.

```
모든 Tool 스키마는 모든 API 호출에 포함됨
→ Tool 하나 추가 = 모든 사용자의 모든 API 호출에 비용 추가
```

Skill은 이 비용을 피하는 방법:
- Tool 스키마는 API 호출 시 항상 전송됨
- Skill(SKILL.md)은 사용자가 `/skill-name`을 입력할 때만 user 메시지로 전송됨
- → 필요할 때만 컨텍스트 소비, 평소엔 0비용

### 새 기능 추가 결정 트리 (Footprint Ladder)

```
새 기능이 필요하다
   ↓
1. 기존 코드 확장으로 해결?  → 확장
2. CLI 커맨드 + Skill로 해결?  → skill 파일 작성 (zero model-tool footprint)
3. 특정 서비스 설정 시에만 필요?  → check_fn 게이팅 Tool
4. 서드파티/니치 기능?  → Plugin
5. 구조화된 I/O가 필요하고 널리 쓰임?  → MCP 서버 카탈로그에 추가
6. 거의 모든 사용자에게 기본 필요?  → Core Tool (마지막 수단)
```

> ❌ "terminal + file로 이미 가능하거나 Skill로 해결되는 건 Core Tool로 추가하지 않는다"

---

## 10. Tool 추가 방법 (치트시트)

### Core Built-in Tool 추가 (기여용)

**필수 2개 파일 수정:**

**1. `tools/my_tool.py` 생성:**

```python
import json, os
from tools.registry import registry

def check_requirements() -> bool:
    return bool(os.getenv("MY_API_KEY"))

def my_tool_fn(param: str, task_id: str = None) -> str:
    # ... 로직 ...
    return json.dumps({"result": "..."})

registry.register(
    name="my_tool",
    toolset="my_toolset",
    schema={
        "name": "my_tool",
        "description": "...",
        "parameters": {
            "type": "object",
            "properties": {
                "param": {"type": "string", "description": "..."},
            },
            "required": ["param"],
        },
    },
    handler=lambda args, **kw: my_tool_fn(
        param=args.get("param", ""), task_id=kw.get("task_id")
    ),
    check_fn=check_requirements,   # 없으면 항상 활성
    requires_env=["MY_API_KEY"],
    emoji="🔧",
)
```

**2. `toolsets.py`에 툴셋 추가 또는 기존 툴셋에 포함:**

```python
# 새 단위 툴셋
"my_toolset": {
    "description": "My new tool",
    "tools": ["my_tool"],
    "includes": []
},
```

또는 `_HERMES_CORE_TOOLS`에 추가 (모든 플랫폼에 기본 포함 시).

### 로컬/커스텀 Tool 추가 (플러그인 방식 — 권장)

core 파일 수정 없이:

```bash
mkdir -p ~/.hermes/plugins/my_plugin
```

```python
# ~/.hermes/plugins/my_plugin/__init__.py
import json

def register(ctx):
    ctx.register_tool(
        name="my_tool",
        description="...",
        parameters={"type": "object", "properties": {...}},
        handler=lambda args, **kw: json.dumps({"result": "done"}),
        toolset="my_plugin",
    )
```

```yaml
# ~/.hermes/plugins/my_plugin/plugin.yaml
name: my_plugin
version: 1.0.0
```

---

## 핵심 설계 원칙 요약

| 원칙 | 내용 |
|------|------|
| **자동 발견** | `tools/*.py`에 `registry.register()` 최상위 호출만 있으면 자동 임포트 |
| **check_fn 게이팅** | API 키나 서비스가 필요한 Tool은 `check_fn`으로 조건부 노출, 30s TTL 캐시 |
| **Toolset 필수** | Tool은 반드시 Toolset에 속해야 LLM에 노출됨 |
| **Core는 좁게** | 새 Tool 추가 비용 = 모든 API 호출 비용; Skill/Plugin/MCP를 먼저 고려 |
| **동적 스키마** | `dynamic_schema_overrides`로 런타임 설정을 스키마에 반영 |
| **캐시 보호** | get_tool_definitions()는 quiet_mode에서 LRU 캐시(8개), 세션 중 스키마 불변 |
| **에이전트 인터셉트** | `todo`, `memory`, `session_search`, `delegate_task`는 에이전트 루프에서 직접 처리 |

---

**문서 버전**: 1.0  
**최종 업데이트**: 2026-06-11  
**작성자**: 코드 분석 기반 정리 (`toolsets.py`, `tools/registry.py`, `model_tools.py` 기준)
