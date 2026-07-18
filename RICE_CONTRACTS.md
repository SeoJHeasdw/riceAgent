# RICE Contracts (레포 간 계약)

> 작성일: 2026-06-12 (개정: 2026-07-15 — H-2 bridge SSE·H-4 session lifecycle 정합화) · 상태: **확정 (결정 완료)**
> 범위: app↔runtime 로컬 API + 마켓 artifact 스펙 + platform API(개요)
> 해소 대상(전건 확정): [RICE_REPO_STRATEGY.md](RICE_REPO_STRATEGY.md) 9장 #2, #4 (+#5 일부)

---

## 1. app ↔ runtime 로컬 API (REPO_STRATEGY #2 해소)

### 1.1 프로세스 라이프사이클

```
Electron main process
  1. 가용 포트 선택 + 일회성 인증 토큰 생성
  2. 런타임 spawn:  rice-runtime --port {p} --token {t}
  3. GET /health 폴링 → ready
  4. 비정상 종료 시 재시작 (백오프, 최대 3회)
  5. 앱 종료 시 POST /shutdown → graceful stop
```

### 1.2 전송 방식

스트리밍은 **HTTP + SSE**로 한다 (비교):
- ★ **HTTP + SSE**: 단순, FastAPI 기본 지원, 단방향 스트리밍에 충분. 중단(abort)은 별도 POST로
- WebSocket: 양방향(중단/승인 요청)이 자연스러우나 구현 비용 ↑

**Tool 실행 승인(human-in-the-loop)**: 위험한 tool(Connector(MCP)·plugin이 제공하는 실행 도구) 실행 전 사용자 승인을 받는다(RUNTIME_DESIGN 5장). WS 승격 없이, SSE에 **`tool_approval_required` 이벤트**를 추가하고 앱이 **`POST /sessions/{id}/tool-approval`**로 응답한다(폴링형). 승인 UI는 개발 영역.

**Skill 개선 승인(human-in-the-loop)**: 스킬 본문 개선(patch)도 동일 폴링 패턴을 쓴다 — SSE **`skill_patch_proposed` 이벤트**(diff 포함)를 발행하고 앱이 **`POST /skills/{id}/patch`**로 승인/거부/편집 응답한다(LEARNING_LOOP 5.2). 자동 무단 수정 없음. **2026-07-02**: 스킬 **생성**도 이 동일 메커니즘으로 통일됐다 — `from_version=0`인 `skill_patch_proposed`가 "신규 스킬 제안"이다(더 이상 별도 `skill_created` 알림 이벤트로 즉시 저장하지 않는다 — LEARNING_LOOP §4).

**승인 상관·정지/재개·복구 (두 승인 공통, 메커니즘 정본 RUNTIME_DESIGN 5.1)**: 두 제어 이벤트는 고유 **`approval_id`** 를 싣고, 대응 POST 본문은 그 `approval_id`를 포함한다(서버가 parking된 요청과 매칭). 에이전트 루프는 응답까지 정지한 뒤 재개한다. 함정 처리:
- **timeout**: 기본 5분(설정) 무응답 시 **안전 기본값=거부/취소**(tool 미실행 / patch 미적용). 스킬 제안은 적용하지 않되, `timeout`/`disconnected` 결정이면 Self-Improvement v3 제안함(`/skills/suggestions`)으로 보존된다.
- **방치/중복**: 세션 `abort`·`shutdown` 시 대기 승인 일괄 취소. 이미 처리/만료된 `approval_id`로 늦게·중복 도착한 POST → **409/410**(idempotent no-op).
- **SSE 끊김·재연결**: 승인 상태는 SSE 연결과 독립(서버 레지스트리). 재연결 시 **`GET /sessions/{id}/pending-approvals`**로 대기 중 프롬프트(`{approval_id, kind, payload}`)를 복구해 카드 재렌더.

**명명 규칙**: SSE 제어 이벤트 타입은 wire vocabulary(`tool_approval_required`, `skill_patch_proposed`)이고, pending approval의 내부 `kind` 값은 parking vocabulary(`tool_approval`, `skill_patch`)다. 두 값은 의도적으로 다른 네임스페이스이며, `approval_id`가 둘을 연결한다.

### 1.3 엔드포인트 초안

