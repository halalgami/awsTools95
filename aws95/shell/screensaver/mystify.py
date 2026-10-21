from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF

from aws95.shell.screensaver.base import Saver


class Mystify(Saver):
    name, title = "mystify", "Mystify"

    def setup(self) -> None:
        w, h = self.canvas.width(), self.canvas.height()
        self.shapes = []
        for _ in range(2):
            pts = [[self.rng.uniform(0, w), self.rng.uniform(0, h),
                    self.rng.choice((-1, 1)) * self.rng.uniform(1, 4),
                    self.rng.choice((-1, 1)) * self.rng.uniform(1, 4)] for _ in range(4)]
            self.shapes.append({"pts": pts, "hue": self.rng.randint(0, 359), "trail": deque(maxlen=8)})

    def step(self) -> None:
        w, h = self.canvas.width(), self.canvas.height()
        self.canvas.fill(QColor("#000000"))
        p = QPainter(self.canvas)
        for shape in self.shapes:
            for pt in shape["pts"]:
                pt[0] += pt[2]
                pt[1] += pt[3]
                if not 0 <= pt[0] <= w:
                    pt[2] = -pt[2]
                if not 0 <= pt[1] <= h:
                    pt[3] = -pt[3]
            shape["hue"] = (shape["hue"] + 1) % 360
            shape["trail"].append(QPolygonF([QPointF(x, y) for x, y, _dx, _dy in shape["pts"]]))
            n = len(shape["trail"])
            for i, poly in enumerate(shape["trail"]):
                color = QColor.fromHsv(shape["hue"], 230, 255, int(255 * (i + 1) / n))
                p.setPen(QPen(color, 1))
                p.drawPolygon(poly)
        p.end()
