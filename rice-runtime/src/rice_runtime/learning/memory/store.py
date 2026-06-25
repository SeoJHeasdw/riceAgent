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

The store doesn't validate *content* — that judgement belongs to the observer
(`loop.py`, T4). We only validate `kind` at the boundary.
"""

from __future__ import annotations

from ...core.errors import ValidationError
from ...core.storage.sqlite import Database
from ...core.types import MemoryItem, new_id, now_iso

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

        mem_id = new_id("mem")
        created_at = now_iso()
        self._db.execute(
            "INSERT INTO memories (id, kind, content, created_at, source_session_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (mem_id, kind, content.strip(), created_at, source_session_id),
        )
        return MemoryItem(kind=kind, content=content.strip(), id=mem_id)  # type: ignore[arg-type]

    def list(self) -> list[MemoryItem]:
        """Return all memories, newest first.

        Tie-break on `rowid DESC` handles the in-memory test case where multiple
        inserts share the same `created_at` second (SQLite datetime resolution is 1s).
        """
        rows = self._db.query_all(
            "SELECT id, kind, content FROM memories ORDER BY created_at DESC, rowid DESC"
        )
        return [MemoryItem(kind=r["kind"], content=r["content"], id=r["id"]) for r in rows]
