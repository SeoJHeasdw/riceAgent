# RICE Platform Design

> 작성일: 2026-06-18 (개정: 2026-06-26, **2026-07-08 — 소스 드리프트 동기화: 목표 설계와 현 MVP 구현 범위 구분**) · 상태: **확정 (Phase 2, 목표 설계 — 실 구현은 MVP 단계)**
> 범위: `rice-platform`(원격 마켓 서버)의 설계 — 모듈 구조, catalog, artifact registry, publish, Point, PostgreSQL 스키마
> 관계: API 계약은 [RICE_CONTRACTS.md](RICE_CONTRACTS.md) 3장이 소유, artifact manifest는 [RICE_CONTRACTS.md](RICE_CONTRACTS.md) 2장이 단일 출처. 레포 책임은 [RICE_REPO_STRATEGY.md](RICE_REPO_STRATEGY.md) 5.3.

---

## 1. 개요

`rice-platform`은 **Skill Market + Plugin Market을 하나의 백엔드**로 제공하고 `artifact_type`(skill|plugin)으로 구분한다([RICE_REPO_STRATEGY.md](RICE_REPO_STRATEGY.md) 5.3). runtime/app과 분리된 원격 배포물이다. PoC에서는 sibling `rice-marketplace` repo를 content source로 두고, platform은 이를 읽어 catalog/download API로 서빙한다.

- 배포(목표, 미착수): Docker 컨테이너(FastAPI), 클라우드(AWS/GCP), `https://platform.rice.app` — Dockerfile 등 실물은 아직 없음(2026-07-08 확인)
- 데모 단계 인증 없음(향후 SSO 확장 포인트)
- Phase 1에는 platform 없이 **로컬 mock 카탈로그**(`file://`)로 동작([RICE_CONTRACTS.md](RICE_CONTRACTS.md) 3장)
- Phase 2 PoC 실행: `RICE_ARTIFACTS_DIR=../rice-marketplace/artifacts`, `RICE_PLUGIN_MARKETPLACES=../rice-marketplace`

### 1.1 앱 ↔ platform 생명주기 (자동 기동 안 함 — 현행 유지, 2026-07-24)

Electron main은 **`rice-runtime`(:8080)만 자식 프로세스로 spawn**하고([RuntimeManager](rice-app/electron/runtime.ts)), `rice-platform`(:7070)은 **자동 기동하지 않는다**. 즉 platform은 개발 래퍼(`app.sh`/`dev.sh`)나 수동으로 별도 실행할 때만 뜬다.

따라서 **마켓 관련 전 기능(카탈로그 조회·설치·게시/공유)은 platform이 떠 있을 때만 동작**한다. 특히 app 게시(publish/share)는 이제 인메모리가 아니라 platform artifact + runtime 스킬로 **실제 영속**하므로, platform 오프라인이면 (조용히 성공한 척하지 않고) **명시적으로 실패**한다 — 설계상 의도된 동작이다.

**결정**: 아직 실제 marketplace 배포 전(sibling `rice-marketplace` PoC 단계)이라 platform 자동 기동은 **하지 않고 현행을 유지**한다. 실 배포/상시 마켓이 생기는 시점에, `RuntimeManager`에 준하는 platform 생명주기(기동·헬스체크·종료)를 앱에 추가할지 재검토한다.

---

## 2. 모듈 구조

아래는 Phase 2 목표 구조와 현재 구현 범위를 함께 표시한 것이다. **현재(2026-07-08 확인)는 서브패키지 분리가 없고**, `contracts/platform.yaml`도 아직 존재하지 않는다:

```
rice-platform/                              # 배포물 ③ : 원격 마켓 서버 (배포 형태는 목표 — 위 §1 참고)
├── pyproject.toml                          # 의존성: fastapi/uvicorn/pyyaml뿐 — DB 드라이버 없음
└── src/rice_platform/                      # ★ 실제로는 flat 4파일
    ├── __init__.py
    ├── __main__.py                         #   :7070 기본 포트
    ├── app.py                              #   FastAPI 라우트 전부(/catalog, /artifacts, /sponsor 등) — api/ 서브패키지 없음
    └── registry.py                         #   카탈로그 스캔·검색·publish·다운로드 패키징 전부 — catalog/·db/ 서브패키지 없음.
                                             #   자체 docstring: "MVP: PostgreSQL 대신 로컬 디렉토리를 registry로 사용"
```

미착수: `api/`·`catalog/`·`sponsor/`·`db/` 서브패키지 분리, `contracts/platform.yaml`(OpenAPI 스펙 파일), `moderation/`.

위 구조가 서술하는 모듈 경계는 **아직 실현되지 않았다** — 현재는 `app.py`(라우트)+`registry.py`(그 외 전부) 2파일 체제로, `rice-runtime`이 지키는 모듈 경계·DI 원칙([RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md) 5장)이 이 레포엔 아직 적용되지 않는다. 트래픽·기능이 늘면 위 구조로 분리해야 한다는 **의도**는 여전히 유효한 목표다.

---

## 3. Catalog (검색·필터·정렬)

