from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QWidget

from aws95.kit.theme import current_theme


class Watermark(QWidget):
    """Diagonal repeating DEMO DATA overlay; never intercepts input."""

    TEXT = "DEMO DATA"

    def __init__(self, target: QWidget) -> None:
        super().__init__(target)
        self._target = target
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        target.installEventFilter(self)
        self.setGeometry(target.rect())
        self.raise_()
        self.show()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._target and event.type() in (QEvent.Type.Resize, QEvent.Type.Show,
                                                     QEvent.Type.ChildAdded, QEvent.Type.LayoutRequest):
            self.setGeometry(self._target.rect())
            self.raise_()
        return False

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        color = QColor(current_theme().bad)
        color.setAlpha(70)
        p.setPen(color)
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 2.2)
        p.setFont(font)
        step_x = p.fontMetrics().horizontalAdvance(self.TEXT) + 60
        step_y = p.fontMetrics().height() * 4
        p.translate(self.width() / 2, self.height() / 2)
        p.rotate(-30)
        span = max(self.width(), self.height())
        y = -span
        while y < span:
            x = -span
            while x < span:
                p.drawText(int(x), int(y), self.TEXT)
                x += step_x
            y += step_y
        p.end()


def attach_watermark(target: QWidget) -> Watermark:
    return Watermark(target)
