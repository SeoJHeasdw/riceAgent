"""learning/episode.py — LearningEpisode: 도구 전사 + 턴 판정의 독립 엔티티.

§10.5의 "완전한 LearningEpisode"(도구 호출 인자/결과 전사 + 성공/중단/사용자
수정 판정을 담는 독립 엔티티)의 구현체다. 두 층으로 구성된다:

  **전사(transcript) 층** — 한 구간에서 모델이 실제로 어떤 도구를 어떤 인자로
  불렀고 환경이 뭐라고 답했는가(유계·정제). 소비자:
  1. learning_jobs.evidence["tool_transcript"] — 잡 수명의 구간 기록.
  2. candidate 평가의 trace-grounded replay (§10.6 / EVAL §10.2).

  **엔티티(entity) 층** — `learning_episodes` 테이블(0018): 턴 1개 = 에피소드
  1행. 잡이 병합·소비돼도 턴 단위 판정(success/corrected/failure/aborted)과
  관찰 본문이 독립적으로 남는다. 판정은 signals.py 의 결정적 신호 기반 —
  휴리스틱임을 숨기지 않고 judgment JSON 에 근거를 함께 남긴다.

전사 레코드 형태 (JSON-serializable, 전 필드 scrub·상한 적용 후):
  {"name": str, "arguments": <json str>, "ok": bool, "result": <str>}
`result` 는 모델이 실제 세션에서 본 렌더된 내용(tool message content) 그대로다 —
재생의 충실도가 곧 평가의 충실도이므로 재가공하지 않는다(정제·절단만).
"""

from __future__ import annotations

import json
import logging

from ..core.storage.sqlite import Database
from ..core.types import Message, new_id, now_iso
from .skill import guard as skill_guard

log = logging.getLogger("rice.learning.episode")

# 상한 — 전사는 evidence(잡마다)·proposal payload·평가 프롬프트에 실린다.
# 무제한이면 시크릿 scrub 을 통과해도 프롬프트 예산의 옆문이 된다.
MAX_TRANSCRIPT_CALLS = 20
MAX_ARG_CHARS = 400
MAX_RESULT_CHARS = 600
_MAX_NAME_CHARS = 80


def extract_tool_transcript(history: list[Message] | tuple[Message, ...]) -> list[dict]:
    """구간 히스토리 → 원시 전사. assistant 의 tool_calls 로 (id → 이름/인자)를
    만들고 tool 메시지의 `metadata.tool_call_id` 로 짝을 맞춘다 — 성패 정본은
    `metadata.ok`(loop._tool_result_message 계약). 정제는 sanitize 몫."""
    by_id: dict[str, tuple[str, dict]] = {}
    for m in history:
        if m.role == "assistant":
            for tc in m.tool_calls or ():
                by_id[tc.id] = (tc.tool_name, dict(tc.arguments or {}))
    records: list[dict] = []
    for m in history:
        if m.role != "tool":
            continue
        call_id = str(m.metadata.get("tool_call_id") or "")
        name, args = by_id.get(call_id, (str(m.metadata.get("tool_name") or ""), {}))
        if not name:
            name = str(m.metadata.get("tool_name") or "")
        if not name:
            continue
        records.append(
            {
                "name": name,
                "arguments": args,
                "ok": bool(m.metadata.get("ok", True)),
                "result": m.content or "",
            }
        )
    return records


def sanitize_tool_transcript(records) -> tuple[dict, ...]:
    """영속/프롬프트 직전의 단일 정제 관문 — 어떤 경로(observe evidence, 리뷰
    엔진 제안, 구형 payload 복원)로 오든 여기를 거친다. 시크릿 scrub + 길이/개수
    상한. 초과분은 **앞을 버린다**(최근 호출이 판정에 더 가깝다)."""
    cleaned: list[dict] = []
    for raw in records or ():
        if not isinstance(raw, dict):
            continue
        name = " ".join(str(raw.get("name") or "").split())[:_MAX_NAME_CHARS]
        if not name:
            continue
        args_raw = raw.get("arguments")
        args_str = (
            args_raw
            if isinstance(args_raw, str)
            else json.dumps(args_raw or {}, ensure_ascii=False, sort_keys=True)
        )
        args_str = " ".join(args_str.split())[:MAX_ARG_CHARS]
        args_str, _ = skill_guard.scrub_secrets(args_str)
        result = " ".join(str(raw.get("result") or "").split())[:MAX_RESULT_CHARS]
        result, _ = skill_guard.scrub_secrets(result)
        cleaned.append(
            {
                "name": name,
                "arguments": args_str[:MAX_ARG_CHARS],
                "ok": bool(raw.get("ok", True)),
                "result": result[:MAX_RESULT_CHARS],
            }
        )
    return tuple(cleaned[-MAX_TRANSCRIPT_CALLS:])


