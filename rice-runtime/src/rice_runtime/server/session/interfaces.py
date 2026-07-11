"""ISessionStore — session/message persistence seam (#9 session part).

Backed by `state.db` (sessions, messages + FTS5). The agent loop and routes read
and append history through this Protocol; they never touch SQLite directly.

Single active session for the prototype (RUNTIME_DESIGN §2), but the store is
multi-session-shaped so history UI and future concurrency are not blocked.

**FROZEN.** Implemented by `session_manager.py` over `core.storage.sqlite`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ...core.types import Message


@dataclass(frozen=True)
class SessionInfo:
    id: str
    title: str | None
    model: str | None
    started_at: str
    ended_at: str | None
    archived: bool


@dataclass(frozen=True)
class MessagePage:
    messages: tuple[Message, ...]
    next_cursor: str | None  # opaque; None = no more


@runtime_checkable
class ISessionStore(Protocol):
    def create(self, *, title: str | None = None, model: str | None = None) -> SessionInfo: ...
    def get(self, session_id: str) -> SessionInfo | None: ...
    def list(self) -> list[SessionInfo]: ...

    def set_title(self, session_id: str, title: str) -> None:
        """Label a session (e.g. derived from its first user message) for the history UI."""
        ...

    def delete(self, session_id: str) -> None:
        """Remove a session and its messages (history sidebar delete)."""
        ...

    def delete_after_message(self, session_id: str, message_id: str) -> None:
        """Drop messages inserted after `message_id` (regenerate drops the stale
        assistant turn). Keyed by the message's unique id — a ts cut-off misses
        same-millisecond rows."""
        ...

    def append(self, session_id: str, message: Message) -> None:
        """Persist one message (indexed into FTS on insert)."""
        ...

    def history(self, session_id: str) -> tuple[Message, ...]:
        """Full ordered history for context assembly."""
        ...

    def page(self, session_id: str, cursor: str | None = None, limit: int = 50) -> MessagePage:
        """Paginated history for the ChatScreen history loader."""
        ...

    def search(self, query: str, limit: int = 10) -> tuple[Message, ...]:
        """FTS5 search across messages (session_search, RUNTIME_DESIGN §3)."""
        ...
