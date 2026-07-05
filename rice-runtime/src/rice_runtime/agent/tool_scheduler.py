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


# 한 배치에서 동시에 실행할 워커 상한 — 모델이 한 라운드에 도구 호출을 수십 개
# 쏟아내도 태스크가 무제한 생성되지 않게 한다(per-batch semaphore).
_MAX_PARALLEL_TOOLS = 5


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
            # 단일 호출도 워커 태스크 경로를 태운다 — 이전의 인라인 `await dispatch`
            # 경로는 실행 **도중** abort 를 감시하지 않아, 장시간 도구 하나가 턴
            # abort/drain 을 무기한 막았다(적대 리뷰 P0#3). 워커 경로는 abort 시
            # task.cancel() 로 실행 자체를 끊는다.
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
        semaphore = asyncio.Semaphore(_MAX_PARALLEL_TOOLS)

        async def worker(index: int, req: ScheduledToolRequest) -> None:
            try:
                async with semaphore:
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
