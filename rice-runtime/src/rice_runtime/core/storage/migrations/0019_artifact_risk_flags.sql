-- 0019_artifact_risk_flags — 설치 artifact 위험 스캔 결과 영속.
-- 설치 시 core/guard.py::scan_risks 가 SKILL.md 본문에서 찾은 라벨 코드를
-- JSON 배열("<skill_name>:<label>")로 기록한다 (execution/plugin/loader.py).
-- 빈 배열 = 깨끗. 플래그가 있는 plugin 은 설치 시 enabled=0 으로 두고
-- 활성화는 사용자 판단에 맡긴다(경고는 /plugins 응답의 risk_flags 로 표시).
-- 기존 행(스캔 도입 전 설치본)은 '[]' — 미검사이지만 재설치 없이 재평가하지
-- 않는다(설치 트리는 사실상 불변, 주입 시 SKILL_TRUST_NOTE 방어선은 유지).

ALTER TABLE installed_artifacts ADD COLUMN risk_flags TEXT NOT NULL DEFAULT '[]';
