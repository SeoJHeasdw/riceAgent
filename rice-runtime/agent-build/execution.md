# 작업: execution — 플러그인 로딩 (#7) · 샌드박스 (#8)

> 먼저 `CONVENTIONS.md`를 읽었다고 가정한다.
> 정본: `../RICE_RUNTIME_DESIGN.md` §5(샌드박스 경계) + `../RICE_CONTRACTS.md` §2(artifact).

## 목표

`IPluginLoader`/`ISandbox` 뒤를 실제 구현으로 채운다. 트리(RUNTIME_DIRECTORY §2):
```
execution/
  interfaces.py        # 동결 — 구현 대상 (읽기)
  service.py           # NoopSandbox + PluginLoader 스텁 → 실제로 교체
  plugin/loader.py     # #7 manifest 파싱·스킬 주입·설치
  plugin/manifest.py   # requires.connectors 등 검증
  plugin/registry.py   # 설치/활성 상태 관리
  sandbox/runner.py    # #8 자식 프로세스 spawn·resource limit
  sandbox/ipc.py       # stdin/stdout 프로토콜
  sandbox/policy.py    # 실행 경계(env 스크럽 등)
  tests/
```

## 🔒 신뢰 모델 (정본 §5) — 먼저 이해하라
| 실행 대상 | 실행 방식 |
|----------|-----------|
| built-in tool | 프로세스 내 직접 |
| **skill (SKILL.md)** | **선언적 → 코드 실행 없음 → 샌드박스 대상 아님** |
| 사용자/마켓 plugin·connector **코드** | **자식 프로세스 + env 스크럽** |

핵심: **마켓 skill은 선언적이라 샌드박스를 우회**한다. 실제 코드 실행은 plugin/
connector 코드뿐이고, **그것만** 샌드박스 경계 안에서 돈다. Docker/컨테이너는
**금지**(데모 제약) — "자식 프로세스 + env 스크럽, 데모 한정 신뢰" 가 정답이다.

## 동결된 계약 — `execution/interfaces.py` 읽기
- `IPluginLoader`: `list_tools()`, `injected_skills()`, `invoke(call)`,
  `list_plugins()`, `set_enabled(id, on)`, `install(path, manifest)`, `remove(id)`.
- `ISandbox`: `run(entrypoint, payload, timeout_s) -> SandboxResult`.
- DTO: `PluginInfo`, `SandboxResult`(interfaces.py). 새로 만들지 마라.

## Sandbox (sandbox/*.py) — §5 핵심
- `ISandbox.run`은 **untrusted 코드가 도는 유일한 choke point.** 절대 in-process
  실행하지 마라(스텁 `NoopSandbox`가 fail-closed로 막아둔 이유다).
- `runner.py`: `subprocess`로 **자식 파이썬 프로세스** spawn. `policy.py`: 자식
  환경을 **스크럽**(부모 env 전달 금지 — 최소 env만, 비밀 제거), cwd 제한, `timeout_s`
  강제(초과 시 kill → `SandboxResult(ok=False, error="timeout")`).
- `ipc.py`: 부모↔자식 통신은 **stdin/stdout JSON 라인 프로토콜**(payload in →
  result out). 간단·결정적으로.
- resource limit: POSIX면 `resource.setrlimit`(CPU/메모리) best-effort. 불가하면 NOTE.
- 결과는 예외가 아니라 `SandboxResult(ok, stdout, stderr, result, error)`.

## Plugin (plugin/*.py) — #7, CONTRACTS §2
- **manifest 스키마 정본은 CONTRACTS §2.1**(`rice-artifact.yaml`): `schema_version,
  type(skill|plugin), name, version, ..., requires.connectors[], checksum`.
- `manifest.py`: 파싱 + 검증(`requires.connectors`는 **connector_type 목록**).
- `loader.py` `install(path, manifest)` (CONTRACTS §2.3):
  ① `checksum`(sha256) 검증 ② `~/.rice/{skills|plugins}/`에 배치 ③ 로드 ④
  `installed_artifacts` 테이블에 기록(0001에 존재). 검증 실패 → 예외(`ValidationError`).
- `injected_skills()`: 활성 plugin이 포함한 SKILL.md들을 `InjectedSkill`로. **포맷은
  learning의 SKILL.md와 동일**(LEARNING_LOOP §2) — 호환 필수(같은 파서를 쓰고
  싶겠지만 learning 구체를 import하면 안 된다; 필요하면 최소 파서를 이 모듈에 두거나
  공용이 필요하면 `# NOTE(agent):`로 표시).
- `list_tools()`: 활성 plugin이 제공하는 atomic tool을 `source="plugin"`,
  적절히 `requires_approval`. `invoke(call)`은 **반드시 `ISandbox.run`을 거쳐** 실행.
- `registry.py`: 설치/활성(enabled) 상태 관리(`set_enabled`, `list_plugins`).

`composition_root.py`는 이미 `PluginLoader(db, NoopSandbox())`로 연결한다 → 너는
`NoopSandbox`를 실제 `ChildProcessSandbox`(또는 동등)로 교체하고 composition_root의
그 한 줄을 새 클래스로 바꾼다(허용된 DI 수정). `RicePaths`가 필요하면 DI 추가.

## 권한 연동
- plugin tool 실행 시 connector 접근 권한 검증이 필요하다 → connector 모듈의
  **interfaces**만 사용(`IConnectorService.check_permission`). 구체 import 금지.
  composition_root가 connector 서비스를 주입하게 할 수 있다(허용).

## 수용 기준 (Acceptance)
1. `ChildProcessSandbox.run`이 간단한 entrypoint를 **자식 프로세스**에서 실행하고
   결과를 돌려준다(in-process 아님을 증명 — 예: 자식에서 `os.getpid()`가 부모와 다름).
2. `run`이 `timeout_s` 초과를 kill하고 `ok=False, error~="timeout"`.
3. 자식 env가 스크럽됨(부모의 임의 비밀 env가 자식에 안 보임 — 테스트로 확인).
4. `install`이 checksum 불일치를 거부.
5. `injected_skills()`가 SKILL.md를 learning과 동일 포맷으로 반환.
6. walking_skeleton.py 정상.

## 하지 말 것
- in-process로 untrusted 코드 실행 절대 금지.
- Docker/컨테이너 도입 금지(데모 범위 밖).
- learning/connector의 **구체 구현** import 금지(interfaces만).
- artifact manifest 스키마를 임의 확장 금지(CONTRACTS §2.1이 정본).
