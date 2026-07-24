"""memory/store.py — fact/preference CRUD on `state.db`.

Responsibility (single): write, list, delete memories. Deletion was intentionally
omitted for T1 (append-only MVP), but Self-Improvement v3 adds it: memory is the
only learning path with no HITL gate (writes apply immediately), so a *post-hoc*
removal affordance is the counterweight — without it a bad fact is injected into
every future turn forever. The FTS delete trigger (`memories_ad`, 0001_init.sql)
keeps `memories_fts` in sync automatically.

The FTS5 trigger defined in `0001_init.sql` (`memories_ai`) keeps `memories_fts`
in sync automatically on INSERT — no extra work here.

Writing rules enforced here (LEARNING_LOOP §6):
  - `fact`: declarative facts only (e.g. "Project uses pytest").
    Procedural "how-to" knowledge must go to a Skill, not here.
  - `preference`: answer-style preferences (concise/verbose/technical, preferred
    model/tool).

The store doesn't validate *semantic* content ("is this a good fact") — that
judgement belongs to the review engine (`review/engine.py`). `observe()` no
longer performs regex fact/preference extraction.
We validate `kind` at the boundary, AND (Phase D, 2026-07-02) scrub obvious
secret patterns (`skill/guard.py::scrub_secrets`) — a narrow, deliberate
exception to "content is the caller's job". Unlike skill writes (which funnel
through exactly 3 `LearningService` methods), memory is NEVER HITL-reviewed
before persisting, so it's the higher-risk, ungated path. Putting the scrub at
this single true chokepoint keeps the write boundary safe even if new memory
callers are added later.
"""

from __future__ import annotations

import json

from ...core.errors import NotFoundError, ValidationError
from ...core.storage.sqlite import Database
from ...core.types import MemoryItem, new_id, now_iso
from ..skill.guard import scrub_secrets

_ALLOWED_KINDS = frozenset({"fact", "preference"})
_ALLOWED_STATUS = frozenset({"active", "quarantined"})

# SELECT 컬럼 목록 — provenance와 quarantine 필드를 항상 함께 읽는다.
_COLS = "id, kind, content, status, derived_from_external, source_session_id, source_message_ids"


def _row_to_item(r) -> MemoryItem:
    raw_ids = r["source_message_ids"] if "source_message_ids" in r.keys() else None
    try:
        ids = tuple(json.loads(raw_ids)) if raw_ids else ()
    except (json.JSONDecodeError, TypeError):
        ids = ()
    return MemoryItem(
        kind=r["kind"],
        content=r["content"],
        id=r["id"],
        status=r["status"] if "status" in r.keys() and r["status"] else "active",
        derived_from_external=bool(r["derived_from_external"]) if "derived_from_external" in r.keys() else False,
        source_session_id=r["source_session_id"] if "source_session_id" in r.keys() else None,
        source_message_ids=ids,
    )


class MemoryStore:
    """Append-only CRUD for the `memories` table (LEARNING_LOOP §6)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def add(
        self,
        kind: str,
        content: str,
        source_session_id: str | None = None,
        *,
        derived_from_external: bool = False,
        source_message_ids: tuple[str, ...] = (),
        status: str | None = None,
    ) -> MemoryItem:
        """Insert a new memory and return the created item.

        Raises `ValidationError` if `kind` is not 'fact' or 'preference' — this
        is the boundary guard so downstream callers don't accidentally corrupt the
        table with free-form kinds.

        외부 콘텐츠(도구 결과)에서 유도된 메모리는 기본적으로
        **격리(quarantined)** 상태로 저장된다 — prefetch가 주입하지 않고 사용자
        승인을 기다린다. `status`를 명시하면 그것을 따르되 화이트리스트로 막는다.
        """
        if kind not in _ALLOWED_KINDS:
            raise ValidationError(
                f"Unknown memory kind {kind!r}; must be one of {sorted(_ALLOWED_KINDS)}"
            )
        if not content or not content.strip():
            raise ValidationError("Memory content must not be empty")

        clean_content, _redacted_labels = scrub_secrets(content.strip())
        # NOTE(agent): 발견 라벨(_redacted_labels)은 memory 경로에선 사용자에게
        # 즉시 보여줄 화면이 없다(비HITL, 즉시 반영) — best-effort로 조용히
        # 치환만 한다. 스킬 경로는 HITL 카드가 있어 PatchProposal.warnings로
        # 노출한다(learning/service.py).

        if status is None:
            status = "quarantined" if derived_from_external else "active"
        if status not in _ALLOWED_STATUS:
            raise ValidationError(f"Unknown memory status {status!r}")

        mem_id = new_id("mem")
        created_at = now_iso()
        self._db.execute(
            "INSERT INTO memories (id, kind, content, created_at, source_session_id, "
            "status, derived_from_external, source_message_ids) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                mem_id, kind, clean_content, created_at, source_session_id,
                status, 1 if derived_from_external else 0,
                json.dumps(list(source_message_ids)) if source_message_ids else None,
            ),
        )
        return MemoryItem(
            kind=kind,  # type: ignore[arg-type]
            content=clean_content,
            id=mem_id,
            status=status,  # type: ignore[arg-type]
            derived_from_external=derived_from_external,
            source_session_id=source_session_id,
            source_message_ids=source_message_ids,
        )

    def set_status(self, memory_id: str, status: str) -> None:
        """격리 메모리 승인(→active)/재격리. 없으면 NotFoundError → 404."""
        if status not in _ALLOWED_STATUS:
            raise ValidationError(f"Unknown memory status {status!r}")
        cur = self._db.execute(
            "UPDATE memories SET status = ? WHERE id = ?", (status, memory_id)
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"memory {memory_id} not found")

    def delete(self, memory_id: str) -> None:
        """메모리 하나를 삭제한다. 없으면 NotFoundError → 404 (routes 관례)."""
        cur = self._db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        if cur.rowcount == 0:
            raise NotFoundError(f"memory {memory_id} not found")

    def list(self) -> list[MemoryItem]:
        """Return all memories (active + quarantined), newest first.

        Tie-break on `rowid DESC` handles the in-memory test case where multiple
        inserts share the same `created_at` second (SQLite datetime resolution is 1s).
        """
        rows = self._db.query_all(
            f"SELECT {_COLS} FROM memories ORDER BY created_at DESC, rowid DESC"
        )
        return [_row_to_item(r) for r in rows]
