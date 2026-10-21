"""Renders the AWS 95 Tools application icon. Original artwork, painted in code: a teal Win95 desktop tile
holding a classic window with Cloudy (the floppy-disk mascot) inside and a "95" badge.

Run: uv run python -m tools.make_icons
Writes aws95/assets/icons/aws95-<size>.png (16…1024), aws95.ico (Windows, PNG-compressed entries) and,
on macOS, aws95.icns (via the system `iconutil`).
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter,  # noqa: E402
                           QPainterPath, QPen)

OUT = Path(__file__).resolve().parents[1] / "aws95" / "assets" / "icons"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
TEAL_TOP, TEAL_BOTTOM = "#16a6a6", "#006b6b"
NAVY, BLUE = "#000080", "#1084d0"
FACE, HI, LO, DARK = "#c0c0c0", "#ffffff", "#808080", "#000000"


def _cloudy(p: QPainter, x: float, y: float, w: float) -> None:
    """Cloudy at (x, y), w wide (same proportions as aws95/kit/mascot.py, on a 64-unit grid)."""
    s = w / 64.0
    p.save()
    p.translate(x, y)
    p.setPen(QPen(QColor(DARK), max(1.0, 1.6 * s)))
    p.setBrush(QColor("#1a3a8a"))
    p.drawRoundedRect(QRectF(8 * s, 16 * s, 44 * s, 44 * s), 3 * s, 3 * s)
    p.setBrush(QColor("#c8c8c8"))
    p.drawRect(QRectF(18 * s, 16 * s, 22 * s, 13 * s))
    p.setBrush(QColor("#1a3a8a"))
    p.drawRect(QRectF(31 * s, 18 * s, 5 * s, 9 * s))
    p.setBrush(QColor(HI))
    p.drawRect(QRectF(14 * s, 35 * s, 32 * s, 21 * s))
    p.setBrush(QColor(DARK))
    p.drawEllipse(QPointF(23 * s, 43 * s), 2.2 * s, 2.6 * s)
    p.drawEllipse(QPointF(37 * s, 43 * s), 2.2 * s, 2.6 * s)
    smile = QPainterPath(QPointF(24 * s, 49 * s))
    smile.quadTo(QPointF(30 * s, 54 * s), QPointF(36 * s, 49 * s))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(smile)
    p.setBrush(QColor(HI))
    cloud = QPainterPath()
    for cx, cy, r in ((40, 12, 8), (50, 10, 9), (58, 14, 6), (46, 16, 7)):
        puff = QPainterPath()
        puff.addEllipse(QPointF(cx * s, cy * s), r * s, r * s)
        cloud = cloud.united(puff)          # one outline, no inner arcs
    p.drawPath(cloud)
    p.restore()


def _bevel(p: QPainter, r: QRectF, t: float) -> None:
    """Win95 raised bevel of thickness t around r (white/grey top-left, black/dark bottom-right)."""
    p.fillRect(r, QColor(FACE))
    p.fillRect(QRectF(r.left(), r.top(), r.width(), t), QColor(HI))
    p.fillRect(QRectF(r.left(), r.top(), t, r.height()), QColor(HI))
    p.fillRect(QRectF(r.left(), r.bottom() - t, r.width(), t), QColor(DARK))
    p.fillRect(QRectF(r.right() - t, r.top(), t, r.height()), QColor(DARK))
    p.fillRect(QRectF(r.left() + t, r.bottom() - 2 * t, r.width() - 2 * t, t), QColor(LO))
    p.fillRect(QRectF(r.right() - 2 * t, r.top() + t, t, r.height() - 2 * t), QColor(LO))


def render(size: int) -> QImage:
    """The icon at `size` px. Small sizes drop the badge and caption buttons to stay legible."""
    S = float(size)
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    detailed = size >= 48

    # desktop tile (macOS-style margin; full bleed at tiny sizes)
    m = S * (0.08 if detailed else 0.0)
    tile = QRectF(m, m, S - 2 * m, S - 2 * m)
    grad = QLinearGradient(tile.topLeft(), tile.bottomLeft())
    grad.setColorAt(0.0, QColor(TEAL_TOP))
    grad.setColorAt(1.0, QColor(TEAL_BOTTOM))
    p.setPen(QPen(QColor("#004040"), max(1.0, S * 0.006)))
    p.setBrush(grad)
    p.drawRoundedRect(tile, S * 0.17, S * 0.17)

    # the window
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    t = max(1.0, round(S * 0.012))
    win = QRectF(S * 0.17, S * 0.20, S * 0.66, S * 0.58) if detailed else QRectF(S * 0.12, S * 0.14, S * 0.76, S * 0.70)
    _bevel(p, win, t)
    bar_h = max(2.0, round(S * (0.085 if detailed else 0.16)))
    bar = QRectF(win.left() + 2 * t, win.top() + 2 * t, win.width() - 4 * t, bar_h)
    title = QLinearGradient(bar.topLeft(), bar.topRight())
    title.setColorAt(0.0, QColor(NAVY))
    title.setColorAt(1.0, QColor(BLUE))
    p.fillRect(bar, title)
    if detailed:
        btn = bar.height() * 0.72
        gap = bar.height() * 0.14
        for i in range(3):
            bx = bar.right() - gap - (i + 1) * btn - i * gap * 0.5
            _bevel(p, QRectF(bx, bar.top() + gap, btn, btn), max(1.0, t * 0.5))
        p.setPen(QColor(HI))
        f = QFont("Helvetica", 1)
        f.setBold(True)
        f.setPixelSize(int(bar.height() * 0.62))
        p.setFont(f)
        p.drawText(QRectF(bar.left() + bar.height() * 0.3, bar.top(), bar.width() * 0.6, bar.height()),
                   int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), "AWS 95")

    # window body (white well) with Cloudy
    body = QRectF(win.left() + 3 * t, bar.bottom() + t, win.width() - 6 * t, win.bottom() - bar.bottom() - 4 * t)
    sky = QLinearGradient(body.topLeft(), body.bottomLeft())
    sky.setColorAt(0.0, QColor("#9fd3ff"))
    sky.setColorAt(1.0, QColor("#e3f3ff"))
    p.fillRect(body, sky)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    cw = min(body.width(), body.height()) * (0.86 if detailed else 0.96)
    _cloudy(p, body.center().x() - cw / 2, body.center().y() - cw / 2, cw)

    # "95" badge
    if detailed:
        r = S * 0.13
        c = QPointF(min(win.right() - r * 0.35, tile.right() - r * 1.15), win.bottom() - r * 0.35)
        p.setPen(QPen(QColor(DARK), max(1.0, S * 0.008)))
        p.setBrush(QColor("#ffd34d"))
        p.drawEllipse(c, r, r)
        f = QFont("Helvetica", 1)
        f.setBold(True)
        f.setPixelSize(int(r * 1.05))
        p.setFont(f)
        p.setPen(QColor(DARK))
        p.drawText(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), int(Qt.AlignmentFlag.AlignCenter), "95")
    p.end()
    return img


def _png_bytes(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def write_ico(path: Path, images: dict[int, QImage]) -> None:
    """Multi-size ICO with PNG-compressed entries (supported since Windows Vista)."""
    entries = [(size, _png_bytes(images[size])) for size in ICO_SIZES]
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    directory, blobs = b"", b""
    for size, png in entries:
        dim = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(blobs))
        blobs += png
    path.write_bytes(header + directory + blobs)


def write_icns(path: Path, images: dict[int, QImage]) -> bool:
    if shutil.which("iconutil") is None:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "aws95.iconset"
        iconset.mkdir()
        for base in (16, 32, 128, 256, 512):
            images[base].save(str(iconset / f"icon_{base}x{base}.png"))
            images[base * 2].save(str(iconset / f"icon_{base}x{base}@2x.png"))
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(path)], check=True)
    return True


def write_all(out: Path = OUT) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    images = {size: render(size) for size in SIZES}
    written = []
    for size, img in images.items():
        target = out / f"aws95-{size}.png"
        img.save(str(target))
        written.append(target)
    write_ico(out / "aws95.ico", images)
    written.append(out / "aws95.ico")
    if write_icns(out / "aws95.icns", images):
        written.append(out / "aws95.icns")
    return written


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841 - fonts need an app
    for path in write_all():
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
