"""App-wide stylesheet: intentionally empty. Any QSS makes Qt re-polish every widget on a theme
switch (the dominant cost) and can bypass Win95Style. Tooltip colours come from the palette
(ToolTipBase / ToolTipText in build_palette)."""
from __future__ import annotations

from aws95.kit.theme import Theme


def build_qss(theme: Theme) -> str:
    return ""
