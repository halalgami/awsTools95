"""Original 16x16 pixel icons defined in code (no external assets). Scaled with nearest-neighbour."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap

_PALETTE = {
    ".": None, "k": "#000000", "w": "#ffffff", "g": "#c0c0c0", "d": "#808080",
    "b": "#000080", "B": "#1084d0", "c": "#008080", "C": "#00c0c0", "y": "#ffff00",
    "o": "#e0a000", "r": "#c00000", "R": "#ff4040", "G": "#008000", "L": "#40c040",
    "n": "#805000", "p": "#8040c0",
}

_ICONS: dict[str, tuple[str, ...]] = {
    "app": (
        "................", ".kkkkkkkkkkkkkk.", ".kbbbbbbbbbbbbk.", ".kbwwbbbbbbbbbk.",
        ".kkkkkkkkkkkkkk.", ".kgggggggggggggk", ".kgwwwgggrrrgggk", ".kgwkwgggrkrgggk",
        ".kgwwwgggrrrgggk", ".kgggggggggggggk", ".kgyyyggggLLLggk", ".kgykyggggLkLggk",
        ".kgyyyggggLLLggk", ".kgggggggggggggk", ".kkkkkkkkkkkkkkk", "................",
    ),
    "computer": (
        "................", "..kkkkkkkkkkkk..", "..kgggggggggdk..", "..kgkkkkkkkkgdk.",
        "..kgkCCCCCCkgdk.", "..kgkCwCCCCkgdk.", "..kgkCCCCCCkgdk.", "..kgkCCCCCCkgdk.",
        "..kgkkkkkkkkgdk.", "..kggggggggggdk.", "..kddddddddddkk.", ".....kgggk......",
        "...kkkkkkkkkk...", "..kgggggggggdk..", "..kkkkkkkkkkkk..", "................",
    ),
    "server": (
        "................", "...kkkkkkkkkk...", "...kgggggggdk...", "...kgLgkkkgdk...",
        "...kgggggggdk...", "...kkkkkkkkkk...", "...kgggggggdk...", "...kgLgkkkgdk...",
        "...kgggggggdk...", "...kkkkkkkkkk...", "...kgggggggdk...", "...kgRgkkkgdk...",
        "...kgggggggdk...", "...kkkkkkkkkk...", "....kk....kk....", "................",
    ),
    "money": (
        "................", "......kkkk......", ".....knnnnk.....", "......kkkk......",
        ".....koooook....", "....kooyyoook...", "...kooykkyoook..", "...koooykyoook..",
        "...kooookyoook..", "...kooykkyoook..", "...koooyyooook..", "...kooooooooook.",
        "....kooooooook..", ".....kkkkkkkk...", "................", "................",
    ),
    "shield": (
        "................", "...kkkkkkkkkk...", "..kBBBBBBBBBBk..", "..kBwwBBBBBBBk..",
        "..kBwBBBBBBBBk..", "..kBBBBkkBBBBk..", "..kBBBkLLkBBBk..", "..kBBkLLLLkBBk..",
        "...kBkLLLLkBk...", "...kBBkLLkBBk...", "....kBBkkBBk....", ".....kBBBBk.....",
        "......kBBk......", ".......kk.......", "................", "................",
    ),
    "magnifier": (
        "................", "....kkkkk.......", "...kCCCCCk......", "..kCwwCCCCk.....",
        "..kCwCCCCCk.....", "..kCCCCCCCk.....", "..kCCCCCCCk.....", "...kCCCCCk......",
        "....kkkkkkk.....", "........kkk.....", ".........kkk....", "..........kkk...",
        "...........kkk..", "............kk..", "................", "................",
    ),
    "camera": (
        "................", "................", ".....kkkk.......", "..kkkkddkkkkkk..",
        "..kggggggggggk..", "..kgggkkkkgggk..", "..kggkCCCCkggk..", "..kgkCCwwCCkgk..",
        "..kgkCCwCCCkgk..", "..kgkCCCCCCkgk..", "..kggkCCCCkggk..", "..kgggkkkkgRgk..",
        "..kggggggggggk..", "..kkkkkkkkkkkk..", "................", "................",
    ),
    "grid": (
        "................", ".kkkkkkkkkkkkkk.", ".kwwwkwwwkwwwwk.", ".kwLwkwRwkwLwwk.",
        ".kwwwkwwwkwwwwk.", ".kkkkkkkkkkkkkk.", ".kwwwkwwwkwwwwk.", ".kwRwkwLwkwLwwk.",
        ".kwwwkwwwkwwwwk.", ".kkkkkkkkkkkkkk.", ".kwwwkwwwkwwwwk.", ".kwLwkwLwkwRwwk.",
        ".kwwwkwwwkwwwwk.", ".kkkkkkkkkkkkkk.", "................", "................",
    ),
    "robot": (
        "................", ".......kk.......", ".......RR.......", "...kkkkkkkkkk...",
        "...kggggggggk...", "...kgCCggCCgk...", "...kgCkggCkgk...", "...kggggggggk...",
        "...kggkkkkggk...", "...kkkkkkkkkk...", ".kk.kggggggk.kk.", ".kgkkggggggkkgk.",
        "....kggggggk....", "....kkk..kkk....", "................", "................",
    ),
    "folder": (
        "................", "................", "..kkkkk.........", ".kyyyyyk........",
        ".kyyyyyykkkkkkk.", ".kyyyyyyyyyyyyk.", ".kyowwwwwwwwwok.", ".kyowyyyyyyyyok.",
        ".kyoyyyyyyyyyok.", ".kyoyyyyyyyyyok.", ".kyoyyyyyyyyyok.", ".kyoooooooooook.",
        ".kkkkkkkkkkkkkk.", "................", "................", "................",
    ),
    "recycle": (
        "................", "....kkkkkkkk....", "...kggggggggk...", "..kkkkkkkkkkkk..",
        "...kgwgdgwgdk...", "...kgwgdgwgdk...", "...kgwLLLwgdk...", "...kgLwgdLgdk...",
        "...kgwgdgLgdk...", "...kgLLLgdgdk...", "...kgwgdgwgdk...", "...kgwgdgwgdk...",
        "....kkkkkkkk....", "................", "................", "................",
    ),
    "display": (
        "................", ".kkkkkkkkkkkkkk.", ".kggggggggggggk.", ".kgkkkkkkkkkkgk.",
        ".kgkBBBBBBBBkgk.", ".kgkBwBBBByBkgk.", ".kgkBBBBBByykgk.", ".kgkCCCCCCCCkgk.",
        ".kgkGGGGGGGGkgk.", ".kgkkkkkkkkkkgk.", ".kggggggggggggk.", ".kkkkkkkkkkkkkk.",
        "......kggk......", "....kkkkkkkk....", "................", "................",
    ),
    "gear": (
        "................", ".......kk.......", "...kk.kddk.kk...", "...kdkkddkkdk...",
        "....kddddddk....", "..kkddkkkkddkk..", ".kddddk..kddddk.", ".kddddk..kddddk.",
        "..kkddkkkkddkk..", "....kddddddk....", "...kdkkddkkdk...", "...kk.kddk.kk...",
        ".......kk.......", "................", "................", "................",
    ),
    "help": (
        "................", ".....kkkkkk.....", "...kkBBBBBBkk...", "..kBBBwwwwBBBk..",
        "..kBBwwBBwwBBk..", ".kBBBBBBBwwBBBk.", ".kBBBBBBwwBBBBk.", ".kBBBBBwwBBBBBk.",
        ".kBBBBBwwBBBBBk.", ".kBBBBBBBBBBBBk.", "..kBBBBwwBBBBk..", "..kBBBBwwBBBBk..",
        "...kkBBBBBBkk...", ".....kkkkkk.....", "................", "................",
    ),
    "run": (
        "................", ".kkkkkkkkkkkkkk.", ".kbbbbbbbbbbbbk.", ".kkkkkkkkkkkkkk.",
        ".kwwwwwwwwwwwwk.", ".kwkkkkkkkkkwwk.", ".kwwwwwwwwwwwwk.", ".kwwwwwwwwwwwwk.",
        ".kwwwwwwwkkkkwk.", ".kwwwwwwwkggkwk.", ".kwwwwwwwkkkkwk.", ".kkkkkkkkkkkkkk.",
        "................", "................", "................", "................",
    ),
    "power": (
        "................", ".......kk.......", "...kk..RR..kk...", "..kRk..RR..kRk..",
        ".kRk...RR...kRk.", ".kRk...RR...kRk.", ".kRk...RR...kRk.", ".kRk...kk...kRk.",
        ".kRk........kRk.", "..kRk......kRk..", "...kRkk..kkRk...", "....kkRRRRkk....",
        "......kkkk......", "................", "................", "................",
    ),
    "speaker": (
        "................", "........k.......", ".......kk...k...", "......kgk....k..",
        ".kkkkkggk..k..k.", ".kgggggdk...k.k.", ".kgggggdk...k.k.", ".kgggggdk...k.k.",
        ".kkkkkggk..k..k.", "......kgk....k..", ".......kk...k...", "........k.......",
        "................", "................", "................", "................",
    ),
    "mute": (
        "................", "........k.......", ".......kk.......", "......kgk.R...R.",
        ".kkkkkggk..R.R..", ".kgggggdk...R...", ".kgggggdk..R.R..", ".kgggggdk.R...R.",
        ".kkkkkggk.......", "......kgk.......", ".......kk.......", "........k.......",
        "................", "................", "................", "................",
    ),
    "window": (
        "................", ".kkkkkkkkkkkkkk.", ".kbbbbbbbbbbbbk.", ".kbwwbbbbbbkgkk.",
        ".kkkkkkkkkkkkkk.", ".kwwwwwwwwwwwwk.", ".kwggggwwwwwwwk.", ".kwwwwwwwwwwwwk.",
        ".kwgggggggwwwwk.", ".kwwwwwwwwwwwwk.", ".kwggggggwwwwwk.", ".kwwwwwwwwwwwwk.",
        ".kkkkkkkkkkkkkk.", "................", "................", "................",
    ),
}

ICON_NAMES: tuple[str, ...] = tuple(_ICONS)


@lru_cache(maxsize=None)
def _base_image(name: str) -> QImage:
    rows = _ICONS[name]
    img = QImage(16, 16, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            hex_color = _PALETTE[ch]
            if hex_color is not None:
                img.setPixelColor(x, y, QColor(hex_color))
    return img


def icon_pixmap(name: str, size: int = 32) -> QPixmap:
    if name not in _ICONS:
        raise KeyError(name)
    if size <= 0 or size % 16:
        raise ValueError(f"icon size must be a positive multiple of 16, got {size}")
    img = _base_image(name).scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
                                   Qt.TransformationMode.FastTransformation)
    return QPixmap.fromImage(img)


def icon(name: str) -> QIcon:
    ic = QIcon()
    for size in (16, 32):
        ic.addPixmap(icon_pixmap(name, size))
    return ic
