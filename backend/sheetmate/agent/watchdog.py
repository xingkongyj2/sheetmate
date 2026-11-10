"""Waiting clocks belong to a specific operation, including concurrent branches."""

from __future__ import annotations

import asyncio
import contextvars
from concurrent.futures import Future
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar
from uuid import uuid4

from sheetmate.conversation_log import record_event

T = TypeVar("T")
_EVENT_SINK: contextvars.ContextVar[Callable[[dict[str, Any]], Awaitable[None]] | None] = contextvars.ContextVar(
    "sheetmate_watchdog_sink", default=None)
_PHASE: contextvars.ContextVar[Phase | None] = contextvars.ContextVar("sheetmate_wait_phase", default=None)
_WORKERS: contextvars.ContextVar[set[Future[Any]] | None] = contextvars.ContextVar("sheetmate_tool_workers", default=None)


class WatchdogTimeout(TimeoutError):
    def __init__(self, phase: str, owner: str, seconds: float, code: str):
        self.phase, self.owner, self.seconds, self.code = phase, owner, seconds, code
        label = "模型回复流" if phase == "streaming_llm" else "模型" if phase == "awaiting_llm" else "工具"
        super().__init__(f"{label}等待超过 {seconds:g} 秒，已停止本次操作并保留已有结果。")


@dataclass
class Phase:
    name: str
    owner: str
    id: str = field(default_factory=lambda: uuid4().hex)
    started: float = field(default_factory=time.monotonic)
    soft_reported: bool = False
    model_waits: int = 0
    model_wait_started: float = 0
    model_wait_total: float = 0

    def borrowed_seconds(self) -> float:
        return self.model_wait_total + (time.monotonic() - self.model_wait_started if self.model_waits else 0)


@contextmanager
def phase(name: str, owner: str, *, state: Phase | None = None) -> Iterator[Phase]:
    """Nested model work borrows its own clock and restores its tool owner."""
    item = state or Phase(name, owner)
    parent = _PHASE.get()
    borrowed = parent if parent is not None and parent.name == "executing_tool" and name in {
        "awaiting_llm", "streaming_llm", "retrying_llm"} else None
    if borrowed is not None:
        if not borrowed.model_waits:
            borrowed.model_wait_started = time.monotonic()
        borrowed.model_waits += 1
    token = _PHASE.set(item)
    try:
        yield item
    finally:
        _PHASE.reset(token)
        if borrowed is not None:
            borrowed.model_waits -= 1
            if not borrowed.model_waits:
                borrowed.model_wait_total += time.monotonic() - borrowed.model_wait_started


@contextmanager
def watchdog_events(sink: Callable[[dict[str, Any]], Awaitable[None]]) -> Iterator[None]:
    """Bind a task's event stream without sharing mutable state across jobs."""
    token = _EVENT_SINK.set(sink)
    try:
        yield
    finally:
        _EVENT_SINK.reset(token)


def current_phase() -> Phase | None:
    return _PHASE.get()


@contextmanager
def track_tool_workers(workers: set[Future[Any]]) -> Iterator[None]:
    """Keep a job fenced until every side-effect-capable worker really exits.

    A timed-out native call can outlive its async waiter. Its job must retain
    ownership of the session and cleanup resources until this set is empty.
    """
    token = _WORKERS.set(workers)
    try:
        yield
    finally:
        _WORKERS.reset(token)


def register_tool_worker(worker: Future[Any]) -> None:
    workers = _WORKERS.get()
    if workers is not None:
        workers.add(worker)
        loop = asyncio.get_running_loop()
        worker.add_done_callback(lambda result: loop.call_soon_threadsafe(workers.discard, result))


async def wait_tool(task: asyncio.Future[Any], item: Phase, timeout: float) -> bool:
    """A streaming tool's nested model wait is owned by the model watchdog."""
    started, borrowed = time.monotonic(), item.borrowed_seconds()
    while not task.done():
        elapsed = time.monotonic() - started - (item.borrowed_seconds() - borrowed)
        remaining = timeout - max(0, elapsed)
        if remaining <= 0:
            return False
        await asyncio.wait({task}, timeout=min(remaining, 0.1))
    return True


async def report_cleanup(item: Phase) -> None:
    event = {"type": "activity", "text": "正在等待已停止的工具清理资源，清理结束后才能继续处理。",
             "code": "tool_cleanup_pending", "phase": "cleaning_tool", "phase_id": item.id,
             "owner": item.owner, "severity": "warning"}
    record_event("watchdog.cleanup_pending", "sheetmate.agent.watchdog", event)
    sink = _EVENT_SINK.get()
    if sink is not None:
        await sink(event)


async def report_wait(item: Phase, *, elapsed: float, hard: bool = False, code: str = "") -> None:
    event = {"type": "activity", "text": "模型暂未返回新内容，仍在等待。" if not hard else "等待已超时，正在清理本次操作。",
             "code": code or "watchdog_soft_idle", "phase": item.name, "phase_id": item.id,
             "owner": item.owner, "idle_seconds": round(elapsed, 3), "severity": "error" if hard else "warning"}
    record_event("watchdog.timeout" if hard else "watchdog.soft_idle", "sheetmate.agent.watchdog", event)
    sink = _EVENT_SINK.get()
    if sink is not None:
        try:
            await sink(event)
        except Exception:
            # Observability must never own the operation or replace its outcome.
            record_event("watchdog.sink_error", "sheetmate.agent.watchdog", {"phase_id": item.id})


async def cancel_wait(task: asyncio.Future[Any], grace: float) -> bool:
    """Bound provider cleanup. Tool callers must fence/terminate before using this."""
    if task.done():
        return True
    task.cancel()
    done, _ = await asyncio.wait({task}, timeout=grace)
    if not done:
        # Consume a late transport failure without pretending the task finished.
        task.add_done_callback(lambda value: value.exception() if not value.cancelled() else None)
        return False
    if not task.cancelled():
        task.exception()
    return True


async def wait_owned(request: Awaitable[T], *, item: Phase, hard_seconds: float | None,
                     soft_seconds: float = 0, code: str = "llm_idle_timeout", grace: float = 5) -> T:
    """A warning never extends the owner's hard deadline or another owner's clock."""
    task = asyncio.ensure_future(request)
    started = time.monotonic()
    try:
        if soft_seconds > 0 and not item.soft_reported and (hard_seconds is None or soft_seconds < hard_seconds):
            done, _ = await asyncio.wait({task}, timeout=soft_seconds)
            if done:
                return task.result()
            item.soft_reported = True
            await report_wait(item, elapsed=time.monotonic() - started)
        remaining = max(0.0, hard_seconds - (time.monotonic() - started)) if hard_seconds is not None else None
        done, _ = await asyncio.wait({task}, timeout=remaining)
        if done:
            return task.result()
        elapsed = time.monotonic() - started
        await report_wait(item, elapsed=elapsed, hard=True, code=code)
        await cancel_wait(task, grace)
        raise WatchdogTimeout(item.name, item.owner, hard_seconds or elapsed, code)
    except asyncio.CancelledError:
        await cancel_wait(task, grace)
        raise
