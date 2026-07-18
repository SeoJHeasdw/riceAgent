-- LearningEpisode 독립 엔티티 (LEARNING_LOOP §10.5 두 번째 후속의 완결).
--
-- 지금까지 "한 턴에 무슨 일이 있었나"는 learning_jobs.evidence 안에만 살았다 —
-- 잡은 리뷰 수명(claim→done)이라 소비되면 그 구간의 관찰 기록도 잡의 종료 상태
-- 뒤에 묻혔고, 여러 턴이 하나의 잡으로 병합되면 턴 단위 판정이 사라졌다.
-- 이 테이블은 **턴 1개 = 에피소드 1행**의 독립 기록이다: 리뷰가 돌았든 말든,
-- 잡이 병합됐든 말든, "이 턴은 어떻게 끝났고(판정) 무엇을 했는가(전사)"가
-- 그대로 남는다. 리뷰 잡 evidence 는 episode_ids 로 여기를 가리킨다(인과 링크).
--
-- outcome — 결정적 신호 기반 판정(휴리스틱임을 숨기지 않는다; 근거는 judgment):
--   'success'   완주 + 교정/실패 신호 없음
--   'corrected' 완주했지만 교정 신호(signals.py has_correction_signal) 감지
--     — "사용자 수정"의 실측 축. 리뷰 게이트·friction 과 같은 신호원.
--   'failure'   완주했지만 실패 신호(has_failure_signal) 감지
--   'aborted'   사용자가 턴을 중단
-- corrected 와 failure 가 동시면 corrected 우선(사용자 의사표시가 더 강한 신호).
--
-- judgment (JSON) — 판정의 감사 가능한 근거:
--   {"aborted": bool, "correction_signal": bool, "failure_signal": bool,
--    "tool_calls": int, "engaged_skills": [...], "user_text_excerpt": "..."}
-- episode (JSON) — 관찰 본문:
--   {"tool_transcript": [...(0017/episode.py 와 동일 레코드)...],
--    "injected_skills": {...}, "viewed_skills": {...},
--    "engaged_skill_versions": {...}, "seed_prompt": "..."}
--
-- 보존: 에피소드는 매 턴 쌓이므로 삽입 시 오래된 행부터 상한(config
-- learning_episode_cap, 기본 500)으로 잘라낸다 — 분석 가치는 최근에 몰려 있고,
-- 장기 감사는 learning_events 가 담당한다.
CREATE TABLE IF NOT EXISTS learning_episodes (
  id                 TEXT PRIMARY KEY,
  session_id         TEXT NOT NULL,
  created_at         TEXT NOT NULL,
  from_message_id    TEXT,
  through_message_id TEXT,
  outcome            TEXT NOT NULL,
  judgment           TEXT,
  episode            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learning_episodes_session ON learning_episodes(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_learning_episodes_outcome ON learning_episodes(outcome, created_at);
