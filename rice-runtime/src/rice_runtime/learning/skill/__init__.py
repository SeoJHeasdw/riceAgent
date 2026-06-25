"""learning.skill — SKILL.md 파일 시스템 스토어, usage 카운터, 생명주기 전이.

이 서브패키지는 learning loop의 스킬(Skill) 절반 (LEARNING_LOOP §2·§3·§4·§5)을
담당한다. 세 개의 좁은 모듈:

  store.py     — ~/.rice/skills/<name>.md + .history/ 파싱·직렬화·읽기·쓰기.
  usage.py     — <name>.usage.json 사이드카: use/view/patch 카운터.
  lifecycle.py — 상태 전이 헬퍼(active→stale→archived) + 롤백.

외부(service.py 파사드)는 이 세 모듈을 조합해 ILearningService를 구현한다.
라우트·오케스트레이터는 service.py만 보고, 이 서브패키지를 직접 import하지 않는다.
"""