def merge_tool_transcripts(existing, incoming) -> list[dict]:
    """리뷰 잡 병합용(ledger._merge_evidence) — 시간순 이어붙이고 상한을 넘으면
    앞(오래된 호출)부터 버린다. watermark 병합의 from-보존과 달리 전사는 재생
    충실도가 목적이라 최근이 우선이다."""
    merged = list(existing or []) + list(incoming or [])
    return merged[-MAX_TRANSCRIPT_CALLS:]


# ── 판정 (LEARNING_LOOP §10.5 "성공/중단/사용자 수정") ────────────────────────

_USER_TEXT_EXCERPT_CHARS = 200


def judge_turn_outcome(
    *, aborted: bool, correction_signal: bool, failure_signal: bool
) -> str:
    """결정적 우선순위: aborted > corrected > failure > success.

    corrected 가 failure 보다 앞서는 이유: 교정은 사용자의 명시적 의사표시라
    "도구가 실패했다"는 환경 신호보다 강한 판정 근거다(리뷰 게이트가 교정 신호를
    1급으로 다루는 것과 같은 서열)."""
    if aborted:
        return "aborted"
    if correction_signal:
        return "corrected"
    if failure_signal:
        return "failure"
    return "success"


class EpisodeStore:
    """learning_episodes CRUD — 턴 1개 = 에피소드 1행 (0018 헤더가 정본).

    모든 쓰기는 best-effort 다: 에피소드 기록 실패가 턴을 깨면 안 된다
    (usage 카운터·ledger 와 동일한 fail-safe 원칙)."""

    def __init__(self, db: Database, *, cap: int = 500) -> None:
        self._db = db
        self._cap = max(0, cap)

    def record(
        self,
        *,
        session_id: str,
        from_message_id: str | None,
        through_message_id: str | None,
        outcome: str,
        judgment: dict,
        episode: dict,
    ) -> str | None:
        """에피소드 1행 기록 + 상한 초과분 프루닝. cap=0 이면 기록 비활성."""
        if self._cap <= 0:
            return None
        try:
            episode_id = new_id("ep")
            self._db.execute(
                "INSERT INTO learning_episodes "
                "(id, session_id, created_at, from_message_id, through_message_id, outcome, judgment, episode) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    episode_id,
                    session_id,
                    now_iso(),
                    from_message_id,
                    through_message_id,
                    outcome,
                    json.dumps(judgment, ensure_ascii=False),
                    json.dumps(episode, ensure_ascii=False),
                ),
            )
            # 최근 cap 행만 보존 — 분석 가치는 최근에 몰려 있고 장기 감사는
            # learning_events 몫. rowid 보조 정렬로 같은 초 삽입도 안정적으로 자른다.
            self._db.execute(
                "DELETE FROM learning_episodes WHERE id NOT IN ("
                "SELECT id FROM learning_episodes ORDER BY created_at DESC, rowid DESC LIMIT ?)",
                (self._cap,),
            )
            return episode_id
        except Exception:  # noqa: BLE001 — 기록 실패는 턴을 깨지 않는다
            log.warning("episode record failed for %s", session_id, exc_info=True)
            return None

    def list(self, limit: int = 50, session_id: str | None = None) -> list[dict]:
        try:
            if session_id:
                rows = self._db.query_all(
                    "SELECT * FROM learning_episodes WHERE session_id = ? "
                    "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (session_id, limit),
                )
            else:
                rows = self._db.query_all(
                    "SELECT * FROM learning_episodes ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (limit,),
                )
            return [self._row_to_dict(r) for r in rows]
        except Exception:  # noqa: BLE001
            log.warning("episode list failed", exc_info=True)
            return []

    @staticmethod
    def _row_to_dict(r) -> dict:
        def _decode(raw):
            if not raw:
                return None
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                return {"_decode_error": True}

        return {
            "id": r["id"],
            "session_id": r["session_id"],
            "created_at": r["created_at"],
            "from_message_id": r["from_message_id"],
            "through_message_id": r["through_message_id"],
            "outcome": r["outcome"],
            "judgment": _decode(r["judgment"]) or {},
            "episode": _decode(r["episode"]) or {},
        }


def build_turn_judgment(
    *,
    aborted: bool,
    correction_signal: bool,
    failure_signal: bool,
    tool_calls: int,
    engaged_skills: tuple[str, ...],
    user_text: str,
) -> dict:
    """판정의 감사 가능한 근거 — outcome 은 이 근거의 결정적 함수다."""
    excerpt = " ".join((user_text or "").split())[:_USER_TEXT_EXCERPT_CHARS]
    excerpt, _ = skill_guard.scrub_secrets(excerpt)
    return {
        "aborted": aborted,
        "correction_signal": correction_signal,
        "failure_signal": failure_signal,
        "tool_calls": int(tool_calls),
        "engaged_skills": list(engaged_skills),
        "user_text_excerpt": excerpt[:_USER_TEXT_EXCERPT_CHARS],
    }
