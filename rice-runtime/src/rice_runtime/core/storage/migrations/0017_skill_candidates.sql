-- candidate 승격 게이트 (LEARNING_LOOP §10.6): "승인된 제안이 실제로 더 나은가"를
-- baseline(현재 버전)과의 비교 평가로 증명한 뒤에만 스킬 스토어에 쓴다.
--
-- candidate_eval_enabled 가 켜져 있으면 HITL 승인(카드/제안함)이 apply_patch 로
-- 직행하지 않고 여기 격리된다. 스킬 파일(SKILL.md)은 candidate 가 promoted 로
-- 전이할 때까지 불변이다 — "후보 스킬을 즉시 active 로 승격하지 않는다"(§10.5)의
-- 저장소 구현. 파일이 아니라 SQLite 인 이유: 후보는 사용자 소유 아티팩트가 아직
-- 아니고(증명 전), learning_jobs 와 같은 상태 머신(원자 claim·재시도 카운터)이
-- 필요하다.
--
-- status: pending(평가 대기) → running(claim됨)
--         → promoted(평가 통과 또는 수동 승격 — apply_patch 실행됨)
--         | discarded(회귀 검출 또는 수동 폐기 — 스킬 불변)
--         | expired(승격 시점 baseline 충돌 — 스킬이 그 사이 다른 경로로 변경됨)
--         | failed(평가 인프라 실패가 attempts 상한 도달 — 수동 결정만 가능)
--
-- payload (JSON) — 제안함(skill_suggestions.payload)과 같은 어휘(proposed_body,
--   description/tags, new_description/new_tags, changelog, warnings …) +
--   seed_prompts(평가 held-out 과제의 원천 — 제안을 낳은 구간의 사용자 발화,
--   시크릿 scrub 후 저장) + session_id/origin(승격 시 source 복원용).
-- report (JSON) — 평가 리포트(과제·arm 별 지표·판정 사유). 폐기된 후보도 리포트와
--   함께 남아 감사 가능하다(증거 없는 폐기는 증발과 같다).
CREATE TABLE IF NOT EXISTS skill_candidates (
  id                 TEXT PRIMARY KEY,
  created_at         TEXT NOT NULL,
  skill_id           TEXT NOT NULL,
  kind               TEXT NOT NULL,                       -- 'create' | 'patch'
  status             TEXT NOT NULL DEFAULT 'pending',
  baseline_version   INTEGER NOT NULL DEFAULT 0,          -- 0 = 신규 생성(baseline 없음)
  baseline_body_hash TEXT,                                -- body_fingerprint(baseline body)
  payload            TEXT NOT NULL,
  attempts           INTEGER NOT NULL DEFAULT 0,
  claimed_at         TEXT,
  decided_at         TEXT,
  report             TEXT
);
CREATE INDEX IF NOT EXISTS idx_skill_candidates_status ON skill_candidates(status, created_at);
CREATE INDEX IF NOT EXISTS idx_skill_candidates_skill ON skill_candidates(skill_id, status);
