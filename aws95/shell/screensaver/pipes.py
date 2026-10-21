from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QRadialGradient

from aws95.shell.screensaver.base import Saver

GRID = 14
DIRS = ((1, 0), (0, 1), (-1, 0), (0, -1))
SEGMENTS_PER_STEP = 2
FADE_FRAMES = 30
COVERAGE = 0.30  # clear once drawn segments exceed this fraction of the grid cells


class Pipes(Saver):
    name, title = "pipes", "Pipes"

    def setup(self) -> None:
        self.pipes = []
        self.total = 0
        self.fade = 0
        for _ in range(self.rng.choice((3, 4))):
            self._new_pipe()

    def _new_pipe(self, p: QPainter | None = None) -> None:
        w, h = self.canvas.width(), self.canvas.height()
        pipe = {"x": self.rng.randrange(0, max(1, w // GRID)) * GRID,
                "y": self.rng.randrange(0, max(1, h // GRID)) * GRID,
                "d": self.rng.randrange(4), "len": 0, "max": self.rng.randint(40, 140),
                "color": QColor.fromHsv(self.rng.randint(0, 359), 220, 230)}
        self.pipes.append(pipe)
        if p is not None:
            self._joint(p, pipe["x"], pipe["y"], pipe["color"])

    def _joint(self, p: QPainter, x: float, y: float, color: QColor) -> None:
        r = 8.0
        g = QRadialGradient(QPointF(x - 2.5, y - 2.5), r)
        g.setColorAt(0.0, QColor("#ffffff"))
        g.setColorAt(0.35, color)
        g.setColorAt(1.0, color.darker(200))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(x, y), r, r)

    def _segment(self, p: QPainter, x0: int, y0: int, x1: int, y1: int, color: QColor) -> None:
        horizontal = y0 == y1
        ox, oy = (0, -2) if horizontal else (-2, 0)  # light comes from the upper left
        cap = Qt.PenCapStyle.FlatCap  # seamless straight runs; joint spheres cover the turns
        p.setPen(QPen(color.darker(170), 12, Qt.PenStyle.SolidLine, cap))
        p.drawLine(x0, y0, x1, y1)
        p.setPen(QPen(color, 9, Qt.PenStyle.SolidLine, cap))
        p.drawLine(x0, y0, x1, y1)
        flat = cap
        p.setPen(QPen(color.lighter(190), 3, Qt.PenStyle.SolidLine, flat))
        p.drawLine(x0 + ox, y0 + oy, x1 + ox, y1 + oy)
        p.setPen(QPen(QColor(255, 255, 255, 140), 1, Qt.PenStyle.SolidLine, flat))
        p.drawLine(x0 + 2 * ox, y0 + 2 * oy, x1 + 2 * ox, y1 + 2 * oy)

    def _advance(self, p: QPainter, pipe: dict, w: int, h: int) -> bool:
        """Draw one segment; return False when the pipe has ended."""
        turned = False
        if self.rng.random() < 0.15:
            pipe["d"] = (pipe["d"] + self.rng.choice((1, 3))) % 4
            turned = True
        for _ in range(4):
            dx, dy = DIRS[pipe["d"]]
            nx, ny = pipe["x"] + dx * GRID, pipe["y"] + dy * GRID
            if 0 <= nx <= w and 0 <= ny <= h:
                break
            pipe["d"] = (pipe["d"] + self.rng.choice((1, 3))) % 4
            turned = True
        else:
            return False
        if turned:
            self._joint(p, pipe["x"], pipe["y"], pipe["color"])
        self._segment(p, pipe["x"], pipe["y"], nx, ny, pipe["color"])
        pipe["x"], pipe["y"] = nx, ny
        pipe["len"] += 1
        self.total += 1
        return True

    def step(self) -> None:
        w, h = self.canvas.width(), self.canvas.height()
        p = QPainter(self.canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self.fade:
            p.fillRect(0, 0, w, h, QColor(0, 0, 0, 36))
            p.end()
            self.fade -= 1
            if not self.fade:
                self.canvas.fill(QColor("#000000"))
                self.setup()
            return
        for pipe in list(self.pipes):
            for _ in range(SEGMENTS_PER_STEP):
                alive = self._advance(p, pipe, w, h)
                if not alive or pipe["len"] >= pipe["max"]:
                    self._joint(p, pipe["x"], pipe["y"], pipe["color"])
                    self.pipes.remove(pipe)
                    self._new_pipe(p)
                    break
        p.end()
        if self.total > COVERAGE * (w // GRID + 1) * (h // GRID + 1):
            self.fade = FADE_FRAMES