| Method | Path | 용도 |
|--------|------|------|
| GET | `/health` | 기동 확인 |
| POST | `/shutdown` | graceful 종료 |
| POST | `/sessions` | 세션 생성 |
| GET | `/sessions` / `/sessions/{id}` | 목록/상세 (히스토리 UI) |
| ~~POST~~ | ~~`/sessions/{id}/attachments`~~ | **설계만 있고 미구현**(선업로드 multipart, xlsx·png만 → `{attachment_id, kind}`) — 라우트가 코드에 없다(2026-07-08 확인, 아래 첨부 절 참고) |
| POST | `/sessions/{id}/messages` | 메시지 전송(현재는 `{content}`뿐 — `attachment_ids[]` 참조는 위 미구현과 함께 보류) → **SSE 스트림 응답** (9종 이벤트, 2026-07-04 정정 — 아래) |
| POST | `/sessions/{id}/title` | 세션 제목 생성(post-turn, 논블로킹) — 첫 턴 `done` 후 호출 |
| **POST** | **`/sessions/{id}/learning-review`** | **게이트된 LLM 스킬/메모리 리뷰(post-turn, 논블로킹) → SSE 스트림(메인 턴과 동일 이벤트 어휘) — LEARNING_LOOP §4. body: `{tool_calls_made}`. 게이트 미통과 시 즉시 `done`만.** |
| **POST** | **`/sessions/{id}/learn`** | **명시적 "이 대화를 스킬로 저장" — 게이트 없이 항상 시도 → SSE 스트림 — LEARNING_LOOP §5.3. body: `{instruction?}`.** |
| POST | `/sessions/{id}/abort` | 생성 중단 |
| **DELETE** | **`/sessions/{id}`** | **세션 삭제: 진행 중 턴에 abort 요청 + bounded drain 대기 → 영속 세션/메시지 제거 → 세션 소유 agent/learning 인메모리 상태 해제. 전역 plugin-skill snapshot은 유지** |
| POST | `/sessions/{id}/tool-approval` | tool 실행 승인 응답 (`tool_approval_required` 이벤트에 대응, `approval_id` 포함) |
| GET | `/sessions/{id}/pending-approvals` | 대기 중 승인 목록 (SSE 재연결 복구 — RUNTIME_DESIGN 5.1) |
| GET | `/sessions/{id}/messages?cursor=` | 과거 메시지 페이지네이션 (히스토리 로드) |
| POST | `/sessions/{id}/regenerate` | 응답 재생성 (ChatScreen) — **경로 정정(2026-07-08)**: `msg_id` 세그먼트 없음, 과거 문서 오기 — 코드·프론트 모두 이 경로 |
| ~~GET~~ | ~~`/sessions/{id}/export?format=md\|json`~~ | **미구현** — 코드에 없음(2026-07-08 확인) |
| GET | `/skills` | 스킬 목록 + 상태/사용 통계 (learning loop 가시화용). **빌트인 스킬(`source:"builtin"`, 앱과 함께 배포·읽기 전용)도 포함된다** |
| GET | `/skills/{id}` | 스킬 상세 (내용 · version · tags · usage — MySkillsScreen) |
| **POST** | **`/skills/{id}/analysis/refresh`** | **스킬 "설명" 사이드카 재생성(LLM 1회, 실패 시 휴리스틱 폴백) — UI 전용, 권한/실행 판단과 무관(2026-07-08 반영)** |
| POST | `/skills/curate` | 수동 "스킬 정리" 트리거. **`?mode=rules\|consolidate`**(기본 `rules`, 하위호환) — `consolidate`는 규칙 전이 + LLM 통합 제안(있으면) 반환, LEARNING_LOOP §5.1 |
| PATCH | `/skills/{id}/state` | 스킬 상태 전환 (archive, activate). **빌트인은 active/archived(활성/비활성)만 허용 — 그 외 전이·PUT·DELETE·rollback은 422** |
| **POST** | **`/skills/{id}/patch`** | **스킬 개선(patch) *또는* 신규 생성(from_version=0) 제안 응답 (approve/reject/edited_body) — LEARNING_LOOP 5.2·§4** |
| **POST** | **`/skills/{id}/archive-decision`** | **curator 통합의 정리(prune) 제안 응답 (approved, absorbed_into?) — approval_id 파킹 없음, LEARNING_LOOP §5.1** |
| **POST** | **`/skills/{id}/consolidate-decision`** | **curator 통합의 병합(umbrella patch/create) 제안 응답 (approved, proposed_body?, description?, tags?) — approval_id 파킹 없음, LEARNING_LOOP §5.1** |
| **GET** | **`/skills/{id}/history`** | **스킬 버전 히스토리 (diff·롤백 UI)** |
| **POST** | **`/skills/{id}/rollback`** | **이전 버전 복구 (선택)** |
| **POST** | **`/skills/{id}/export`** | **스킬 → 마켓 artifact 내보내기(2026-07-12, 학습 루프↔마켓 다리) — 워크스페이스 `skill-exports/`에 §2.1 manifest 디렉토리(platform/git-ready) + 재현 가능 tar.gz + sha256 반환. 유통본과 원본의 계보 없음(관계 단절 — 설치 순간 받은 쪽의 독립 학습 스킬). MySkills 공개(publish) 확정의 실체** |
| **GET** | **`/skills/suggestions`** | **영속 제안함(Self-Improvement v3) — 타임아웃된 HITL 제안 + 발굴 산출물. payload는 `skill_patch_proposed`와 동일 어휘(approval_id 없음) — LEARNING_LOOP §9** |
| **POST** | **`/skills/suggestions/{sid}/decision`** | **제안함 항목 승인/거부 (approved, edited_body?) — 승인 시 create/patch는 apply_patch, archive는 decide_archive로 수렴. 결정은 ledger에 기록 — LEARNING_LOOP §9** |
| **POST** | **`/skills/discover`** | **크로스세션 반복 패턴 발굴(수동 트리거, LLM 1회) — 산출 제안은 제안함으로. `{scanned_sessions, suggestions}` 반환 — LEARNING_LOOP §9. 백그라운드 주기는 `discovery_interval_seconds`(기본 0=off)** |
| **GET** | **`/skills/candidates`** | **candidate 승격 게이트(2026-07-19, LEARNING_LOOP §10.6) 목록 — 승인됐지만 baseline-vs-candidate 평가 전/중/후인 제안들. 각 행: `{id, skill_id, kind, status(pending\|running\|promoted\|discarded\|expired\|failed), baseline_version, payload(제안함과 동일 어휘 + seed_prompts), attempts, report(평가 리포트 — LEARNING_EVAL §10)}`** |
| **POST** | **`/skills/candidates/{cid}/evaluate`** | **candidate 1건 즉시 평가(수동 트리거 — 백그라운드는 `candidate_eval_interval_seconds` 틱). LLM 호출 다수라 느린 일반 REST(로딩 표시 필요). 통과 시 승격(apply_patch)까지 완료 후 갱신된 행 반환. pending 아니면 409** |
| **POST** | **`/skills/candidates/{cid}/decision`** | **candidate 수동 승격/폐기 `{approved}` — 평가를 기다리지 않는 사용자 오버라이드(스킬은 사용자 소유; provider 오프라인·평가 실패의 탈출구). pending/failed에서만 가능(running은 409), 승격 시 baseline 충돌이면 409. `{applied, version, status}` 반환** |
| **GET** | **`/learning/events?limit=`** | **학습 감사 로그 — 제안/승인/거부/타임아웃/메모리 기록의 영속 이력 (learning_events) — LEARNING_LOOP §9** |
| **GET** | **`/eval/impact`** | **학습효과(cold/warm) 리포트 — LEARNING_EVAL §6.3 정본. 프론트는 이 엔드포인트를 우선 호출하고, 실패 시 번들 시드를 폴백으로 사용한다** |
| GET | `/plugins` | 설치된 플러그인 목록 |
| PATCH | `/plugins/{id}/enabled` | 플러그인 활성화/비활성화 |
| DELETE | `/plugins/{id}` | 플러그인 삭제 |
| **GET** | **`/connectors`** | **연결된 Connector 목록(원격 OAuth — gmail/calendar/drive/커스텀)** |
| **GET** | **`/connectors/{id}`** | **Connector 상세 (ConnectorsScreen `/connectors/:id` 라우트)** |
| **POST** | **`/connectors`** | **새 Connector 연결 (OAuth 시작)** |
| **DELETE** | **`/connectors/{id}`** | **Connector 삭제** |
| **GET** | **`/connectors/{id}/status`** | **연결 상태 확인** |
| **GET** | **`/connectors/oauth/callback`** | **OAuth callback 처리(bearer 토큰 면제)** |
| **POST** | **`/connectors/{id}/tools/resync`** | **원격 MCP 도구 카탈로그 강제 재조회(2026-07-01, 이전 "재인증" 버튼 자리 — 2026-07-08 문서 반영)** |
| **POST** | **`/connectors/{id}/tools/check`** | **원격 도구 카탈로그 변경 감지(diff만, 미적용) — 업데이트 배지용(2026-07-08 반영)** |
| **POST** | **`/connectors/{id}/tools/apply-update`** | **감지된 pending 도구 카탈로그를 active로 승격(2026-07-08 반영)** |
| **PUT** | **`/connectors/{id}/tools/permission`** | **도구별 allow\|approval\|block 정책 저장 — dispatcher가 강제(2026-07-08 반영)** |
| **GET** | **`/connectors/local`** | **로컬 MCP(`.mcp.json`) 서버 목록 + 캐시된 도구 카탈로그, spawn 없음(2026-07-08 반영)** |
| **POST** | **`/connectors/local/refresh`** | **전체 로컬 서버 재-spawn + tools/list 갱신(2026-07-08 반영)** |
| **POST** | **`/connectors/local/test`** | **로컬 MCP 서버 연결 테스트(부작용 없음, `.mcp.json` 안 씀 — 등록 전 미리보기, 2026-07-08 반영)** |
| **POST** | **`/connectors/local`** | **로컬 MCP 서버 등록(`.mcp.json`에 기록 + 해당 서버만 warm, 2026-07-08 반영)** |
| **DELETE** | **`/connectors/local/{name}`** | **로컬 MCP 서버 제거(`.mcp.json`·캐시·권한 정리, idempotent, 2026-07-08 반영)** |
| **POST** | **`/connectors/custom/discover`** | **커스텀 원격 커넥터 OAuth 메타데이터 자동 발견(RFC 9728/8414, 부작용 없음 — 미리보기, 2026-07-08 반영)** |
| **POST** | **`/connectors/custom`** | **커스텀 원격 커넥터 등록 확정(RFC 7591 동적 client 등록 포함) + 즉시 OAuth 시작(2026-07-08 반영)** |
| GET | `/memories` | 메모리 목록 (개인화 가시화용) — 각 항목에 `status`(active\|quarantined) 포함(2026-07-08 반영, §6.7) |
| **DELETE** | **`/memories/{id}`** | **메모리 삭제(Self-Improvement v3 위생) — 메모리는 HITL 없이 즉시 반영되는 유일한 학습 경로라 사후 제거 수단이 필수. LEARNING_LOOP §6** |
| **POST** | **`/memories/{id}/approve`** | **격리(quarantined) 메모리 승인 → active 전환(2026-07-08 반영, §6.7). 없는 id는 404** |
| GET | `/models` | 사용 가능 모델 목록 |
| **GET** | **`/ollama/models`** | **로컬 Ollama 데몬의 설치 모델 목록 프록시(2026-07-08 반영)** |
| **GET** | **`/ollama/health`** | **현재 설정된 Ollama 모델의 설치 여부 확인(2026-07-08 반영)** |
| GET | `/settings` | 설정 조회 (provider·learning·context·ui·**agent**(plan-and-execute 노브, AGENT_EXECUTION §8 정본) + registered_models) |
| PUT | `/settings` | 설정 갱신 (provider/model/temperature, context 렌더링 예산/threshold, **agent 섹션**, **registered_models**(멀티 SaaS 모델+API 키 등록), api_key→키체인, language, theme, **font**) |
| POST | `/artifacts/install` | platform에서 받은 artifact 설치 (2장 manifest 전달) |
| DELETE | `/artifacts/{id}` | 제거 |
| **GET** | **`/workspace`** | **표시용 워크스페이스 절대 경로 조회(`RICE_HOME` 재배치 반영; 변경 대기 중이면 대기 경로 반환) — 2026-07-10 반영** |
| **PUT** | **`/workspace/location`** | **생성 파일 저장 폴더 변경(config 영속 + 라이브 홀더에 큐잉 → 다음 턴 경계에서 promote되어 재시작 없이 적용; `path:null`은 기본 위치 복원) — 2026-07-11 라이브 반영 개정** |
| **GET** | **`/workspace/file?path=`** | **워크스페이스 샌드박스 내 파일 읽기(경로 탈출 방지, 256KB 상한) — 2026-07-08 반영** |
| **POST** | **`/workspace/open`** | **파일을 OS 기본 앱으로 열기(macOS `open`/Windows `os.startfile`/`xdg-open`) — 2026-07-08 반영** |
| **POST** | **`/workspace/open-folder`** | **워크스페이스 폴더를 Finder/Explorer/파일 관리자에서 열기 — 2026-07-10 반영** |

