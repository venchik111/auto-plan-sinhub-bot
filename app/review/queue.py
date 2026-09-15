from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any


log = logging.getLogger(__name__)
Worker = Callable[[str, str], Awaitable[None]]


class ReviewQueue:
    """Sequential in-process queue: one OpenCode call at a time.

    Not thread-safe: enqueue from the event loop thread only.
    """

    def __init__(self, worker: Worker, autostart: bool = True):
        self._worker = worker
        self._autostart = autostart
        self._pending: deque[tuple[str, str]] = deque()
        self._current: tuple[str, str] | None = None
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._progress: dict[str, Any] = {"week": None, "total": 0, "done": 0, "failed": 0}

    @property
    def busy(self) -> bool:
        return bool(self._pending) or self._current is not None

    def enqueue_group(self, week_label: str, students: list[str]) -> int:
        keys = [
            (student, week_label)
            for student in dict.fromkeys(students)
            if (student, week_label) not in self._pending and (student, week_label) != self._current
        ]
        if keys:
            self._start_batch(week_label, len(keys))
            self._pending.extend(keys)
            self._notify()
        return len(keys)

    def enqueue_student(self, student: str, week_label: str) -> None:
        key = (student, week_label)
        if key == self._current:
            return
        if key in self._pending:
            self._pending.remove(key)
        else:
            self._start_batch(week_label, 1)
        self._pending.appendleft(key)
        self._notify()

    def state_of(self, student: str, week_label: str) -> str | None:
        key = (student, week_label)
        if key == self._current:
            return "running"
        if key in self._pending:
            return "queued"
        return None

    def progress(self) -> dict[str, Any]:
        return {
            **self._progress,
            "current": self._current[0] if self._current else None,
            "queued": len(self._pending),
            "busy": self.busy,
        }

    async def run_pending(self) -> None:
        while self._pending:
            self._current = self._pending.popleft()
            try:
                await self._worker(*self._current)
            except Exception:
                log.exception("Анализ плана %s за %s не удался", *self._current)
                self._progress["failed"] += 1
            finally:
                self._progress["done"] += 1
                self._current = None

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _start_batch(self, week_label: str, added: int) -> None:
        if not self.busy:
            self._progress = {"week": week_label, "total": 0, "done": 0, "failed": 0}
        self._progress["week"] = week_label
        self._progress["total"] += added

    def _notify(self) -> None:
        self._wakeup.set()
        if self._autostart and self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            await self._wakeup.wait()
            self._wakeup.clear()
            await self.run_pending()
