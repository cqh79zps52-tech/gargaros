"""In-memory job queue + correlation for the browser-extension bridge.

Flow:
  1. An HTTP request arrives at a /browser/* route. The route calls
     `BrowserBridge.dispatch(op, args, timeout=30)`. This enqueues a job
     and returns an awaitable that completes when the extension posts back.
  2. The extension long-polls `GET /browser/_pull`. Each call returns at
     most one job; if the queue is empty it waits up to N seconds before
     returning 204.
  3. The extension executes the job in the tab and POSTs the result to
     `/browser/_result` with the same job_id. The bridge resolves the
     awaitable.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Job:
    op: str
    args: dict[str, Any]
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)


class BrowserBridge:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._waiters: dict[str, asyncio.Future[Any]] = {}

    async def dispatch(self, op: str, args: dict[str, Any], timeout: float = 30.0) -> Any:
        loop = asyncio.get_running_loop()
        job = Job(op=op, args=args)
        fut: asyncio.Future[Any] = loop.create_future()
        self._waiters[job.job_id] = fut
        await self._queue.put(job)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._waiters.pop(job.job_id, None)

    async def pull(self, wait: float = 25.0) -> Job | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=wait)
        except TimeoutError:
            return None

    def deliver(self, job_id: str, result: Any = None, error: str | None = None) -> bool:
        fut = self._waiters.get(job_id)
        if fut is None or fut.done():
            return False
        if error is not None:
            fut.set_exception(RuntimeError(error))
        else:
            fut.set_result(result)
        return True

    @property
    def pending(self) -> int:
        return len(self._waiters)
