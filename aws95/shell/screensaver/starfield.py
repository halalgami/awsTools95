from __future__ import annotations

from PySide6.QtGui import QColor, QPainter, QPen

from aws95.shell.screensaver.base import Saver

STARS = 500
FOCAL = 128.0


class Starfield(Saver):
    name, title = "starfield", "Starfield"

    def _spawn(self, s: list, w: int, h: int, z: float) -> None:
        s[0], s[1], s[2] = self.rng.uniform(-w / 2, w / 2), self.rng.uniform(-h / 2, h / 2), z
        k = FOCAL / z
        s[3], s[4] = s[0] * k + w / 2, s[1] * k + h / 2

    def setup(self) -> None:
        w, h = self.canvas.width(), self.canvas.height()
        self.stars = []
        for _ in range(STARS):
            s = [0.0, 0.0, 0.0, 0.0, 0.0]  # x, y, z, previous screen x, previous screen y
            self._spawn(s, w, h, self.rng.uniform(1, w))
            self.stars.append(s)
        self._pens = [QPen(QColor(g, g, min(255, g + 20)), 1) for g in range(0, 256, 16)]
        self._pens_wide = [QPen(QColor(g, g, min(255, g + 20)), 2) for g in range(0, 256, 16)]

    def step(self) -> None:
        w, h = self.canvas.width(), self.canvas.height()
        self.canvas.fill(QColor("#000000"))
        p = QPainter(self.canvas)
        speed = max(4.0, w / 120.0)
        for s in self.stars:
            s[2] -= speed
            if s[2] < 1:
                self._spawn(s, w, h, float(w))
                continue
            k = FOCAL / s[2]
            x, y = s[0] * k + w / 2, s[1] * k + h / 2
            if not (-w <= x <= 2 * w and -h <= y <= 2 * h):
                self._spawn(s, w, h, float(w))
                continue
            near = 1.0 - s[2] / w
            idx = min(15, max(0, int(near * 15) + 4))
            p.setPen(self._pens_wide[idx] if near > 0.7 else self._pens[idx])
            p.drawLine(int(s[3]), int(s[4]), int(x), int(y))
            s[3], s[4] = x, y
        p.end()
