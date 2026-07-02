# RICE Contracts (레포 간 계약)

> 작성일: 2026-06-12 (개정: 2026-06-26) · 상태: **확정 (결정 완료)**
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
- **timeout**: 기본 5분(설정) 무응답 시 **안전 기본값=거부/취소**(tool 미실행 / patch 미적용).
- **방치/중복**: 세션 `abort`·`shutdown` 시 대기 승인 일괄 취소. 이미 처리/만료된 `approval_id`로 늦게·중복 도착한 POST → **409/410**(idempotent no-op).
- **SSE 끊김·재연결**: 승인 상태는 SSE 연결과 독립(서버 레지스트리). 재연결 시 **`GET /sessions/{id}/pending-approvals`**로 대기 중 프롬프트(`{approval_id, kind, payload}`)를 복구해 카드 재렌더.

### 1.3 엔드포인트 초안

| Method | Path | 용도 |
|--------|------|------|
| GET | `/health` | 기동 확인 |
| POST | `/shutdown` | graceful 종료 |
| POST | `/sessions` | 세션 생성 |
| GET | `/sessions` / `/sessions/{id}` | 목록/상세 (히스토리 UI) |
| POST | `/sessions/{id}/attachments` | **첨부 선업로드 (multipart, xlsx·png만) → `{attachment_id, kind}` 반환** |
| POST | `/sessions/{id}/messages` | 메시지 전송(본문 + `attachment_ids[]` 참조) → **SSE 스트림 응답** (8종 이벤트, 2026-07-02 정정 — 아래) |
| POST | `/sessions/{id}/title` | 세션 제목 생성(post-turn, 논블로킹) — 첫 턴 `done` 후 호출 |
| **POST** | **`/sessions/{id}/learning-review`** | **게이트된 LLM 스킬/메모리 리뷰(post-turn, 논블로킹) → SSE 스트림(메인 턴과 동일 이벤트 어휘) — LEARNING_LOOP §4. body: `{tool_calls_made}`. 게이트 미통과 시 즉시 `done`만.** |
| **POST** | **`/sessions/{id}/learn`** | **명시적 "이 대화를 스킬로 저장" — 게이트 없이 항상 시도 → SSE 스트림 — LEARNING_LOOP §5.3. body: `{instruction?}`.** |
| POST | `/sessions/{id}/abort` | 생성 중단 |
| POST | `/sessions/{id}/tool-approval` | tool 실행 승인 응답 (`tool_approval_required` 이벤트에 대응, `approval_id` 포함) |
| GET | `/sessions/{id}/pending-approvals` | 대기 중 승인 목록 (SSE 재연결 복구 — RUNTIME_DESIGN 5.1) |
| GET | `/sessions/{id}/messages?cursor=` | 과거 메시지 페이지네이션 (히스토리 로드) |
| POST | `/sessions/{id}/messages/{msg_id}/regenerate` | 응답 재생성 (ChatScreen) |
| GET | `/sessions/{id}/export?format=md\|json` | 대화 내보내기 (ChatScreen) |
| GET | `/skills` | 스킬 목록 + 상태/사용 통계 (learning loop 가시화용) |
| GET | `/skills/{id}` | 스킬 상세 (내용 · version · tags · usage — MySkillsScreen) |
| POST | `/skills/curate` | 수동 "스킬 정리" 트리거. **`?mode=rules\|consolidate`**(기본 `rules`, 하위호환) — `consolidate`는 규칙 전이 + LLM 통합 제안(있으면) 반환, LEARNING_LOOP §5.1 |
| PATCH | `/skills/{id}/state` | 스킬 상태 전환 (archive, activate) |
| **POST** | **`/skills/{id}/patch`** | **스킬 개선(patch) *또는* 신규 생성(from_version=0) 제안 응답 (approve/reject/edited_body) — LEARNING_LOOP 5.2·§4** |
| **POST** | **`/skills/{id}/archive-decision`** | **curator 통합의 정리(prune) 제안 응답 (approved, absorbed_into?) — approval_id 파킹 없음, LEARNING_LOOP §5.1** |
| **POST** | **`/skills/{id}/consolidate-decision`** | **curator 통합의 병합(umbrella patch/create) 제안 응답 (approved, proposed_body?, description?, tags?) — approval_id 파킹 없음, LEARNING_LOOP §5.1** |
| **GET** | **`/skills/{id}/history`** | **스킬 버전 히스토리 (diff·롤백 UI)** |
| **POST** | **`/skills/{id}/rollback`** | **이전 버전 복구 (선택)** |
| GET | `/plugins` | 설치된 플러그인 목록 |
| PATCH | `/plugins/{id}/enabled` | 플러그인 활성화/비활성화 |
| DELETE | `/plugins/{id}` | 플러그인 삭제 |
| **GET** | **`/connectors`** | **연결된 Connector 목록** |
| **GET** | **`/connectors/{id}`** | **Connector 상세 (ConnectorsScreen `/connectors/:id` 라우트)** |
| **POST** | **`/connectors`** | **새 Connector 연결 (OAuth 시작)** |
| **DELETE** | **`/connectors/{id}`** | **Connector 삭제** |
| **GET** | **`/connectors/{id}/status`** | **연결 상태 확인** |
| **GET** | **`/connectors/oauth/callback`** | **OAuth callback 처리** |
| GET | `/memories` | 메모리 목록 (개인화 가시화용) |
| GET | `/models` | 사용 가능 모델 목록 |
| GET | `/settings` | 설정 조회 (모델·언어·테마·글꼴) |
| PUT | `/settings` | 설정 갱신 (provider/model/temperature, api_key→키체인, language, theme, **font**) |
| POST | `/artifacts/install` | platform에서 받은 artifact 설치 (2장 manifest 전달) |
| DELETE | `/artifacts/{id}` | 제거 |

