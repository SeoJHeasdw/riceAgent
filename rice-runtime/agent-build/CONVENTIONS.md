# CONVENTIONS — 모든 모듈 빌드 작업의 공통 규칙

> 이 파일은 `agent-build/*.md` **모든 작업 프롬프트가 전제로 하는 공통 계약**이다.
> 각 모듈 작업을 시작하기 전에 **이 파일을 먼저 끝까지 읽어라.** 모듈 파일은 이
> 규칙을 다시 설명하지 않는다.

대상 실행기(개발 담당 AI)에게: 너는 RICE의 `rice-runtime`(Python 로컬 런타임)의
**한 모듈을 구현**한다. 골격(뼈대)은 이미 완성되어 있고 **동작한다**. 너의 일은
스텁(stub)으로 비어 있는 본문을 **설계 문서대로 실제 구현으로 교체**하는 것이다.

---

## -1. 환경 부트스트랩 (코드 만지기 전에 먼저)

이 프로젝트는 **Python 3.13** 전용이다(코드에 PEP 695 `type` 문법이 있어 3.12
미만에서는 컴파일조차 안 된다). 너의 실행 환경에서 **격리된 venv를 직접 만들고**
거기 설치한 뒤, 이후 모든 명령을 그 venv의 파이썬으로 실행하라. 의존성의 단일
출처는 `pyproject.toml`이다(requirements.txt에 패키지를 나열하지 마라).

```bash
cd rice-runtime
python3.13 --version            # 3.13.x 인지 먼저 확인. 없으면 3.13을 설치/지정하라.
python3.13 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e ".[dev]"     # 기본(테스트·서버 포함)
# 네 모듈이 필요로 하면 extra 추가:
#   providers(claude)  → .venv/bin/python -m pip install -e ".[dev,claude]"
#   connector          → .venv/bin/python -m pip install -e ".[dev,connectors]"
```

이후 이 문서의 모든 `python3.13 ...` 명령은 **`.venv/bin/python ...`** 로 실행하면
된다(둘 다 3.13이다). `.venv/`는 커밋하지 않는다(이미 `.gitignore` 처리됨).
새 런타임 의존성이 필요하면 **`pyproject.toml`의 적절한 extra에 추가**하고
재설치하라(§2 참조). `requirements-dev.txt`는 `-e .[dev]` 로 pyproject에 위임만 한다.

---

## 0. 황금률 (어기면 작업 실패로 간주)

1. **interfaces.py 는 동결(FROZEN)이다.** 어떤 모듈의 `interfaces.py`도 수정하지
   마라. 시그니처가 틀렸다고 판단되면 **고치지 말고 멈춰서** 해당 줄 위에
   `# NOTE(agent): <왜 막혔는지>` 주석만 남기고 그대로 둔 채 보고하라.
2. **모듈 간 직접 import 금지.** 다른 모듈의 **구체 구현**(`service.py`,
   `loop.py`, `manager.py`, `loader.py`, `*.py` 구현체)을 절대 import 하지 마라.
   다른 모듈이 필요하면 그 모듈의 `interfaces.py`(Protocol/DTO)만 import 한다.
   - ❌ `from ..learning.skill.store import SkillStore`
   - ✅ `from ..learning.interfaces import ILearningService, SkillSummary`
3. **와이어링은 `server/composition_root.py` 한 곳뿐.** 너는 이 파일을 **오직
   생성자 의존성 주입을 추가**하기 위해서만 수정할 수 있다(예: 네 구현이
   `RicePaths`가 필요하면 거기서 넘겨라). **비즈니스 로직을 여기 두지 마라.**
4. **계약(contract)을 바꾸지 마라.** `contracts/runtime.yaml`,
   `contracts/sse-events.schema.json` 은 읽기 전용이다. 새 SSE 이벤트·새
   엔드포인트·새 응답 필드를 **발명하지 마라**. UI(`rice-app`)가 이미 이 계약에
   묶여 있다.
5. **walking skeleton 을 깨지 마라.** 작업 후 `python3.13 walking_skeleton.py`가
   여전히 정상 출력해야 한다(§5 참조).

---

## 1. 무엇을 읽어야 하나 (단일 출처 지도)

설계 결정은 항상 아래 문서가 **정본(source of truth)**이다. 추측하지 말고 찾아라.
경로는 레포 루트(`rice-runtime/`의 부모) 기준이다.

