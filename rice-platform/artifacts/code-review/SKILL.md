---
name: code-review
version: 1
description: "체계적인 코드 리뷰: 보안, 성능, 가독성, 테스트 검토"
tags: [code-review, quality, security]
---

코드 리뷰 시 아래 체크리스트 순서로 검토합니다.

## /review — 코드 리뷰 수행

### 1단계: 보안 (Critical)
- [ ] SQL Injection / XSS / CSRF 취약점
- [ ] 하드코딩된 시크릿 / API 키
- [ ] 인증·인가 누락
- [ ] 입력값 검증 누락

### 2단계: 정확성 (High)
- [ ] 비즈니스 로직 오류
- [ ] 엣지 케이스 미처리 (null, empty, overflow)
- [ ] 에러 핸들링 누락
- [ ] 동시성 문제 (race condition, deadlock)

### 3단계: 성능 (Medium)
- [ ] N+1 쿼리
- [ ] 불필요한 루프 / 중첩 루프
- [ ] 메모리 누수
- [ ] 캐싱 기회 미활용

### 4단계: 가독성 (Low)
- [ ] 변수/함수명 명확성
- [ ] 함수 단일 책임 원칙
- [ ] 주석 필요 여부 (복잡한 로직만)
- [ ] 일관된 코드 스타일

## 피드백 형식

```
L<라인>: 🔴 [critical] <문제>. <해결책>.
L<라인>: 🟡 [suggestion] <개선 제안>.
L<라인>: 🟢 [nit] <선택적 개선>.
```

예시:
```
L42: 🔴 [critical] user 입력 직접 SQL에 삽입. parameterized query 사용.
L78: 🟡 [suggestion] N+1 쿼리 발생. include() / eager loading으로 최적화.
L103: 🟢 [nit] 변수명 `d` → `duration`으로 명확화.
```

## 원칙

- 사람이 아닌 코드를 비판
- 문제 지적과 함께 해결책 제시
- Critical 이슈가 없으면 LGTM 명시
