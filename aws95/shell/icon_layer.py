"""Desktop icons: single-click select, double-click/Enter open, arrows move, drag to arrange."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QMenu, QWidget

from aws95.core.settings import Settings
from aws95.kit.icons import icon_pixmap
from aws95.kit.paint import draw_focus_rect
from aws95.shell.registry import ProgramManifest, Registry

ICON_W, ICON_H = 76, 76              # room for a two-line label ("Take Snapshot", "Recycle Bin")
TEXT_TOP = 36
GRID_X, GRID_Y, MARGIN = 84, 80, 8


def clamp_to(pos: QPoint, area: QSize) -> QPoint:
    """Keep an icon fully inside `area`; a degenerate area leaves the position alone."""
    if area.width() < ICON_W or area.height() < ICON_H:
        return pos
    return QPoint(max(0, min(pos.x(), area.width() - ICON_W)), max(0, min(pos.y(), area.height() - ICON_H)))


@dataclass(frozen=True)
class DesktopCommand:
    """A desktop icon that runs a shell command instead of opening a program (e.g. Take Snapshot). Commands come
    before the program icons."""
    id: str
    title: str
    icon: str
    run: Callable[[], object]


class DesktopIcon(QWidget):
    activated = Signal(str)
    selected = Signal(str)
    moved = Signal(str, QPoint)

    def __init__(self, manifest: ProgramManifest | DesktopCommand, parent: QWidget) -> None:
        super().__init__(parent)
        self.program_id = manifest.id
        self._title = manifest.title
        self._pixmap = icon_pixmap(manifest.icon, 32)
        self._selected = False
        self._press: QPoint | None = None
        self._dragging = False
        self.setFixedSize(QSize(ICON_W, ICON_H))
        self.setToolTip(manifest.title)

    def set_selected(self, on: bool) -> None:
        self._selected = on
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
            self._dragging = False
            self.selected.emit(self.program_id)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press is None:
            return
        offset = event.position().toPoint() - self._press
        if self._dragging or offset.manhattanLength() > 6:
            self._dragging = True
            # follow the cursor in parent coordinates, so a clamped icon does not drift
            self.move(self._clamped(self.mapToParent(event.position().toPoint()) - self._press))

    def _clamped(self, pos: QPoint) -> QPoint:
        return clamp_to(pos, self.parentWidget().size())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._dragging:
            self.moved.emit(self.program_id, self.pos())
        self._press = None
        self._dragging = False

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.program_id)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.drawPixmap((ICON_W - 32) // 2, 2, self._pixmap)
        text_rect = QRect(0, TEXT_TOP, ICON_W, ICON_H - TEXT_TOP)
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() * 0.9))
        p.setFont(font)
        bounds = p.fontMetrics().boundingRect(text_rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap), self._title)
        if self._selected:
            p.fillRect(bounds.adjusted(-2, 0, 2, 0), QColor("#000080"))
            draw_focus_rect(p, bounds.adjusted(-2, 0, 2, 0), "#ffffff")
        else:
            p.setPen(QColor("#000000"))
            p.drawText(text_rect.translated(1, 1), int(Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap), self._title)
        p.setPen(QColor("#ffffff"))
        p.drawText(text_rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap), self._title)
        p.end()


class IconLayer(QWidget):
    context_menu_requested = Signal(object)

    def __init__(self, desktop, registry: Registry, settings: Settings, open_program: Callable[[str], object], *,
                 commands: Sequence[DesktopCommand] = ()) -> None:
        super().__init__(desktop.viewport())
        self._desktop = desktop
        self._settings = settings
        self._open = open_program
        self._icons: list[DesktopIcon] = []
        self._selected: str | None = None
        self._commands = {c.id: c for c in commands}
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for m in (*commands, *registry.desktop()):
            ic = DesktopIcon(m, self)
            ic.activated.connect(self._open_id)
            ic.selected.connect(self.select)
            ic.moved.connect(self.move_icon)
            self._icons.append(ic)
        self.setGeometry(desktop.viewport().rect())
        self._place_all()
        desktop.viewport().installEventFilter(self)
        desktop.window_opened.connect(self._keep_below)
        self.lower()
        self.show()

    def icons(self) -> list[DesktopIcon]:
        return list(self._icons)

    def selected_id(self) -> str | None:
        return self._selected

    def select(self, program_id: str) -> None:
        self._selected = program_id
        for ic in self._icons:
            ic.set_selected(ic.program_id == program_id)
        self.setFocus()

    def _open_id(self, program_id: str) -> None:
        command = self._commands.get(program_id)
        if command is not None:
            command.run()
        else:
            self._open(program_id)

    def _rows(self) -> int:
        return max(1, (self.height() or 600) // GRID_Y)

    def _default_pos(self, index: int) -> QPoint:
        rows = self._rows()
        return QPoint(MARGIN + (index // rows) * GRID_X, MARGIN + (index % rows) * GRID_Y)

    def _place_all(self) -> None:
        for i, ic in enumerate(self._icons):
            saved = self._settings.icon_position(ic.program_id)
            ic.move(clamp_to(QPoint(*saved) if saved else self._default_pos(i), self.size()))

    def move_icon(self, program_id: str, pos: QPoint) -> None:
        pos = clamp_to(pos, self.size())
        for ic in self._icons:
            if ic.program_id == program_id:
                ic.move(pos)
                ic.raise_()
        self._settings.set_icon_position(program_id, pos.x(), pos.y())

    def arrange_icons(self) -> None:
        self._settings.clear_icon_positions()
        self._place_all()

    def _keep_below(self, *_args) -> None:
        self.lower()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._desktop.viewport() and event.type() == QEvent.Type.Resize:
            self.setGeometry(self._desktop.viewport().rect())
            self.lower()
            self._place_all()
        return False

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if not self._icons:
            return
        ids = [ic.program_id for ic in self._icons]
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._selected in ids:
                self._open_id(self._selected)
            return
        rows = self._rows()
        step = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1, Qt.Key.Key_Right: rows, Qt.Key.Key_Left: -rows}.get(key)
        if step is None:
            super().keyPressEvent(event)
            return
        if self._selected not in ids:
            self.select(ids[0])
            return
        self.select(ids[max(0, min(len(ids) - 1, ids.index(self._selected) + step))])

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._selected = None
        for ic in self._icons:
            ic.set_selected(False)
        self.setFocus()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        event.accept()

    def build_context_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("&Arrange Icons").triggered.connect(self.arrange_icons)
        self.context_menu_requested.emit(menu)
        return menu

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = self.build_context_menu()
        # One menu per right-click: delete it once hidden. popup(), not exec(): deleting a menu whose
        # nested exec() loop is still on the stack is unsafe. Triggered actions run before the deferred delete.
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup(event.globalPos())
