"""ProgramWindow: one program's MDI window (Win95 title bar painted by Win95Style)."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QEvent, QRect, Qt, QTimer, Signal
from PySide6.QtWidgets import QMdiSubWindow, QWidget

from aws95.core.settings import Settings
from aws95.kit.icons import icon
from aws95.shell.registry import ProgramManifest


def encode_rect(rect: QRect) -> QByteArray:
    return QByteArray(f"{rect.x()},{rect.y()},{rect.width()},{rect.height()}".encode())


def decode_rect(data: QByteArray | None) -> QRect | None:
    if data is None:
        return None
    try:
        x, y, w, h = (int(v) for v in bytes(data).decode().split(","))
    except (ValueError, UnicodeDecodeError):
        return None
    if w <= 0 or h <= 0:
        return None
    return QRect(x, y, w, h)


def clamp_rect(rect: QRect, area: QRect, min_visible: int = 40) -> QRect | None:
    """Keep the title bar reachable; None if the rect is hopelessly off-desktop."""
    if not rect.intersects(area.adjusted(-rect.width(), -rect.height(), 0, 0)):
        return None
    w, h = min(rect.width(), area.width()), min(rect.height(), area.height())
    x = max(area.left() - w + min_visible, min(rect.x(), area.right() - min_visible))
    y = max(area.top(), min(rect.y(), area.bottom() - min_visible))
    if w < rect.width():        # oversized: shrunk to the area, so pin it there
        x = area.left()
    if h < rect.height():
        y = area.top()
    return QRect(x, y, w, h)


class ProgramWindow(QMdiSubWindow):
    closed = Signal(str, str)
    minimized_changed = Signal(bool)
    maximized = Signal()    # a real transition into the maximized state (not a silent re-maximize)

    def __init__(self, manifest: ProgramManifest, content: QWidget, settings: Settings, instance: int = 0) -> None:
        super().__init__()
        self.manifest = manifest
        self.instance = instance
        self._settings = settings
        self._minimized = False
        self._demo = False
        self._silent_state = False
        self._is_max = False        # own record: QMdiSubWindow sends stale oldState() values
        self.is_closing = False
        self._normal_rect: QRect | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWidget(content)
        self.setWindowIcon(icon(manifest.icon))
        self.setProperty("aws95ChipText", "")
        self.setProperty("aws95ChipColor", "")
        self._update_title()

    @property
    def geometry_key(self) -> str:
        return self.manifest.id if self.manifest.instancing == "singleton" else f"{self.manifest.id}#{self.instance}"

    def content(self) -> QWidget:
        return self.widget()

    def set_scope_chip(self, text: str | None, color: str | None) -> None:
        self.setProperty("aws95ChipText", text or "")
        self.setProperty("aws95ChipColor", color or "")
        self.update()

    def set_demo(self, on: bool) -> None:
        self._demo = on
        self._update_title()

    def _update_title(self) -> None:
        self.setWindowTitle(self.manifest.title + (" [DEMO]" if self._demo else ""))

    def restore_saved_geometry(self, area: QRect) -> bool:
        rect = decode_rect(self._settings.geometry(self.geometry_key))
        if rect is None:
            return False
        clamped = clamp_rect(rect, area)
        if clamped is None:
            return False
        self.setGeometry(clamped)
        return True

    def save_geometry(self) -> None:
        if not self.isMaximized() and not self._minimized:
            self._settings.set_geometry(self.geometry_key, encode_rect(self.geometry()))

    @property
    def is_minimized_to_taskbar(self) -> bool:
        return self._minimized

    def minimize_to_taskbar(self) -> None:
        if self._minimized:
            return
        self._minimized = True
        self.hide()
        self.minimized_changed.emit(True)

    def restore_from_taskbar(self) -> None:
        was = self._minimized
        self._minimized = False
        self.show()
        area = self.mdiArea()
        if area is not None:
            area.setActiveSubWindow(self)
        if was:
            self.minimized_changed.emit(False)

    def _track_normal_rect(self) -> None:
        # Test flags, not equality: an active MDI subwindow also reports WindowActive.
        special = (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowMinimized
                   | Qt.WindowState.WindowFullScreen)
        if not (self.windowState() & special) and self.isVisible():
            self._normal_rect = QRect(self.geometry())

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._track_normal_rect()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.clearMask()    # Win95 windows are square: drop the rounded-corner mask the style would set
        self._track_normal_rect()

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.StyleChange:
            super().changeEvent(event)      # QMdiSubWindow re-applies its frame mask on setStyle
            self.clearMask()
            return
        minimizing = (event.type() == QEvent.Type.WindowStateChange
                      and bool(self.windowState() & Qt.WindowState.WindowMinimized)
                      and not self._minimized)
        was_max = minimizing and bool(event.oldState() & Qt.WindowState.WindowMaximized)
        maximizing = False
        if event.type() == QEvent.Type.WindowStateChange:
            now_max = bool(self.windowState() & Qt.WindowState.WindowMaximized)
            maximizing = now_max and not self._is_max and not self._silent_state
            self._is_max = now_max
        super().changeEvent(event)
        if maximizing:
            self.maximized.emit()
        if minimizing:
            # Let Qt finish its own minimize, then undo it and route to the taskbar.
            QTimer.singleShot(0, lambda: self._route_native_minimize(self._normal_rect, was_max))

    def _route_native_minimize(self, rect: QRect | None, was_max: bool) -> None:
        if self._minimized or self.is_closing:
            return
        self._silent_state = True   # undoing Qt's minimize is not a user maximize
        try:
            self.setWindowState(Qt.WindowState.WindowNoState)
            if rect is not None:
                self.setGeometry(rect)
            if was_max:
                self.setWindowState(Qt.WindowState.WindowMaximized)
        finally:
            self._silent_state = False
        self.minimize_to_taskbar()

    def closeEvent(self, event) -> None:  # noqa: N802
        super().closeEvent(event)
        if event.isAccepted():
            self.is_closing = True
            self.save_geometry()
            self.closed.emit(self.manifest.id, self.manifest.title)