| 주제 | 정본 문서 |
|------|-----------|
| 모듈 구조·디렉토리·핵심 불변식·화면→모듈 매핑 | `../RICE_RUNTIME_DIRECTORY.md` |
| 런타임 설계·패키징·보안·HITL 메커니즘·Connector | `../RICE_RUNTIME_DESIGN.md` |
| learning loop 자체 스펙(SKILL.md·usage·생성·patch·curator·memory) | `../RICE_LEARNING_LOOP_DESIGN.md` |
| 학습 측정(cold/warm·eval harness·Learning Impact) | `../RICE_LEARNING_EVAL.md` |
| SSE 이벤트·엔드포인트·artifact manifest 계약 | `../RICE_CONTRACTS.md` |
| 결정 등록부(왜 이렇게 정했나) | `../DECISIONS.md` |
| Hermes 참조(읽기 전용 분석 자료) | `../HERMES_LEARNING_LOOP_REFERENCE.md` |

코드 쪽 정본:
- **공유 타입(DTO)**: `src/rice_runtime/core/types.py` — 모듈 경계를 넘는 모든
  데이터는 여기 정의돼 있다. 새 공유 타입이 꼭 필요하면 여기 추가한다(그 외 위치 금지).
- **네 모듈의 계약**: `src/rice_runtime/<모듈>/interfaces.py` — 네가 구현할 Protocol과
  반환 DTO. 이게 "해야 할 일 목록"이다.
- **네가 교체할 스텁**: `src/rice_runtime/<모듈>/service.py`(또는 해당 파일). 내부에
  `# TODO(module): ...` 로 무엇을 채워야 하는지 표시돼 있다.

---

## 2. MVP 경계 (스코프 크리프 금지)

설계가 **의도적으로 단순화**한 부분이다. "더 좋게" 만들지 마라. 데모용이다.

- **검색은 SQLite FTS5만.** 임베딩/시맨틱/벡터 검색 **금지**(on-prem 부담·패키징
  복잡도 회피 — RUNTIME_DESIGN §3). 약점은 `skill_hit` 지표로 *진단*만 한다.
- **단일 활성 세션.** 동시성 머신러리(큐·락 풀) 추가 금지(RUNTIME_DESIGN §2).
- **Connector 프로토타입 = Gmail 하나.** 일반 MCP 어댑터는 Phase 2. 만들지 마라.
- **샌드박스 = 자식 프로세스 + env 스크럽.** Docker/컨테이너 금지(데모 제약).
- **자동 전이 주기는 일(day) 단위지만 데모는 수동 "스킬 정리" 버튼으로 시연** —
  둘 다 구현하되 시간 기준을 분 단위로 바꾸지 마라(설정으로 노출만).
- **새 외부 의존성**은 함부로 추가하지 말 것. 꼭 필요하면 `pyproject.toml`의
  적절한 optional extra(`[project.optional-dependencies]`)에 선언하고, 그 모듈
  파일에서 **지연 import**(함수 안 import)로 감싸 골격이 의존성 없이도 import 되게
  유지한다(기존 `providers/registry.py`의 lazy import 패턴을 따라라).

---

## 3. 코드 스타일 (기존 코드와 동일하게)

- **Python 3.13** 타깃. 모든 파일 상단에 `from __future__ import annotations`.
- 공개 dataclass는 가능하면 `frozen=True`. 타입 힌트 필수.
- **주석은 "무엇"이 아니라 "왜"를 적는다.** 기존 파일들의 주석 밀도·톤을 흉내내라
  (각 파일 상단에 그 파일의 책임과 설계 근거를 설명하는 모듈 docstring을 둔다).
- 설계 문서를 인용할 때는 기존 코드처럼 `(LEARNING_LOOP §5.2)` 식으로 섹션을 단다.
- 에러는 `core/errors.py`의 타입을 던진다(새 예외 클래스 남발 금지).
- 비밀(토큰·API 키)을 **로그에 찍지 마라**(RUNTIME_DESIGN §10).

---

## 4. 디렉토리/파일 배치

`../RICE_RUNTIME_DIRECTORY.md` §2 트리가 정본이다. 새 파일은 **반드시 그 트리에
이미 명시된 경로**에 만든다(예: `learning/skill/store.py`). 트리에 없는 새 폴더를
임의로 만들지 마라. 각 새 하위 패키지에는 `__init__.py`를 둔다.

