"""Win95 two-line bevels, focus rects and arrows. Pure QPainter helpers, no widget state."""
from __future__ import annotations

from typing import Literal

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygon, QRegion

from aws95.kit.theme import Theme

BevelKind = Literal["button", "panel", "field"]
Direction = Literal["up", "down", "left", "right"]


def bevel_colors(theme: Theme, *, sunken: bool, kind: BevelKind) -> tuple[str, str, str, str]:
    if kind == "field":                       # text boxes, grids, wells
        return (theme.lo, theme.hi, theme.dark, theme.face2)
    if kind == "panel":                       # window frames, raised panels
        return (theme.face2, theme.dark, theme.hi, theme.lo) if not sunken else (theme.lo, theme.hi, theme.dark, theme.face2)
    if sunken:                                # pressed button
        return (theme.dark, theme.hi, theme.lo, theme.face2)
    return (theme.hi, theme.dark, theme.face2, theme.lo)


def _frame(p: QPainter, r: QRect, tl: str, br: str) -> None:
    p.setPen(QPen(QColor(tl), 1))
    p.drawLine(r.topLeft(), r.topRight())
    p.drawLine(r.topLeft(), r.bottomLeft())
    p.setPen(QPen(QColor(br), 1))
    p.drawLine(r.bottomLeft(), r.bottomRight())
    p.drawLine(r.topRight(), r.bottomRight())


def draw_bevel(p: QPainter, rect: QRect, theme: Theme, *, sunken: bool = False,
               kind: BevelKind = "button", fill: str | None = None) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    r = QRect(rect)
    if fill is not None:
        p.fillRect(r, QColor(fill))
    otl, obr, itl, ibr = bevel_colors(theme, sunken=sunken, kind=kind)
    _frame(p, r, otl, obr)
    if r.width() > 2 and r.height() > 2:
        _frame(p, r.adjusted(1, 1, -1, -1), itl, ibr)
    p.restore()


def draw_focus_rect(p: QPainter, rect: QRect, color: str) -> None:
    p.save()
    pen = QPen(QColor(color), 1, Qt.PenStyle.DotLine)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(rect.adjusted(0, 0, -1, -1))
    p.restore()


def draw_arrow(p: QPainter, rect: QRect, direction: Direction, color: str) -> None:
    c = rect.center()
    s = max(2, min(rect.width(), rect.height()) // 4)
    points = {
        "up": [QPoint(c.x() - s, c.y() + s // 2), QPoint(c.x() + s, c.y() + s // 2), QPoint(c.x(), c.y() - s // 2)],
        "down": [QPoint(c.x() - s, c.y() - s // 2), QPoint(c.x() + s, c.y() - s // 2), QPoint(c.x(), c.y() + s // 2)],
        "left": [QPoint(c.x() + s // 2, c.y() - s), QPoint(c.x() + s // 2, c.y() + s), QPoint(c.x() - s // 2, c.y())],
        "right": [QPoint(c.x() - s // 2, c.y() - s), QPoint(c.x() - s // 2, c.y() + s), QPoint(c.x() + s // 2, c.y())],
    }[direction]
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawPolygon(QPolygon(points))
    p.restore()


Glyph = Literal["min", "max", "restore", "close"]


def draw_title_glyph(p: QPainter, rect: QRect, glyph: Glyph, color: str) -> None:
    """Win95 caption-button glyphs, drawn crisp (no antialiasing)."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    c = QColor(color)
    w, h = rect.width(), rect.height()
    x0, y0 = rect.left() + w // 2 - 4, rect.top() + h // 2 - 4   # 8x8 glyph box
    if glyph == "min":
        p.fillRect(QRect(x0 + 1, y0 + 6, 6, 2), c)
    elif glyph == "max":
        p.setPen(QPen(c, 1))
        p.drawRect(QRect(x0, y0, 8, 7))
        p.fillRect(QRect(x0, y0, 9, 2), c)
    elif glyph == "restore":
        front = QRect(x0, y0 + 3, 7, 6)
        p.setClipRegion(QRegion(QRect(x0, y0, 9, 9)) - QRegion(front))  # back window hides behind front
        p.setPen(QPen(c, 1))
        p.drawRect(QRect(x0 + 2, y0, 6, 5))
        p.fillRect(QRect(x0 + 2, y0, 7, 2), c)
        p.setClipping(False)
        p.drawRect(QRect(x0, y0 + 3, 6, 5))
        p.fillRect(QRect(x0, y0 + 3, 7, 2), c)
    else:  # close
        p.setPen(QPen(c, 2))
        p.drawLine(x0, y0, x0 + 7, y0 + 7)
        p.drawLine(x0 + 7, y0, x0, y0 + 7)
    p.restore()
