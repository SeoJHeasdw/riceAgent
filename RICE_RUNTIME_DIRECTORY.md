# RICE Runtime Directory (rice-runtime 내부 모듈 레이아웃)

> 작성일: 2026-06-12 (개정: 2026-06-18) · 상태: ✅ 정본
> 목적: `rice-runtime`의 내부 모듈 구조와 디렉토리 레이아웃, 모듈↔책임↔화면 매핑, 핵심 불변식을 정의한다.
> 이 문서는 **모듈 구조의 단일 출처(source of truth)**다. 다른 문서는 여기를 링크로 참조한다.
> 선행 문서: [RICE_REPO_STRATEGY.md](RICE_REPO_STRATEGY.md)(레포 분리 전략), [RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md)(런타임 설계)

---

## 1. 레포 컨텍스트

RICE는 **3개 분리 레포(폴리레포)**로 구성된다 ([REPO_STRATEGY 3장](RICE_REPO_STRATEGY.md)):

- `rice-app` — Electron + React (배포물 ①)
- `rice-runtime` — Python 로컬 단일 프로세스 (배포물 ②, **이 문서의 대상**)
- `rice-platform` — Python 원격 마켓 서버 (배포물 ③)

**계약(OpenAPI) 소유 규칙** ([REPO_STRATEGY 4.3](RICE_REPO_STRATEGY.md)): 공용 contracts 레포를 두지 않는다. 각 API 제공 레포가 스펙을 소유하고 소비 레포가 코드젠한다.
- `rice-runtime`이 `contracts/runtime.yaml` + `sse-events.schema.json`을 소유 → `rice-app`이 TS 클라이언트 코드젠
- `rice-platform`이 `platform.yaml`을 소유 → `rice-app`/`rice-runtime`이 코드젠

---

## 2. rice-runtime 디렉토리 트리

