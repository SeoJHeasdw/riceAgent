"""Tool-call scheduling for one assistant tool-use round.

Gemini CLI uses a full event-driven scheduler with a state manager, queue,
parallelizable batches, approval waiting, and cancellation. RICE's dispatcher
already owns policy/HITL/routing, so this scheduler focuses on the missing
orchestration layer:

* group contiguous safe calls into a parallel batch;
* keep approval/destructive calls sequential;
* surface each dispatch event as it happens;
* collect the results in model-call order for the next LLM iteration;
* cancel queued/running worker tasks when the turn aborts.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass, field

from ..core.types import SSEEvent, ToolCall, ToolResult, ToolSpec
from .interfaces import IToolDispatcher


@dataclass(frozen=True)
class ScheduledToolRequest:
    call: ToolCall
    spec: ToolSpec


@dataclass
class ToolBatchResult:
    results: dict[str, ToolResult] = field(default_factory=dict)

    def ordered(self, calls: Sequence[ToolCall]) -> tuple[ToolResult, ...]:
        return tuple(
            self.results.get(call.id, ToolResult(call.id, ok=False, error="tool did not complete"))
            for call in calls
        )


class ToolScheduler:
    def __init__(self, dispatcher: IToolDispatcher) -> None:
        self._dispatcher = dispatcher

    async def schedule(
        self,
        session_id: str,
        requests: Sequence[ScheduledToolRequest],
        result: ToolBatchResult,
        abort: asyncio.Event,
    ) -> AsyncGenerator[SSEEvent, None]:
        for batch in _batches(requests):
            if abort.is_set():
                for req in batch:
                    result.results[req.call.id] = ToolResult(req.call.id, ok=False, error="tool cancelled")
                return

            if len(batch) == 1:
                req = batch[0]
                async for event in self._dispatcher.dispatch(session_id, req.call, req.spec):
                    yield event
                result.results[req.call.id] = await self._dispatcher.result_for(req.call)
                continue

            async for event in self._run_parallel_batch(session_id, batch, result, abort):
                yield event

    async def _run_parallel_batch(
        self,
        session_id: str,
        batch: Sequence[ScheduledToolRequest],
        result: ToolBatchResult,
        abort: asyncio.Event,
    ) -> AsyncGenerator[SSEEvent, None]:
        queue: asyncio.Queue[tuple[str, int, SSEEvent | ToolResult]] = asyncio.Queue()

        async def worker(index: int, req: ScheduledToolRequest) -> None:
            try:
                async for event in self._dispatcher.dispatch(session_id, req.call, req.spec):
                    await queue.put(("event", index, event))
                await queue.put(("result", index, await self._dispatcher.result_for(req.call)))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # defensive: dispatcher normally converts failures to ToolResult
                await queue.put(("result", index, ToolResult(req.call.id, ok=False, error=str(exc))))

        tasks = [asyncio.create_task(worker(i, req)) for i, req in enumerate(batch)]
        pending = len(tasks)
        try:
            while pending:
                if abort.is_set():
                    for task in tasks:
                        task.cancel()
                    for req in batch:
                        result.results.setdefault(req.call.id, ToolResult(req.call.id, ok=False, error="tool cancelled"))
                    return
                queue_task = asyncio.create_task(queue.get())
                abort_task = asyncio.create_task(abort.wait())
                done, pending_tasks = await asyncio.wait(
                    {queue_task, abort_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if abort_task in done:
                    queue_task.cancel()
                    await asyncio.gather(queue_task, return_exceptions=True)
                    for task in tasks:
                        task.cancel()
                    for req in batch:
                        result.results.setdefault(req.call.id, ToolResult(req.call.id, ok=False, error="tool cancelled"))
                    return
                abort_task.cancel()
                await asyncio.gather(*pending_tasks, return_exceptions=True)
                kind, index, item = queue_task.result()
                if kind == "event":
                    yield item  # type: ignore[misc]
                else:
                    req = batch[index]
                    result.results[req.call.id] = item  # type: ignore[assignment]
                    pending -= 1
        finally:
            await asyncio.gather(*tasks, return_exceptions=True)


def _batches(requests: Sequence[ScheduledToolRequest]) -> list[tuple[ScheduledToolRequest, ...]]:
    batches: list[tuple[ScheduledToolRequest, ...]] = []
    current: list[ScheduledToolRequest] = []
    for req in requests:
        if _is_parallelizable(req):
            current.append(req)
            continue
        if current:
            batches.append(tuple(current))
            current = []
        batches.append((req,))
    if current:
        batches.append(tuple(current))
    return batches


def _is_parallelizable(req: ScheduledToolRequest) -> bool:
    if req.spec.requires_approval:
        return False
    if req.call.arguments.get("wait_for_previous") is True:
        return False
    # These builtin names either mutate state or intentionally stage user-visible
    # effects. Keep them ordered even if they do not require the first HITL gate.
    if req.call.tool_name in {"write_file", "terminal", "propose_skill"}:
        return False
    return True
