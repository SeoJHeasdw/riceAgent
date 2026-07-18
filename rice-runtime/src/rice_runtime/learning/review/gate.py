"""learning/review/gate.py — 값싼 휴리스틱 게이트: LLM 리뷰를 부를 가치가 있는가.

책임(single): "review()(LLM 1회 호출)를 실제로 실행할 가치가 있는 턴인가"만
판단한다. **내용 판단은 절대 하지 않는다** — 그건 review()의 LLM이 한다. 이 게이트가
하는 일은 오직 비용 통제(매 턴 LLM을 부르면 비싸다)뿐이다.

판단 기준 (Hermes의 `_turns_since_memory`/`_iters_since_skill` nudge-interval
카운터와 같은 목적이지만, RICE는 스케줄 기반이 아니라 결과 기반이다 — 이번 턴이
"리뷰할 가치가 있어 보이는가"를 신호로 먼저 보고, 그래도 확신이 없으면 마지막
폴백으로 "턴이 너무 오래 지났다"를 쓴다):
  - tool_calls_made가 임계값 이상 (복잡한 작업 = 재사용 가치 있는 절차일 가능성)
  - 교정/실패/스킬화 의도 신호 (signals.py 재사용 — 예전엔 이 신호들이 직접 제안을
    만들었지만, 이제는 "LLM을 불러볼까"를 판단하는 게이트로만 쓰인다)
  - 세션당 "마지막 리뷰 이후 턴 수"가 nudge_turns 이상 (서서히 쌓이는 학습 기회를
    신호 없이도 놓치지 않기 위한 폴백)

세션별 턴 카운터는 in-memory dict다 — 재시작 시 소실 허용(server/approval.py의
"in-memory로 충분하다... 재시작 시 파킹된 승인을 잃는 건 데모에서 감내할 만하다"는
기존 입장과 동일 전례, RUNTIME_DESIGN §5.1). `LearningService` 안의 private
collaborator로 산다.
"""

from __future__ import annotations

from ..signals import has_correction_signal, has_failure_signal, has_skill_creation_intent


class ReviewGate:
    """review() 호출 여부를 판단하는 휴리스틱 게이트 (Phase A.2)."""

    def __init__(self, *, min_tool_calls: int, nudge_turns: int) -> None:
        self._min_tool_calls = min_tool_calls
        self._nudge_turns = nudge_turns
        self._turns_since_review: dict[str, int] = {}

    def should_review(
        self,
        session_id: str,
        *,
        tool_calls_made: int,
        final_message: str = "",
        user_text: str | None = None,
        turns_elapsed: int = 1,
    ) -> bool:
        """이 턴에 대해 review()를 부를지 판단한다.

        호출할 때마다 세션의 "마지막 리뷰 이후 턴 수" 카운터에
        `turns_elapsed`를 더한다. pending review job이 여러 턴의 evidence를
        병합했으면 그 턴 수를 한 번에 반영해 nudge를 과소 계수하지 않는다.
        단순 체크(응답이 False였던 경우 포함)로는 리셋되지 않고,
        실제 리뷰가 성공했을 때만 `mark_reviewed()`를 호출해 리셋한다.
        """
        turns = self._turns_since_review.get(session_id, 0) + max(1, int(turns_elapsed))
        self._turns_since_review[session_id] = turns

        haystack = final_message if not user_text else f"{final_message}\n{user_text}"

        return (
            tool_calls_made >= self._min_tool_calls
            or has_correction_signal(haystack)
            or has_failure_signal(haystack)
            or has_skill_creation_intent(haystack)
            or turns >= self._nudge_turns
        )

    def mark_reviewed(self, session_id: str) -> None:
        """실제로 review()를 실행했을 때 호출 — 세션의 턴 카운터를 초기화한다."""
        # Absent and zero are behaviorally identical to should_review(), while
        # removing the entry avoids retaining fully reviewed one-off sessions.
        self._turns_since_review.pop(session_id, None)

    def release_session(self, session_id: str) -> None:
        """Drop one deleted session's counter. Idempotent for missing sessions."""
        self._turns_since_review.pop(session_id, None)
