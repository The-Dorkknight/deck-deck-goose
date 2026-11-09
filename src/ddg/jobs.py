# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""One long-running job at a time (move a game, clean junk, compress),
with progress and cancel. Used by the window and the command line."""
from __future__ import annotations

import threading
import time
import traceback
from typing import Callable, List, Optional

from .core import Step


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, title: str, on_progress: Optional[Callable[["Job"], None]] = None):
        self.id = str(int(time.time() * 1000))
        self.title = title
        self.state = "running"          # running / done / failed / cancelled
        self.done = 0
        self.total = 0
        self.message = "Starting..."
        self.steps: List[Step] = []
        self.cancel_event = threading.Event()
        self.on_progress = on_progress
        self._last_emit = 0.0

    def progress(self, done: Optional[int] = None, total: Optional[int] = None,
                 message: Optional[str] = None) -> None:
        if done is not None:
            self.done = done
        if total is not None:
            self.total = total
        if message is not None:
            self.message = message
        now = time.time()
        if self.on_progress and (now - self._last_emit > 0.5 or message is not None):
            self._last_emit = now
            self.on_progress(self)

    def check(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()

    def as_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "state": self.state,
                "done": self.done, "total": self.total, "message": self.message,
                "steps": [s.as_dict() for s in self.steps]}


def run_now(job: Job, fn: Callable[[Job], List[Step]]) -> Job:
    """Run fn(job) in this thread and fill in the job's final state."""
    try:
        job.steps = fn(job)
        job.state = "done" if all(s.ok for s in job.steps) else "failed"
        if job.state == "done":
            job.message = "Finished"
        else:
            job.message = next((s.detail for s in job.steps if not s.ok), "Failed")
    except Cancelled:
        job.state = "cancelled"
        job.message = "Cancelled. Nothing was changed."
    except Exception as e:  # never leave the window hanging
        job.state = "failed"
        job.message = "Error: %s" % e
        job.steps.append(Step("Unexpected error", False, traceback.format_exc(limit=3)))
    return job


class Runner:
    def __init__(self) -> None:
        self.current: Optional[Job] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def busy(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, title: str, fn: Callable[[Job], List[Step]]) -> Job:
        with self._lock:
            if self.busy():
                raise RuntimeError("Another job is still running: %s" % self.current.title)
            job = Job(title)
            self.current = job
            self._thread = threading.Thread(target=run_now, args=(job, fn), daemon=False)
            self._thread.start()
            return job

    def cancel(self) -> None:
        if self.current:
            self.current.cancel_event.set()

    def wait(self, timeout: Optional[float] = None) -> None:
        if self._thread:
            self._thread.join(timeout)


RUNNER = Runner()