`GET /catalog?type=&q=&category=&tags=&sort=` ([RICE_CONTRACTS.md](RICE_CONTRACTS.md) 3장)

- **type**: skill | plugin (**선택** — 생략하면 skill+plugin이 섞여서 반환된다. "필수 구분"은 원래 설계였으나 실제 쿼리 파라미터는 optional, 2026-07-08 확인)
- **q**: 전문 검색 (name/display_name/description)
- **category / tags**: manifest의 `category`·`tags`(CONTRACTS 2.1) 기반 필터 — SkillMarket/PluginMarket UI의 카테고리·태그 필터
- **sort**: `popular`(다운로드수) | `recent`(게시일) | `name`
- ★ 인덱스: `(type, category)`, GIN(tags), `tsvector`(q)

### 3.1 카탈로그 응답 (파생 필드)

manifest(CONTRACTS 2.1) 필드에 더해, registry가 artifact 디렉토리 구조에서 파생해 함께 노출하는 필드 — Anthropic skills 구조(SKILL.md + scripts/references/assets) 반영:

- `license`: manifest `license` 또는 `LICENSE.txt` 존재 시 도출
- `skill_files`: 진입점 파일 목록(skill=`SKILL.md`, plugin=`skills/*.md`) — app이 파일 내용을 lazy load 할 때 사용
- `bundled_resources` / `has_bundled_resources`: 번들 리소스 디렉토리(`scripts`·`references`·`assets`·`templates`) 존재 여부
- `description`: **LLM의 스킬 트리거 결정에 쓰이는 1차 메커니즘**이므로 트리거 조건을 포함해 완전하게 노출(요약·절단 금지)
- `connectors`: plugin의 `.mcp.json`에서 파싱한 커넥터 목록(title/description 포함) — plugin ingestion 전용(2026-07-08 문서 반영)
- `plugin_skills`: plugin이 묶고 있는 각 `skills/*/SKILL.md`의 frontmatter 메타(2026-07-08 문서 반영)
- `examples`: 마크다운 본문에서 정규식 휴리스틱으로 채굴한 프롬프트 예시, 중복 제거(2026-07-08 문서 반영)

### 3.2 파일 내용 조회

`GET /artifacts/{id}/files/{path}` — artifact 내 개별 파일 내용. app의 SkillFileBrowser가 `skill_files`를 받아 파일별로 lazy load 한다.

- 텍스트: `{ path, content, language }` (language는 확장자에서 추론)
- 바이너리(폰트·이미지): `{ path, content: null, language: "binary", binary: true }`
- 보안: artifact 루트 밖으로 나가는 경로는 403 (resolve 후 루트 포함 검사)

---

## 4. Artifact Registry & 버전 관리

### 4.0 다중 소스 (rice artifact + Claude 플러그인)

registry는 두 종류의 소스를 함께 카탈로그로 노출한다 (artifact id = 디렉토리명):

1. **rice 네이티브 artifact** — `artifacts/<name>/rice-artifact.yaml` (skill 중심)
2. **실제 Claude Code 플러그인** — `.claude-plugin/{marketplace.json, plugin.json}` + `.mcp.json` 형식. `RICE_PLUGIN_MARKETPLACES`로 지정한 marketplace root를 **in-place 인제스트**(복사·변환 없음)한다. `.mcp.json`의 `mcpServers`가 `requires.connectors`로 매핑된다. Upstream marketplace에 섞인 remote source object는 platform이 직접 fetch하지 않고 skip한다. fetch·검수·pinning은 `rice-marketplace`가 담당한다.

> ★ skill ≠ plugin: skill은 단일 `SKILL.md`(선언적). plugin은 `skills/` + `agents/` + `hooks/` + MCP 커넥터 묶음 — sandbox 경계와 Connector 권한은 plugin에만 해당(CONTRACTS §2.2).

### 4.0.1 실행 중 소스 연결

로컬 개발 스크립트(`app.sh`, `dev.sh`, `rice-platform/run.sh`)는 sibling `../rice-marketplace`를 자동 감지한다.

```
rice-marketplace/
├── artifacts/                 # Rice-native skill/plugin artifacts
├── .claude-plugin/marketplace.json
└── sources/                   # upstream submodules
```

감지되면 platform은 다음 값을 사용한다.

```bash
RICE_ARTIFACTS_DIR=../rice-marketplace/artifacts
RICE_PLUGIN_MARKETPLACES=../rice-marketplace
```

없으면 기존처럼 `rice-platform/artifacts` seed만 사용한다.

**주의(2026-07-08 확인)**: 위 동작은 `app.sh`/`dev.sh`/`run.sh` 같은 **래퍼 스크립트가 설정하는 env**를 전제로 한다. `rice-platform`(또는 `python -m rice_platform`)을 래퍼 없이 직접 실행하면, `registry.py::_marketplace_dirs()`의 자체 폴백은 `../rice-marketplace`가 아니라 레포 내부의 `claude-for-legal`이라는 현재 존재하지 않는 디렉토리다(marketplace 분리 이전 경로로 추정). 이 경로에서는 plugin ingestion이 실행되지 않는다. 문서가 서술하는 자동 감지는 래퍼 스크립트 기준이다.

