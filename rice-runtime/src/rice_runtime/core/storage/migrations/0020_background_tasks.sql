-- 백그라운드 셸 작업 (RICE_AGENT_EXECUTION.md §11) — 턴을 넘겨 사는 실행의 영속 기록.
--
-- terminal 이 `background:true` 로 띄우거나 auto-background 승격으로 넘어간 명령은
-- 턴이 끝나도 계속 돈다. 이 테이블이 그 작업의 durable 레코드다: 완료 전달을
-- 하나의 "미확인 완료 큐"로 통일하는 DB 백업(§11.2)이라, 런타임이 재시작해도
-- 미확인 완료가 살아남아 다음 턴에 관찰로 전달된다.
--
-- status:
--   'running'     프로세스가 아직 돈다(런타임 재시작 시 부팅 화해가 interrupted 로 정정).
--   'exited'      정상 종료(exit_code 유효).
--   'killed'      kill_task / kill_session / shutdown 으로 프로세스 그룹 종료.
--   'interrupted' 런타임 크래시/재시작으로 OS 프로세스가 죽음(부팅 화해가 마킹).
--
-- acknowledged: 완료가 대화에 관찰로 주입됐는지(창 A: 턴 내 반복 사이 / 창 B: 다음
--   턴 ASSEMBLE). 완료 후 acknowledged=0 인 행이 "미확인 완료 큐"다 — 드레인 시 1.
--   DB 가 소스라 프로세스 재시작 뒤에도 미전달 완료가 유실되지 않는다.
--
-- output_path: stdout 을 스풀한 파일 경로(메모리 폭주 방지, §11.3). 완료 관찰과
--   task_output 이 여기서 40KB 상한으로 읽는다. ack 후에도 카드 조회용으로 남기고
--   최근 완료 보존 상한 prune / 세션 종료 시 삭제.
--
-- 보존: ack된 완료는 세션당 최근 20개로 prune한다. 세션 삭제 시 purge_session이
--   프로세스 종료 + 행·스풀 정리를 함께 한다(FK 없이 명시적).
CREATE TABLE IF NOT EXISTS background_tasks (
  id            TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL,
  command       TEXT NOT NULL,
  cwd           TEXT,
  status        TEXT NOT NULL,
  exit_code     INTEGER,
  started_at    TEXT NOT NULL,
  ended_at      TEXT,
  output_path   TEXT,
  acknowledged  INTEGER NOT NULL DEFAULT 0
);
-- 창 B 드레인의 핫 쿼리: 세션의 미확인 완료(acknowledged=0 AND status!='running').
CREATE INDEX IF NOT EXISTS idx_background_tasks_session ON background_tasks(session_id, acknowledged);
-- 부팅 화해의 핫 쿼리: status='running' 전수 스캔(크래시 후 고아 정정).
CREATE INDEX IF NOT EXISTS idx_background_tasks_status ON background_tasks(status);
