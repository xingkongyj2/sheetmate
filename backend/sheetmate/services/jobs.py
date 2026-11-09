"""后台任务：对话、Recipe 执行这类耗时操作放在后台协程里跑，SSE 可回放事件并持续订阅。

好处：浏览器刷新或关闭页面，任务也会跑完并把结果存下来；同一个会话同时只允许一个任务。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from sheetmate.conversation_log import ConversationTrace, current_trace, error_data, record_event
from sheetmate.services.errors import Conflict


@dataclass
class Job:
    key: str
    id: str = field(default_factory=lambda: uuid4().hex)
    message_id: int | None = None
    task: asyncio.Task | None = None
    finished: bool = False
    started: bool = False
    cancel_source: str | None = None
    trace: ConversationTrace | None = None
    _history: list[dict[str, Any]] = field(default_factory=list)
    _changed: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def emit(self, event: dict[str, Any]) -> None:
        async with self._changed:
            self._history.append(event)
            self._changed.notify_all()

    async def finish(self) -> None:
        async with self._changed:
            self.finished = True
            self._changed.notify_all()

    async def events(self, *, snapshot: bool = False) -> AsyncIterator[dict[str, Any]]:
        """订阅任务；恢复页面时把已有事件一次性发出，再继续实时事件。

        快照和续传游标在同一把锁里取得，避免快照之后、订阅之前产生的事件丢失或重复。
        """
        cursor = 0
        if snapshot:
            async with self._changed:
                history = list(self._history)
                cursor = len(history)
            yield {"type": "stream_snapshot", "events": history}
        while True:
            async with self._changed:
                await self._changed.wait_for(lambda: cursor < len(self._history) or self.finished)
                if cursor == len(self._history):
                    return
                event = self._history[cursor]
                cursor += 1
            yield event


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def running(self, key: str) -> bool:
        return self.get(key) is not None

    def get(self, key: str) -> Job | None:
        job = self._jobs.get(key)
        return job if job is not None and not job.finished else None

    def start(self, key: str, work: Callable[[Job], Awaitable[None]], *, message_id: int | None = None) -> Job:
        if self.running(key):
            raise Conflict("上一个任务还在处理中，请稍等它完成")
        job = Job(key, message_id=message_id, trace=current_trace())

        async def runner() -> None:
            job.started = True
            status = "completed"
            try:
                # A just-created task cancelled before its first instruction never
                # runs finally. Let it enter the runner to persist/log the stop.
                if job.cancel_source is not None:
                    raise asyncio.CancelledError
                await work(job)
            except asyncio.CancelledError:
                status = "cancelled" if job.cancel_source == "user" else "interrupted"
                record_event("job.cancelled", "sheetmate.services.jobs.JobManager.runner",
                             {"status": status, "source": job.cancel_source or "system"})
                code = "user_stop" if job.cancel_source == "user" else "system_cancelled"
                summary = "已停止" if job.cancel_source == "user" else "执行被系统中断"
                needs_error = job.message_id is None
                if job.message_id is not None:
                    from sheetmate.services.chat import persist_unfinished_job

                    event = persist_unfinished_job(job, status=status, code=code, summary=summary)
                    if event:
                        await job.emit(event)
                        needs_error = True
                    else:
                        final = next((event for event in reversed(job._history) if event.get("type") == "execution_status"), {})
                        needs_error = final.get("status") in {"cancelled", "interrupted"} and not any(
                            event.get("type") == "error" and event.get("code") == code for event in job._history
                        )
                if needs_error:
                    await job.emit({"type": "error", "content": summary, "code": code})
            except Exception as exc:  # 兜底，保证前端能收到结束信号
                status = "failed"
                record_event("job.error", "sheetmate.services.jobs.JobManager.runner", error_data(exc))
                summary = f"处理出错：{exc}"
                code = getattr(exc, "code", "execution_error")
                needs_error = job.message_id is None
                if job.message_id is not None:
                    from sheetmate.services.chat import persist_unfinished_job

                    event = persist_unfinished_job(job, status="failed", code=code, summary=summary)
                    if event:
                        await job.emit(event)
                        needs_error = True
                if needs_error:
                    await job.emit({"type": "error", "content": summary, "code": code})
            finally:
                await job.finish()
                execution = next((event for event in reversed(job._history)
                                  if event.get("type") == "execution_status"), {})
                record_event("turn.end", "sheetmate.services.jobs.JobManager.runner",
                             {"job_id": job.id, "request_message_id": job.message_id,
                              "status": execution.get("status", status),
                              "code": execution.get("code"), "summary": execution.get("summary"),
                              "cancel_source": job.cancel_source})
                if self._jobs.get(key) is job:
                    del self._jobs[key]

        job.task = asyncio.create_task(runner())
        self._jobs[key] = job
        return job

    def stop(self, key: str) -> bool:
        job = self._jobs.get(key)
        if job is None or job.task is None or job.finished:
            return False
        if not job.task.cancelling():
            job.cancel_source = "user"
            if job.started:
                job.task.cancel()
        return True

    async def stop_and_wait(self, keys: list[str]) -> None:
        """Stop active scoped jobs and wait until they can no longer write resources."""
        tasks = []
        for key in set(keys):
            job = self._jobs.get(key)
            if job is None or job.finished or job.task is None:
                continue
            self.stop(key)
            tasks.append(job.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def shutdown(self) -> None:
        tasks = []
        for job in list(self._jobs.values()):
            if job.task is not None and not job.task.done():
                job.cancel_source = job.cancel_source or "shutdown"
                if job.started:
                    job.task.cancel()
                tasks.append(job.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


jobs = JobManager()
