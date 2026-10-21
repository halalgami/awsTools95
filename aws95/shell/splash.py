"""Boot splash: logo + sliding block progress (spec §6.1)."""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from aws95.kit.mascot import cloudy_pixmap

W, H = 480, 280
BLOCK_W, GAP = 10, 3


def make_splash_pixmap() -> QPixmap:
    pm = QPixmap(W, H)
    p = QPainter(pm)
    grad = QLinearGradient(0, 0, 0, H)
    grad.setColorAt(0.0, QColor("#1084d0"))
    grad.setColorAt(1.0, QColor("#000080"))
    p.fillRect(0, 0, W, H, grad)
    p.drawPixmap(W - 120, 24, cloudy_pixmap(96))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(52)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(28, 110, "AWS")
    p.setPen(QColor("#c0c0c0"))
    p.drawText(28 + p.fontMetrics().horizontalAdvance("AWS "), 110, "95")
    f.setPixelSize(18)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(32, 140, "T O O L S")
    p.end()
    return pm


class Splash(QWidget):
    """A plain frameless widget, not QSplashScreen: QSplashScreen.show() blocks ~1 s waiting for window
    exposure (M4-R3), which blew the 300 ms splash budget."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)   # finish() frees the splash
        self._pixmap = make_splash_pixmap()
        self._progress = 0
        self._text = "Starting AWS 95…"
        self.setFixedSize(self._pixmap.size())
        screen = QApplication.primaryScreen()
        if screen is not None:
            self.move(screen.geometry().center() - self.rect().center())

    def progress(self) -> int:
        return self._progress

    def set_progress(self, percent: int, text: str) -> None:
        self._progress = max(0, min(100, int(percent)))
        self._text = text
        self.repaint()
        QApplication.processEvents()    # the boot is synchronous: let the bar show between steps

    def finish(self, widget: QWidget | None = None) -> None:
        self.close()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._pixmap)
        self._draw_contents(painter)
        painter.end()

    def _draw_contents(self, painter: QPainter) -> None:
        bar = QRect(28, H - 52, W - 56, 16)
        painter.fillRect(bar, QColor("#ffffff"))
        painter.setPen(QColor("#000000"))
        painter.drawRect(bar.adjusted(0, 0, -1, -1))
        limit = bar.left() + 2 + (bar.width() - 4) * self._progress // 100
        x = bar.left() + 2
        while x + BLOCK_W <= limit:
            painter.fillRect(QRect(x, bar.top() + 2, BLOCK_W, bar.height() - 4), QColor("#000080"))
            x += BLOCK_W + GAP
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QRect(28, H - 30, W - 56, 20), int(Qt.AlignmentFlag.AlignLeft), self._text)