```
rice-runtime/                                # 배포물 ② : 로컬 단일 프로세스 (PyInstaller)
├── pyproject.toml
├── contracts/                               # 이 레포가 소유하는 API 계약 (rice-app이 코드젠)
│   ├── runtime.yaml                         #   runtime OpenAPI 스펙
│   └── sse-events.schema.json               #   9종 SSE 이벤트 + 제어 이벤트(tool_approval_required·skill_patch_proposed) 스키마 (CONTRACTS 문서 1.3)
├── src/rice_runtime/
│   │
│   ├── server/                              # ▣ #13 로컬 API 서버  + #9(세션 부분)
│   │   ├── app.py                           #   FastAPI 인스턴스
│   │   ├── composition_root.py              #   ★ 모든 모듈 와이어링 (유일한 DI 진입점)
│   │   ├── routes/
│   │   │   ├── messages.py                  #   POST /sessions/{id}/messages (SSE) · abort
│   │   │   │                                #     tool-approval · pending-approvals (HITL, RUNTIME_DESIGN 5장)
│   │   │   ├── skills.py                    #   GET /skills · curate · state
│   │   │   │                                #     patch/history/rollback (개선, LEARNING_LOOP 5.2)
│   │   │   ├── plugins.py                   #   GET /plugins ...
│   │   │   ├── connectors.py                #   GET/POST/DELETE /connectors
│   │   │   ├── artifacts.py                 #   POST /artifacts/install
│   │   │   └── settings.py
│   │   ├── sse/
│   │   │   └── emitter.py                   #   thinking/tool_call/tool_result/message/
│   │   │                                    #     skill_created/memory_written/skill_patched/done/error
│   │   │                                    #     + 제어: tool_approval_required·skill_patch_proposed
│   │   ├── approval.py                      #   ▣ HITL 승인 parking 레지스트리 (approval_id→Future,
│   │   │                                    #     timeout·세션 abort/shutdown 정리) — RUNTIME_DESIGN 5장
│   │   └── session/                         # ▣ #9 세션 lifecycle
│   │       ├── interfaces.py                #     ISessionStore (Protocol)
│   │       ├── session_manager.py
│   │       └── tests/
│   │
│   ├── agent/                               # ▣ #1 대화 루프  + #2 tool dispatch
│   │   ├── interfaces.py                    #   IAgent / ILLMProvider (Protocol)
│   │   ├── loop.py                          # ▣ #1 conversation/agent 루프 (max_iterations)
│   │   ├── tool_dispatch.py                 # ▣ #2 스키마 조립·가용성 필터(check_fn식)·라우팅·결과 수집
│   │   │                                    #     연결 안 된 Connector tool 제외 (RUNTIME_DESIGN 2.1)
│   │   ├── orchestrator.py                  #   얇은 조율: learning/connector/execution 호출
│   │   ├── providers/                       #   모델 교체 지점 (Claude SaaS ↔ Ollama on-prem)
│   │   │   ├── interfaces.py                #     ILLMProvider (tool_use·streaming·caching 정규화)
│   │   │   ├── claude.py                    #     SaaS (Anthropic API)
│   │   │   ├── ollama.py                    #     on-prem (로컬 모델)
│   │   │   └── registry.py
│   │   └── tests/
│   │
│   ├── execution/                           # ▣ #7 plugin loading  + #8 sandbox
│   │   ├── interfaces.py                    #   IPluginLoader / ISandbox (Protocol)
│   │   ├── plugin/                          # ▣ #7
│   │   │   ├── loader.py                    #   manifest 파싱·스킬 주입
│   │   │   ├── manifest.py                  #   requires.connectors 등 검증
│   │   │   └── registry.py
│   │   ├── sandbox/                         # ▣ #8 (★ 유일한 별도-프로세스 후보)
│   │   │   ├── runner.py                    #   자식 프로세스 spawn·resource limit
│   │   │   ├── ipc.py                       #   stdin/stdout 프로토콜
│   │   │   └── policy.py                    #   실행 경계(RUNTIME_DESIGN 5장)
│   │   └── tests/
│   │
│   ├── learning/                            # ▣ #3 memory #4 skill #5 curator #6 loop +#9 user
│   │   ├── interfaces.py                    #   ILearningService (Protocol) — 분리 솔기
│   │   ├── loop.py                          # ▣ #6 learning loop (경험→축적→재사용→개선)
│   │   ├── memory/                          # ▣ #3 memory manager
│   │   │   ├── store.py                     #   사실 메모리·선호 프로필
│   │   │   └── search.py                    #   FTS5 세션 검색
│   │   ├── skill/                           # ▣ #4 skill lifecycle
│   │   │   ├── lifecycle.py                 #   생성·상태전이(active→stale→archived)
│   │   │   ├── patch.py                     #   개선(patch) 제안 산출·승인 적용·version++·.history 보관 (LEARNING_LOOP 5.2)
│   │   │   ├── usage.py                     #   use/view/patch 카운터
│   │   │   └── store.py                     #   ~/.rice/skills/*.md + .usage.json + <name>/.history/v<N>.md
│   │   ├── curator/                         # ▣ #5 curator
│   │   │   └── curator.py                   #   자동 전이 규칙 + 수동 "스킬 정리"
│   │   ├── user_model/                      # ▣ #9 (user modeling 부분)
│   │   │   └── profile.py                   #   개인화·답변 스타일 학습
│   │   └── tests/
│   │
│   ├── connector/                           # ▣ #10 connector #11 credential #12 permission
│   │   ├── interfaces.py                    #   IConnectorService (Protocol) — 분리 솔기
│   │   ├── manager.py                       # ▣ #10 외부 계정/데이터소스 연결·해제
│   │   ├── oauth/                           #   OAuth2 flow·refresh·redirect 처리
│   │   ├── credential/                      # ▣ #11 credential storage
│   │   │   └── keychain.py                  #   OS 키체인 연동(keyring)
│   │   ├── permission/                      # ▣ #12 permission system
│   │   │   └── checker.py                   #   plugin/tool → connector 접근 검증
│   │   └── tests/
│   │
│   └── core/                                # ▣ (#14 암묵) 공용 인프라 — 모듈 간 직접 import 금지선
│       ├── storage/                         #   local storage abstraction
│       │   ├── sqlite.py                    #   WAL 모드·연결 풀
│       │   └── migrations/
│       ├── events/                          #   in-process 이벤트 버스 (모듈 간 결합 완화)
│       ├── paths.py                         #   ~/.rice/ 디렉토리 관리
│       ├── config.py                        #   config.yaml
│       └── errors.py
│
├── build/
│   └── pyinstaller.spec                     #   단일 바이너리 패키징
├── eval/                                    #   학습 측정 하네스 (tests/와 분리, LEARNING_EVAL 5장)
│   ├── tasks/                               #     task 정의 (prompt + success_check + related_skill)
│   ├── profiles/{cold,warm}/                #     cold=빈 ~/.rice · warm=스냅샷 시드(방법 B)
│   ├── runner.py · metrics.py · report.py   #     suite 실행 → turns/tool_calls/success/skill_hit → results JSON
│   └── results/                             #     타임스탬프별 cold/warm/delta (UI fixture 소스, EVAL 6.3)
└── tests/
    └── e2e/                                 #   7개 화면 시나리오 통합 테스트
```

