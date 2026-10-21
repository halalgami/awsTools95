from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from aws95.kit.theme import current_theme

PAD, WIDTH = 9, 240


class Balloon(QWidget):
    """Classic yellow tray balloon. Auto-hides after timeout_ms; click to dismiss early."""

    clicked = Signal()
    dismissed = Signal()

    def __init__(self, title: str, text: str, timeout_ms: int = 6000, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._title = title
        self._text = text
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(self._dismiss)
        self.resize(self.sizeHint())

    def title(self) -> str:
        return self._title

    def text(self) -> str:
        return self._text

    def _title_font(self) -> QFont:
        f = QFont(self.font())
        f.setBold(True)
        return f

    def _width(self) -> int:
        """At least WIDTH; wider when the title or a large font needs it (the title never wraps or clips)."""
        fm = QFontMetrics(self.font())
        title = QFontMetrics(self._title_font()).horizontalAdvance(self._title) + 2 * PAD + 2
        return max(WIDTH, title, fm.averageCharWidth() * 36)

    def sizeHint(self) -> QSize:  # noqa: N802
        width = self._width()
        body = QFontMetrics(self.font()).boundingRect(QRect(0, 0, width - 2 * PAD, 1000),
                                                      Qt.TextFlag.TextWordWrap, self._text)
        title_h = QFontMetrics(self._title_font()).height()
        return QSize(width, title_h + body.height() + 2 * PAD + 4)

    def show_at(self, global_point: QPoint) -> None:
        size = self.sizeHint()
        self.resize(size)
        self.move(global_point.x() - size.width(), global_point.y() - size.height())
        self.show()
        self._timer.start()

    def _dismiss(self) -> None:
        self._timer.stop()
        if self.isVisible():
            self.hide()
        self.dismissed.emit()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit()
        self._dismiss()

    def paintEvent(self, event) -> None:  # noqa: N802
        t = current_theme()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(0, 0, -1, -1), 6, 6)
        p.fillPath(path, QColor(t.tooltip_bg))
        p.setPen(QPen(QColor(t.tooltip_text), 1))
        p.drawPath(path)
        p.setFont(self._title_font())
        title_h = QFontMetrics(self._title_font()).height()
        p.drawText(QRect(PAD, PAD, self.width() - 2 * PAD, title_h), Qt.AlignmentFlag.AlignLeft, self._title)
        p.setFont(self.font())
        p.drawText(QRect(PAD, PAD + title_h + 2, self.width() - 2 * PAD, self.height()),
                   Qt.TextFlag.TextWordWrap, self._text)
        p.end()
