"""learning.review — LLM 기반 스킬/메모리 저작 리뷰 (Hermes learning_loop 이식).

이 서브패키지는 observe()가 규칙 기반으로 하던 스킬 생성/patch 저작 판단을
대체한다. 턴의 SSE 스트림 밖(별도 `/learning-review`·`/learn` 엔드포인트)에서만
호출된다 — LLM 호출이 턴의 `done`을 지연시키면 안 되기 때문이다.

  gate.py   — 값싼 휴리스틱: "LLM을 부를 가치가 있는 턴인가"만 판단(내용 판단은 안 함).
  engine.py — 실제 LLM 1회 호출 + 구조화된 제안(LearningEffect) 산출. 실행 권한 없음
              (propose_* 툴만 노출 — 실제 파일 쓰기는 apply_patch가 HITL 승인 후에만).

외부(service.py 파사드)는 이 서브패키지를 조합해 ILearningService.review()/learn()을
구현한다. 라우트·오케스트레이터는 service.py만 본다.
"""
