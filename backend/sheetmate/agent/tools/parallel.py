"""Conservative batching for independent, explicitly trusted workspace reads."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from dataclasses import dataclass, replace
from typing import Any

from sheetmate.agent.tools.contracts import AgentContext, Tool, ToolResult, validate_tool_args
from sheetmate.agent.tools.execution import execute_tool
from sheetmate.agent.framework.shared.watchdog import track_tool_workers

PARALLEL_READ_TOOLS = frozenset({"table_info", "value_counts", "column_stats", "preview_table"})


def read_resource(call: dict[str, Any], tools: dict[str, Tool], ctx: AgentContext) -> str | None:
    """Unknown tools, schemas and resources always retain serial behavior."""
    tool = tools.get(call["function"]["name"])
    if (tool is None or tool.name not in PARALLEL_READ_TOOLS or not tool.parallel_read_safe
            or tool.stream is not None or tool.run is None):
        return None
    try:
        args = validate_tool_args(tool, json.loads(call["function"]["arguments"]))
        table = args.get("table")
        if (not isinstance(table, str) or not table or not ctx.ws.exists(table)
                or ctx.table_filter is not None and table not in ctx.table_filter):
            return None
    except (ValueError, TypeError, KeyError):
        return None
    return table


def independent_batch(calls: list[dict[str, Any]], start: int, tools: dict[str, Tool],
                      ctx: AgentContext, maximum: int) -> list[dict[str, Any]]:
    """Only adjacent reads of distinct tables can share a batch; never reorder."""
    batch, resources = [], set()
    for call in calls[start:start + max(0, maximum)]:
        resource = read_resource(call, tools, ctx)
        if resource is None or resource in resources:
            break
        resources.add(resource)
        batch.append(call)
    return batch


@dataclass
class ParallelOutcome:
    call_id: str
    result: ToolResult
    events: list[dict[str, Any]]
    duration_ms: int


async def run_parallel_tools(calls: list[dict[str, Any]], tools: dict[str, Tool], ctx: AgentContext,
                             on_result: Callable[[ParallelOutcome], Awaitable[None]]) -> None:
    """Settle every task and real worker before returning, including on cancellation.

    The owner invokes durable callbacks serially as results arrive. Completed
    siblings are still delivered during cancellation cleanup, before ownership
    of their workspace can pass to another request.
    """
    workers: set[Future[Any]] = set()

    async def execute(call: dict[str, Any]) -> ParallelOutcome:
        started = time.perf_counter()
        events, result = [], None
        worker_ctx = replace(ctx, parallel_read_only=True)
        async for item in execute_tool(tools[call["function"]["name"]],
                                       json.loads(call["function"]["arguments"]), worker_ctx,
                                       call_id=call["id"]):
            if isinstance(item, ToolResult):
                result = item
            else:
                events.append({**item, "tool_call_id": call["id"]})
        assert result is not None
        return ParallelOutcome(call["id"], result, events, int((time.perf_counter() - started) * 1000))

    with track_tool_workers(workers):
        tasks = [asyncio.create_task(execute(call)) for call in calls]
        delivered: set[str] = set()

        async def deliver(outcome: ParallelOutcome) -> None:
            if outcome.call_id not in delivered:
                delivered.add(outcome.call_id)
                await on_result(outcome)

        async def cleanup() -> None:
            for task in tasks:
                if not task.done():
                    task.cancel()
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            while workers:
                current = list(workers)
                await asyncio.gather(*(asyncio.wrap_future(worker) for worker in current), return_exceptions=True)
                workers.difference_update(worker for worker in current if worker.done())
            for outcome in outcomes:
                if isinstance(outcome, ParallelOutcome):
                    await deliver(outcome)

        try:
            failure: BaseException | None = None
            for task in asyncio.as_completed(tasks):
                try:
                    outcome = await task
                except Exception as exc:
                    failure = failure or exc
                else:
                    await deliver(outcome)
            if failure is not None:
                raise failure
        finally:
            fence = asyncio.create_task(cleanup())
            cancelled = False
            while not fence.done():
                try:
                    await asyncio.shield(fence)
                except asyncio.CancelledError:
                    cancelled = True
            fence.result()
            if cancelled:
                raise asyncio.CancelledError()
