# agent-build — 모듈별 구현 지시서

이 폴더는 `rice-runtime`의 **남은 모듈을 외부 agentic AI(예: Claude 3.5/3.7)에게
구현시키기 위한 작업 프롬프트** 모음이다. 골격·계약·인터페이스는 이미 완성·동결돼
있고, 각 지시서는 "그 인터페이스 뒤를 실제 구현으로 채우는" 한 모듈 작업을 정의한다.

## 사용 방법 (오케스트레이터/사람)

각 모듈 작업마다, 담당 AI에게 **다음 순서로** 읽히고 시작시켜라:

1. `agent-build/CONVENTIONS.md` ← **공통 규칙(필수, 먼저)**
2. `agent-build/<모듈>.md` ← 그 모듈의 작업 지시서
3. 그 지시서가 가리키는 **설계 문서 섹션**(`../RICE_*.md`)
4. 그 모듈의 `src/rice_runtime/<모듈>/interfaces.py` + 교체 대상 스텁

한 세션당 **한 지시서**만 맡겨라(컨텍스트 오염·스코프 크리프 방지). 끝나면
`CONVENTIONS.md` §5 체크리스트 결과를 받고, 그걸 claude-code(품질 검증)로 넘긴다.

## 작업 목록 & 권장 순서

| # | 지시서 | 모듈 | 의존 | 비고 |
|---|--------|------|------|------|
| 1 | [providers.md](providers.md) | `agent/providers/` (claude·ollama) | 없음 | 독립적·가장 안전. 먼저 하면 이후 실모델로 테스트 가능 |
| 2 | [learning.md](learning.md) | `learning/` (memory·skill·curator·loop·user) | core | **데모 핵심·최대 규모.** 서브태스크로 나눠도 됨 |
| 3 | [connector.md](connector.md) | `connector/` (gmail oauth·keychain·permission) | core | Gmail 프로토타입만 |
| 4 | [execution.md](execution.md) | `execution/` (plugin loader·sandbox) | core, connector(interfaces) | 자식 프로세스 샌드박스 |
| 5 | [eval.md](eval.md) | `eval/` (학습 측정 하네스) | 전 모듈 | 마지막. cold/warm·Learning Impact |

> `core/`·`server/`(app·routes·composition_root·approval·session)는 이미 실제
> 구현이라 별도 작업서가 없다. 손대지 마라(필요 시 composition_root DI만).

## 파이프라인

```
[Claude 3.5/3.7 agentic]  지시서 + 설계문서 읽기 → 모듈 구현 → §5 자가검증
        │
        ▼
[claude-code / Opus]      품질 검증·고도화: 불변식 재스캔, 엣지케이스, 계약 정합성,
                          테스트 보강, NOTE(agent) 해소
```
