from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPainter

from aws95.shell.screensaver.base import Saver

BOX_W, BOX_H = 150, 46
MIN_SCALE = 0.3
HUE_COOLDOWN = 30  # frames: at most one colour change per ~0.5 s at 60 fps (photosensitivity)


class BouncingLogo(Saver):
    name, title = "bounce", "Bouncing Logo"

    def setup(self) -> None:
        self.x, self.y = 20.0, 20.0
        self.vx, self.vy = 2.2, 1.7
        self.hue = self.rng.randint(0, 359)
        self.frame = 0
        self.last_hue_frame = 0

    def step(self) -> None:
        w, h = self.canvas.width(), self.canvas.height()
        self.frame += 1
        s = max(MIN_SCALE, min(1.0, w / BOX_W, h / BOX_H))
        bw, bh = BOX_W * s, BOX_H * s
        self.x += self.vx
        self.y += self.vy
        bounced = False
        max_x, max_y = max(0.0, w - bw), max(0.0, h - bh)
        if (self.x < 0 and self.vx < 0) or (self.x > max_x and self.vx > 0):
            self.vx = -self.vx
            bounced = True
        if (self.y < 0 and self.vy < 0) or (self.y > max_y and self.vy > 0):
            self.vy = -self.vy
            bounced = True
        self.x = min(max(self.x, 0.0), max_x)
        self.y = min(max(self.y, 0.0), max_y)
        if bounced and self.frame - self.last_hue_frame >= HUE_COOLDOWN:
            self.hue = (self.hue + 67) % 360
            self.last_hue_frame = self.frame
        self.canvas.fill(QColor("#000000"))
        p = QPainter(self.canvas)
        p.scale(s, s)
        ix, iy = int(self.x / s), int(self.y / s)
        f = QFont()
        f.setBold(True)
        f.setPixelSize(30)
        p.setFont(f)
        p.setPen(QColor.fromHsv(self.hue, 230, 255))
        p.drawText(ix, iy + 34, "AWS")
        p.setPen(QColor("#ffffff"))
        p.drawText(ix + 72, iy + 34, "95")
        f.setPixelSize(10)
        p.setFont(f)
        p.setPen(QColor.fromHsv(self.hue, 150, 255))
        p.drawText(ix + 108, iy + 44, "TOOLS")
        p.end()
