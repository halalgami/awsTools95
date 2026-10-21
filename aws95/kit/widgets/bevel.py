from __future__ import annotations

from typing import Literal

from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFrame, QWidget

from aws95.kit.paint import draw_bevel
from aws95.kit.theme import current_theme

Look = Literal["raised", "sunken", "well"]
_LOOKS = ("raised", "sunken", "well")


class BevelPanel(QFrame):
    """A Win95 panel: raised (window face), sunken (face, inset) or well (white/graphite inset)."""

    def __init__(self, look: Look = "raised", parent: QWidget | None = None) -> None:
        if look not in _LOOKS:
            raise ValueError(f"unknown look {look!r}; expected one of {_LOOKS}")
        super().__init__(parent)
        self._look = look
        self.setContentsMargins(3, 3, 3, 3)

    def paintEvent(self, event) -> None:  # noqa: N802
        t = current_theme()
        p = QPainter(self)
        if self._look == "raised":
            draw_bevel(p, self.rect(), t, kind="panel", fill=t.face)
        elif self._look == "sunken":
            draw_bevel(p, self.rect(), t, kind="field", fill=t.face)
        else:
            draw_bevel(p, self.rect(), t, kind="field", fill=t.well)
        p.end()
