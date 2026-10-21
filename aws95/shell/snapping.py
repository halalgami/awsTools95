"""Drag-to-edge and keyboard snapping for ProgramWindows (spec §6.3)."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QStyle, QWidget
from shiboken6 import isValid

from aws95.shell.window import ProgramWindow


class Zone(Enum):
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"


def zone_at(pos: QPoint, area: QRect, threshold: int = 8) -> Zone | None:
    left = pos.x() <= area.left() + threshold
    right = pos.x() >= area.right() - threshold
    top = pos.y() <= area.top() + threshold
    bottom = pos.y() >= area.bottom() - threshold
    if top and left:
        return Zone.TOP_LEFT
    if top and right:
        return Zone.TOP_RIGHT
    if bottom and left:
        return Zone.BOTTOM_LEFT
    if bottom and right:
        return Zone.BOTTOM_RIGHT
    if left:
        return Zone.LEFT
    if right:
        return Zone.RIGHT
    if top:
        return Zone.TOP
    return None


def zone_rect(zone: Zone, area: QRect) -> QRect:
    x, y, w, h = area.x(), area.y(), area.width(), area.height()
    hw, hh = w // 2, h // 2
    return {
        Zone.LEFT: QRect(x, y, hw, h),
        Zone.RIGHT: QRect(x + hw, y, w - hw, h),
        Zone.TOP: QRect(area),
        Zone.TOP_LEFT: QRect(x, y, hw, hh),
        Zone.TOP_RIGHT: QRect(x + hw, y, w - hw, hh),
        Zone.BOTTOM_LEFT: QRect(x, y + hh, hw, h - hh),
        Zone.BOTTOM_RIGHT: QRect(x + hw, y + hh, w - hw, h - hh),
    }[zone]


def _left_button_down() -> bool:
    return bool(QGuiApplication.mouseButtons() & Qt.MouseButton.LeftButton)


def _cursor_pos() -> QPoint:
    return QCursor.pos()


class SnapPreview(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def show_rect(self, rect: QRect) -> None:
        self.setGeometry(rect)
        self.raise_()
        self.show()

    def clear(self) -> None:
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(255, 255, 255, 30))
        p.setPen(QPen(QColor(255, 255, 255, 220), 2, Qt.PenStyle.DashLine))
        p.drawRect(self.rect().adjusted(1, 1, -2, -2))
        p.end()


class SnapController(QObject):
    def __init__(self, desktop) -> None:
        super().__init__(desktop)
        self._desktop = desktop
        self.preview = SnapPreview(desktop.viewport())
        self._watched: set[ProgramWindow] = set()
        self._pending: dict[ProgramWindow, Zone | None] = {}
        self._dragging: set[ProgramWindow] = set()  # left press started on the title bar
        desktop.window_opened.connect(self.watch)

    def watch(self, window: ProgramWindow) -> None:
        if window in self._watched:
            return
        self._watched.add(window)
        window.installEventFilter(self)
        window.destroyed.connect(self._forget)  # bound method: no sender capture

    def _forget(self, *_args) -> None:
        # destroyed(obj) hands us a half-dead QObject whose wrapper identity is unreliable,
        # so prune by validity instead of matching the argument.
        self._watched = {w for w in self._watched if isValid(w)}
        self._pending = {w: z for w, z in self._pending.items() if isValid(w)}
        self._dragging = {w for w in self._dragging if isValid(w)}
        if not self._pending and isValid(self.preview):  # preview dies with the desktop
            self.preview.clear()

    def _in_title_bar(self, window: ProgramWindow, pos: QPoint) -> bool:
        style = window.style()
        fw = style.pixelMetric(QStyle.PixelMetric.PM_MdiSubWindowFrameWidth, None, window)
        th = style.pixelMetric(QStyle.PixelMetric.PM_TitleBarHeight, None, window)
        return fw <= pos.x() < window.width() - fw and fw <= pos.y() < fw + th

    def cancel(self, window: ProgramWindow) -> None:
        """Abandon any in-progress drag of `window` without applying a zone."""
        self._dragging.discard(window)
        self._pending.pop(window, None)
        if not self._pending and isValid(self.preview):
            self.preview.clear()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if isinstance(obj, ProgramWindow):
            et = event.type()
            if et in (QEvent.Type.MouseButtonPress, QEvent.Type.NonClientAreaMouseButtonPress):
                if event.button() == Qt.MouseButton.LeftButton and self._in_title_bar(
                        obj, event.position().toPoint()):
                    self._dragging.add(obj)
                else:
                    self._dragging.discard(obj)
            elif et == QEvent.Type.Move:
                if obj in self._dragging and _left_button_down():
                    self.update(obj, self._desktop.viewport().mapFromGlobal(_cursor_pos()))
                elif obj in self._pending or obj in self._dragging:
                    self.cancel(obj)  # moved with no button held: the release was missed
            elif et in (QEvent.Type.MouseButtonRelease, QEvent.Type.NonClientAreaMouseButtonRelease):
                if obj in self._dragging:
                    self._dragging.discard(obj)
                    self.finish(obj)
                else:
                    self.cancel(obj)
            elif et in (QEvent.Type.Hide, QEvent.Type.Close, QEvent.Type.WindowStateChange):
                self.cancel(obj)
        return False

    def update(self, window: ProgramWindow, pos: QPoint) -> None:
        area = self._desktop.viewport().rect()
        zone = zone_at(pos, area)
        self._pending[window] = zone
        if zone is None:
            self.preview.clear()
        else:
            self.preview.show_rect(zone_rect(zone, area))

    def finish(self, window: ProgramWindow) -> Zone | None:
        zone = self._pending.pop(window, None)
        self.preview.clear()
        if zone is not None:
            self.apply(window, zone)
        return zone

    def apply(self, window: ProgramWindow, zone: Zone) -> None:
        if window.is_closing or window.is_minimized_to_taskbar:
            return
        if zone is Zone.TOP:
            window.showMaximized()
            return
        window.showNormal()
        window.setGeometry(zone_rect(zone, self._desktop.viewport().rect()))

    def snap_active(self, direction: Literal["left", "right", "up", "down"]) -> None:
        window = self._desktop.active_program_window()
        if window is None or window.is_minimized_to_taskbar:
            return
        if direction == "left":
            self.apply(window, Zone.LEFT)
        elif direction == "right":
            self.apply(window, Zone.RIGHT)
        elif direction == "up":
            self.apply(window, Zone.TOP)
        elif window.isMaximized():
            window.showNormal()
        else:
            window.minimize_to_taskbar()
