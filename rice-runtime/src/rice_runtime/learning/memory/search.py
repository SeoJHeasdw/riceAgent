"""memory/search.py — FTS5 keyword retrieval over the memories table.

WHY: The prefetch path (LEARNING_LOOP §6, §7) combines two retrieval strategies:
  1. Recency: the N most-recently created facts (time-ordered) — always stable.
  2. Relevance: FTS5 keyword match against the user's message — top-K rows.

FTS5 (SQLite built-in) is the *only* retrieval mechanism here; semantic / vector /
embedding search is **explicitly forbidden** at the MVP scope (CONVENTIONS §2,
RUNTIME_DESIGN §3). The FTS5 weakness (exact keyword mismatch) is diagnosed by the
`skill_hit` metric in the eval harness (RICE_LEARNING_EVAL.md §2), not fixed here.

The `memories_fts` table is an external-content FTS5 table mirroring `memories`;
it is kept in sync by the triggers in `0001_init.sql`.
"""

from __future__ import annotations

from ...core.storage.sqlite import Database
from ...core.types import MemoryItem


class MemorySearch:
    """FTS5-backed keyword search over the `memories` table (LEARNING_LOOP §6)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def recent(self, n: int) -> list[MemoryItem]:
        """Return the `n` most-recently created fact memories.

        Only `fact` kind is returned here; preferences are retrieved by the
        user-model layer (T5) which decides how to render them in the system prompt.

        Tie-break on `rowid DESC` handles in-memory tests where multiple inserts
        share the same `created_at` second (SQLite datetime resolution is 1s).
        """
        # status='active'만 — 격리(quarantined) 메모리는 사용자 승인 전까지 절대
        # 주입하지 않는다(외부 콘텐츠 오염 방지).
        rows = self._db.query_all(
            "SELECT id, kind, content FROM memories "
            "WHERE kind = 'fact' AND status = 'active' "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (n,),
        )
        return [MemoryItem(kind=r["kind"], content=r["content"], id=r["id"]) for r in rows]

    def fts_search(self, query: str, k: int) -> list[MemoryItem]:
        """Full-text search against `memories_fts`. Returns up to `k` matches.

        FTS5 `MATCH` raises sqlite3.OperationalError on a malformed query
        (e.g. bare `*`). We catch that and return [] — fail-closed is safer than
        crashing the prefetch path (CONVENTIONS §7).

        The FTS5 content table keeps row-id alignment with the `memories` base
        table, so we JOIN back to get the `kind` and original `id`.
        """
        if not query or not query.strip():
            return []
        try:
            rows = self._db.query_all(
                "SELECT m.id, m.kind, m.content "
                "FROM memories_fts f "
                "JOIN memories m ON m.rowid = f.rowid "
                "WHERE f.content MATCH ? AND m.status = 'active' "  # 격리 메모리 제외 (P0)
                "ORDER BY rank "  # FTS5 rank: lower is better
                "LIMIT ?",
                (query.strip(), k),
            )
        except Exception:
            # NOTE(agent): sqlite3.OperationalError for invalid FTS5 query syntax.
            # Returning [] is fail-closed — the turn proceeds without extra context.
            return []
        return [MemoryItem(kind=r["kind"], content=r["content"], id=r["id"]) for r in rows]
