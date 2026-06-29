---
name: git-workflow
version: 1
description: "체계적인 Git 워크플로우: 브랜치 전략, 커밋, PR, 충돌 해결"
tags: [git, workflow, vcs]
---

Git 작업 시 아래 절차를 따릅니다.

## /git-commit — 커밋 메시지 작성

1. `git diff --staged` 또는 변경 내용 확인
2. Conventional Commits 형식으로 작성: `<type>(<scope>): <subject>`
   - type: feat / fix / docs / style / refactor / test / chore
   - subject: 50자 이하, 현재형, 마침표 없음
3. 본문 필요 시 72자 줄바꿈, 무엇보다 **왜** 변경했는지 기술

예시:
```
feat(auth): add JWT refresh token rotation

Prevents token theft by invalidating old tokens on each refresh.
Closes #142
```

## /git-pr — PR 설명 작성

1. 제목: `<type>: <what changed>` (50자 이하)
2. 본문 구조:
   - **What**: 변경 내용 한 줄 요약
   - **Why**: 비즈니스/기술적 이유
   - **How**: 주요 구현 방법
   - **Test**: 검증 방법
3. 관련 이슈 링크: `Closes #<number>`

## /git-conflict — 충돌 해결

1. 충돌 파일 목록: `git status`
2. 각 파일에서 `<<<<<<<`, `=======`, `>>>>>>>` 마커 찾기
3. 두 변경 사항의 **의도**를 파악하고 통합
4. 마커 제거 후 `git add <file>`
5. 모든 충돌 해결 후 `git commit`

충돌 원칙: 기계적으로 "ours" 또는 "theirs" 선택 금지. 항상 양쪽 의도를 유지하는 방향으로.

## 브랜치 전략 (기본)

- `main`: 배포 가능한 상태만
- `develop`: 통합 브랜치
- `feature/<name>`: 기능 개발
- `fix/<name>`: 버그 수정
- `release/<version>`: 릴리즈 준비