**SSE 이벤트 8종** (Learning Loop 가시화, **2026-07-02: `skill_created` 제거** — 아래 참고):
1. `thinking` - LLM 사고 과정 스트리밍
2. `tool_call` - Tool 호출 시작 (tool_name, arguments)
3. `tool_result` - Tool 실행 결과
4. `message` - 최종 응답 메시지 (delta 또는 complete)
5. `memory_written` - 메모리 저장됨 (memory_type, content)
6. `skill_patched` - 스킬 개선 **또는 신규 생성** 적용됨 (skill_id, version) — LEARNING_LOOP 5.2·§4. `version === 1`이면 신규 생성이었다는 뜻 — 클라이언트는 로컬 목록에 해당 skill_id가 없으면 갱신이 아니라 **추가**해야 한다.
7. `done` - 응답 완료
8. `error` - 오류 발생 (error_code, message)

> ~~`skill_created`(skill_id, title, version)~~ — **2026-07-02 제거.** 스킬 생성이 HITL 승인 없이 즉시 저장+사후 통지되던 경로였는데, LEARNING_LOOP §4가 요구하는 "스킬은 항상 사용자 확인 후 저장"과 어긋나는 채로 방치돼 있었다(설계 문서와 구현의 불일치). 생성을 `skill_patch_proposed`(`from_version=0`)로 통일해 패치와 동일한 승인 게이트를 거치게 하면서 이 이벤트가 도달 불가능한 죽은 코드가 됐다 — 삭제. `POST /sessions/{id}/learning-review`·`/learn`(아래)도 이 8종과 동일 이벤트 어휘만 방출한다.
>
> 제어 이벤트(별도, 각자 고유 `approval_id` 포함 — 정지/재개·timeout·재연결 처리는 1.2, 메커니즘은 RUNTIME_DESIGN 5.1): `tool_approval_required`(approval_id, tool_name, arguments) — 위험 tool 실행 전 발행 → 앱이 `POST /sessions/{id}/tool-approval`(approval_id 포함)로 응답. `skill_patch_proposed`(approval_id, skill_id, changelog, diff, trigger, from_version?, to_version?, **proposed_body?**, **warnings?**) — 스킬 개선/생성 제안 발행 → 앱이 diff 카드 표시 후 `POST /skills/{id}/patch`(approval_id 포함)로 응답 (LEARNING_LOOP 5.2). **`proposed_body`/`warnings`는 2026-07-02 추가**: 이전엔 제안의 실제 전체 본문이 SSE payload에 실리지 않아 프론트가 `diff.lines`를 필터링해 재구성했는데(edge case에서 미묘하게 어긋날 위험), 이제 서버가 준 원문을 그대로 쓸 수 있다. `warnings`는 §8 가드(시크릿 스크러빙)가 뭔가 마스킹했을 때의 경고 문자열 배열.
>
> **curator 통합(`/skills/curate?mode=consolidate`, LEARNING_LOOP §5.1)이 낳는 제안은 이 SSE 메커니즘을 쓰지 않는다** — 스트림이 아닌 일반 REST 응답에 그대로 실려온다(`{transitioned, proposals}`, 각 proposal은 `{"kind":"patch",...}` 또는 `{"kind":"archive",...}`). 승인은 `kind:"patch"`면 `/skills/{id}/consolidate-decision`, `kind:"archive"`면 `/skills/{id}/archive-decision`으로 — 둘 다 `approval_id` 불필요(제안이 애초에 파킹된 적이 없다).

- ✅ 스펙 소유: `rice-runtime`이 `contracts/runtime.yaml`(runtime OpenAPI 스펙)을 소유, `rice-app`은 코드젠 (REPO_STRATEGY 4.3, RUNTIME_DIRECTORY 1장)

