"""Background work: ranking and building take minutes, so they run in a
separate thread while the server keeps answering the dashboard.

At most one task of each kind runs per profile at a time, and every task can
be asked to stop. A task checks `stop.is_set()` between jobs and finishes
the job it is on, so nothing is left half-written.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger("roleradar")


class AlreadyRunning(Exception):
    pass


@dataclass
class _Task:
    thread: threading.Thread
    stop: threading.Event


class Workers:
    def __init__(self) -> None:
        self._tasks: dict[tuple[str, str], _Task] = {}
        self._lock = threading.Lock()

    def is_running(self, profile: str, kind: str) -> bool:
        task = self._tasks.get((profile, kind))
        return bool(task and task.thread.is_alive())

    def start(self, profile: str, kind: str, target: Callable[[threading.Event], None]) -> None:
        with self._lock:
            if self.is_running(profile, kind):
                raise AlreadyRunning(f"A {kind} run is already in progress for {profile}")
            stop = threading.Event()

            def run() -> None:
                try:
                    target(stop)
                except Exception:  # the task records its own failure; this is a last resort
                    log.exception("%s task for %s crashed", kind, profile)

            thread = threading.Thread(target=run, name=f"{kind}-{profile}", daemon=True)
            self._tasks[(profile, kind)] = _Task(thread, stop)
            thread.start()

    def stop(self, profile: str, kind: str) -> bool:
        task = self._tasks.get((profile, kind))
        if task and task.thread.is_alive():
            task.stop.set()
            return True
        return False

    def wait(self, profile: str, kind: str, timeout: float | None = None) -> None:
        """Used by tests: block until the task finishes."""
        task = self._tasks.get((profile, kind))
        if task:
            task.thread.join(timeout)
