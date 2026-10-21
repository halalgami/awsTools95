"""Cloudy: an original mascot (a floppy disk wearing a small cloud), painted in code."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap


def cloudy_pixmap(size: int = 64) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    s = size / 64.0
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    outline = QPen(QColor("#000000"), max(1.0, 1.5 * s))
    # floppy body
    p.setPen(outline)
    p.setBrush(QColor("#1a3a8a"))
    p.drawRoundedRect(QRectF(8 * s, 16 * s, 44 * s, 44 * s), 3 * s, 3 * s)
    # metal shutter
    p.setBrush(QColor("#c8c8c8"))
    p.drawRect(QRectF(18 * s, 16 * s, 22 * s, 13 * s))
    p.setBrush(QColor("#1a3a8a"))
    p.drawRect(QRectF(31 * s, 18 * s, 5 * s, 9 * s))
    # label with face
    p.setBrush(QColor("#ffffff"))
    p.drawRect(QRectF(14 * s, 35 * s, 32 * s, 21 * s))
    p.setBrush(QColor("#000000"))
    p.drawEllipse(QPointF(23 * s, 43 * s), 2.2 * s, 2.6 * s)
    p.drawEllipse(QPointF(37 * s, 43 * s), 2.2 * s, 2.6 * s)
    smile = QPainterPath(QPointF(24 * s, 49 * s))
    smile.quadTo(QPointF(30 * s, 54 * s), QPointF(36 * s, 49 * s))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(smile)
    # cloud hat
    p.setBrush(QColor("#ffffff"))
    cloud = QPainterPath()
    for cx, cy, r in ((40, 12, 8), (50, 10, 9), (58, 14, 6), (46, 16, 7)):
        puff = QPainterPath()
        puff.addEllipse(QPointF(cx * s, cy * s), r * s, r * s)
        cloud = cloud.united(puff)          # one outline, no inner arcs
    p.drawPath(cloud)
    p.end()
    return pm