**Learning Impact(eval) 결과 노출**: MVP는 엔드포인트 없이 `eval/results/latest.json`을 **정적 fixture**로 노출하고 앱이 읽는다(스키마 정본 [RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md) 6.3). 향후 `GET /eval/runs`·`/eval/runs/{id}` 추가는 선택.

**엔드포인트**: 위 초안을 정본으로 하며, `contracts/runtime.yaml`(OpenAPI)로 상세화한다.

**첨부/멀티모달 입력**: **MVP 포함, 대상은 xlsx·png 2종만**(벤치마크: Claude cowork의 파일 첨부). 방식은 **선업로드 후 참조**: 앱이 `POST /sessions/{id}/attachments`(multipart)로 먼저 올려 `attachment_id`를 받고, `POST /sessions/{id}/messages`는 `attachment_ids[]`로 참조한다. 이는 SSE 스트림 응답과 multipart 본문이 한 요청에서 충돌하는 것을 피하기 위함이다. 런타임은 xlsx·png 외 MIME은 **거부**(415)한다. 음성 입력(STT)은 **이번 개발 제외**(향후 Web Speech 또는 로컬 STT).

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
  # skill:  선언적 절차 (SKILL.md — 실행 코드 없음, sandbox 불필요)
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
└── SKILL.md                         └── skills/                 # SKILL.md 묶음 (RUNTIME_DESIGN 문서 4장과 동일 형식)
   (선언적 절차 — 실행 코드 없음)         └── */SKILL.md           #   + manifest의 requires.connectors로 외부 접근 선언
```

핵심 설계:
- ★ **skill = 선언적 절차(SKILL.md)** — 실행 코드가 없어 sandbox 경계(RUNTIME_DESIGN 문서 5장) 대상이 아님. 가장 안전하며 Skill Market의 기본 단위.
- ★ **plugin = skills 묶음 + 요구 Connector(들)** — "Plugin은 머리에 추가되는 전문성"(PRODUCT_IDEA)과 일치. 외부 서비스 접근·원자적 실행 도구가 필요하면 `requires.connectors`로 선언하고, 해당 실행/접근은 **Connector(MCP) 레이어**가 담당한다.

→ 마켓 artifact(skill·plugin) 자체는 **선언적**이므로 sandbox를 우회한다. 실제 코드 실행/외부 접근의 sandbox 경계(RUNTIME_DESIGN 5장)는 **Connector(MCP 서버) 및 plugin이 포함한 connector 코드**에 적용된다.

원자적 실행 도구·외부 접근은 **Connector(MCP) 레이어**로 일원화한다. 자체 MCP 어댑터 구현 수준은 Phase 2 — 근거:
1. ★ Phase 1은 프로토타입 Connector(Gmail OAuth)만; MCP 서버 일반 어댑터는 Phase 2 확장 포인트로 명시
2. 처음부터 광범위한 MCP 서버 형식 채택 — 생태계 호환 ↑, 그러나 데모 복잡도와 패키징(Node/uv 의존) 부담 ↑

**manifest/레이아웃**: 마켓 패키지의 실체는 `rice-artifact.yaml`(schema_version·type·name·version·requires.connectors·checksum 등) + skill은 `SKILL.md` 단일 파일, plugin은 `skills/` 묶음으로 고정한다. 이 스키마가 runtime·platform·app 3곳의 단일 출처다(5장).

### 2.3 설치 흐름

```
앱(마켓 화면) → platform: artifact 다운로드
 → 앱 → runtime: POST /artifacts/install (파일 경로 + manifest)
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

**정의**: Plugin이 외부 서비스에 접근하기 위한 인증된 연결 (프로토타입은 **Gmail 단일 커넥터만** 진지하게 구현 — RUNTIME_DESIGN 9장)

**구성 요소**:
- Connector ID (예: `gmail` — 프로토타입)
- OAuth 2.0 기반 인증 (Authorization Code Flow)
- 요구 scope 목록 (connector_type별) — `GET /connectors/{id}`가 부여된 scope를 노출 (ConnectorsScreen)
- Credential 저장 (OS 키체인)
- Permission 검증 (Plugin → Connector 접근 권한)

### 4.2 OAuth 흐름

```
1. 사용자가 ConnectorsScreen에서 "Gmail 연결" 클릭
2. POST /connectors { connector_type: "gmail" }
3. Runtime이 OAuth URL 생성 → 앱이 브라우저 열기
4. 사용자 인증 후 redirect → GET /connectors/oauth/callback?code=...
5. Runtime이 access_token 획득 → OS 키체인 저장
6. Connector 상태 "connected"로 업데이트
```

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
- `DELETE /skills/{id}` → 본문·usage·history 사이드카 모두 삭제.

