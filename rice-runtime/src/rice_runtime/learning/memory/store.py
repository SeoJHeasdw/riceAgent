"""memory/store.py — fact/preference CRUD on `state.db`.

Responsibility (single): write and list memories. Deletion is intentionally
omitted for T1 — memories are append-only in the MVP (LEARNING_LOOP §6 says
nothing about deletion, and fail-closed means we don't delete without a clear
contract).

The FTS5 trigger defined in `0001_init.sql` (`memories_ai`) keeps `memories_fts`
in sync automatically on INSERT — no extra work here.

Writing rules enforced here (LEARNING_LOOP §6):
  - `fact`: declarative facts only (e.g. "Project uses pytest").
    Procedural "how-to" knowledge must go to a Skill, not here.
  - `preference`: answer-style preferences (concise/verbose/technical, preferred
    model/tool).

The store doesn't validate *semantic* content ("is this a good fact") — that
judgement belongs to the observer (`service.py::observe`, `review/engine.py`).
We validate `kind` at the boundary, AND (Phase D, 2026-07-02) scrub obvious
secret patterns (`skill/guard.py::scrub_secrets`) — a narrow, deliberate
exception to "content is the caller's job". Unlike skill writes (which funnel
through exactly 3 `LearningService` methods), memory is written from two
different modules (`observe()`'s regex path AND `ReviewEngine`'s LLM path,
both calling `add()` directly) — memory is also NEVER HITL-reviewed before
persisting, so it's the higher-risk, ungated path. Putting the scrub at this
single true chokepoint means neither caller can forget it.
"""

from __future__ import annotations

from ...core.errors import ValidationError
from ...core.storage.sqlite import Database
from ...core.types import MemoryItem, new_id, now_iso
from ..skill.guard import scrub_secrets

_ALLOWED_KINDS = frozenset({"fact", "preference"})


class MemoryStore:
    """Append-only CRUD for the `memories` table (LEARNING_LOOP §6)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def add(
        self,
        kind: str,
        content: str,
        source_session_id: str | None = None,
    ) -> MemoryItem:
        """Insert a new memory and return the created item.

        Raises `ValidationError` if `kind` is not 'fact' or 'preference' — this
        is the boundary guard so downstream callers don't accidentally corrupt the
        table with free-form kinds.
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

        mem_id = new_id("mem")
        created_at = now_iso()
        self._db.execute(
            "INSERT INTO memories (id, kind, content, created_at, source_session_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (mem_id, kind, clean_content, created_at, source_session_id),
        )
        return MemoryItem(kind=kind, content=clean_content, id=mem_id)  # type: ignore[arg-type]

    def list(self) -> list[MemoryItem]:
        """Return all memories, newest first.

        Tie-break on `rowid DESC` handles the in-memory test case where multiple
        inserts share the same `created_at` second (SQLite datetime resolution is 1s).
        """
        rows = self._db.query_all(
            "SELECT id, kind, content FROM memories ORDER BY created_at DESC, rowid DESC"
        )
        return [MemoryItem(kind=r["kind"], content=r["content"], id=r["id"]) for r in rows]
