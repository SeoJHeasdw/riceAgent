# rice-platform

RICE Skill & Plugin Market API 서버 (최소 버전, RICE_PLATFORM_DESIGN.md)

## 구조

```
rice-platform/
├── src/rice_platform/
│   ├── app.py          FastAPI 앱 + 라우터
│   └── registry.py     로컬 디렉토리 기반 artifact 레지스트리
├── artifacts/          예시 artifact 패키지들
│   ├── caveman/        SKILL.md + rice-artifact.yaml
│   ├── git-workflow/
│   ├── code-review/
│   └── data-analysis/
└── pyproject.toml
```

## 실행

```bash
cd rice-platform
pip install -e ".[dev]"      # 또는 uv pip install -e .
rice-platform                # 기본: 0.0.0.0:7070
rice-platform --port 7070 --artifacts-dir ./artifacts
```

환경변수로 artifacts 경로 지정:
```bash
RICE_ARTIFACTS_DIR=/path/to/artifacts rice-platform
```

## API

| Method | Path | 설명 |
|--------|------|------|
| GET | `/health` | 서버 상태 |
| GET | `/catalog?type=skill&q=git&sort=popular` | artifact 목록 |
| GET | `/artifacts/{id}` | 단일 artifact 상세 |
| GET | `/artifacts/{id}/files/{path}` | artifact 내 개별 파일 내용 (SkillFileBrowser lazy load) |
| GET | `/artifacts/{id}/download` | tar.gz 다운로드 (`X-Checksum: sha256-…` 헤더) |
| POST | `/artifacts/{id}/sponsor` | Blue Point 후원 (mock) |
| POST | `/artifacts` | artifact 게시 (JSON body) |

> `/files/{path}`: 텍스트 파일은 `{ path, content, language }`, 바이너리(폰트·이미지 등)는
> `{ path, content: null, language: "binary", binary: true }`. artifact 루트 밖 경로는 403.

### 카탈로그 쿼리 파라미터

| 파라미터 | 설명 | 예시 |
|----------|------|------|
| `type` | `skill` \| `plugin` | `?type=skill` |
| `q` | 이름/설명 검색 | `?q=git` |
| `category` | 카테고리 필터 | `?category=개발` |
| `tags` | 태그 필터 (AND) | `?tags=git&tags=workflow` |
| `sort` | `popular` \| `recent` \| `name` | `?sort=recent` |

### 카탈로그 응답 (파생 필드)

manifest 필드 외에, registry가 디렉토리 구조에서 파생해 함께 내려주는 필드:

| 필드 | 의미 |
|------|------|
| `license` | manifest `license` 또는 `LICENSE.txt` 존재 시 `"See LICENSE.txt"` |
| `skill_files` | 진입점 파일 목록 (skill=`SKILL.md`, plugin=`skills/<name>/SKILL.md`) — 프론트가 `/files/{path}`로 lazy load |
| `bundled_resources` | 번들된 리소스 디렉토리 (`scripts`·`references`·`assets`·`templates`·`agents`·`hooks`·`commands`·`skills` 중 존재하는 것) |
| `has_bundled_resources` | `bundled_resources` 존재 여부 (boolean) |

## Claude 플러그인 마켓플레이스 인제스트

실제 Anthropic 플러그인(예: `claude-for-legal`)은 `rice-artifact.yaml`이 아니라 **Claude Code 플러그인 형식**을 쓴다:
`.claude-plugin/marketplace.json`(플러그인 목록) + 각 플러그인의 `.claude-plugin/plugin.json` + `.mcp.json`(MCP 커넥터).
registry는 이 형식을 **파일 복사·변환 없이 in-place로 직접 읽어** 카탈로그에 노출한다.

- 매핑: `.mcp.json`의 `mcpServers` → `requires.connectors`, `recommendedCategories` → `tags`,
  `marketplace.json`의 `displayName/description` → 카탈로그 필드.
- skill = 단일 `SKILL.md`(선언적 절차)인 반면, **plugin = `skills/` + `agents/` + `hooks/` + MCP 커넥터** 묶음이다.
- 경로 지정: `RICE_PLUGIN_MARKETPLACES`(os.pathsep 구분)로 지정, 없으면 레포 루트의 `claude-for-legal` 자동 감지.

```bash
RICE_PLUGIN_MARKETPLACES=/path/to/claude-for-legal rice-platform
```

## artifact 추가 방법

1. `artifacts/<name>/` 디렉토리 생성
2. `rice-artifact.yaml` 작성 (RICE_CONTRACTS.md §2.1)
3. `SKILL.md` 작성 (skill 타입) 또는 `skills/*.md` 추가 (plugin 타입)
4. 서버 재시작 없이 즉시 반영됨

### rice-artifact.yaml 예시

```yaml
schema_version: 1
type: skill
name: my-skill
version: 1.0.0
display_name: "나의 스킬"
description: "스킬 설명"
category: "개발"
tags: ["tag1", "tag2"]
author:
  name: "작성자"
  team: "팀명"
requires:
  runtime: ">=0.1"
```
