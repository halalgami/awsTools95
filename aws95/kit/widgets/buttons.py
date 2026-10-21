from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from aws95.kit.theme import relative_luminance

RAINBOW = ("#ff6b6b", "#ffd34d", "#5fd38d", "#7ec8ff", "#b48cff")


class Chip(QWidget):
    """Account colour chip: solid colour, 1px black border, auto-contrast bold label."""

    def __init__(self, text: str, color: str, parent: QWidget | None = None) -> None:
        if color != "rainbow" and not QColor(color).isValid():
            raise ValueError(f"invalid colour {color!r}")
        super().__init__(parent)
        self._text = text
        self._color = color
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    @staticmethod
    def text_color_for(bg_hex: str) -> str:
        return "#000000" if relative_luminance(bg_hex) > 0.18 else "#ffffff"

    def text(self) -> str:
        return self._text

    def color(self) -> str:
        return self._color

    def set_text(self, text: str) -> None:
        self._text = text
        self.updateGeometry()
        self.update()

    def set_color(self, color: str) -> None:
        if color != "rainbow" and not QColor(color).isValid():
            raise ValueError(f"invalid colour {color!r}")
        self._color = color
        self.update()

    def _font(self) -> QFont:
        f = QFont(self.font())
        f.setBold(True)
        f.setPointSizeF(max(6.0, f.pointSizeF() * 0.9))
        return f

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(self._font())
        return QSize(fm.horizontalAdvance(self._text) + 12, fm.height() + 4)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        r = self.rect()
        if self._color == "rainbow":
            grad = QLinearGradient(r.topLeft(), r.topRight())
            for i, c in enumerate(RAINBOW):
                grad.setColorAt(i / (len(RAINBOW) - 1), QColor(c))
            p.fillRect(r, grad)
            fg = "#000000"
        else:
            p.fillRect(r, QColor(self._color))
            fg = self.text_color_for(self._color)
        p.setPen(QPen(QColor("#000000"), 1))
        p.drawRect(r.adjusted(0, 0, -1, -1))
        p.setFont(self._font())
        p.setPen(QColor(fg))
        p.drawText(QRect(r), Qt.AlignmentFlag.AlignCenter, self._text)
        p.end()
