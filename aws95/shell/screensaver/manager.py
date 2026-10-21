"""Screensaver manager: one random saver per idle session, every screen covered, data hidden,
dismissed ONLY by the Resume button (spec §6.8).

Privacy guarantees while a saver session is active:
- every visible top-level window is hidden (popups are closed and never re-shown);
- a top-level window that appears during the session is made transparent at once and hidden
  on the next event-loop turn (a modal one cannot be hidden without cancelling its exec(), so it
  stays transparent, under the stay-on-top saver windows, and the saver windows become its
  transient children so the modal does not block the Resume button);
- keyboard, mouse and shortcut input aimed at anything but the saver windows is swallowed;
- on dismissal exactly the windows this session hid are re-shown (unless closed meanwhile).

Background idle (ruling R13): when idle fires while another application is active, start_idle() opens a
privacy-only session instead: the windows are hidden and the guarantees above hold, but there are no
saver windows, no stay-on-top, no activation and no sound, so the other app is never hijacked. When
AWS 95 becomes the active application again the session is promoted to the full saver with its Resume
panel showing, so the data stays hidden until the user clicks Resume.

Ruling R15: a process with no visible top-level window has no Windows taskbar button, so the user could
not come back. During a privacy-only session the main window (``private_window``) is therefore blanked
(its central widget hidden) and then minimized instead of hidden, which keeps its taskbar button / Dock
tile; every other window is hidden as before. On promotion the sound plays; on Resume the window returns
to its prior state (normal, maximized or fullscreen) with its content visible again.
"""
from __future__ import annotations

import logging
import random
import weakref
from typing import Callable

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette, QScreen, QWindow
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget
from shiboken6 import isValid

from aws95.core.settings import Settings
from aws95.kit.widgets.bevel import BevelPanel
from aws95.shell.screensaver import SAVERS

log = logging.getLogger(__name__)

# Managers with a live session (lets test teardown end sessions that outlive closed windows).
live_sessions: weakref.WeakSet[ScreensaverManager] = weakref.WeakSet()

# Input that must never reach anything but the saver windows while a session is active.
_FOREIGN_INPUT = {
    QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride,
    QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick,
    QEvent.Type.Wheel, QEvent.Type.ContextMenu, QEvent.Type.InputMethod,
}
# Swallowed foreign input that should also reveal the Resume panel.
_REVEALING = {QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel}
_POPUP_TYPES = (Qt.WindowType.Popup, Qt.WindowType.ToolTip)
_ACTIVATE_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space)


def _is_popup(w: QWidget) -> bool:
    return w.windowType() in _POPUP_TYPES


