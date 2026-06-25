"""SSE emitter — turn `SSEEvent`s into the HTTP streaming response (CONTRACTS §1.3).

The orchestrator yields `core.types.SSEEvent`s; this adapter is the thin boundary
that serializes them to SSE wire frames and (optionally) injects keep-alive
comments. Keeping serialization here (not in the orchestrator) means the agent core
stays transport-agnostic and unit-testable.

The frontend client (`rice-app/src/lib/sse.ts`) accepts the `event:`/`data:` frame
form emitted by `SSEEvent.to_frame()`.
"""

from __future__ import annotations

from typing import AsyncIterator

from ...core.types import SSEEvent

KEEPALIVE_COMMENT = ": keep-alive\n\n"


async def to_sse_stream(events: AsyncIterator[SSEEvent]) -> AsyncIterator[str]:
    """Adapt a stream of `SSEEvent`s to SSE wire text. Each event → one frame."""
    async for event in events:
        yield event.to_frame()
