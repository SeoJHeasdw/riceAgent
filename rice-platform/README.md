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
| GET | `/artifacts/{id}/download` | tar.gz 다운로드 |
| POST | `/artifacts/{id}/sponsor` | Blue Point 후원 (mock) |
| POST | `/artifacts` | artifact 게시 (JSON body) |

### 카탈로그 쿼리 파라미터

| 파라미터 | 설명 | 예시 |
|----------|------|------|
| `type` | `skill` \| `plugin` | `?type=skill` |
| `q` | 이름/설명 검색 | `?q=git` |
| `category` | 카테고리 필터 | `?category=개발` |
| `tags` | 태그 필터 (AND) | `?tags=git&tags=workflow` |
| `sort` | `popular` \| `recent` \| `name` | `?sort=recent` |

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
display_name: "내 스킬"
description: "스킬 설명"
category: "개발"
tags: ["tag1", "tag2"]
author:
  name: "작성자"
  team: "팀명"
requires:
  runtime: ">=0.1"
```