**SSE 이벤트 9종** (Learning Loop 가시화, **2026-07-02: `skill_created` 제거**, **2026-07-04: `plan_updated` 추가** — 아래 참고):
1. `thinking` - LLM 사고 과정 스트리밍
2. `plan_updated` - 턴 계획 생성/전이/수정 (plan-and-execute × ReAct, AGENT_EXECUTION §3). 항상 **전체 스냅샷**(plan_id, goal, status, revision, steps[]) — 클라이언트는 같은 plan_id 카드를 통째로 교체한다. 같은 스냅샷이 메시지 metadata(`plan`)로 영속화되어 리로드가 카드를 복원한다.
3. `tool_call` - Tool 호출 시작 (tool_name, arguments)
4. `tool_result` - Tool 실행 결과
5. `message` - 최종 응답 메시지 (delta 또는 complete)
6. `memory_written` - 메모리 저장됨 (memory_type, content)
7. `skill_patched` - 스킬 개선 **또는 신규 생성** 적용됨 (skill_id, version) — LEARNING_LOOP 5.2·§4. `version === 1`이면 신규 생성이었다는 뜻 — 클라이언트는 로컬 목록에 해당 skill_id가 없으면 갱신이 아니라 **추가**해야 한다.
8. `done` - 응답 완료
9. `error` - 오류 발생 (error_code, message). 모델 호출의 사용자 조치 가능 오류는 `provider.ollama_unreachable`·`provider.model_not_found`·`provider.auth_failed`·`provider.quota_exceeded` 네 안정 코드로 전달한다.

