"""App-wide idle detection for the screensaver (never over a modal; 0 = never)."""
from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from aws95.core.settings import Settings

_INPUT = {QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.Wheel,
          QEvent.Type.TouchBegin, QEvent.Type.TabletPress}


class IdleMonitor(QObject):
    idle = Signal()

    def __init__(self, settings: Settings, clock: Callable[[], float] = time.monotonic, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._clock = clock
        self._last = clock()
        self._fired = False
        self._inhibited = False
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.check)

    def start(self, app: QApplication) -> None:
        app.installEventFilter(self)
        self._timer.start()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() in _INPUT:
            self.poke()
        return False

    def poke(self) -> None:
        self._last = self._clock()

    def set_inhibited(self, on: bool) -> None:
        self._inhibited = on

    def rearm(self) -> None:
        self._fired = False
        self.poke()

    def check(self) -> None:
        timeout = self._settings.saver_timeout_min
        if timeout <= 0 or self._inhibited or self._fired:
            return
        if QApplication.activeModalWidget() is not None:
            return
        if self._clock() - self._last >= timeout * 60:
            self._fired = True
            self.idle.emit()
