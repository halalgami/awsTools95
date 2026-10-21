"""Saver base: an offscreen canvas advanced by step(), painted by paintEvent, timer only while visible."""
from __future__ import annotations

import random

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QWidget


class Saver(QWidget):
    name = ""
    title = ""

    def __init__(self, parent: QWidget | None = None, seed: int | None = None) -> None:
        super().__init__(parent)
        self.rng = random.Random(seed)
        self.canvas = QImage(1, 1, QImage.Format.Format_RGB32)
        self.canvas.fill(QColor("#000000"))
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.tick)
        self.reset()  # so step() is safe before the widget is ever shown

    def is_running(self) -> bool:
        return self._timer.isActive()

    def reset(self) -> None:
        w, h = max(1, self.width()), max(1, self.height())
        self.canvas = QImage(w, h, QImage.Format.Format_RGB32)
        self.canvas.fill(QColor("#000000"))
        self.setup()

    def setup(self) -> None:
        """Subclasses initialise their state for the current canvas size."""

    def step(self) -> None:
        raise NotImplementedError

    def tick(self) -> None:
        self.step()
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.canvas.size() != self.size():
            self.reset()
        self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._timer.stop()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.reset()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.drawImage(0, 0, self.canvas)
        p.end()
