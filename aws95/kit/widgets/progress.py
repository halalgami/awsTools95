from __future__ import annotations

from PySide6.QtCore import QRect, QSize, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from aws95.kit.paint import draw_bevel
from aws95.kit.theme import current_theme

BLOCK_W, GAP = 8, 2


class BlockProgress(QWidget):
    """Win95 block progress bar. Indeterminate mode is a marquee that only animates when visible."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = 0
        self._indeterminate = False
        self._offset = 0
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(160, 18)

    def value(self) -> int:
        return self._value

    def set_value(self, value: int) -> None:
        self._value = max(0, min(100, int(value)))
        self.update()

    def set_indeterminate(self, on: bool) -> None:
        self._indeterminate = on
        self._sync_timer()
        self.update()

    def is_animating(self) -> bool:
        return self._timer.isActive()

    def _sync_timer(self) -> None:
        if self._indeterminate and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_timer()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._sync_timer()

    def _tick(self) -> None:
        self._offset = (self._offset + BLOCK_W + GAP) % max(1, self.width())
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        t = current_theme()
        p = QPainter(self)
        draw_bevel(p, self.rect(), t, kind="field", fill=t.well)
        inner = self.rect().adjusted(3, 3, -3, -3)
        color = QColor(t.sel)
        if self._indeterminate:
            x = inner.left() + self._offset
            for _ in range(5):
                if x + BLOCK_W > inner.right():
                    x = inner.left()
                p.fillRect(QRect(x, inner.top(), BLOCK_W, inner.height()), color)
                x += BLOCK_W + GAP
        else:
            limit = inner.left() + inner.width() * self._value // 100
            x = inner.left()
            while x + BLOCK_W <= limit:
                p.fillRect(QRect(x, inner.top(), BLOCK_W, inner.height()), color)
                x += BLOCK_W + GAP
        p.end()
