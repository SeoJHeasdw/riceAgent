---
name: rice-connector-and-frontend-state
description: "Connector 서브시스템(Gmail/Calendar/Drive+로컬MCP+커스텀원격) 전부 실배선 완료 — 정본은 RICE_RUNTIME_DESIGN.md §9·RICE_RUNTIME_DIRECTORY.md connector/·RICE_CONTRACTS.md(2026-07-08 동기화), 여기는 문서에 없는 잔여 제약만"
metadata: 
  node_type: memory
  type: project
  originSessionId: e1ea2c93-216e-4f3c-9c19-61db3840dd7e
---

Connector 서브시스템(OAuth 3종: Gmail/Calendar/Drive 같은 client 공유 + 로컬 MCP stdio/http + 커스텀 원격 MCP RFC 9728/8414/7591 디스커버리 + 도구별 permission + 업데이트 감지)은 백엔드·프론트 양쪽 전부 실제로 배선되어 있다(가짜 프로토타입 모듈 없음). 프로토콜·모듈 구조는 [RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) §9, 디렉토리는 [RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md) `connector/` 트리, REST 계약은 [RICE_CONTRACTS.md](RICE_CONTRACTS.md)가 2026-07-08 기준 전부 정본으로 갱신됐다 — 상세를 여기 재기술하지 않는다.

**Why:** 이 memory는 원래 2026-06-30~07-02 사이 세션별 구축 로그(27KB)였다. 그 내용이 전부 위 문서들로 승격됐으므로(이번 문서-소스 동기화 작업), 세션별 서사는 git log가 이미 충분한 출처라 memory에서는 제거하고 문서에 없는 잔여 사실만 남긴다.

**문서에 없는 잔여 제약(의도적 수용, 낮은 우선순위)**:
- PKCE 미구현 — 기존 `OAuthFlow` 전체가 원래 미구현(Gmail도 동일).
- 같은 `mcp_endpoint`로 커스텀 커넥터를 재등록해도 dedup 안 함(매번 새 `custom_<n>` 타입 생성).
- 도구 이름 충돌 시 로컬 우선·첫 매칭 라우팅.
- `prompt=consent`/`access_type=offline`(Google 전용 OAuth 파라미터)이 커스텀 서버에도 그대로 전송됨 — descriptor별 extra auth params는 미착수.

관련: [[rice-title-gen-and-i18n-direction]], [[system-authority-and-architecture-principles]](미배포 프로토타입 마이그레이션 fix-forward 원칙은 거기 정본)
