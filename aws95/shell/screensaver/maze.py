from __future__ import annotations

from PySide6.QtGui import QColor, QPainter

from aws95.shell.screensaver.base import Saver

CELL = 18
PAUSE_FRAMES = 60
FRESH = QColor("#b8b8b8")
DONE = QColor("#707070")
HEAD = QColor("#ff4040")


class MazeWalk(Saver):
    name, title = "maze", "Maze Walk"

    def setup(self) -> None:
        self.cols = max(1, self.canvas.width() // CELL)
        self.rows = max(1, self.canvas.height() // CELL)
        self.visited = {(0, 0)}
        self.stack = [(0, 0)]
        self.pause = 0

    def _link(self, p: QPainter, a: tuple[int, int], b: tuple[int, int], color: QColor) -> None:
        p.fillRect(min(a[0], b[0]) * CELL + 3, min(a[1], b[1]) * CELL + 3,
                   abs(a[0] - b[0]) * CELL + CELL - 6, abs(a[1] - b[1]) * CELL + CELL - 6, color)

    def step(self) -> None:
        if not self.stack:
            self.pause += 1
            if self.pause >= PAUSE_FRAMES:
                self.canvas.fill(QColor("#000000"))
                self.setup()
            return
        p = QPainter(self.canvas)
        for _ in range(3):
            if not self.stack:
                break
            x, y = self.stack[-1]
            options = [(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                       if 0 <= x + dx < self.cols and 0 <= y + dy < self.rows and (x + dx, y + dy) not in self.visited]
            if not options:
                self.stack.pop()
                self._link(p, (x, y), self.stack[-1] if self.stack else (x, y), DONE)
                continue
            nx, ny = self.rng.choice(options)
            self.visited.add((nx, ny))
            self._link(p, (x, y), (nx, ny), FRESH)
            p.fillRect(nx * CELL + 5, ny * CELL + 5, CELL - 10, CELL - 10, HEAD)
            self.stack.append((nx, ny))
        p.end()
