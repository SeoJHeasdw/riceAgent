# 작업: agent/providers — 실제 LLM 프로바이더 (Claude · Ollama)

> 먼저 `CONVENTIONS.md`를 읽었다고 가정한다. 정본: `../RICE_RUNTIME_DESIGN.md` §6.

## 목표

`ILLMProvider` 뒤에 **실제 모델 2개**를 붙인다. 지금은 `echo`(오프라인 스텁)만
있다. 너는 다음 두 파일을 **새로** 만든다:

- `src/rice_runtime/agent/providers/claude.py` — SaaS (Anthropic API)
- `src/rice_runtime/agent/providers/ollama.py` — on-prem (로컬 모델)

`registry.py`는 이미 이 둘을 **lazy import**로 등록해 두었다(`_make_claude`,
`_make_ollama`). 너는 그 import가 가리키는 클래스 이름(`ClaudeProvider`,
`OllamaProvider`)과 생성자 시그니처(`(cfg: ProviderConfig)`)만 맞추면 된다.

## 동결된 계약 (구현할 것)

`agent/providers/interfaces.py` 를 읽어라. 구현해야 하는 것:

```python
class XProvider:                 # ILLMProvider 를 구조적으로 만족
    name: str                    # "claude" / "ollama"
    def __init__(self, cfg: ProviderConfig): ...
    def capabilities(self) -> ProviderCapabilities: ...
    async def stream(self, *, system, messages, tools=(), temperature=0.7) -> AsyncIterator[ProviderChunk]: ...
```

`ProviderConfig`(`core/config.py`): `kind, model, temperature, base_url`. **모델 id는
하드코딩하지 말고 `cfg.model`을 써라.** Claude는 키가 없으니(키는 OS 키체인) —
§"키 로딩" 참조. Ollama는 `cfg.base_url` 사용, 키 없음.

`stream(...)`은 반드시 **`ProviderChunk` 시퀀스**를 yield하고 **마지막에 정확히
하나의 `kind="done"`** 으로 끝나야 한다. 매핑:

| 모델에서 온 것 | yield 할 ProviderChunk |
|----------------|------------------------|
| 사고/reasoning 토큰 | `ProviderChunk(kind="thinking", text=...)` |
| 답변 텍스트 토큰 | `ProviderChunk(kind="text", text=...)` |
| 도구 호출 요청(완성된 1건) | `ProviderChunk(kind="tool_call", tool_call=ToolCall(tool_name, arguments))` |
| 턴 종료 | `ProviderChunk(kind="done", stop_reason="end_turn"|"tool_use"|"max_tokens"|"error")` |

`ToolCall`은 `core/types.py`에 있다(`tool_name`, `arguments: dict`, 자동 `id`).

## ⚠️ 최대 난관: 정규화된 history ↔ 프로바이더 도구 프로토콜 변환

agent 루프가 주는 `messages: Sequence[Message]`(`core/types.py`)는 **프로바이더
중립 형식**이다. 너는 이걸 각 프로바이더의 네이티브 형식으로 변환해야 한다:

- `Message(role="user"|"assistant", content=str)` — 일반 텍스트 턴.
- `Message(role="assistant", tool_calls=(ToolCall, ...))` — 모델이 도구를 부른 턴.
  각 `ToolCall`은 `id`, `tool_name`, `arguments`를 가진다.
- `Message(role="tool", content=<결과문자열>, metadata={"tool_call_id": <그 호출 id>})`
  — 도구 실행 결과. `metadata["tool_call_id"]`가 위 `ToolCall.id`와 짝이다.

### Claude (Anthropic Messages API)로의 매핑
- `system=` 파라미터로 `system` 문자열 전달. (긴 시스템 프롬프트엔 `cache_control`
  적용 — 스킬 주입 비용 상쇄, RUNTIME_DESIGN §6. 가능하면 마지막 system 블록에
  `{"type":"text","text":..., "cache_control":{"type":"ephemeral"}}`.)
