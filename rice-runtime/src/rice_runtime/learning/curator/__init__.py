"""learning.curator — 자동 시간 규칙 기반 스킬 상태 전이 + 수동 큐레이션.

이 서브패키지는 learning loop의 큐레이터 (LEARNING_LOOP §5.1)를 담당한다.

  curator.py — active→stale→archived 자동 전이 규칙 적용 + 수동 "스킬 정리".

외부(service.py 파사드)는 이 패키지를 직접 쓰고, 라우트는 service.py만 본다.
"""
