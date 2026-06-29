# RICE Platform Design

> 작성일: 2026-06-18 (개정: 2026-06-26) · 상태: **확정 (Phase 2)**
> 범위: `rice-platform`(원격 마켓 서버)의 설계 — 모듈 구조, catalog, artifact registry, publish, Blue Point, PostgreSQL 스키마
> 관계: API 계약은 [RICE_CONTRACTS.md](RICE_CONTRACTS.md) 3장이 소유, artifact manifest는 [RICE_CONTRACTS.md](RICE_CONTRACTS.md) 2장이 단일 출처. 레포 책임은 [RICE_REPO_STRATEGY.md](RICE_REPO_STRATEGY.md) 5.3.

---

## 1. 개요

`rice-platform`은 **Skill Market + Plugin Market을 하나의 백엔드**로 제공하고 `artifact_type`(skill|plugin)으로 구분한다([RICE_REPO_STRATEGY.md](RICE_REPO_STRATEGY.md) 5.3). 원격 배포물이며 runtime/​app과 **진짜 분리**된다.

- 배포: Docker 컨테이너(FastAPI), 클라우드(AWS/GCP), `https://platform.rice.app`
- 데모 단계 인증 없음(향후 SSO 확장 포인트)
- Phase 1에는 platform 없이 **로컬 mock 카탈로그**(`file://`)로 동작([RICE_CONTRACTS.md](RICE_CONTRACTS.md) 3장)

---

## 2. 모듈 구조

```
rice-platform/                              # 배포물 ③ : 원격 마켓 서버 (Docker)
├── pyproject.toml
├── contracts/
│   └── platform.yaml                       # platform OpenAPI 스펙 (이 레포 소유 → app/runtime 코드젠)
├── src/rice_platform/
│   ├── api/                                # FastAPI 라우트 (/catalog, /artifacts, /sponsor)
│   ├── catalog/                            # 검색·필터·정렬 (artifact_type/category/tags)
│   ├── registry/                           # 메타데이터·버전·체크섬·manifest 저장
│   ├── sponsor/                            # Blue Point (mock)
│   ├── moderation/                         # 게시 검수 (★ 향후)
│   └── db/                                 # PostgreSQL 모델·마이그레이션
└── tests/
```

핵심 불변식은 runtime과 동일 철학(모듈 경계 + 명확한 계약)이나, platform은 상태가 DB에 있어 모듈 결합도가 낮다.

---

## 3. Catalog (검색·필터·정렬)

`GET /catalog?type=&q=&category=&tags=&sort=` ([RICE_CONTRACTS.md](RICE_CONTRACTS.md) 3장)

- **type**: skill | plugin (필수 구분)
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
2. **실제 Claude Code 플러그인** — `.claude-plugin/{marketplace.json, plugin.json}` + `.mcp.json` 형식 (예: `claude-for-legal`). `RICE_PLUGIN_MARKETPLACES` 또는 레포 루트 자동 감지로 **in-place 인제스트**(복사·변환 없음). `.mcp.json`의 `mcpServers`가 `requires.connectors`로 매핑된다.

> ★ skill ≠ plugin: skill은 단일 `SKILL.md`(선언적). plugin은 `skills/` + `agents/` + `hooks/` + MCP 커넥터 묶음 — sandbox 경계와 Connector 권한은 plugin에만 해당(CONTRACTS §2.2).

### 4.1 버전 관리

- artifact는 **다중 버전 보관**(semver). 설치 시 특정 버전 다운로드, `requires.runtime` 호환 검사([RICE_REPO_STRATEGY.md](RICE_REPO_STRATEGY.md) 4.4).
- 각 버전: manifest + 패키지(`artifact.tar.gz`) + `checksum`(sha256) 저장. 다운로드 무결성은 runtime이 검증(CONTRACTS 2.3).
- 버전 정책: **최신 + 과거 N개 보관 + yank(철회)**

---

## 5. Publish (게시)

`POST /artifacts` (등록) — SkillMarket의 "스킬 게시"(PRODUCT_IDEA 4.8 로드맵 화면)와 연결.

```
작성자(app) → POST /artifacts (manifest + 패키지)
 → registry: manifest 검증(schema_version, requires) + 체크섬 계산
 → (★ 향후) moderation 큐 → 승인 시 catalog 노출
 → catalog 등재 (type/category/tags 색인)
```

- 게시 검수: **데모는 자동 승인** (제품화 시 조직 승인 워크플로우 — REPO_STRATEGY 거버넌스 확장 포인트)

---

## 6. Blue Point (sponsor, mock)

- `POST /artifacts/{id}/sponsor` — 유용한 스킬에 포인트 후원(데모 mock). 실제 정산 없음.
- 집계는 `sponsors` 테이블, catalog `sort=popular`에 가중 반영 가능(★ 향후).

---

## 7. PostgreSQL 스키마 (초안)

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
