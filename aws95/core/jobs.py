"""One long local job at a time (findings-tools spec §2): a snapshot or a findings backfill. Qt-free."""
from __future__ import annotations

import threading

SNAPSHOT_RUNNING = "A snapshot is running. Try again when it finishes."
BACKFILL_RUNNING = "Backfill is running. Try again when it finishes."


class JobLock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.holder: str | None = None

    def try_acquire(self, name: str) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        self.holder = name
        return True

    def release(self) -> None:
        self.holder = None
        self._lock.release()

    def busy_message(self) -> str:
        return BACKFILL_RUNNING if self.holder == "backfill" else SNAPSHOT_RUNNING
