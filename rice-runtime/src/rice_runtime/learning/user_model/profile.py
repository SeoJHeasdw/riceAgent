"""user_model/profile.py — 사용자 개인화 / 답변 스타일 관리.

책임(single): 사용자 선호(preferences)를 조회해 system prompt에 반영할 텍스트를
  산출한다. 저장은 `memories(kind='preference')`를 재사용한다 — 별도 테이블 불필요.

구현 원칙 (LEARNING_LOOP §6, CONVENTIONS §2):
  - 가장 단순하게. 데모 가시성 위주.
  - 선호 키워드를 preferences 목록에서 추출해 compact한 안내 문구로 만든다.
  - 임베딩/복잡한 NLP 없이 키워드 매칭으로만 처리한다.

WHY memories 재사용: LEARNING_LOOP §6에서 T5는 "저장은 memories(kind='preference')
재사용으로 충분(별도 테이블 불필요)"으로 명시한다. DI를 줄이고 단일 스토리지를
유지한다.

NOTE(agent): 선호 감지 로직은 MVP 범위에서 키워드 기반으로 단순화한다.
실제 배포에서는 더 정교한 NLU가 필요하다.
"""

from __future__ import annotations

from ..memory.store import MemoryStore


# Style preferences become model-facing system-prompt text. Keep the generated
# guidance in neutral English so a style preference cannot accidentally switch
# the response language. Put negative/specific phrases before their substrings:
# otherwise "non-technical" incorrectly matches "technical".
_STYLE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("non-technical", "The user prefers simple, non-technical explanations."),
    ("concise", "The user prefers concise responses."),
    ("verbose", "The user prefers detailed explanations."),
    ("detailed", "The user prefers detailed explanations."),
    ("technical", "The user prefers technical, professional explanations."),
    ("simple", "The user prefers simple, easy-to-understand explanations."),
    ("korean", "The user prefers responses in Korean."),
    ("english", "The user prefers responses in English."),
    ("간결", "The user prefers concise responses."),
    ("자세", "The user prefers detailed explanations."),
    ("기술적", "The user prefers technical, professional explanations."),
)


class UserProfile:
    """preferences 목록을 읽어 system prompt 추가 텍스트를 산출한다.

    WHY: LearningService.prefetch가 이 클래스를 통해 사용자 선호를
    system_prompt_additions에 반영하도록 한다. 선호 저장은 MemoryStore를 통해
    observe()에서 이미 이루어지므로, 여기서는 읽기/렌더링만 담당한다.
    """

    def __init__(self, mem_store: MemoryStore) -> None:
        self._mem_store = mem_store

    def render_preference_block(self) -> str:
        """저장된 preferences를 읽어 system prompt 블록으로 렌더링한다.

        preference 내용에서 알려진 스타일 키워드를 감지해 가이드 문구로 변환한다.
        매칭되는 키워드가 없으면 preference 내용을 그대로 나열한다.
        (LEARNING_LOOP §6, T5 데모 가시성 우선)
        """
        # MemoryStore.list()는 all kind/status를 반환; active preference만 필터.
        # 격리(quarantined) 선호는 사용자 승인 전까지 주입하지 않는다.
        all_memories = self._mem_store.list()
        preferences = [m for m in all_memories if m.kind == "preference" and m.status == "active"]
        if not preferences:
            return ""

        lines: list[str] = ["## User Preferences"]
        seen_styles: set[str] = set()

        for pref in preferences:
            content_lower = pref.content.lower()
            matched = False
            for keyword, guide_text in _STYLE_KEYWORDS:
                if keyword in content_lower and guide_text not in seen_styles:
                    lines.append(f"- {guide_text}")
                    seen_styles.add(guide_text)
                    matched = True
                    break
            if not matched:
                # 매칭 안 된 preference는 원문 그대로
                lines.append(f"- [PREF] {pref.content}")

        return "\n".join(lines)
