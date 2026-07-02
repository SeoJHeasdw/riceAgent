-- 0011_oauth_pending_states — 진행 중 OAuth 세션(state → connector + PKCE verifier) 영속화.
--
-- 이전엔 ConnectorManager 의 인메모리 dict 라서 begin_connect 와 브라우저 콜백 사이에
-- 런타임이 재시작하면 "unknown state" 로 실패했다. DB 에 두면 재시작을 견딘다.
-- state 는 CSRF 토큰, code_verifier 는 PKCE(RFC 7636) 비밀 — 둘 다 수 분짜리 단명 값이고
-- authorization code + client 자격증명 없이는 단독으로 쓸모가 없으므로 키체인이 아니라
-- 로컬 DB 에 둔다(§10.4 의 금지 대상은 access/refresh "토큰"). 콜백에서 one-shot 으로
-- 소비(DELETE)되고, 15분 지난 행은 begin_connect 가 만료 정리한다.
CREATE TABLE IF NOT EXISTS oauth_pending_states (
  state         TEXT PRIMARY KEY,
  connector_id  TEXT NOT NULL,
  code_verifier TEXT NOT NULL,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
