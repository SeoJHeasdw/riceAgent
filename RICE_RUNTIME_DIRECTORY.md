# RICE Runtime Directory (rice-runtime 내부 모듈 레이아웃)

> 작성일: 2026-06-12 (개정: 2026-07-08 — 소스 드리프트 동기화) · 상태: ✅ 정본
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
rice-runtime/                                # 배포물 ② : 로컬 단일 프로세스 (PyInstaller 패키징은 계획 단계 — 아직 미실행, build/ 없음)
├── pyproject.toml
├── contracts/                               # 이 레포가 소유하는 API 계약(rice-app 코드젠). 2026-07 기준 최신 계약은 CONTRACTS.md, runtime.yaml은 별도 동기화 대상
│   ├── runtime.yaml                         #   runtime OpenAPI 스펙
│   └── sse-events.schema.json               #   9종 SSE 이벤트 + 제어 이벤트(tool_approval_required·skill_patch_proposed) 스키마 (CONTRACTS 문서 1.3 — 2026-07-04 plan_updated 추가로 8→9종)
├── src/rice_runtime/
│   │
│   ├── server/                              # ▣ #13 로컬 API 서버  + #9(세션 부분)
│   │   ├── app.py                           #   FastAPI 인스턴스
│   │   ├── composition_root.py              #   ★ 모든 모듈 와이어링 (유일한 DI 진입점) — 도구 카탈로그 검증용 _known_tool_names()도 여기서 조립(learning/skill/verifier.py가 소비)
│   │   ├── scheduler.py                     #   ★ 신규(2026-07-03) — curator·discovery 백그라운드 스케줄러 (learning/{ledger,discovery,proposals}.py의 실행체)
│   │   ├── routes/
│   │   │   ├── messages.py                  #   POST /sessions/{id}/messages (SSE) · title · learning-review · learn · abort · regenerate · DELETE /sessions/{id}
│   │   │   │                                #     tool-approval · pending-approvals (HITL, RUNTIME_DESIGN 5장)
│   │   │   ├── skills.py                    #   GET /skills · curate(mode=rules|consolidate) · state
│   │   │   │                                #     patch/history/rollback (개선, LEARNING_LOOP 5.2) · archive-decision/consolidate-decision (통합, 5.1) · analysis/refresh(설명 사이드카)
│   │   │   ├── plugins.py                   #   GET /plugins · enabled · artifacts install/delete (artifacts.py라는 별도 파일은 없음 — 전부 여기)
│   │   │   ├── connectors.py                #   GET/POST/DELETE /connectors + local/*(등록·해제·테스트·새로고침) + custom/*(discover·등록) + {id}/tools/*(resync·check·apply-update·permission) — CONTRACTS.md가 전체 라우트 정본
│   │   │   ├── workspace.py                 #   ★ 신규 — GET /workspace/file · POST /workspace/open (OS 기본 앱으로 열기, 경로 샌드박스)
│   │   │   ├── eval_impact.py               #   ★ 신규(2026-07-03) — GET /eval/impact (학습효과 패널 데이터, LEARNING_EVAL 6.3 정본)
│   │   │   ├── settings.py                  #   GET/PUT /settings(agent 섹션 포함) · registered_models · GET /ollama/models · /ollama/health · POST /memories/{id}/approve
│   │   │   └── deps.py                      #   라우트 공용 DI 헬퍼
│   │   ├── sse/
│   │   │   └── emitter.py                   #   thinking/tool_call/tool_result/message/
│   │   │                                    #     memory_written/skill_patched/plan_updated/done/error (9종 — 2026-07-04 plan_updated 추가)
│   │   │                                    #     + 제어: tool_approval_required·skill_patch_proposed
│   │   ├── approval.py                      #   ▣ HITL 승인 parking 레지스트리 (approval_id→Future,
│   │   │                                    #     timeout·세션 abort/shutdown 정리) — RUNTIME_DESIGN 5장
│   │   └── session/                         # ▣ #9 세션 lifecycle
│   │       ├── interfaces.py                #     ISessionStore (Protocol)
│   │       └── session_manager.py
│   │
│   ├── agent/                               # ▣ #1 대화 루프  + #2 tool dispatch/scheduling + sub-agent
│   │   ├── interfaces.py                    #   IAgent / ILLMProvider (Protocol)
│   │   ├── loop.py                          # ▣ #1 conversation/agent 루프 — plan-and-execute × ReAct + plan 강제종결(reconcile) (max_iterations, context render, scheduler 호출) + is_web_tool 결과 provenance를 external `web`으로 기록(학습 오염 격리) — AGENT_EXECUTION §3
│   │   ├── planner.py                       #   TurnPlanner(의도 파악·계획 수립 LLM 1회) + PlanState(update_plan/start_plan 상태 기계, reconcile_abandoned) — AGENT_EXECUTION §2·§3
│   │   ├── profiles.py                      #   ★ 신규(2026-07-10) OrchestrationProfile — local↔frontier 이원화(액션규칙 에필로그·서사 넛지·num_ctx 클램프) — AGENT_EXECUTION §10
│   │   ├── context_manager.py               #   provider-facing context 렌더링/압축/마스킹 (원본 transcript 불변, 최근 도구출력 증류 완화)
│   │   ├── tool_scheduler.py                #   tool call batch 조율: 안전한 독립 tool 병렬화, 승인/파괴적 tool 순차
│   │   ├── tool_dispatch.py                 # ▣ #2 스키마 조립·가용성 필터(check_fn식)·라우팅·결과 수집
│   │   │                                    #     연결 안 된 Connector tool 제외, 예산 초과 시 bridge+관련도 스포트라이트로 폴딩(AGENT_EXECUTION §5.2, RUNTIME_DESIGN 2.1)
│   │   ├── orchestrator.py                  #   얇은 조율: learning/connector/execution 호출
│   │   ├── effect_surfacing.py              #   LearningEffect→SSE(+HITL), 메인 턴과 /learning-review·/learn이 공유
│   │   ├── subagents.py                     #   focused sub-agent runner (delegate_subagent builtin의 실행체) — 격리 미니 ReAct 루프 + 스킬 인덱스 주입 + tools 화이트리스트 + write_scratch 아티팩트 채널(2026-07-10 v2)
│   │   ├── titler.py                        #   세션 제목 생성 (post-turn, 논블로킹 — /learning-review·/learn과 같은 트리거 지점 패턴)
│   │   ├── builtins/                        #   등록 도구: read_file/write_file/edit_file/list_dir/search_files, terminal, fetch_url(v3 — markdown 추출·홉별 SSRF·재시도·페이지네이션·링크·JS껍데기 진단 + §hygiene 소형모델 위생: 문장/사실밀도 기반 main/article 스코핑·링크팜/숨김CSS·ARIA 비본문/레이아웃표 제거·반복 위젯/저엔트로피 명시실패(no-echo·no-cache)·짧은 고밀도 공지 보존·기본 12k자) + web_search(2026-07-18 — DuckDuckGo HTML 키리스 검색; 검색엔진 URL을 fetch하던 소형 모델 함정 해소), vision_analyze, skill_search/skill_view/propose_skill, delegate_subagent, create_*/edit_presentation; workspace.py는 경로 샌드박스 helper, write_scratch는 sub-agent 전용(thread 격리)
│   │   │   └── office/                      #   ★ 2026-07-05/06(+07-10 v2) — create_presentation/edit_presentation/create_workbook/create_document 빌트인, stdlib-only OOXML 라이터(sandbox 미적용 — RUNTIME_DESIGN 5장)
│   │   │       ├── pptx_writer.py           #     11레이아웃(title/section/bullets/two_column/stats/table/chart/timeline/quote/image/closing) + speaker notes + 그리드라인 차트 + autofit 폰트 스케일 + 임베디드 스펙(docProps/riceSpec.json — edit_presentation의 편집 원본)
│   │   │       ├── xlsx_writer.py           #     수식 로컬 평가 → 캐시값+계산결과 반환
│   │   │       ├── docx_writer.py           #     heading/paragraph/bullets/numbered/table/quote/callout/divider 블록
│   │   │       ├── formula.py               #     수식 엔진(SUM/AVERAGE/IF/NPV/IRR/SUMPRODUCT 등 22함수, 교차시트 참조, 순환→#CYCLE!, 미지원 함수는 #NAME? warning으로 분리)
│   │   │       └── palette.py               #     테마 프리셋(forest/health/finance/midnight/rose/mono), lenient 색상 해석(나쁜 입력→기본값+warning, 크래시 없음)
│   │   └── providers/                       #   모델 교체 지점
│   │       ├── interfaces.py                #     ILLMProvider (tool_use·streaming·caching 정규화)
│   │       ├── claude.py                    #     SaaS (Anthropic API)
│   │       ├── ollama.py                    #     on-prem (로컬 모델) — 메인 대상
│   │       ├── openai.py                    #     SaaS (OpenAI 호환)
│   │       ├── watsonx.py                   #     SaaS (IBM watsonx) — 실사용 검증 미완, 재검토 필요(코드 주석 명시)
│   │       ├── echo.py                      #     모델 미설정 시 폴백(테스트/데모, 플래너 건너뜀)
│   │       ├── _openai_compat.py            #     openai/watsonx가 공유하는 OpenAI 호환 wire 헬퍼
│   │       └── registry.py
│   │
│   ├── prompts/                             # model-facing 프롬프트 조립 전용 leaf 모듈
│   │   ├── system.py                        #   시스템 프롬프트 합성(base + 스킬/메모리/플러그인 스킬)
│   │   ├── planning.py                      #   TurnPlanner 메시지·응답 스키마·plan block 렌더링
│   │   ├── review.py                        #   learning review/learn 프롬프트와 tool schema
│   │   ├── curator.py                       #   스킬 통합(consolidate) 프롬프트
│   │   ├── discovery.py                     #   크로스세션 패턴 발굴 프롬프트·세션 다이제스트
│   │   ├── analysis.py                      #   스킬 설명 사이드카 생성 프롬프트
│   │   ├── title.py                         #   세션 제목 생성 프롬프트
│   │   └── __init__.py                      #   title helper 재수출
│   │
│   ├── execution/                           # ▣ #7 plugin loading  + #8 sandbox
│   │   ├── interfaces.py                    #   IPluginLoader / ISandbox (Protocol)
│   │   ├── service.py                       #   PluginLoaderService + ChildProcessSandbox 와이어링 파사드(composition_root 진입점)
│   │   ├── plugin/                          # ▣ #7
│   │   │   ├── loader.py                    #   manifest 파싱·스킬 주입·자체 실행 도구(entrypoint.tools, dotted module.func) 라우팅
│   │   │   ├── manifest.py                  #   requires.connectors 등 검증
│   │   │   └── registry.py
│   │   ├── sandbox/                         # ▣ #8 (plugin 자체 실행 도구의 자식 프로세스 sandbox)
│   │   │   ├── runner.py                    #   자식 프로세스 spawn·resource limit(allowlist env, RLIMIT_CPU/AS, timeout+kill) — plugin 실행에만 적용
│   │   │   ├── ipc.py                       #   stdin/stdout 프로토콜
│   │   │   └── policy.py                    #   실행 경계(RUNTIME_DESIGN 5장). terminal builtin(agent/builtins/terminal_tool.py)은 이 sandbox를 쓰지 않는 별도 경계 — RUNTIME_DESIGN 5장 참고
│   │   └── tests/
│   │       └── __init__.py                  #   vestige 패키지 마커. 실제 테스트는 top-level tests/에 배치
│   │
│   ├── learning/                            # ▣ #3 memory #4 skill #5 curator #6 loop +#9 user
│   │   ├── interfaces.py                    #   ILearningService (Protocol) — 분리 솔기
│   │   ├── service.py                       # ▣ #6 learning loop 파사드 (경험→축적→재사용→개선; prefetch/observe/검증게이트 조립)
│   │   ├── signals.py                       #   교정/실패/스킬화 의도 신호셋(한/영) — review gate가 재사용
│   │   ├── ledger.py                        #   ★ 신규(2026-07-03, Self-Improvement v3) — append-only learning_events 감사로그 + 영속 skill_suggestions 인박스(재시작 생존)
│   │   ├── discovery.py                     #   ★ 신규(2026-07-03) — 세션 간 패턴 발견 엔진(최소 2세션, 생성 전용, 실행 트리거는 server/scheduler.py)
│   │   ├── proposals.py                     #   ★ 신규(2026-07-03) — PatchProposal 공용 빌더
│   │   ├── memory/                          # ▣ #3 memory manager
│   │   │   ├── store.py                     #   사실 메모리·선호 프로필 (add() 시 §8 시크릿 스크러빙 적용) — 외부 콘텐츠 파생 메모리는 quarantined로 저장
│   │   │   └── search.py                    #   FTS5 세션 검색 (status='active'만 반환 — quarantine 필터링)
│   │   ├── skill/                           # ▣ #4 skill lifecycle
│   │   │   ├── lifecycle.py                 #   상태전이(active→stale→archived) + curator 통합 archive
│   │   │   ├── patch.py                     #   diff 산출(build_diff)만 — patch 저작 판단은 review/engine.py
│   │   │   ├── usage.py                     #   use/view/patch 카운터 + 관측된 도구 사용 빈도(tools dict, 상한 40개)
│   │   │   ├── guard.py                     #   크기 상한 + 시크릿 스크러빙 + 위협 패턴 스캔(scan_risks — 프롬프트 인젝션·시스템프롬프트 override·자격증명 유출·파괴적 쉘·역쉘·SSH 키 영속성 등) + 스킬 이름 규약
│   │   │   ├── relevance.py                 #   ★ 신규(2026-07-06/07) — 결정론적 스킬 관련도 스코어러(`core/lexical.py` 사다리 공유). 상위(임계 3.0, 최대 2개)만 자동 본문주입, 나머지는 인덱스+온디맨드 skill_view
│   │   │   ├── verifier.py                  #   ★ 신규(2026-07-07) — 스킬 승격 검증 게이트(결정론 lint, LLM 재채점 없음). fail=객관적 무결성(빈 본문·펜스 미폐합 등), warn=판단항목(thin-body·no-steps·unknown-tool 등). stage_proposal·screen·apply_patch/create/update 3중 관문
│   │   │   ├── builtin.py                   #   ★ 신규(2026-07-05) — 패키지 동봉 빌트인 스킬 카탈로그(예약 네임스페이스, active/archived 토글만 허용, 패치·삭제·롤백 불가)
│   │   │   ├── analysis.py                  #   ★ 신규(2026-07-06) — 스킬 "설명" 사이드카 포맷(SkillAnalysisData) — UI 전용, 권한/실행 판단에 미사용
│   │   │   ├── analyzer.py                  #   ★ 신규(2026-07-06) — 위 사이드카를 만드는 휴리스틱+LLM 엔진
│   │   │   ├── store.py                     #   ~/.rice/skills/*.md + .usage.json + <name>/.history/v<N>.md
│   │   │   └── builtin_skills/              #   패키지 동봉 스킬 원문 마크다운(예: business-planning.md) — pyproject package-data로 포함
│   │   ├── review/                          #   LLM 기반 스킬/메모리 저작
│   │   │   ├── gate.py                      #     ReviewGate — LLM 호출 가치 판단(값싼 휴리스틱, 내용 판단은 안 함)
│   │   │   ├── engine.py                    #     ReviewEngine — review()/learn() LLM 1회 호출, 쓰기 권한 없음(제안만) + connector/plugin/bridge/web 외부 증거의 메모리 quarantine 판정(_memory_effect)
│   │   │   └── context.py                   #     리뷰 전용 턴 컨텍스트 렌더러
│   │   ├── curator/                         # ▣ #5 curator
│   │   │   ├── curator.py                   #   자동 전이 규칙 + 수동 "스킬 정리"(mode=rules)
│   │   │   └── consolidate.py               #   ConsolidationEngine, LLM 스킬 통합 분석(mode=consolidate)
│   │   └── user_model/                      # ▣ #9 (user modeling 부분)
│   │       └── profile.py                   #   개인화·답변 스타일 학습
│   │
│   ├── connector/                           # ▣ #10 connector #11 credential #12 permission
│   │   ├── interfaces.py                    #   IConnectorService (Protocol) — 분리 솔기
│   │   ├── manager.py                       # ▣ #10 OAuth 연결/해제 흐름(state는 DB 영속 — 재시작 내성 + replay 방지)
│   │   ├── service.py                       #   ▣ #10 IConnectorService 실구현 파사드 — list_tools/invoke/permission/update-check 전부 여기로 수렴, 다중 커넥터 소유권 라우팅(_owner_of_tool)
│   │   ├── registry.py                      #   정적 커넥터 디스크립터(gmail/calendar/drive — auth/token/mcp endpoint, scope, client secret 우선순위/폴백)
│   │   ├── custom_registry.py               #   ★ 신규(2026-07-01) — 사용자가 등록한 커스텀 원격 커넥터 영속화(DB) + 동적 client 등록(RFC 7591)
│   │   ├── discovery.py                     #   ★ 신규(2026-07-01) — OAuth 자동 디스커버리(RFC 9728 Protected Resource Metadata → RFC 8414 AS Metadata, 부작용 없음)
│   │   ├── oauth/
│   │   │   └── flow.py                      #   OAuth2 Authorization Code Flow(제네릭, descriptor 기반)·refresh(rotation 지원)·redirect 처리
│   │   ├── mcp/                             #   ★ 신규 — 원격 MCP 클라이언트
│   │   │   ├── client.py                    #     Streamable HTTP: JSON-RPC 2.0 + SSE 파싱 + initialize 핸드셰이크(원격 OAuth 커넥터 + http 로컬 서버 공용)
│   │   │   └── stdio_client.py              #     로컬 stdio MCP 서버(spawn-per-op, 개행구분 JSON-RPC)
│   │   ├── local/                           #   ★ 신규 — 로컬 MCP(`~/.rice/.mcp.json`, Claude 표준 mcpServers 포맷)
│   │   │   └── config.py                    #     로드 + 원자적 쓰기(add/remove) + fail-closed 파싱(깨진 파일은 덮어쓰기 거부)
│   │   ├── credential/                      # ▣ #11 credential storage
│   │   │   └── keychain.py                  #   OS 키체인 연동(keyring) — 커스텀 커넥터 client_secret도 여기(DB에는 안 둠)
│   │   └── permission/                      # ▣ #12 permission system
│   │       └── checker.py                   #   plugin/tool → connector 접근 검증 + 도구별 allow/approval/block 정책(tool_dispatch.py가 강제)
│   │
│   └── core/                                # ▣ (#14 암묵) 공용 인프라 — 모듈 간 직접 import 금지선
│       ├── storage/
│       │   ├── sqlite.py                    #   WAL 모드·연결 풀
│       │   └── migrations/                  #   0001~0014(0009 결번 — 미배포 프로토타입이라 과거 파일을 직접 고쳐 병합한 흔적, [[system-authority]] 원칙)
│       ├── events.py                        #   in-process 이벤트 버스(모듈 간 결합 완화) — 단일 파일, 디렉토리 아님
│       ├── lexical.py                       #   ★ 신규(2026-07-07) — 결정론적 매칭 사다리(exact→substring→char-bigram fuzzy→ko→en 의도어 확장). tool_dispatch·skill relevance가 공유. 시멘틱(임베딩) 매칭은 8GB 바닥사양 로컬 제품 트레이드오프상 의도적 미채택
│       ├── model_json.py                    #   프로바이더 JSON 응답 파싱 공용 헬퍼
│       ├── tokens.py                        #   CJK 인식 토큰 추정 휴리스틱 + TokenCalibrator(실측 보정)
│       ├── types.py                         #   공용 타입(TurnPlan/PlanStep/EventType/MemoryItem 등) — CONTRACTS "triple lock"의 한 축
│       ├── paths.py                         #   ~/.rice/ 디렉토리 관리
│       ├── config.py                        #   config.yaml 스키마(AgentConfig·ContextConfig 등 — RUNTIME_DESIGN §2 정본)
│       └── errors.py
│
├── scripts/                                 #   데모/운영 스크립트 — gmail_oauth_demo.py(브라우저 로그인→키체인 저장→테스트 발송), seed_demo_sessions.py
├── eval/                                    #   학습 측정 하네스 (tests/와 분리, LEARNING_EVAL 5장)
│   ├── tasks/                               #     task 정의 — 현재 3종 전부 script 블록 보유(결정론 재생, 실LLM 미호출)
│   ├── scripted_provider.py                 #   ★ task의 script 블록을 재생하는 결정론 프로바이더(LLM 아님) — skill_marker 문자열로 cold/warm 분기
│   ├── profiles/warm/                       #     warm=스냅샷 시드(방법 B). cold는 checked-in 디렉토리가 아니라 매 실행 tempfile로 생성
│   ├── negative.py                          #   ★ 안전성 negative eval(N1~N6: 시크릿 비영속·인젝션 탐지/메모리 격리·재작성 쿨다운·stale patch 충돌 등) — LEARNING_EVAL 정본
│   ├── runner.py · metrics.py · report.py   #     suite 실행 → turns/tool_calls/success/skill_hit → results JSON
│   └── results/                             #     타임스탬프별 cold/warm/delta + negative-latest.json (UI fixture 소스, EVAL 6.3)
└── tests/                                   #   ★ 전 테스트가 여기 top-level에 flat 배치(30개 파일) — 모듈별 co-located tests/도 tests/e2e/도 실재하지 않음(execution/tests/만 __init__.py 하나 남은 vestige)
```

---

## 3. 모듈 ↔ 책임 매핑

| # | 책임 | 위치 |
|---|------|------|
| #1 | 대화 루프 + provider-facing context 관리 | `agent/loop.py`, `agent/context_manager.py` |
| #2 | tool dispatch/scheduling (스키마 조립·가용성 필터·라우팅·병렬 batch) | `agent/tool_dispatch.py`, `agent/tool_scheduler.py` |
| #2a | sub-agent delegation | `agent/subagents.py`, `agent/builtins/subagent_tools.py` |
| #2b | model-facing prompt assembly | `prompts/` |
| #3 | memory manager | `learning/memory/` |
| #4 | skill lifecycle (생성·상태전이·개선patch) | `learning/skill/` |
| #5 | curator | `learning/curator/` |
| #6 | learning loop | `learning/service.py` |
| #7 | plugin loading | `execution/service.py`, `execution/plugin/` |
| #8 | plugin tool execution sandbox | `execution/service.py`, `execution/sandbox/` |
| #9 | session / user modeling | `server/session/` (세션) + `learning/user_model/` (유저) |
| #10 | connector management | `connector/manager.py` |
| #11 | credential storage | `connector/credential/` |
| #12 | permission system | `connector/permission/` |
| #13 | 로컬 API 서버 | `server/` |
| #14 | local storage abstraction | `core/storage/` |
| #15 | 백그라운드 학습 스케줄러(curator·discovery 주기 실행, Self-Improvement v3) | `server/scheduler.py`, `learning/{ledger,discovery,proposals}.py` |

---

## 4. UI 화면 → 백엔드 → 모듈 매핑

> 8개 화면([RICE_PRODUCT_IDEA.md 4장](RICE_PRODUCT_IDEA.md))이 어느 백엔드·모듈로 연결되는지의 추적표. 엔드포인트 계약은 [RICE_CONTRACTS.md](RICE_CONTRACTS.md)가 소유.

| UI 화면 | 백엔드 | 주요 모듈 |
|---------|--------|-----------|
| HomeScreen | rice-runtime | `learning/`(스킬 사용 통계·대기 중 제안 집계) — 전용 엔드포인트 신설 없이 기존 API 재사용 |
| ChatScreen | rice-runtime | `agent/`(context/tool scheduling/sub-agent), `server/session/` |
| MySkillsScreen | rice-runtime | `learning/skill/`, `learning/curator/` |
| MyPluginsScreen | rice-runtime | `execution/plugin/`, `connector/permission/` |
| ConnectorsScreen | rice-runtime | `connector/` |
| SettingsScreen | rice-runtime | `core/config.py`(provider/learning/context/ui), `agent/providers/` |
| SkillMarketScreen | rice-platform | `rice_platform/registry.py`(카탈로그 스캔) + `app.py`(라우트) — `catalog/`·`artifacts/` 서브패키지 아님, 실제론 flat 구조(PLATFORM_DESIGN 정본). 설치는 runtime `execution/plugin/` |
| PluginMarketScreen | rice-platform | 위와 동일 — `type` 쿼리로만 구분(PLATFORM_DESIGN 정본) |

→ 8개 화면 모두 **2개 백엔드(rice-runtime 로컬 + rice-platform 원격)**로 커버된다. `rice-marketplace`는 `rice-platform`이 마운트하는 sibling 콘텐츠 저장소이며, 별도 API 백엔드로 보지 않는다.

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