> **`skill_candidate_staged`(skill_id, candidate_id, baseline_version) — 2026-07-19 순수 추가(조건부 10번째 스트림 이벤트).** `candidate_eval_enabled`(기본 off)가 켜진 경우에만, 스킬 제안 승인이 즉시 적용 대신 candidate 격리로 귀결됐을 때 `skill_patched` **대신** 발행된다 — "적용됨"과 "평가 대기"는 다른 사건이다(LEARNING_LOOP §10.6). 이후의 승격/폐기는 백그라운드에서 일어나므로 SSE가 아니라 `GET /skills/candidates`·`GET /learning/events`로 관찰한다(승격되면 `GET /skills`의 version이 오른다). 구형 클라이언트는 미지 타입을 드랍하므로(sse.ts) 하위호환 — 게이트가 꺼져 있으면(기본) 이 이벤트는 절대 발행되지 않고 스트림은 기존 9종 그대로다.

> **`tool_call` bridge 표현 계약**: abort 없이 결과까지 도달한 bridge 실행은 public
> SSE에서 wrapper의 outer `tool_call_id`를 유지한 요청 대상 이름·인자의 `tool_call`
> 1회와 같은 id의 `tool_result` 1회로 표현한다. 내부 `:bridged` id와 call/result
> envelope는 공개하지 않으며, 대상이 승인을 요구하면 `tool_approval_required`만
> 대상 이름·인자로 두 envelope 사이에 전달한다. malformed wrapper도 dispatch가
> 계속되면 outer id의 단일 실패 결과로 끝난다. Provider-compatible durable
> history는 모델이 낸 `tool_call({name, arguments})` wrapper를 보존하고, 리로드
> UI는 대응 tool message의 `metadata.tool_source="bridge"`를 근거로 표시만 요청
> 대상으로 unwrap한다.
>
> **도구 결과 성패의 정본 계약 (2026-07-15)**: 라이브 SSE `tool_result`의 `ok`와
> 짝을 이루는 리로드 축은 tool message의 **`metadata.ok`**다. tool message의
> `content`는 **모델용 렌더**라 실패가 `"ERROR: "` 접두사로만 남는데, 리로드 UI가
> 그걸 문자열로 되짚게 하면 안 된다 — `metadata.ok`가 없던 동안 리로드는 모든 도구
> 카드를 성공(완료)으로 그렸다(2026-07-15 실측: chat_1.png, 실패한
> `create_presentation` 1회차가 완료로 표시). 런타임은 모든 tool message를
> `loop._tool_result_message` 한 곳에서 만들어 `tool_call_id`·`tool_source`·
> `tool_name`·`ok`를 항상 싣는다. **이 필드 이전에 저장된 세션에는 `ok`가 없다** —
> 클라이언트는 `ok`가 boolean일 때만 정본으로 쓰고, 없으면 `"ERROR: "` 접두사
> 폴백으로 하위호환한다.
>
> ~~`skill_created`(skill_id, title, version)~~ — **2026-07-02 제거.** 스킬 생성은 이제 `skill_patch_proposed`(`from_version=0`)로 표현되며, 패치와 동일한 HITL 승인 게이트를 거친다. 따라서 승인 없이 즉시 저장한 뒤 `skill_created`로 통지하던 이전 경로는 제거됐다. `POST /sessions/{id}/learning-review`·`/learn`(아래)도 위 표의 9종과 동일한 이벤트 어휘만 방출한다.
>
> 제어 이벤트(별도, 각자 고유 `approval_id` 포함 — 정지/재개·timeout·재연결 처리는 1.2, 메커니즘은 RUNTIME_DESIGN 5.1): `tool_approval_required`(approval_id, tool_name, arguments) — 위험 tool 실행 전 발행 → 앱이 `POST /sessions/{id}/tool-approval`(approval_id 포함)로 응답. `skill_patch_proposed`(approval_id, skill_id, changelog, diff, trigger, from_version?, to_version?, **proposed_body?**, **warnings?**, **derived_from_external?**, **evidence_message_ids?**) — 스킬 개선/생성 제안 발행 → 앱이 diff 카드 표시 후 `POST /skills/{id}/patch`(approval_id 포함)로 응답 (LEARNING_LOOP 5.2). **`proposed_body`/`warnings`는 2026-07-02 추가**: 이전엔 제안의 실제 전체 본문이 SSE payload에 실리지 않아 프론트가 `diff.lines`를 필터링해 재구성했는데(edge case에서 미묘하게 어긋날 위험), 이제 서버가 준 원문을 그대로 쓸 수 있다. `warnings`는 §8 가드(시크릿 스크러빙)가 뭔가 마스킹했을 때의 경고 문자열 배열. **`derived_from_external`/`evidence_message_ids`(2026-07-08 문서 반영)**: 제안이 외부 콘텐츠 섞인 턴에서 나왔는지와 그 근거 tool 메시지 id — 메모리 격리(§6.7)와 같은 판정 축을 스킬 제안에도 노출한다.
>
> **curator 통합(`/skills/curate?mode=consolidate`, LEARNING_LOOP §5.1)이 낳는 제안은 이 SSE 메커니즘을 쓰지 않는다** — 스트림이 아닌 일반 REST 응답에 그대로 실려온다(`{transitioned, proposals}`, 각 proposal은 `{"kind":"patch",...}` 또는 `{"kind":"archive",...}`). 승인은 `kind:"patch"`면 `/skills/{id}/consolidate-decision`, `kind:"archive"`면 `/skills/{id}/archive-decision`으로 — 둘 다 `approval_id` 불필요(제안이 애초에 파킹된 적이 없다).

