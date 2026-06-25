---
name: run-tests
version: 1
description: 프로젝트 테스트 실행 절차 (pytest)
state: active
tags: [pytest, testing, ci]
source: eval:warm-seed
created_at: 2024-01-01T00:00:00Z
updated_at: 2024-06-01T00:00:00Z
---

## 테스트 실행 절차

### Steps
1. `cd rice-runtime`
2. `.venv/bin/python -m pytest tests/ -q` — 전체 테스트 빠른 실행
3. 실패 시 `-v` 옵션으로 상세 출력: `.venv/bin/python -m pytest tests/ -v`
4. 특정 테스트만: `.venv/bin/python -m pytest tests/test_XXX.py -q`

### 주요 주의사항
- asyncio_mode = "auto" 설정 확인 (pyproject.toml)
- 테스트 격리: `tempfile.mkdtemp()` 로 임시 경로 사용
