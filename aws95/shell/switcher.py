"""Alt+` switcher (Alt+Tab belongs to the OS): classic centred icon strip, MRU order."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QDialog, QWidget
from shiboken6 import isValid

from aws95.kit.icons import icon_pixmap
from aws95.kit.paint import draw_bevel, draw_focus_rect
from aws95.kit.theme import current_theme
from aws95.shell.window import ProgramWindow

CELL = 48


class WindowSwitcher(QDialog):
    def __init__(self, desktop, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self._desktop = desktop
        self._windows: list[ProgramWindow] = []
        self._index = 0
        self._filtering = False
        self._was_active = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    @staticmethod
    def _alive(w: ProgramWindow) -> bool:
        return isValid(w) and not w.is_closing

    def _prune(self) -> None:
        """Drop windows closed since open(), keeping the selection on the same window."""
        chosen = self.selected()
        self._windows = [w for w in self._windows if self._alive(w)]
        if chosen in self._windows:
            self._index = self._windows.index(chosen)
        else:
            self._index = min(self._index, max(len(self._windows) - 1, 0))

    def open(self) -> bool:  # noqa: A003
        self._windows = self._desktop.mru_windows()
        if len(self._windows) < 2:
            return False
        self._index = 1
        self._was_active = False
        self.resize(max(240, CELL * len(self._windows) + 24), CELL + 48)
        host = self._desktop.window()
        self.move(host.geometry().center() - self.rect().center())
        self._set_filter(True)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.PopupFocusReason)
        QTimer.singleShot(0, self._take_focus)
        return True

    def _take_focus(self) -> None:
        if self.isVisible():
            self.activateWindow()
            self.setFocus(Qt.FocusReason.PopupFocusReason)

    def _set_filter(self, on: bool) -> None:
        app = QApplication.instance()
        if app is None or on == self._filtering:
            return
        (app.installEventFilter if on else app.removeEventFilter)(self)
        self._filtering = on

    def selected(self) -> ProgramWindow | None:
        if self._windows and 0 <= self._index < len(self._windows):
            w = self._windows[self._index]
            return w if self._alive(w) else None
        return None

    def cycle(self, step: int = 1) -> None:
        self._prune()
        if self._windows:
            self._index = (self._index + step) % len(self._windows)
            self.update()

    def commit(self) -> None:
        self._prune()
        chosen = self.selected()
        self.hide()
        if chosen is not None:
            self._desktop.activate(chosen)

    def cancel(self) -> None:
        self.hide()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._set_filter(False)
        super().hideEvent(event)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        # Alt may be released before this dialog has focus; catch it app-wide so it never sticks open.
        if (
            self.isVisible()
            and event.type() == QEvent.Type.KeyRelease
            and event.key() == Qt.Key.Key_Alt
            and not event.isAutoRepeat()
        ):
            self.commit()
        return False

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.ActivationChange and self.isVisible():
            if self.isActiveWindow():
                self._was_active = True
            elif self._was_active:
                self.commit()  # lost activation while open: do not linger
                return
        super().changeEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_QuoteLeft:
            self.cycle(-1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
        elif event.key() == Qt.Key.Key_Escape:
            self.cancel()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.commit()

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Alt and self.isVisible():
            self.commit()

    def paintEvent(self, event) -> None:  # noqa: N802
        self._prune()
        t = current_theme()
        p = QPainter(self)
        try:
            draw_bevel(p, self.rect(), t, kind="panel", fill=t.face)
            x0 = (self.width() - CELL * len(self._windows)) // 2
            for i, w in enumerate(self._windows):
                cell = QRect(x0 + i * CELL, 10, CELL, CELL)
                p.drawPixmap(cell.left() + 8, cell.top() + 8, icon_pixmap(w.manifest.icon, 32))
                if i == self._index:
                    draw_focus_rect(p, cell.adjusted(2, 2, -2, -2), t.text)
            p.setPen(QColor(t.text))
            chosen = self.selected()
            if chosen is not None:
                # drawText is plain text; no rich-text interpretation of titles.
                p.drawText(QRect(8, CELL + 16, self.width() - 16, 24),
                           int(Qt.AlignmentFlag.AlignCenter), chosen.windowTitle())
        finally:
            p.end()