- ✅ 스펙 소유: `rice-runtime`이 `contracts/runtime.yaml`(runtime OpenAPI 스펙)을 소유, `rice-app`은 코드젠 (REPO_STRATEGY 4.3, RUNTIME_DIRECTORY 1장)

**라우트 선언 순서 주의사항**: `server/routes/skills.py`에서 정적 하위 경로(`/skills/suggestions`, `/skills/discover` 등)는 동적 `GET /skills/{id}`보다 먼저 선언해야 한다. FastAPI는 선언 순서대로 매칭하므로 같은 method의 정적 경로가 뒤에 있으면 해당 세그먼트가 `skill_id`로 해석될 수 있다. 신규 `/skills/*` 라우트 추가 시 이 순서를 유지한다.

**Learning Impact(eval) 결과 노출**: `GET /eval/impact`는 `server/routes/eval_impact.py`에 구현되어 있으며, `$RICE_EVAL_RESULTS/latest.json` → `~/.rice/eval/results/latest.json` → repo 내 `eval/results/latest.json` 순서로 최신 리포트를 조회한다. 프론트(`MySkillsScreen`)는 이 엔드포인트를 우선 호출하고, 번들된 시드(`IMPACT_REPORT`)는 fetch 실패 시에만 사용한다(`demo={!impact}`로 구분). 스키마 정본은 [RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md) 6.3.

**엔드포인트**: 위 초안을 정본으로 하며, `contracts/runtime.yaml`(OpenAPI)로 상세화한다.

**첨부/멀티모달 입력**: **설계 확정, 구현은 아직(2026-07-08 확인)** — 대상은 xlsx·png 2종만(벤치마크: Claude cowork의 파일 첨부). 설계된 방식은 **선업로드 후 참조**: 앱이 `POST /sessions/{id}/attachments`(multipart)로 먼저 올려 `attachment_id`를 받고, `POST /sessions/{id}/messages`는 `attachment_ids[]`로 참조 — SSE 스트림 응답과 multipart 본문이 한 요청에서 충돌하는 걸 피하기 위함이다. 런타임이 xlsx·png 외 MIME을 **거부**(415)하는 것도 설계값이다. **그러나 이 라우트도 `MessageIn.attachment_ids` 필드도 코드에 없다** — `messages.py`의 `MessageIn`은 `content` 하나뿐이고, 프론트 `sse.ts` 주석도 여전히 미래형("CONTRACTS decision 1-C (future)")으로 남아 있다. 착수 시 이 절 전체를 실제 스펙으로 갱신할 것).

**스킬 개선(patch) 계약** (LEARNING_LOOP 소유): `skill_patched`·`skill_patch_proposed` 이벤트 + `/skills/{id}/patch|history|rollback` 엔드포인트. 본 절에 반영 완료.

**LLM 기반 스킬/메모리 저작 계약 (2026-07-02, LEARNING_LOOP §4·§5.2·§5.3 소유)**: `POST /sessions/{id}/learning-review`(게이트된 자동 리뷰)·`/learn`(명시적 요청) — 둘 다 메인 턴과 분리된 SSE 스트림(같은 이벤트 어휘)으로, LLM 호출이 메인 턴의 `done`을 지연시키지 않는다(`/title`과 동일 원칙). **curator 통합 계약 (LEARNING_LOOP §5.1 소유)**: `POST /skills/curate?mode=consolidate` + `/skills/{id}/archive-decision`·`/consolidate-decision` — SSE가 아닌 일반 REST(제안이 JSON 응답에 그대로 실림, approval_id 파킹 없음). 본 절에 반영 완료.

---

## 2. Artifact 스펙 (REPO_STRATEGY #4 해소)

> Skill Market / Plugin Market에서 유통되는 패키지의 실체. runtime의 plugin loading과 platform의 catalog 양쪽이 이 스펙에 의존한다.

### 2.1 manifest 초안