### 6.2 HITL 승인 응답 코드 (§1.2 준수)
- `POST /sessions/{id}/tool-approval`, `POST /skills/{id}/patch`: 이미 처리/만료된 `approval_id` → **409 Conflict**(과거: 항상 200). `resolve()`가 처리 여부(bool)를 반환한다.
- `POST /skills/{id}/patch`: URL `skill_id` 와 `approval_id` 의 대상 스킬이 **일치해야** 함(불일치 422).

### 6.3 Artifact 설치 무결성 (§2.3)
- `POST /artifacts/install` 에 선택 필드 **`checksum`** 추가 — 앱이 platform download 응답의 `X-Checksum` 헤더 값을 그대로 전달하면 manifest.checksum 보다 우선해 검증한다(빈 값이면 dev 모드).
- manifest `version` 은 YAML 숫자(`1.0`)로 와도 문자열로 정규화(과거: ValidationError).
- skill artifact 는 설치 시 **평면 `~/.rice/skills/<name>.md`** 로 배치되어 학습 루프 주입·lifecycle 대상이 된다.
- plugin 의 `skills/<sub>/SKILL.md`(표준 레이아웃)도 주입 대상에 포함(과거: 평면 `*.md` 만).

### 6.4 Plugin 활성화 게이트 (§4.4 실행)
- `PATCH /plugins/{id}/enabled` (enable): `requires.connectors` 가 **모두 연결돼 있지 않으면 422** 로 차단(과거: 무검증 → 도구 실행 시 조용히 실패).
- PermissionChecker 는 설치 manifest(`rice-artifact.json`)를 읽는다(과거: `.yaml` 만 찾아 항상 fail-closed).

### 6.5 Connector(Gmail) 동작 (§4.2)
- OAuth client 자격증명 해석 우선순위: env(`RICE_GMAIL_CLIENT_ID/SECRET`) → `RICE_GMAIL_CLIENT_SECRET_FILE` → `~/.rice/connectors/gmail_client_secret.json`(Google "installed" JSON).
- `redirect_uri` 는 런타임 바인딩 포트로 구성: `http://localhost:{port}/connectors/oauth/callback`. 콜백은 **bearer 토큰 면제**(브라우저가 직접 호출) + 사람이 읽는 HTML 응답.
- Gmail 도구 표면에 **`gmail_send`** 추가(send scope, requires_approval=True). 기존 `gmail_search`/`gmail_read` 유지.

### 6.6 학습 루프 LLM 저작 이식 (2026-07-02, Hermes learning_loop 이식 — 계약 표면 변경분만)

> 상세 설계는 [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md) §4·§5·§8. 여기는 §1.3/§1.2에 반영된 wire 계약 변경만 고정.

- **버그 교정 (계약 변경 없음, 동작 교정)**: `skill_patch_proposed` 승인 시 `edited_body` 없이(=사용자가 diff만 보고 그냥 "승인") 그냥 승인하면 이전 구현은 조용히 `current.body`로 되돌아가 — 버전만 올라가고 제안 본문이 실제로 반영되지 않았다. `apply_patch`가 이제 `edited_body > proposed_body(제안 원문) > 현재 본문` 순으로 우선한다. wire 스키마는 안 바뀜(서버가 이미 갖고 있던 `PatchProposal.proposed_body`를 내부적으로 전달하도록 고쳤을 뿐).
- **SSE 이벤트 9종 → 8종**: `skill_created` 제거(§1.3 표 참고). 스킬 생성이 `skill_patch_proposed`(`from_version=0`)로 통일되며 도달 불가능해짐 — HITL 우회 문제(설계 문서는 항상 "확인 후 저장"이라 했는데 구현은 즉시 저장이었음)도 같이 닫힘.
- **`skill_patch_proposed`/`PatchProposal` 필드 추가**(하위호환 optional): `proposed_body`(서버 원문 — 프론트의 diff 재구성 폴백 제거), `warnings`(시크릿 스크러빙 경고), `description`/`tags`(신규 생성 제안 전용).
- **`PatchTrigger` 열거값 추가**: `T4`(신규 생성). 기존 T1a/T1b/T1c는 의미 불변이나 판단 주체가 정규식→LLM 자기분류로 바뀜(§5.2.2).
- **신규 엔드포인트 5개**: `POST /sessions/{id}/title`(기존에 있었으나 이 문서에 누락돼 있던 것 — 이번에 §1.3에 추가), `POST /sessions/{id}/learning-review`, `POST /sessions/{id}/learn`, `POST /skills/{id}/archive-decision`, `POST /skills/{id}/consolidate-decision`.
- **`POST /skills/curate`에 `mode` 쿼리 파라미터 추가**(하위호환, 기본 `rules`).
