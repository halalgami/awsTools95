"""Desktop: the teal MDI area that hosts every ProgramWindow."""
from __future__ import annotations

from functools import partial
from typing import Callable, Literal

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPixmap
from PySide6.QtWidgets import QMdiArea, QMdiSubWindow
from shiboken6 import isValid

from aws95.core.settings import Settings
from aws95.kit.theme import current_theme, theme_bus
from aws95.shell.context import ProgramContext
from aws95.shell.recycle import RecycleBinStore
from aws95.shell.registry import Registry
from aws95.shell.window import ProgramWindow

DEFAULT_SIZE = (640, 420)
CASCADE_STEP = 24


def wallpaper_brush(kind: str, base_hex: str) -> QBrush:
    base = QColor(base_hex)
    if kind == "none":
        return QBrush(base)
    tile = QPixmap(8, 8)
    tile.fill(base)
    p = QPainter(tile)
    p.setPen(base.darker(125))
    if kind == "dots":
        p.drawPoint(1, 1)
        p.drawPoint(5, 5)
    else:  # bricks
        p.drawLine(0, 3, 7, 3)
        p.drawLine(0, 7, 7, 7)
        p.drawLine(3, 0, 3, 3)
        p.drawLine(7, 4, 7, 7)
    p.end()
    return QBrush(tile)


class Desktop(QMdiArea):
    windows_changed = Signal()
    active_window_changed = Signal(object)
    window_opened = Signal(object)

    def __init__(self, registry: Registry, context_factory: Callable[[], ProgramContext],
                 settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self._registry = registry
        self._ctx_factory = context_factory
        self._settings = settings
        self._mru: list[ProgramWindow] = []
        self.closing_all = False        # set by ShellWindow.closeEvent: a shutdown, not the user closing windows
        self.recycle = RecycleBinStore(self.open_program, parent=self)
        self.setOption(QMdiArea.AreaOption.DontMaximizeSubWindowOnActivation, True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.subWindowActivated.connect(self._on_activated)
        theme_bus().changed.connect(self._apply_background)
        settings.changed.connect(self._on_setting)
        self._apply_background()

    # ----- windows -----
    def program_windows(self) -> list[ProgramWindow]:
        return [w for w in self.subWindowList() if isinstance(w, ProgramWindow) and not w.is_closing]

    def active_program_window(self) -> ProgramWindow | None:
        w = self.activeSubWindow()
        return w if isinstance(w, ProgramWindow) and not w.is_closing else None

    def mru_windows(self) -> list[ProgramWindow]:
        live = self.program_windows()
        self._mru = [w for w in self._mru if isValid(w) and w in live]
        return self._mru + [w for w in live if w not in self._mru]

    def activate(self, window: ProgramWindow) -> None:
        if window.is_minimized_to_taskbar:
            window.restore_from_taskbar()
        else:
            self.setActiveSubWindow(window)

    def open_program(self, program_id: str) -> ProgramWindow:
        manifest = self._registry.get(program_id)
        same = [w for w in self.program_windows() if w.manifest.id == program_id]
        if manifest.instancing == "singleton" and same:
            self.activate(same[0])
            return same[0]
        used = {w.instance for w in same}
        instance = next(i for i in range(len(used) + 1) if i not in used)
        ctx = self._ctx_factory()
        content = manifest.load_factory()(ctx)
        window = ProgramWindow(manifest, content, self._settings, instance)
        if getattr(ctx.providers, "is_demo", False):
            window.set_demo(True)       # demo data is always watermarked, title included
        self.addSubWindow(window)
        area = self.viewport().rect()
        if not window.restore_saved_geometry(area):
            n = len(self.program_windows()) - 1
            w, h = min(DEFAULT_SIZE[0], area.width()), min(DEFAULT_SIZE[1], area.height())
            offset = CASCADE_STEP * (n % 8)
            window.setGeometry(QRect(area.left() + offset, area.top() + offset, w, h))
        # QMdiArea.removeSubWindow() (run inside QMdiSubWindow.closeEvent) disconnects every
        # window->area connection, so closed/destroyed go through receiver-less partials.
        window.closed.connect(partial(self._on_window_closed))
        window.minimized_changed.connect(self._on_minimized_changed)
        window.destroyed.connect(partial(self._emit_windows_changed))
        window.show()
        self.setActiveSubWindow(window)
        self.window_opened.emit(window)
        self.windows_changed.emit()
        return window

    def _on_window_closed(self, program_id: str, title: str) -> None:
        if not self.closing_all:
            self.recycle.add(program_id, title)
        self.windows_changed.emit()

    def _on_minimized_changed(self, minimized: bool) -> None:
        if minimized:
            sender = self.sender()
            if sender is self.activeSubWindow():
                nxt = next((w for w in self.mru_windows()
                            if w is not sender and not w.is_minimized_to_taskbar), None)
                if nxt is not None:
                    self.setActiveSubWindow(nxt)
                else:
                    self.setActiveSubWindow(None)
                    self.active_window_changed.emit(None)
        self.windows_changed.emit()

    def _emit_windows_changed(self, *_args) -> None:
        self.windows_changed.emit()

    def _on_activated(self, window: QMdiSubWindow | None) -> None:
        if isinstance(window, ProgramWindow):
            self._mru = [w for w in self._mru if isValid(w) and w is not window]
            self._mru.insert(0, window)
            self.active_window_changed.emit(window)
        else:
            self.active_window_changed.emit(None)

    # ----- arrangement -----
    def _visible(self) -> list[ProgramWindow]:
        return [w for w in self.program_windows() if not w.is_minimized_to_taskbar]

    def tile(self, orientation: Literal["horizontal", "vertical"]) -> None:
        wins = self._visible()
        if not wins:
            return
        area = self.viewport().rect()
        n = len(wins)
        for i, w in enumerate(wins):
            w.showNormal()
            if orientation == "vertical":       # side-by-side columns
                cw = area.width() // n
                w.setGeometry(QRect(area.left() + i * cw, area.top(), cw, area.height()))
            else:                                # stacked rows
                rh = area.height() // n
                w.setGeometry(QRect(area.left(), area.top() + i * rh, area.width(), rh))

    def cascade(self) -> None:
        area = self.viewport().rect()
        w = int(area.width() * 0.6)
        h = int(area.height() * 0.6)
        for i, win in enumerate(self._visible()):
            win.showNormal()
            off = CASCADE_STEP * (i % 10)
            win.setGeometry(QRect(area.left() + off, area.top() + off, w, h))
            win.raise_()

    def minimize_all(self) -> None:
        for w in self.program_windows():
            w.minimize_to_taskbar()

    # ----- wallpaper -----
    def _apply_background(self, *_args) -> None:
        self.setBackground(wallpaper_brush(self._settings.wallpaper, current_theme().desktop))

    def _on_setting(self, key: str) -> None:
        if key == "appearance/wallpaper":
            self._apply_background()
