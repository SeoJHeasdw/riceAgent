"""SSE emitter — turn `SSEEvent`s into the HTTP streaming response (CONTRACTS §1.3).

The orchestrator yields `core.types.SSEEvent`s; this adapter is the thin boundary
that serializes them to SSE wire frames and (optionally) injects keep-alive
comments. Keeping serialization here (not in the orchestrator) means the agent core
stays transport-agnostic and unit-testable.

The frontend client (`rice-app/src/lib/sse.ts`) accepts the `event:`/`data:` frame
form emitted by `SSEEvent.to_frame()`.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from ...core.types import SSEEvent

KEEPALIVE_COMMENT = ": keep-alive\n\n"

# 승인 대기(HITL park, 최대 approval_timeout 300초) 동안 이벤트가 하나도 없으면
# 프록시/클라이언트 유휴 타임아웃이 스트림을 끊는다. 이 주기마다
# SSE 주석 프레임을 흘린다 — 프론트 파서(`:` 시작 줄)와 표준 EventSource 모두 무시한다.
KEEPALIVE_INTERVAL_S = 15.0


async def to_sse_stream(
    events: AsyncIterator[SSEEvent], *, keepalive_s: float | None = KEEPALIVE_INTERVAL_S
) -> AsyncIterator[str]:
    """Adapt a stream of `SSEEvent`s to SSE wire text. Each event → one frame.

    `keepalive_s` 동안 다음 이벤트가 없으면 KEEPALIVE_COMMENT 를 내보내고 계속
    기다린다(None 이면 비활성). 이벤트 소스를 shield 로 감싸 keepalive 타임아웃이
    진행 중인 턴을 취소하지 않게 한다.
    """
    if keepalive_s is None:
        async for event in events:
            yield event.to_frame()
        return

    iterator = events.__aiter__()
    pending: asyncio.Task | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            try:
                event = await asyncio.wait_for(asyncio.shield(pending), timeout=keepalive_s)
            except asyncio.TimeoutError:
                yield KEEPALIVE_COMMENT
                continue
            except StopAsyncIteration:
                pending = None
                return
            pending = None
            yield event.to_frame()
    finally:
        # 클라이언트 단절(GeneratorExit) 시 소스 제너레이터도 함께 닫는다 —
        # 종전의 단순 async-for 와 동일한 취소 전파를 유지한다.
        if pending is not None:
            pending.cancel()
            try:
                await pending
            except BaseException:  # noqa: BLE001 — CancelledError/StopAsyncIteration 등
                pass
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except BaseException:  # noqa: BLE001
                pass
