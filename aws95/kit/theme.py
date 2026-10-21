"""Classic+ Light / Dark tokens (spec §5.1), WCAG checks, and the live theme manager."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication

from aws95.core.settings import Settings, ThemeMode


@dataclass(frozen=True)
class Theme:
    name: str
    face: str
    face2: str
    hi: str
    lo: str
    dark: str
    well: str
    zebra: str
    text: str
    muted: str
    sel: str
    sel_text: str
    link: str
    title_active_start: str
    title_active_end: str
    title_inactive_start: str
    title_inactive_end: str
    title_text: str
    title_text_inactive: str
    desktop: str
    money: str
    ok: str
    warn: str
    bad: str
    tooltip_bg: str
    tooltip_text: str
    banner_bg: str
    banner_text: str
    disabled_text: str         # disabled item text; Dark meets 4.5:1 on face and well
    indicator_border: str      # check/radio box outline: >= 3:1 against face and well


LIGHT = Theme(
    name="light", face="#c0c0c0", face2="#d4d4d4", hi="#ffffff", lo="#808080", dark="#404040",
    well="#ffffff", zebra="#f2f2f7", text="#000000", muted="#444444",
    sel="#000080", sel_text="#ffffff", link="#000080",
    title_active_start="#000080", title_active_end="#1084d0",
    title_inactive_start="#808080", title_inactive_end="#b5b5b5",
    title_text="#ffffff", title_text_inactive="#d8d8d8",
    desktop="#008080", money="#000000", ok="#00a000", warn="#c88000", bad="#b00000",
    tooltip_bg="#ffffe1", tooltip_text="#000000", banner_bg="#ffffe1", banner_text="#000000",
    disabled_text="#666666", indicator_border="#404040",
)

DARK = Theme(
    name="dark", face="#2d2f36", face2="#383b43", hi="#4f5360", lo="#17181c", dark="#0e0f12",
    well="#1d1e23", zebra="#23252b", text="#e3e5ec", muted="#9a9fb0",
    sel="#2f5fa8", sel_text="#ffffff", link="#8ab4ff",
    title_active_start="#1a2a66", title_active_end="#2f6fb3",
    title_inactive_start="#3a3d46", title_inactive_end="#4a4e59",
    title_text="#ffffff", title_text_inactive="#a8acb8",
    desktop="#123c40", money="#ffcc73", ok="#5fd38d", warn="#ffc46b", bad="#ff7b7b",
    tooltip_bg="#3b3a2a", tooltip_text="#f0ecd0", banner_bg="#4a4220", banner_text="#ffe9a8",
    disabled_text="#a3a8b8", indicator_border="#9a9fb0",
)

# Text that must meet WCAG AA (4.5:1). Title text is checked against the gradient start only,
# because that is where the title text is drawn. Inactive title text is exempt (WCAG 1.4.3
# excludes text of inactive UI components).
TEXT_PAIRS: tuple[tuple[str, str], ...] = (
    ("text", "face"), ("text", "face2"), ("text", "well"), ("text", "zebra"),
    ("muted", "face"), ("muted", "well"), ("sel_text", "sel"), ("link", "face"), ("link", "well"),
    ("title_text", "title_active_start"), ("money", "well"), ("money", "face"),
    ("tooltip_text", "tooltip_bg"), ("banner_text", "banner_bg"),
    ("disabled_text", "well"),
)
# Disabled text is exempt from WCAG 1.4.3. Dark still meets 4.5:1 (checked in test_dark_disabled_text_is_readable);
# Light keeps the classic grey look, so it is held to 3:1 on the chrome surfaces instead.
EXEMPT_TEXT_PAIRS: tuple[tuple[str, str], ...] = (
    ("disabled_text", "face"), ("disabled_text", "face2"),
)
# Status colours are used for dots, icons and short labels: non-text UI contrast, 3:1.
UI_PAIRS: tuple[tuple[str, str], ...] = (
    ("ok", "well"), ("warn", "well"), ("bad", "well"),
    ("indicator_border", "face"), ("indicator_border", "well"),
)


def relative_luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(fg: str, bg: str) -> float:
    a, b = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


class _ThemeBus(QObject):
    changed = Signal(object)


_current: Theme = LIGHT
_bus: _ThemeBus | None = None


def theme_bus() -> _ThemeBus:
    global _bus
    if _bus is None:
        _bus = _ThemeBus()
    return _bus


def current_theme() -> Theme:
    return _current


def set_current(theme: Theme, *, notify: bool = True) -> bool:
    """Make `theme` current. Returns True if it changed; emits the bus unless notify=False."""
    global _current
    changed = theme is not _current
    _current = theme
    if changed and notify and _bus is not None:
        _bus.changed.emit(theme)
    return changed


class ThemeManager(QObject):
    changed = Signal(object)

    def __init__(self, settings: Settings, app: QGuiApplication, parent: QObject | None = None):
        super().__init__(parent)
        self._settings = settings
        self._app = app
        self._theme = self.resolve()
        set_current(self._theme, notify=False)
        settings.changed.connect(self._on_setting)
        app.styleHints().colorSchemeChanged.connect(self._on_os_scheme)

    @property
    def theme(self) -> Theme:
        return self._theme

    def _os_is_dark(self) -> bool:
        return self._app.styleHints().colorScheme() == Qt.ColorScheme.Dark

    def resolve(self) -> Theme:
        mode = self._settings.theme_mode
        if mode is ThemeMode.DARK:
            return DARK
        if mode is ThemeMode.LIGHT:
            return LIGHT
        return DARK if self._os_is_dark() else LIGHT

    def refresh(self) -> None:
        new = self.resolve()
        if new is not self._theme:
            self._theme = new
            set_current(new, notify=False)  # apply_theme is the single bus emitter
            self.changed.emit(new)

    @Slot()
    def _on_os_scheme(self, *_args) -> None:
        self.refresh()

    def _on_setting(self, key: str) -> None:
        if key == "appearance/theme_mode":
            self.refresh()
