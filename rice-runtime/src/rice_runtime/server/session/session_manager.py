"""SessionManager — `ISessionStore` over `core.storage` (#9 session part).

A real (not stubbed) implementation: the walking skeleton needs to persist and
replay history for context assembly. Messages are indexed into FTS5 on insert
(see migration triggers) so `search` works without extra bookkeeping.
"""

from __future__ import annotations

import json

from ...core.storage.sqlite import Database
from ...core.types import Message, ToolCall, new_id, now_ms
from .interfaces import ISessionStore, MessagePage, SessionInfo


class SessionManager(ISessionStore):
    def __init__(self, db: Database) -> None:
        self._db = db

    # ── sessions ──────────────────────────────────────────────────────────────
    def create(self, *, title: str | None = None, model: str | None = None) -> SessionInfo:
        sid = new_id("sess")
        self._db.execute(
            "INSERT INTO sessions(id, title, model) VALUES (?, ?, ?)", (sid, title, model)
        )
        info = self.get(sid)
        assert info is not None
        return info

    def get(self, session_id: str) -> SessionInfo | None:
        row = self._db.query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
        return self._to_info(row) if row else None

    def list(self) -> list[SessionInfo]:
        rows = self._db.query_all("SELECT * FROM sessions ORDER BY started_at DESC")
        return [self._to_info(r) for r in rows]

    # ── messages ──────────────────────────────────────────────────────────────
    def append(self, session_id: str, message: Message) -> None:
        self._db.execute(
            "INSERT INTO messages(id, session_id, role, content, tool_calls, ts) VALUES (?, ?, ?, ?, ?, ?)",
            (
                message.id,
                session_id,
                message.role,
                message.content,
                json.dumps([{"id": c.id, "tool_name": c.tool_name, "arguments": c.arguments} for c in message.tool_calls])
                if message.tool_calls
                else None,
                message.ts,
            ),
        )

    def history(self, session_id: str) -> tuple[Message, ...]:
        rows = self._db.query_all(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY ts ASC", (session_id,)
        )
        return tuple(self._to_message(r) for r in rows)

    def page(self, session_id: str, cursor: str | None = None, limit: int = 50) -> MessagePage:
        # cursor = last seen ts (descending page). Simple + stable for the MVP.
        if cursor:
            rows = self._db.query_all(
                "SELECT * FROM messages WHERE session_id = ? AND ts < ? ORDER BY ts DESC LIMIT ?",
                (session_id, int(cursor), limit),
            )
        else:
            rows = self._db.query_all(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY ts DESC LIMIT ?",
                (session_id, limit),
            )
        msgs = [self._to_message(r) for r in rows]
        next_cursor = str(msgs[-1].ts) if len(msgs) == limit and msgs else None
        return MessagePage(messages=tuple(reversed(msgs)), next_cursor=next_cursor)

    def search(self, query: str, limit: int = 10) -> tuple[Message, ...]:
        rows = self._db.query_all(
            "SELECT m.* FROM messages m JOIN messages_fts f ON m.rowid = f.rowid "
            "WHERE messages_fts MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        )
        return tuple(self._to_message(r) for r in rows)

    # ── mapping ───────────────────────────────────────────────────────────────
    @staticmethod
    def _to_info(row) -> SessionInfo:
        return SessionInfo(
            id=row["id"],
            title=row["title"],
            model=row["model"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            archived=bool(row["archived"]),
        )

    @staticmethod
    def _to_message(row) -> Message:
        raw_calls = row["tool_calls"]
        calls: tuple[ToolCall, ...] = ()
        if raw_calls:
            calls = tuple(
                ToolCall(tool_name=c["tool_name"], arguments=c["arguments"], id=c["id"])
                for c in json.loads(raw_calls)
            )
        return Message(
            role=row["role"],
            content=row["content"],
            id=row["id"],
            ts=row["ts"] or now_ms(),
            tool_calls=calls,
        )