```yaml
# rice-artifact.yaml
schema_version: 1
type: skill | plugin         # 마켓 구분 (REPO_STRATEGY 5.3의 artifact_type)
name: wxo-expert
version: 1.2.0               # semver
display_name: "WXO 전문가"
description: "..."
category: expert                          # 마켓 필터용 (PLATFORM_DESIGN catalog)
tags: ["wxo", "ibm", "troubleshooting"]   # 마켓 검색/필터용
author: { name: "...", team: "..." }
entrypoint:                   # type별 상이
  # skill:  선언적 절차 (SKILL.md + 선택적 references/scripts/templates)
  # plugin: 지식 패키지 (skills/ 디렉토리 + 요구 Connector)
requires:
  runtime: ">=0.1"
  connectors: ["gmail"]                    # 선택적: 필요한 외부 서비스 연결 (프로토타입 = gmail)
checksum: sha256-...          # 다운로드 무결성 (RUNTIME_DESIGN 문서 9장)
```

### 2.2 패키지 레이아웃

```
artifact.tar.gz (skill)              artifact.tar.gz (plugin)
├── rice-artifact.yaml               ├── rice-artifact.yaml
├── SKILL.md                         └── skills/                 # SKILL.md 묶음 (RUNTIME_DESIGN 문서 4장과 동일 형식)
├── references/ (선택)                   └── */SKILL.md           #   + manifest의 requires.connectors로 외부 접근 선언
├── scripts/ (선택)
└── templates/ (선택)
```

핵심 설계:
- ★ **skill = 선언적 절차(SKILL.md) + 선택적 번들 파일** — references/scripts/templates는 절차가 읽는 리소스이며, 포함됐다는 이유만으로 자동 실행 권한을 얻지 않는다. 원자적 실행과 외부 접근은 아래 Connector/도구 승인 경계를 그대로 통과한다.
- ★ **plugin = skills 묶음 + 요구 Connector(들)** — "Plugin은 머리에 추가되는 전문성"(PRODUCT_IDEA)과 일치. 외부 서비스 접근·원자적 실행 도구가 필요하면 `requires.connectors`로 선언하고, 해당 실행/접근은 **Connector(MCP) 레이어**가 담당한다.

→ 마켓 artifact(skill·plugin) 자체는 **선언적**이므로 sandbox를 우회한다. 실제 코드 실행/외부 접근의 sandbox 경계(RUNTIME_DESIGN 5장)는 **Connector(MCP 서버) 및 plugin이 포함한 connector 코드**에 적용된다.

원자적 실행 도구·외부 접근은 **Connector(MCP) 레이어**로 일원화한다. 자체 MCP 어댑터 구현 수준은 Phase 2 — 근거:
1. ★ Phase 1은 프로토타입 Connector(Gmail OAuth)만; MCP 서버 일반 어댑터는 Phase 2 확장 포인트로 명시
2. 처음부터 광범위한 MCP 서버 형식 채택 — 생태계 호환 ↑, 그러나 데모 복잡도와 패키징(Node/uv 의존) 부담 ↑

**manifest/레이아웃**: 마켓 패키지의 실체는 `rice-artifact.yaml`(schema_version·type·name·version·requires.connectors·checksum 등) + skill은 루트 `SKILL.md`와 선택적 번들 파일, plugin은 `skills/` 묶음으로 고정한다. 이 스키마가 runtime·platform·app 3곳의 단일 출처다(5장).

### 2.3 설치 흐름

```
앱(마켓 화면) → platform: artifact 상세/다운로드 URL 조회
 → Electron 앱: main process가 tar.gz 다운로드 → runtime: POST /artifacts/install (파일 경로 + manifest + checksum)
 → 브라우저 dev: runtime: POST /artifacts/install (localhost platform download URL + manifest)
 → runtime: 체크섬 검증 → ~/.rice/{skills|plugins}/ 배치 → 로드
 → 이후 대화부터 스킬 주입 / 요구 Connector(MCP) 도구 등록 반영
```

---

## 3. Platform API (Phase 2 직전 상세화 — 지금은 개요만)

> 플랫폼 설계 상세(catalog · registry · 버전 · publish · moderation · Blue Point · PostgreSQL 스키마)는 [RICE_PLATFORM_DESIGN.md](RICE_PLATFORM_DESIGN.md)를 참조.

| Method | Path | 용도 |
|--------|------|------|
| GET | `/catalog?type=&q=&category=&tags=&sort=` | 검색/목록/필터/정렬 |
| GET | `/artifacts/{id}` / `/artifacts/{id}/download` | 상세/다운로드 (download는 `X-Checksum: sha256-…` 헤더 동봉) |
| GET | `/artifacts/{id}/files/{path}` | artifact 내 개별 파일 내용 (app SkillFileBrowser lazy load) |
| POST | `/artifacts` | 등록(publish) |
| POST | `/artifacts/{id}/sponsor` | Blue Point 후원 (mock) |

- ✅ 스펙 소유: `rice-platform`의 `apps/api`
- 데모 단계 인증 없음 (REPO_STRATEGY 4.2)
- **Phase 1 mock 카탈로그**: platform 없이 로컬 디렉토리를 mock 카탈로그로 사용(`file://` 설치). 마켓 화면은 같은 manifest를 읽는다.

---

## 4. Connector 계약

> ConnectorsScreen 지원을 위한 외부 서비스 연결 관리 계약

### 4.1 Connector 개념

**정의**: Plugin/에이전트가 외부 서비스에 접근하기 위한 인증된 연결. 원래 **Gmail 단일 커넥터만** 프로토타입 범위였으나, 실제로는 **Google 계열 3종(Gmail·Calendar·Drive, 같은 OAuth client 공유) + 로컬 MCP(`.mcp.json`) + 커스텀 원격 MCP(OAuth 자동 디스커버리)**까지 확장됐다(2026-07-01, 2026-07-08 문서 정정 — RUNTIME_DESIGN 9장이 정본).