대부분의 경우 패턴은:
1. 하위 모듈 구현 파일들을 트리대로 새로 만든다(`store.py`, `search.py`, …).
2. 모듈의 `service.py`(= `interfaces.py`의 Protocol을 구현하는 **파사드**)가 그
   하위 파일들을 **조합**하도록 본문을 채운다. 외부(orchestrator·routes)는 계속
   `service.py`만 보고, 내부 분해는 캡슐화된다.

---

## 5. 정의(Definition of Done) — 이 체크리스트를 모두 통과해야 완료

작업 종료 전 **반드시** 아래를 직접 실행하고 결과를 보고에 붙여라.

```bash
cd rice-runtime
# (1) 전 파일 컴파일 (타깃 인터프리터)
python3.13 -m py_compile $(find src -name '*.py')

# (2) 골격이 안 깨졌는지 — 반드시 thinking→message…→done 출력 + 메시지 영속화
python3.13 walking_skeleton.py

# (3) 불변식 1 자동 검사 — 출력이 비어 있어야 한다
grep -rn --include="*.py" -E "^from \.\.(agent|learning|connector|execution|server)\." src/rice_runtime \
  | grep -vE "interfaces|composition_root"

# (4) 네 모듈 테스트 (아래 §6 참조)
python3.13 -m pytest tests/ -q     # pytest 없으면 §6의 인라인 하네스로 대체
```

체크리스트:
- [ ] `interfaces.py`를 **하나도** 수정하지 않았다.
- [ ] 다른 모듈의 구체 구현을 import 하지 않았다((3)의 grep 출력이 비어 있다).
- [ ] `contracts/`를 수정하지 않았고 새 SSE 이벤트/엔드포인트를 만들지 않았다.
- [ ] `composition_root.py`는 (수정했다면) **의존성 주입만** 바꿨다.
- [ ] walking skeleton 이 정상 동작한다.
- [ ] 내 모듈의 모든 `# TODO(module)`를 실제 구현으로 교체했다(남기지 않았다).
- [ ] 새 외부 의존성은 pyproject optional extra + lazy import로 처리했다.
- [ ] 각 새 파일에 "왜"를 설명하는 모듈 docstring을 달았다.

---

## 6. 테스트 작성 규약

- 테스트는 해당 모듈 트리의 `tests/` 폴더(예: `src/rice_runtime/learning/tests/`)
  **또는** 최상위 `tests/`에 `test_<module>_*.py`로 둔다.
- 격리: 절대 실제 `~/.rice`를 건드리지 마라. `RicePaths.from_env(tempfile.mkdtemp())`
  로 임시 루트를 쓴다(기존 `walking_skeleton.py`·`tests/test_walking_skeleton.py`
  패턴 참고).
- async 코드는 `pytest.mark.asyncio`(설정은 `pyproject.toml`에 이미 있음). pytest가
  없으면 `asyncio.run(...)`로 도는 **인라인 하네스 스크립트**라도 만들어 결과를
  보고에 붙여라(검증 없는 "완료"는 인정 안 됨).
- 각 모듈 파일에 그 모듈만의 **수용 기준(Acceptance)**이 있다. 그것을 검증하는
  테스트를 최소 1개씩 작성한다.

---

## 7. 막혔을 때 / 불확실할 때

- 계약·인터페이스·다른 모듈에 영향을 주는 결정이 필요하면 **추측하지 말고**
  `# NOTE(agent): <질문/가정>` 주석을 남기고 가장 보수적이고 안전한 선택(fail-closed)
  을 하라. 이후 claude-code(품질 검증 단계)가 처리한다.
- "안전한 기본값"의 의미: 권한은 거부, 코드 실행은 거부, 미승인은 미실행, 데이터
  손상보다 no-op. (스텁들이 이미 이 자세로 작성돼 있으니 톤을 맞춰라.)

---

## 8. 작업 산출물 보고 형식 (claude-code 인계용)

작업을 마치면 다음을 간단히 보고하라:
1. 새로 만든/수정한 파일 목록.
2. §5 체크리스트 실행 결과(명령 출력 붙여넣기).
3. 남긴 모든 `# NOTE(agent):` 목록과 이유.
4. 의도적으로 단순화하거나 미룬 부분.