- `tools=` 에 `ToolSpec` → `{"name", "description", "input_schema": spec.parameters}`.
- assistant tool 호출 턴 → `content` 블록 배열에 `{"type":"tool_use","id":tc.id,
  "name":tc.tool_name,"input":tc.arguments}`.
- `role="tool"` 메시지 → **user 메시지**의 `{"type":"tool_result","tool_use_id":
  metadata["tool_call_id"], "content": message.content}` 블록으로 변환.
- 스트리밍: SDK의 스트리밍 이벤트에서 text delta → `kind="text"`,
  tool_use 블록의 누적된 input(JSON) 완성 시 → `kind="tool_call"`,
  종료 → `kind="done"`(stop_reason 매핑).
- **모델 id/SDK 시그니처는 변동 가능** → 정확한 최신 사용법은 추측하지 말고
  Anthropic 공식 문서를 따르되, 불확실하면 `# NOTE(agent):`로 남겨라(품질 단계가
  검증한다). `capabilities()`는 `native_tool_calling=True, streaming=True,
  prompt_caching=True, vision=True`.

### Ollama 로의 매핑
- `cfg.base_url`(예: `http://localhost:11434`)의 `/api/chat`에 `stream=True`로 호출.
- 함수 호출은 **모델마다 지원이 다르다.** 지원 모델이면 `tools=` 전달 후 응답의
  `message.tool_calls`를 `kind="tool_call"`로 변환. **미지원이면 프롬프트 기반
  폴백**(시스템 프롬프트에 도구 설명을 넣고, 정해진 JSON 형식으로 답하게 유도 후
  파싱). 폴백은 best-effort — 불확실하면 NOTE 남기고 텍스트만 스트리밍해도 된다.
- caching 미지원 → `capabilities()`는 `prompt_caching=False`. keep-alive로 대체
  (`keep_alive` 파라미터).

## 키 로딩 (Claude)
- API 키는 **OS 키체인**에 있다(평문 `.env` 금지, RUNTIME_DESIGN §6/§10). `keyring`
  라이브러리로 읽는다. 키 이름 규약은 connector와 동일 네임스페이스를 따르되
  (`rice.provider.claude.api_key` 권장), **확실치 않으면 `# NOTE(agent):`** 남기고
  환경변수 `ANTHROPIC_API_KEY` 폴백을 둬라. 키가 없으면 `ProviderError`를 던져라
  → 그러면 골격이 자동으로 echo로 폴백한다(이미 그렇게 동작함).

## 의존성
- `pyproject.toml`의 `[project.optional-dependencies]`에 이미 `claude=["anthropic..."]`,
  `connectors=[...,"httpx..."]`가 있다. Ollama엔 `httpx`를 쓰면 된다(필요 시 extra에
  추가). **import는 함수/메서드 안에서 지연 import**(파일 최상단 금지) — 골격이
  의존성 없이도 import 가능해야 한다(registry의 기존 패턴과 동일).

## 수용 기준 (Acceptance)
1. `OllamaProvider`/`ClaudeProvider`가 `ILLMProvider`를 구조적으로 만족
   (`isinstance(p, ILLMProvider)` 가 메서드 기준 True).
2. 가짜 입력(텍스트 1턴)에 대해 `stream(...)`이 `text*` 후 정확히 하나의 `done`을
   낸다 — **모델 호출 부분을 모킹**한 단위 테스트로 증명(네트워크 없이).
3. tool 호출 → tool_result 왕복을 포함한 history를 프로바이더 네이티브 형식으로
   변환하는 순수 함수(`_to_provider_messages` 등)를 분리하고, 그 변환을 단위
   테스트한다(이게 가장 깨지기 쉬움).
4. 키 부재 시 `ProviderError` → echo 폴백이 동작(`build_container` 후
   `resolve_provider().name == "echo"` 확인).

## 하지 말 것
- `interfaces.py`·`registry.py`의 시그니처 변경 금지(registry는 이미 너를 부른다).
- 실제 API 키를 코드/테스트/로그에 넣지 마라.
- 9종 SSE 이벤트로의 변환은 **네 일이 아니다**(loop가 ProviderChunk를 SSE로 바꾼다). 너는 ProviderChunk까지만 책임진다.