**구성 요소**:
- Connector ID(예: `gmail`/`calendar`/`drive`/`local:<name>`/`custom_<slug>`)
- 원격 타입은 OAuth 2.0(Authorization Code Flow, 커스텀은 RFC 7591 동적 client 등록 포함), 로컬은 `.mcp.json`(stdio/http, 무인증도 허용)
- 요구 scope 목록 (connector_type별) — `GET /connectors/{id}`가 부여된 scope를 노출 (ConnectorsScreen)
- Credential 저장 (OS 키체인 — 커스텀 커넥터 client_secret도 동일 저장소)
- Permission 검증 (Plugin/도구 → Connector 접근 권한, 도구별 allow/approval/block 정책 포함)

### 4.2 OAuth 흐름

```
1. 사용자가 ConnectorsScreen에서 "Gmail 연결" 클릭
2. POST /connectors { connector_type: "gmail" }
3. Runtime이 OAuth URL 생성 → 앱이 브라우저 열기
4. 사용자 인증 후 redirect → GET /connectors/oauth/callback?code=...
5. Runtime이 access_token 획득 → OS 키체인 저장
6. Connector 상태 "connected"로 업데이트
```

> 동일 흐름이 `calendar`/`drive`/커스텀 원격 타입에도 그대로 적용된다(`connector_type`만 다름) — descriptor 기반 제네릭 구현이라 Gmail 전용 분기가 없다(2026-07-08 반영).

### 4.3 Credential 저장

- **저장 위치**: OS 키체인 (macOS Keychain / Windows Credential Manager)
- **저장 항목**: access_token, refresh_token, expires_at
- **갱신 로직**: Runtime이 자동으로 refresh_token 사용하여 갱신

### 4.4 Permission 검증

Plugin manifest의 `requires.connectors` 필드 기반 (값은 connector_type 목록):

```yaml
# plugin manifest
requires:
  connectors: ["gmail"]                     # connector_type 목록 (프로토타입)
```

**검증 시점**:
1. Plugin 설치 시: 필요한 Connector 목록 표시
2. Plugin 활성화 시: 모든 필요한 Connector가 연결되었는지 확인
3. Tool 실행 시: Runtime이 Permission System으로 접근 권한 검증

---

## 5. 타입 공유 규칙 (확정 사항 재확인)

- ✅ 각 API 제공 레포가 OpenAPI 스펙 소유 → 소비 레포 코드젠 (REPO_STRATEGY 4.3)
- ✅ artifact manifest 스키마는 이 문서가 단일 출처 — runtime / platform / app 3곳에서 참조하므로 JSON Schema로 작성해 각 레포에 복사가 아닌 **스펙 파일 참조**로 배포

---

## 6. Skill·Plugin·Connector 고도화 반영 (2026-06-30, 분석 리포트 v1/v2 교정)

> Skill/Plugin/Connector 심층 분석에서 발견한 계약-코드 갭을 코드에서 교정했고, 그중
> **계약 표면이 바뀐 항목**만 여기 고정한다(상세 동작은 각 모듈 docstring).

### 6.1 Skill CRUD (신규 — §1.3 보강)
- `POST /skills` `{name(kebab), body, description?, tags?}` → 사용자 직접 스킬 생성(source=`user`).
- `PUT /skills/{id}` `{body?, description?, tags?, state?}` → 직접 편집. **본문 변경 시 version+1 + .history 보관**.
- `PUT /skills/{id}/files/{file_path}` `{content}` → 번들 reference/script/template 파일 편집. **version+1 + 해당 버전의 전체 번들 스냅샷 보관**.
- `DELETE /skills/{id}` → 본문·usage·history 사이드카 모두 삭제.

### 6.2 HITL 승인 응답 코드 (§1.2 준수)
- `POST /sessions/{id}/tool-approval`, `POST /skills/{id}/patch`: 이미 처리/만료된 `approval_id` → **409 Conflict**(과거: 항상 200). `resolve()`가 처리 여부(bool)를 반환한다.
- `POST /skills/{id}/patch`: URL `skill_id` 와 `approval_id` 의 대상 스킬이 **일치해야** 함(불일치 422).

### 6.3 Artifact 설치 무결성 (§2.3)
- `POST /artifacts/install` 에 선택 필드 **`checksum`** 추가 — 앱이 platform download 응답의 `X-Checksum` 헤더 값을 그대로 전달하면 manifest.checksum 보다 우선해 검증한다(빈 값이면 dev 모드).
- `POST /artifacts/install` 은 **`path` 또는 `url`** 을 받는다. `path`는 Electron main이 저장한 로컬 tar.gz, `url`은 브라우저 dev용 localhost platform `/artifacts/{id}/download` URL이다. 원격 URL은 거부한다.
- manifest `version` 은 YAML 숫자(`1.0`)로 와도 문자열로 정규화(과거: ValidationError).
- skill artifact의 정본 `SKILL.md`는 설치 시 **평면 `~/.rice/skills/<name>.md`** 로 정규화되어 기존 학습 루프 주입·lifecycle 대상이 된다. 함께 든 번들 파일은 `~/.rice/skills/<name>/` 아래 원래 상대경로로 보존되며, 관리 UI와 `skill_resource`가 `SKILL.md`를 루트로 한 하나의 가상 저장소처럼 노출한다.
- plugin 의 `skills/<sub>/SKILL.md`(표준 레이아웃)도 주입 대상에 포함(과거: 평면 `*.md` 만). platform tarball의 최상위 `<artifact_id>/` 폴더는 runtime 설치 시 벗겨 `~/.rice/plugins/plugin:<name>/skills/...` 로 정규화한다.

