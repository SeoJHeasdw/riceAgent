"""In-process async event bus (RUNTIME_DIRECTORY: `core/events/`).

Purpose: loosen coupling between modules that need to *announce* something without
knowing who listens. It is **not** the SSE transport (that is request-scoped and
lives in `server/sse/emitter.py`). This bus is for cross-cutting, fire-and-forget
notifications — e.g. learning announces "skill created" and both the SSE layer and
a telemetry/eval listener can react, without learning importing either.

Design notes:
  - Topics are plain strings (keep a small, documented set; see TOPICS below).
  - Handlers are async callables; publish awaits them sequentially so ordering is
    deterministic and a slow handler can't be silently dropped. A handler raising
    is logged-and-isolated so one bad subscriber can't break a turn.
  - Single-process, single active session (RUNTIME_DESIGN §2) → no threading,
    no backpressure machinery needed for the MVP.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger("rice.events")

Handler = Callable[["Event"], Awaitable[None]]


@dataclass(frozen=True)
class Event:
    topic: str
    payload: dict[str, Any] = field(default_factory=dict)


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """Register `handler` for `topic`. Returns an unsubscribe callable."""
        self._subs[topic].append(handler)

        def _unsub() -> None:
            try:
                self._subs[topic].remove(handler)
            except ValueError:
                pass

        return _unsub

    async def publish(self, topic: str, **payload: Any) -> None:
        for handler in list(self._subs.get(topic, ())):
            try:
                await handler(Event(topic=topic, payload=payload))
            except Exception:  # isolate subscriber failures
                log.exception("event handler failed for topic=%s", topic)


# Documented topic constants — the only sanctioned cross-module signals.
class TOPICS:
    SKILL_CREATED = "learning.skill_created"
    SKILL_PATCHED = "learning.skill_patched"
    MEMORY_WRITTEN = "learning.memory_written"
    CONNECTOR_CHANGED = "connector.changed"
    PLUGIN_CHANGED = "execution.plugin_changed"

    # Helper for asyncio task lifetime in case a subscriber wants to spawn work.
    @staticmethod
    def background(coro: Awaitable[None]) -> "asyncio.Task[None]":
        return asyncio.ensure_future(coro)
