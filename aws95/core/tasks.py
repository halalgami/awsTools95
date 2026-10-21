"""Background work on a QThreadPool; results come back to the GUI thread via queued signals.

A worker emits the private _deliver signal. It is connected to a slot on the handle, which
lives in the GUI thread, so Qt queues the call. Public signals are therefore only ever emitted
on the GUI thread, and lambdas connected to them run there.
"""
from __future__ import annotations

import inspect
import os
import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot

from aws95.core.cancel import CancelToken


def _accepts_cancel(fn: Callable[..., Any]) -> bool:
    """The runner passes cancel=token only to callables that declare a `cancel` parameter."""
    try:
        return "cancel" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


class TaskHandle(QObject):
    finished = Signal(object)
    failed = Signal(str, str)
    settled = Signal()
    _deliver = Signal(bool, object, str, str)

    def __init__(self, token: CancelToken) -> None:
        super().__init__()
        self.token = token
        self.done = False
        self._deliver.connect(self._on_deliver)

    def cancel(self) -> None:
        self.token.cancel()

    @Slot(bool, object, str, str)
    def _on_deliver(self, ok: bool, result: object, message: str, tb: str) -> None:
        self.done = True
        if not self.token.cancelled:
            if ok:
                self.finished.emit(result)
            else:
                self.failed.emit(message, tb)
        self.settled.emit()


class _Job(QRunnable):
    def __init__(self, handle: TaskHandle, fn: Callable[..., Any], args: tuple, kwargs: dict) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._handle = handle
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        handle = self._handle
        self._handle = None  # the last reference is then released on the GUI thread
        if handle is None:
            return
        payload: tuple[bool, object, str, str]
        if handle.token.cancelled:
            payload = (False, None, "cancelled", "")
        else:
            try:
                payload = (True, self._fn(*self._args, **self._kwargs), "", "")
            except BaseException as exc:  # noqa: BLE001 - every failure must reach the UI
                try:
                    payload = (False, None, f"{type(exc).__name__}: {exc}", traceback.format_exc())
                except BaseException:  # noqa: BLE001 - formatting must never block delivery
                    payload = (False, None, type(exc).__name__, "")
        self._fn = self._args = self._kwargs = None  # type: ignore[assignment]
        handle._deliver.emit(*payload)


class TaskRunner(QObject):
    active_changed = Signal(int)

    def __init__(self, max_threads: int | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max_threads or max(4, min(16, (os.cpu_count() or 4) * 2)))
        self._handles: set[TaskHandle] = set()
        # Not QThread.currentThread() vs self.thread(): QObject.thread() trips shiboken's return-value
        # heuristic and parents the GUI thread's single QThread wrapper to this short-lived runner
        # (segv-report.md). A thread ident compares the same thing without touching that wrapper.
        self._owner_ident = threading.get_ident()
        self.closed = False
        self.dropped = 0

    @property
    def active_count(self) -> int:
        return len(self._handles)

    def submit(self, fn: Callable[..., Any], *args: Any, cancel: CancelToken | None = None, **kwargs: Any) -> TaskHandle:
        """Start fn(*args, **kwargs) in the pool. Must be called from the thread that owns the runner
        (the GUI thread), because the handle takes that thread's affinity for result delivery."""
        if threading.get_ident() != self._owner_ident:
            raise RuntimeError("TaskRunner.submit must be called from the thread that owns the runner")
        token = cancel if cancel is not None else CancelToken()
        if self.closed:
            # Teardown: windows' first-load timers may still fire. Hand back a handle that never runs or delivers
            # (raising here was measured to crash teardown - plan rev 2, review C2).
            # A view that loads after shutdown stays on "Reading..."; acceptable only because this is teardown.
            token.cancel()
            dead = TaskHandle(token)
            dead.done = True
            self.dropped += 1
            return dead
        handle = TaskHandle(token)
        if _accepts_cancel(fn):
            kwargs.setdefault("cancel", token)
        self._handles.add(handle)
        handle.settled.connect(self._on_settled)
        self.active_changed.emit(len(self._handles))
        self._pool.start(_Job(handle, fn, args, kwargs))
        return handle

    @Slot()
    def _on_settled(self) -> None:
        handle = self.sender()
        self._handles.discard(handle)  # type: ignore[arg-type]
        self.active_changed.emit(len(self._handles))

    def shutdown(self, timeout_ms: int = 3000) -> bool:
        """Refuse new work, drop queued jobs that have not started, cancel running ones, then wait."""
        self.closed = True
        self._pool.clear()
        for handle in list(self._handles):
            handle.cancel()
        self._handles.clear()               # cleared jobs never settle; running ones settle later and discard() is a no-op
        self.active_changed.emit(0)
        return self._pool.waitForDone(timeout_ms)


class CancelScope:
    """Groups the tasks a window starts so closing the window cancels them all."""

    def __init__(self) -> None:
        self._handles: list[TaskHandle] = []

    @property
    def pending(self) -> int:
        return len(self._handles)

    def track(self, handle: TaskHandle) -> TaskHandle:
        self._handles = [h for h in self._handles if not h.done]
        self._handles.append(handle)
        # Drop it once it settles (M3-R7). Not inside the settled emission itself: releasing the last Python
        # reference there deletes the handle while Qt is still emitting from it (a use-after-free).
        handle.settled.connect(self._schedule_prune)
        return handle

    def _schedule_prune(self) -> None:
        QTimer.singleShot(0, self._prune)

    def _prune(self) -> None:
        self._handles = [h for h in self._handles if not h.done]

    def cancel_all(self) -> None:
        for handle in self._handles:
            handle.cancel()
        self._handles.clear()