### 6.4 Plugin 활성화 게이트 (§4.4 실행)
- `PATCH /plugins/{id}/enabled` (enable): `requires.connectors` 가 **모두 연결돼 있지 않으면 422** 로 차단(과거: 무검증 → 도구 실행 시 조용히 실패).
- PermissionChecker 는 설치 manifest(`rice-artifact.json`)를 읽는다(과거: `.yaml` 만 찾아 항상 fail-closed).

### 6.5 Connector(Gmail) 동작 (§4.2)
- OAuth client 자격증명 해석 우선순위: env(`RICE_GMAIL_CLIENT_ID/SECRET`) → `RICE_GMAIL_CLIENT_SECRET_FILE` → `~/.rice/connectors/gmail_client_secret.json`(Google "installed" JSON).
- `redirect_uri` 는 런타임 바인딩 포트로 구성: `http://localhost:{port}/connectors/oauth/callback`. 콜백은 **bearer 토큰 면제**(브라우저가 직접 호출) + 사람이 읽는 HTML 응답.
- Gmail 도구 표면에 **`gmail_send`** 추가(send scope, requires_approval=True). 기존 `gmail_search`/`gmail_read` 유지.

### 6.6 학습 루프 LLM 저작 이식 (2026-07-02, Hermes learning_loop 이식 — 계약 표면 변경분만)

> 상세 설계는 [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md) §4·§5·§8. 여기는 §1.3/§1.2에 반영된 wire 계약 변경만 고정.

- **버그 교정 (계약 변경 없음, 동작 교정)**: `skill_patch_proposed` 승인 시 `edited_body`가 없으면 이전 구현은 제안 본문 대신 `current.body`를 적용해 버전만 증가시킬 수 있었다. `apply_patch`는 이제 `edited_body > proposed_body(제안 원문) > 현재 본문` 순으로 본문을 선택한다. wire 스키마는 변경하지 않고, 서버가 보유한 `PatchProposal.proposed_body`를 내부 적용 경로에 전달하도록 수정했다.
- **`skill_created` 제거**: 2026-07-02에 스킬 생성이 `skill_patch_proposed`(`from_version=0`)로 통일되면서 기존 생성 이벤트가 제거됐다. 2026-07-04에 `plan_updated`가 추가되어 현재 스트림 이벤트는 §1.3의 9종이다.
- **`skill_patch_proposed`/`PatchProposal` 필드 추가**(하위호환 optional): `proposed_body`(서버 원문 — 프론트의 diff 재구성 폴백 제거), `warnings`(시크릿 스크러빙 경고), `description`/`tags`(신규 생성 제안 전용).
- **`PatchTrigger` 열거값 추가**: `T4`(신규 생성). 기존 T1a/T1b/T1c는 의미 불변이나 판단 주체가 정규식→LLM 자기분류로 바뀜(§5.2.2).
- **신규 엔드포인트 5개**: `POST /sessions/{id}/title`(기존에 있었으나 이 문서에 누락돼 있던 것 — 이번에 §1.3에 추가), `POST /sessions/{id}/learning-review`, `POST /sessions/{id}/learn`, `POST /skills/{id}/archive-decision`, `POST /skills/{id}/consolidate-decision`.
- **`POST /skills/curate`에 `mode` 쿼리 파라미터 추가**(하위호환, 기본 `rules`).

### 6.7 메모리 격리(Quarantine) — 신규 반영, 이 문서 최초 기재 (2026-07-08)

- 외부 콘텐츠(connector/plugin/bridge 도구 결과)가 섞인 턴에서 생성된 메모리는 **격리(quarantined) 상태로 저장**되고, 승인 전까지 `learning/memory/search.py`의 조회(recent/FTS)에 걸리지 않는다 — 프롬프트 인젝션이 메모리를 오염시켜 이후 턴에 조용히 재주입되는 경로를 막는다.
- `GET /memories` 응답의 각 항목에 `status`(`active`|`quarantined`) 필드가 실린다.
- 승인은 신규 `POST /memories/{id}/approve`(§1.3) — 격리를 풀고 `active`로 전환한다.
- `memory_written` SSE 이벤트에도 대응 필드가 실린다: `derived_from_external`(bool), `evidence_message_ids`(격리 판정 근거가 된 tool 메시지 id 목록). `skill_patch_proposed`도 같은 두 필드를 공유한다(§1.3 SSE 절 참고).
- 판정 로직 상세는 [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md) 정본.

### 6.8 2026-07-08 문서-소스 드리프트 동기화 — 반영분 요약

이번 동기화에서 §1.3에 새로 반영한 것: Connector 로컬 MCP 5종 + 커스텀 원격 2종 + 도구 permission/resync/update 4종(총 11개 라우트), `/workspace/file`·`/workspace/open`, `DELETE /sessions/{id}`, `/ollama/models`·`/ollama/health`, `/skills/{id}/analysis/refresh`, `POST /memories/{id}/approve`, `GET /learning/events`, `GET /eval/impact`. 정정한 것: `/sessions/{id}/regenerate` 경로 오기, `/sessions/{id}/export`·`/sessions/{id}/attachments`는 미구현으로 표기, Gmail 단일 커넥터 서술, eval 결과 노출 방식(`/eval/impact` 구현 반영). `PUT /settings`에 `agent`·`registered_models` 명기.

**남은 알려진 격차(이번 범위 밖)**: `contracts/runtime.yaml`(OpenAPI)은 이 markdown 계약과 아직 완전히 동기화되지 않았다 — 위 신규 라우트 다수가 spec에 없다. `rice-app`이 이 파일로 코드젠하는 구조(§1.3 상단 "스펙 소유" 원칙)이므로, 이 markdown이 갱신됐다고 코드젠 산출물이 자동으로 맞아지는 것은 아니다. 별도 갱신 필요.
