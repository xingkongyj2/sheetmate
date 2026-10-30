"""后台任务：对话、Recipe 执行这类耗时操作放在后台协程里跑，事件写进队列，SSE 接口从队列读。

好处：浏览器刷新或关闭页面，任务也会跑完并把结果存下来；同一个会话同时只允许一个任务。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sheetmate.services.errors import Conflict

_END = object()


@dataclass
class Job:
    key: str
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    task: asyncio.Task | None = None
    finished: bool = False

    async def emit(self, event: dict[str, Any]) -> None:
        await self.queue.put(event)

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            item = await self.queue.get()
            if item is _END:
                return
            yield item


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def running(self, key: str) -> bool:
        job = self._jobs.get(key)
        return job is not None and not job.finished

    def start(self, key: str, work: Callable[[Job], Awaitable[None]]) -> Job:
        if self.running(key):
            raise Conflict("上一个任务还在处理中，请稍等它完成")
        job = Job(key)

        async def runner() -> None:
            try:
                await work(job)
            except asyncio.CancelledError:
                await job.emit({"type": "error", "content": "已停止"})
            except Exception as exc:  # 兜底，保证前端能收到结束信号
                await job.emit({"type": "error", "content": f"处理出错：{exc}"})
            finally:
                job.finished = True
                await job.queue.put(_END)
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
            job.task.cancel()
        return True


jobs = JobManager()