class ResumePanel(BevelPanel):
    resume = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__("raised", parent)
        self.setFixedSize(300, 120)
        title = QLabel("AWS 95 Tools", self)
        f = QFont(title.font())
        f.setBold(True)
        title.setFont(f)
        title.setAutoFillBackground(True)
        pal = title.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor("#000080"))
        pal.setColor(QPalette.ColorRole.WindowText, QColor("#ffffff"))
        title.setPalette(pal)
        title.setContentsMargins(6, 2, 6, 2)
        body = QLabel("🔒 AWS 95 is idle. Account data hidden.", self)
        body.setTextFormat(Qt.TextFormat.PlainText)
        self.resume_button = QPushButton("Resume", self)
        self.resume_button.setDefault(True)
        self.resume_button.clicked.connect(self.resume)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 8)
        lay.addWidget(title)
        lay.addWidget(body)
        lay.addWidget(self.resume_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self._held_key: int | None = None    # the key that revealed the panel, until it is released
        self.resume_button.installEventFilter(self)
        self.hide()

    def arm(self, revealing_key: int | None) -> None:
        """Called on reveal: activation keys are ignored until ``revealing_key`` is released, so a
        held Enter/Space (auto-repeat) can never dismiss the saver it just revealed."""
        self._held_key = revealing_key

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.resume_button:
            t = event.type()
            if t == QEvent.Type.KeyPress and event.key() in _ACTIVATE_KEYS:
                if event.isAutoRepeat() or self._held_key is not None:
                    return True
            elif t == QEvent.Type.KeyRelease:
                self.key_released(event)
        return False

    def key_released(self, event) -> None:
        if not event.isAutoRepeat() and event.key() == self._held_key:
            self._held_key = None

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in _ACTIVATE_KEYS and self.resume_button.hasFocus() and not event.isAutoRepeat():
            self.resume_button.click()
            return
        event.ignore()


class SaverWindow(QWidget):
    input = Signal()
    closed = Signal()   # closed by someone other than the window manager (e.g. closeAllWindows on quit)

    def __init__(self, saver_cls, screen: QScreen, seed: int | None = None) -> None:
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        # Closing the saver while every app window is hidden must never count as "last window closed".
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        # Keys land here (and reveal the panel) when the Resume button is not focused.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.target_screen = screen
        self.revealing_key: int | None = None   # key of the latest input, None for mouse input
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.saver = saver_cls(self, seed=seed)
        self.saver.setMouseTracking(True)
        self.saver.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.saver.setGeometry(self.rect())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.saver.setGeometry(self.rect())

    def _poke(self) -> None:
        self.unsetCursor()
        self.input.emit()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self.revealing_key = None
        self._poke()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.revealing_key = None
        self._poke()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        self.revealing_key = event.key()
        self._poke()

    def wheelEvent(self, event) -> None:  # noqa: N802
        self.revealing_key = None
        self._poke()

    def closeEvent(self, event) -> None:  # noqa: N802
        # Alt+F4 / Cmd+W from the window manager must not uncover anything: only Resume ends a session.
        if event.spontaneous():
            event.ignore()
            self._poke()
            return
        super().closeEvent(event)
        self.closed.emit()


def _default_hide_targets() -> list[QWidget]:
    return [w for w in QApplication.topLevelWidgets() if w.isVisible() and not isinstance(w, SaverWindow)]


def _contains(items, obj) -> bool:
    return any(obj is item for item in items)


class ScreensaverManager(QObject):
    started = Signal(str)
    dismissed = Signal()

    def __init__(self, settings: Settings, sound=None, rng: random.Random | None = None,
                 hide_targets: Callable[[], list[QWidget]] | None = None, panel_hide_ms: int = 10_000,
                 app_state: Callable[[], Qt.ApplicationState] | None = None,
                 private_window: QMainWindow | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._sound = sound
        self._rng = rng or random.Random()
        self._hide_targets = hide_targets or _default_hide_targets
        self._app_state = app_state or QGuiApplication.applicationState
        self._private_window = private_window
        self._minimized: tuple[QMainWindow, Qt.WindowState] | None = None   # (window, state before R15 minimize)
        self._hidden: list[QWidget] = []                     # re-shown on dismiss
        self._suppressed: list[tuple[QWidget, float]] = []   # appeared mid-session: (window, opacity)
        self._modals: list[QWidget] = []                     # visible modals that appeared mid-session
        self._pending_hide: list[QWidget] = []
        self._prev_active: QWidget | None = None
        self._app_connected = False
        self.windows: list[SaverWindow] = []
        self.panel: ResumePanel | None = None
        self.active = False
        self.private = False    # privacy-only session: windows hidden, no saver windows yet (R13)
        self.current_name: str | None = None
        self._panel_timer = QTimer(self)
        self._panel_timer.setSingleShot(True)
        self._panel_timer.setInterval(panel_hide_ms)
        self._panel_timer.timeout.connect(self._hide_panel)

    # ----- public API -----
    def choose(self) -> str:
        choice = self._settings.saver_choice
        if choice in SAVERS:
            return choice
        return self._rng.choice(sorted(SAVERS))

    def preview(self, name: str | None = None) -> None:
        self.start(name)

    def start_idle(self) -> None:
        """Idle entry point: the full saver when AWS 95 is the active application, otherwise a
        privacy-only session that is promoted once the user comes back (R13)."""
        if self._app_state() == Qt.ApplicationState.ApplicationActive:
            self.start()
        else:
            self._start_private()

    def start(self, name: str | None = None) -> None:
        if self.active or QApplication.activeModalWidget() is not None:
            return
        screens = QGuiApplication.screens()
        if not screens:
            return
        name = name if name in SAVERS else self.choose()
        try:
            targets = self._visible_targets()
            self.current_name = name
            self._prev_active = QApplication.activeWindow()
            for screen in screens:
                self._add_window(screen)
            self.panel = self._make_panel(self.windows[0])
            # Only once every saver window exists does the session go live.
            self.active = True
            live_sessions.add(self)
            self._connect_app()
            self._hide_all(targets)
            self.windows[0].activateWindow()
        except Exception:
            log.exception("screensaver %r failed to start; rolling back", name)
            self._end_session(restore=True, emit=False)
            return
        if self._sound is not None:
            self._sound.play("saver_start")
        self.started.emit(name)

    def _start_private(self) -> None:
        if self.active or QApplication.activeModalWidget() is not None:
            return
        try:
            targets = self._visible_targets()
            self._prev_active = QApplication.activeWindow()
            self.active = self.private = True
            live_sessions.add(self)
            targets = self._minimize_private_window(targets)
            self._connect_app()
            self._hide_all(targets)
        except Exception:
            log.exception("privacy session failed to start; rolling back")
            self._end_session(restore=True, emit=False)

    def _minimize_private_window(self, targets: list[QWidget]) -> list[QWidget]:
        """R15: blank, then minimize, the main window; returns the targets still to hide."""
        w = self._private_window
        if w is None or not isValid(w) or not _contains(targets, w):
            return targets
        central = w.centralWidget()
        self._minimized = (w, w.windowState() & ~Qt.WindowState.WindowMinimized)
        if central is not None:
            central.hide()      # before minimizing, so OS thumbnails / Alt+Tab / Mission Control show nothing
        w.showMinimized()
        return [t for t in targets if t is not w]

    def _restore_private_window(self) -> None:
        if self._minimized is None:
            return
        w, state = self._minimized
        self._minimized = None
        if not isValid(w):
            return
        central = w.centralWidget()
        if central is not None and isValid(central):
            central.show()
        if state & Qt.WindowState.WindowFullScreen:
            w.showFullScreen()
        elif state & Qt.WindowState.WindowMaximized:
            w.showMaximized()
        else:
            w.showNormal()

    def _promote(self) -> None:
        """AWS 95 is active again: cover every screen and offer Resume straight away."""
        if not (self.active and self.private) or not QGuiApplication.screens():
            return      # no screen yet: stay private (still hidden); the next activation retries
        try:
            self.current_name = self.choose()
            for screen in QGuiApplication.screens():
                self._add_window(screen)
            self.panel = self._make_panel(self.windows[0])
            self.private = False
            self._show_panel(self.windows[0])
            if self._sound is not None:
                self._sound.play("saver_start")
        except Exception:
            # Never leave the user with hidden windows and no Resume: restore, and say so.
            log.exception("screensaver %r failed to cover a privacy session; restoring", self.current_name)
            self._end_session(restore=True)
            return
        self.started.emit(self.current_name)

    def _on_app_state_changed(self, state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive and self.active and self.private:
            self._promote()

    def _visible_targets(self) -> list[QWidget]:
        return [w for w in self._hide_targets() if w.isVisible() and not isinstance(w, SaverWindow)]

    def _hide_all(self, targets: list[QWidget]) -> None:
        for w in targets:
            w.hide()
            if not _is_popup(w):    # a closed menu/tooltip/balloon is never re-shown
                self._hidden.append(w)

    def dismiss(self) -> None:
        if self.active:
            self._end_session(restore=True)

    def _connect_app(self) -> None:
        app = QApplication.instance()
        app.installEventFilter(self)
        app.screenAdded.connect(self._on_screen_added)
        app.screenRemoved.connect(self._on_screen_removed)
        app.aboutToQuit.connect(self._on_about_to_quit)
        app.applicationStateChanged.connect(self._on_app_state_changed)
        self._app_connected = True

    def _disconnect_app(self) -> None:
        app = QApplication.instance()
        app.removeEventFilter(self)
        if not self._app_connected:
            return
        self._app_connected = False
        for sig, slot in ((app.screenAdded, self._on_screen_added), (app.screenRemoved, self._on_screen_removed),
                          (app.aboutToQuit, self._on_about_to_quit),
                          (app.applicationStateChanged, self._on_app_state_changed)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def _on_about_to_quit(self) -> None:
        """Real teardown: the app is quitting, so close the saver without uncovering anything."""
        if self.active:
            self._end_session(restore=False, emit=False)

    def _end_session(self, restore: bool, emit: bool = True) -> None:
        self._panel_timer.stop()
        self._disconnect_app()
        try:
            if restore:
                try:
                    self._restore_private_window()
                except Exception:
                    log.exception("screensaver: could not restore the main window")
                for w, opacity in self._suppressed:
                    try:
                        if isValid(w):
                            w.setWindowOpacity(opacity)
                    except Exception:
                        log.exception("screensaver: could not restore a window's opacity")
                for w in self._hidden:
                    try:
                        if isValid(w):
                            w.show()
                    except Exception:
                        log.exception("screensaver: could not re-show a hidden window")
        finally:
            # Whatever happened above, no saver window may outlive the session with a dead Resume.
            windows, self.windows = self.windows, []
            self.panel = None
            for win in windows:
                if isValid(win):
                    win.hide()
                    win.close()     # WA_DeleteOnClose: the window, its saver and the panel are deleted
            self.active = self.private = False
            live_sessions.discard(self)
            prev = self._prev_active
            self._minimized = None
            self._hidden = []
            self._suppressed = []
            self._modals = []
            self._pending_hide = []
            self._prev_active = None
            self.current_name = None
        if restore and prev is not None and isValid(prev) and prev.isVisible():
            prev.activateWindow()
        if restore and emit:
            self.dismissed.emit()

    # ----- saver windows and the Resume panel -----
    def _add_window(self, screen: QScreen) -> SaverWindow:
        win = SaverWindow(SAVERS[self.current_name], screen)
        win.input.connect(self._on_input)
        win.closed.connect(self._on_window_closed)
        self.windows.append(win)
        win.show()
        win.raise_()
        self._point_transients()
        return win

    def _make_panel(self, host: SaverWindow) -> ResumePanel:
        panel = ResumePanel(host)
        panel.resume.connect(self.dismiss)
        return panel

    def _panel_host(self) -> SaverWindow | None:
        if self.panel is not None and isValid(self.panel):
            host = self.panel.parentWidget()
            if _contains(self.windows, host):
                return host
        return next((w for w in self.windows if isValid(w)), None)

    def _on_input(self) -> None:
        if not self.active:
            return
        src = self.sender()
        if _contains(self.windows, src):
            self._show_panel(src, src.revealing_key)
        else:
            self._reveal()

    def _show_panel(self, host: SaverWindow, key: int | None = None) -> None:
        if self.panel is None or not isValid(self.panel):
            self.panel = self._make_panel(host)
        elif self.panel.parentWidget() is not host:
            self.panel.setParent(host)      # reveal on the screen the user is actually at
        panel = self.panel
        if not panel.isVisible() or key is not None:
            panel.arm(key)
        panel.move(host.rect().center() - panel.rect().center())
        panel.show()
        panel.raise_()
        host.raise_()
        host.activateWindow()
        panel.resume_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._panel_timer.start()

    def _hide_panel(self) -> None:
        if self.panel is not None and isValid(self.panel):
            self.panel.hide()
        for w in self.windows:
            if isValid(w):
                w.setCursor(Qt.CursorShape.BlankCursor)

    def _on_window_closed(self) -> None:
        """A saver window was closed by someone else (e.g. QApplication.closeAllWindows). That never
        ends the session: only Resume does (or aboutToQuit, for real teardown)."""
        src = self.sender()
        if not self.active or not _contains(self.windows, src):
            return      # a close the manager initiated itself
        if self.panel is not None and isValid(self.panel) and self.panel.parentWidget() is src:
            self.panel = None   # goes with its host
        self.windows = [w for w in self.windows if isValid(w) and w is not src]
        if not self.windows:
            # Deferred: closeAllWindows rescans the window list, so recreating synchronously loops forever.
            QTimer.singleShot(0, self._recover)
        elif self.panel is None:
            self.panel = self._make_panel(self.windows[0])

    def _recover(self) -> None:
        if not self.active or self.private or self.windows:
            return
        for screen in QGuiApplication.screens():
            self._add_window(screen)
        if self.windows:
            self.panel = self._make_panel(self.windows[0])
            self.windows[0].activateWindow()

    # ----- screens coming and going -----
    def _on_screen_added(self, screen: QScreen) -> None:
        if self.active and not self.private:
            self._add_window(screen)

    def _on_screen_removed(self, screen: QScreen) -> None:
        if not self.active or self.private:
            return
        for win in [w for w in self.windows if w.target_screen is screen]:
            self.windows.remove(win)
            if self.panel is not None and isValid(self.panel) and self.panel.parentWidget() is win:
                self.panel = None   # deleted with its host
            win.hide()
            win.close()
        if not self.windows:
            fallback = QGuiApplication.primaryScreen()
            if fallback is not None and fallback is not screen:
                self._add_window(fallback)
            elif QGuiApplication.screens():
                self._add_window(QGuiApplication.screens()[0])
        if self.windows and (self.panel is None or not isValid(self.panel)):
            self.panel = self._make_panel(self.windows[0])

    # ----- keeping everything else hidden and deaf -----
    def _owns(self, obj: QObject) -> bool:
        if isinstance(obj, QWidget):
            return _contains(self.windows, obj.window())
        if isinstance(obj, QWindow):
            return any(isValid(w) and obj is w.windowHandle() for w in self.windows)
        return True     # non-visual objects are not input targets

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if not self.active:
            return False
        t = event.type()
        if t == QEvent.Type.Show:
            if isinstance(obj, QWidget) and obj.isWindow() and not self._owns(obj):
                self._suppress(obj)
            return False
        if t == QEvent.Type.Hide:
            if _contains(self._modals, obj):
                self._modals = [m for m in self._modals if m is not obj]
                self._point_transients()
            return False
        if t == QEvent.Type.Close:
            if _contains(self._hidden, obj):     # closed while hidden: do not resurrect it
                self._hidden = [w for w in self._hidden if w is not obj]
            return False
        if t == QEvent.Type.KeyRelease and self.panel is not None and isValid(self.panel):
            self.panel.key_released(event)        # the revealing key may be released on any saver widget
        if t == QEvent.Type.Shortcut:             # no app shortcut may fire behind the saver
            self._reveal()
            return True
        if t in _FOREIGN_INPUT and not self._owns(obj):
            if t in _REVEALING:
                self._reveal(event.key() if t == QEvent.Type.KeyPress else None)
            return True
        return False

    def _reveal(self, key: int | None = None) -> None:
        host = self._panel_host()
        if host is not None:
            self._show_panel(host, key)

    def _suppress(self, w: QWidget) -> None:
        if not _contains([s for s, _ in self._suppressed], w):
            self._suppressed.append((w, w.windowOpacity()))
        w.setWindowOpacity(0.0)
        if w.isModal():
            # Hiding would cancel its exec(); keep it transparent and covered, and stop it blocking Resume.
            if not _contains(self._modals, w):
                self._modals.append(w)
            self._point_transients()
        else:
            self._pending_hide.append(w)
            QTimer.singleShot(0, self._hide_pending)
        QTimer.singleShot(0, self._reassert)

    def _hide_pending(self) -> None:
        pending, self._pending_hide = self._pending_hide, []
        if not self.active:
            return
        for w in pending:
            if isValid(w) and w.isVisible():
                w.hide()
                if not _is_popup(w) and not _contains(self._hidden, w):
                    self._hidden.append(w)

    def _point_transients(self) -> None:
        """Saver windows become transient children of the newest visible mid-session modal, so it does
        not block them (Qt never blocks a modal's descendants)."""
        modal = None
        for m in reversed(self._modals):
            if isValid(m) and m.isVisible() and m.windowHandle() is not None:
                modal = m.windowHandle()
                break
        for win in self.windows:
            handle = win.windowHandle() if isValid(win) else None
            if handle is not None:
                handle.setTransientParent(modal)

    def _reassert(self) -> None:
        host = self._panel_host() if self.active else None
        if host is None:
            return
        for win in self.windows:
            if isValid(win):
                win.raise_()
        host.activateWindow()
        if self.panel is not None and isValid(self.panel) and self.panel.isVisible():
            self.panel.resume_button.setFocus(Qt.FocusReason.OtherFocusReason)