---

## 3. 모듈 ↔ 책임 매핑

| # | 책임 | 위치 |
|---|------|------|
| #1 | 대화 루프 | `agent/loop.py` |
| #2 | tool dispatch (스키마 조립·가용성 필터·라우팅) | `agent/tool_dispatch.py` |
| #3 | memory manager | `learning/memory/` |
| #4 | skill lifecycle (생성·상태전이·개선patch) | `learning/skill/` |
| #5 | curator | `learning/curator/` |
| #6 | learning loop | `learning/loop.py` |
| #7 | plugin loading | `execution/plugin/` |
| #8 | tool execution sandbox | `execution/sandbox/` (★ 향후 별도 프로세스) |
| #9 | session / user modeling | `server/session/` (세션) + `learning/user_model/` (유저) |
| #10 | connector management | `connector/manager.py` |
| #11 | credential storage | `connector/credential/` |
| #12 | permission system | `connector/permission/` |
| #13 | 로컬 API 서버 | `server/` |
| #14 | local storage abstraction | `core/storage/` |

---

## 4. UI 화면 → 백엔드 → 모듈 매핑

> 7개 화면([RICE_PRODUCT_IDEA.md 4장](RICE_PRODUCT_IDEA.md))이 어느 백엔드·모듈로 연결되는지의 추적표. 엔드포인트 계약은 [RICE_CONTRACTS.md](RICE_CONTRACTS.md)가 소유.

| UI 화면 | 백엔드 | 주요 모듈 |
|---------|--------|-----------|
| ChatScreen | rice-runtime | `agent/`, `server/session/` |
| MySkillsScreen | rice-runtime | `learning/skill/`, `learning/curator/` |
| MyPluginsScreen | rice-runtime | `execution/plugin/`, `connector/permission/` |
| ConnectorsScreen | rice-runtime | `connector/` |
| SettingsScreen | rice-runtime | `core/config.py`, `agent/providers/` |
| SkillMarketScreen | rice-platform | `catalog/`, `artifacts/` (설치는 runtime `execution/plugin/`) |
| PluginMarketScreen | rice-platform | `catalog/`, `artifacts/` (설치는 runtime `execution/plugin/`) |

→ 7개 화면 모두 **2개 백엔드(rice-runtime 로컬 + rice-platform 원격)**로 커버된다.

---

## 5. 핵심 불변식

```
1. 모듈 간 직접 import 금지
   ❌ from learning.skill.lifecycle import SkillLifecycle
   ✅ from learning.interfaces import ILearningService

2. server/composition_root.py가 유일한 와이어링 지점
   ❌ agent/loop.py에서 LearningService() 직접 생성
   ✅ composition_root.py에서 주입받음

3. Protocol 기반 인터페이스
   ✅ 각 모듈은 interfaces.py에 Protocol 정의
   ✅ 구현체는 모듈 내부에 캡슐화 → 향후 모듈을 별도 서비스로 분리 가능
```