### 4.1 버전 관리 — 설계, 미구현(2026-07-08 확인)

아래는 여전히 목표 설계이지 현재 동작이 아니다:
- artifact는 **다중 버전 보관**(semver) 목표. 설치 시 특정 버전 다운로드, `requires.runtime` 호환 검사([RICE_REPO_STRATEGY.md](RICE_REPO_STRATEGY.md) 4.4)도 목표.
- 각 버전: manifest + 패키지(`artifact.tar.gz`) + `checksum`(sha256) 저장 — 목표.
- 버전 정책: **최신 + 과거 N개 보관 + yank(철회)** — 목표.

**실제**: `artifacts/<name>/` 디렉토리 하나가 그 artifact의 현재 상태 전부다 — `version`은 manifest 필드를 문자열로 그대로 통과시킬 뿐(`registry.py::_to_catalog_entry`), 저장소가 여러 버전을 따로 갖고 있지 않는다. 다운로드 패키지(`artifact.tar.gz`)도 디스크에 없다 — 요청마다 `build_download_package()`가 즉석으로 만든다. 버전 선택 엔드포인트도 `yank`도 코드에 없다. 다운로드 무결성 검증(runtime 쪽, CONTRACTS 2.3, `X-Checksum` 헤더)만은 **이미 동작**한다 — 이건 버전 저장과 무관하게 매 다운로드마다 즉석 계산되기 때문이다.

---

## 5. Publish (게시)

`POST /artifacts` (등록) — SkillMarket의 "스킬 게시"(PRODUCT_IDEA 4.8. 2026-07-08 확인: 이 화면의 **UI는 이제 존재**하지만 실제로 이 엔드포인트를 호출하진 않는다 — RUNTIME_DESIGN §11 참고)와 연결.

```
작성자(app) → POST /artifacts (manifest + 파일 목록)
 → registry: manifest 검증(현재는 name 비어있지 않음 + type∈{skill,plugin}뿐 —
   schema_version·requires는 미검사, 2026-07-08 확인)
 → (★ 향후) moderation 큐 → 승인 시 catalog 노출 [미구현]
 → catalog 등재 (type/category/tags 색인)
```

- **실제 요청 형식(2026-07-08 확인)**: 위 다이어그램은 "manifest + 패키지(바이너리 tar.gz)"처럼 읽히지만, 실제 엔드포인트는 JSON 본문에 `files: [{path, content}]`(**텍스트만**)를 받는다 — 바이너리 업로드(tar.gz, multipart)는 함수 자체 docstring이 "추후 multipart로 확장" 예정이라 명시한 미구현 상태다.
- **체크섬은 게시 시점에 계산되지 않는다** — 위 다이어그램의 "체크섬 계산" 단계는 실제로 없다(체크섬은 다운로드 응답의 `X-Checksum` 헤더 계산 시에만 등장, §4.1 참고).
- 게시 검수: **데모는 자동 승인** (제품화 시 조직 승인 워크플로우 — REPO_STRATEGY 거버넌스 확장 포인트)

---

## 6. Point (sponsor, mock)

- `POST /artifacts/{id}/sponsor` — 유용한 스킬에 포인트 후원(데모 mock). 실제 정산 없음.
- **집계는 in-process 딕셔너리다**(2026-07-08 확인 — `app.py`의 `_sponsor_totals: dict[str, int]`, 코드 주석 그대로 "메모리 내 포인트 집계, 재시작 시 초기화") — `sponsors` 테이블은 §7과 마찬가지로 목표 설계일 뿐 존재하지 않는다. catalog `sort=popular`에 가중 반영은 여전히 향후 항목.

---

## 7. PostgreSQL 스키마 (초안 — 미구현, 2026-07-08 확인)

현재 구현에는 DB 계층이 없다. `pyproject.toml` 의존성에 `psycopg`/`sqlalchemy`/`asyncpg` 등 DB 드라이버가 없고, `registry.py`는 매 요청마다 `artifacts_dir.iterdir()`로 파일시스템을 스캔한다(자체 docstring: "MVP: PostgreSQL 대신 로컬 디렉토리를 registry로 사용"). 이 절은 **Phase 2 이후의 목표 스키마**로 읽어야 하며, 아래 테이블(`sponsors` 포함)은 현재 런타임에 생성되지 않는다.

```sql
artifacts(
  id, type, name, display_name, description,
  category, latest_version, author_name, author_team,
  created_at, download_count
)                                  -- name+type 유니크

artifact_versions(
  id, artifact_id, version, manifest_json, checksum,
  package_url, requires_runtime, yanked, published_at,
  FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
)

artifact_tags(artifact_id, tag)    -- GIN 인덱스 / 다대다

sponsors(id, artifact_id, points, sponsor_user, created_at)

downloads(id, artifact_id, version, installed_at)   -- popular 정렬·통계
```

- 스키마는 manifest_json을 CONTRACTS 2.1과 정렬해 유지한다.
