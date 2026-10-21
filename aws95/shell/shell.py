"""ShellWindow: the AWS 95 desktop. Composes desktop, icons, taskbar, Start menu, palette,
switcher, snapping, sounds and the idle screensaver, and implements ShellServices for programs."""
from __future__ import annotations

import random
import time
from functools import partial
from typing import Callable, Literal

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QTabWidget, QVBoxLayout, QWidget
from shiboken6 import isValid

from aws95.contracts.common import Scope, Status
from aws95.core.settings import Settings
from aws95.core.tasks import TaskRunner
from aws95.kit.icons import icon
from aws95.kit.widgets.balloon import Balloon
from aws95.providers.base import ProviderSet
from aws95.shell.about import AboutDialog
from aws95.shell.activity_jobs import ActivityController
from aws95.shell.context import ProgramContext
from aws95.shell.desktop import Desktop
from aws95.shell.icon_layer import DesktopCommand, IconLayer
from aws95.shell.palette import CommandPalette, build_items
from aws95.shell.registry import Registry
from aws95.shell.screensaver.idle import IdleMonitor
from aws95.shell.screensaver.manager import ScreensaverManager
from aws95.shell.shutdown import ShutdownDialog, perform_shutdown
from aws95.shell.snapshot_prompt import DATA_PROGRAMS, LocalDataCleaner, SnapshotController
from aws95.shell.snapping import SnapController
from aws95.shell.startmenu import build_start_menu, popup_above
from aws95.shell.switcher import WindowSwitcher
from aws95.shell.taskbar import Taskbar
from aws95.shell.window import ProgramWindow


RUN_TAB = {"tab": "run"}            # resumable-snapshots spec §7.2: tray words and balloon open the Run tab

class ShellWindow(QMainWindow):
    SEARCH_LIMIT = 32                        # Ctrl+K store hits: up to 8 per kind, 4 kinds (2d spec §4)
    sign_in_succeeded = Signal(str)          # the CLI profile that just signed in; programs re-verify on it
    snapshot_finished = Signal(object)       # Result[SnapshotOutcome], or None after Clear Local Data
    snapshot_progress = Signal(str)          # "dev: 7/42 sections · lambda me-south-1"; "" when idle
    activity_finished = Signal(object)       # Result[HistoryOutcome], or None after Clear Local Data
    activity_progress = Signal(str)          # "Activity dev: 23/90 days · me-south-1"; "" when idle

    def __init__(self, settings: Settings, runner: TaskRunner, providers: ProviderSet, sound, registry: Registry,
                 *, idle_clock: Callable[[], float] = time.monotonic, saver_rng: random.Random | None = None,
                 app_state: Callable[[], Qt.ApplicationState] | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.runner = runner
        self.providers = providers
        self.sound = sound
        self.registry = registry
        self.setWindowTitle("AWS 95 Tools")
        self.setWindowIcon(icon("app"))

        self.desktop = Desktop(registry, self._make_context, settings)
        self.taskbar = Taskbar(self, self.desktop, settings, runner)
        central = QWidget(self)
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.desktop, 1)
        lay.addWidget(self.taskbar)
        self.setCentralWidget(central)

        self.snap = SnapController(self.desktop)
        # Take Snapshot sits first on the desktop so it is easy to find (owner, 2026-10-17).
        self.icons = IconLayer(self.desktop, registry, settings, self.open_program,
                               commands=(DesktopCommand("cmd:take_snapshot", "Take Snapshot", "camera",
                                                        self.take_snapshot),))
        self.icons.context_menu_requested.connect(self._extend_desktop_menu)
        self.start_menu = build_start_menu(registry, self, self.settings)
        self.start_menu.setParent(self, self.start_menu.windowFlags())  # owned by (and torn down with) the shell
        self.start_menu.aboutToHide.connect(self._start_menu_hidden)
        self.command_palette: CommandPalette | None = None
        self.switcher: WindowSwitcher | None = None
        self.shutdown_dialog: ShutdownDialog | None = None
        self._about: AboutDialog | None = None
        self._wizard = None
        self._perm_dialogs: dict[tuple[str, str], object] = {}
        self._sign_in_dialogs: dict[str, object] = {}

        self.idle = IdleMonitor(settings, clock=idle_clock, parent=self)
        self.saver = ScreensaverManager(settings, sound, rng=saver_rng, app_state=app_state,
                                     private_window=self, parent=self)
        self.idle.idle.connect(self._on_idle)
        self.saver.dismissed.connect(self.idle.rearm)

        self.desktop.window_opened.connect(self._on_window_opened)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.save_window_geometry)
            app.installEventFilter(self)    # Ctrl+Tab: QMdiArea consumes it before a program's tabs see it
        self._tab_filter_installed = app is not None
        self.shortcuts: dict[str, QShortcut] = {}
        self._install_shortcuts()
        self._job_texts: dict[str, str] = {}
        self.snapshots = SnapshotController(settings, runner, providers, self)
        self.job_balloon: Balloon | None = None
        self.snapshots.finished.connect(self.snapshot_finished)
        self.snapshots.finished.connect(self._on_snapshot_done)
        self.snapshots.progress.connect(self.snapshot_progress)
        self.snapshots.progress.connect(partial(self._set_job_text, "snapshot"))
        self.cleaner = LocalDataCleaner(self, runner, providers, self.snapshots)
        self.cleaner.cleared.connect(self._on_local_data_cleared)
        self.activity = ActivityController(settings, runner, providers, self)
        self.activity.finished.connect(self.activity_finished)
        self.activity.finished.connect(self._on_activity_done)
        self.activity.progress.connect(self.activity_progress)
        self.activity.progress.connect(partial(self._set_job_text, "activity"))
        self.snapshots.register_option(self.activity.prompt_option())
        self.cleaner.add_busy_check(self.activity.busy_text)      # 2b: refuses the clear while a job runs
        self.cleaner.add_busy_check(self._findings_busy)          # and while a findings backfill runs
        self.activity.add_refusal(self._activity_refusal)         # and no job starts while the clear runs (I6)
        QTimer.singleShot(0, self.activity.start_session)        # live only: the history flag and retention pruning

    # ----- context / lifecycle -----
    def _make_context(self) -> ProgramContext:
        return ProgramContext(self.settings, self.runner, self.providers, self.sound, self)

    def start_idle(self, app: QApplication) -> None:
        self.idle.start(app)

    def _on_idle(self) -> None:
        self.saver.start_idle()
        if not self.saver.active:
            # The start was refused or rolled back (no screens, a modal, an error): re-arm, or idle
            # would never fire again. A live session (full or privacy-only) re-arms on dismissal.
            self.idle.rearm()

    def active_content(self) -> QWidget | None:
        w = self.desktop.active_program_window()
        return w.content() if w is not None else None

    def save_window_geometry(self) -> None:
        """Persist every live window's rect: child windows get no closeEvent when the app quits."""
        if not isValid(self.desktop):
            return
        for w in self.desktop.program_windows():
            if isValid(w):
                w.save_geometry()

    def _on_window_opened(self, window: ProgramWindow) -> None:
        self.sound.play("window_open")
        if window.manifest.id in DATA_PROGRAMS:
            self.snapshots.maybe_prompt()
        window.closed.connect(self._on_window_closed)
        window.minimized_changed.connect(self._on_minimized_changed)
        window.maximized.connect(self._on_maximized)

    def _on_window_closed(self, *_args) -> None:
        if not self.desktop.closing_all:
            self.sound.play("window_close")

    def _on_minimized_changed(self, minimized: bool) -> None:
        self.sound.play("minimize" if minimized else "maximize")

    def _on_maximized(self) -> None:
        self.sound.play("maximize")

    def _extend_desktop_menu(self, menu: QMenu) -> None:
        tile = QMenu("&Tile", menu)
        tile.addAction("Tile &Horizontally").triggered.connect(partial(self.tile, "horizontal"))
        tile.addAction("Tile &Vertically").triggered.connect(partial(self.tile, "vertical"))
        menu.addMenu(tile)
        menu.addAction("&Cascade").triggered.connect(self.cascade)
        menu.addAction("&Minimize All").triggered.connect(self.minimize_all)
        menu.addSeparator()
        menu.addAction("&Properties…").triggered.connect(partial(self.open_program, "display"))

    # ----- Ctrl+Tab -----
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        """Ctrl+Tab / Ctrl+Shift+Tab move between the active program's tabs; with no visible tab widget the
        key falls through to QMdiArea's own window cycling."""
        if (event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier
                and not event.modifiers() & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
                and isinstance(obj, QWidget) and obj.window() is self
                and QApplication.activeModalWidget() is None):
            backwards = (event.key() == Qt.Key.Key_Backtab
                         or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            if self._cycle_program_tab(-1 if backwards else 1):
                return True
        return super().eventFilter(obj, event)

    def _cycle_program_tab(self, step: int) -> bool:
        window = self.desktop.active_program_window()
        if window is None or window.is_minimized_to_taskbar:
            return False
        for tabs in window.content().findChildren(QTabWidget):     # parent before nested: the main tabs first
            if tabs.isVisible() and tabs.count() > 1:
                tabs.setCurrentIndex((tabs.currentIndex() + step) % tabs.count())
                return True
        return False

    # ----- shortcuts -----
    def _shortcut(self, keys: str, slot) -> None:
        sc = QShortcut(QKeySequence(keys), self)
        sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        sc.activated.connect(partial(self._run_shortcut, slot))
        self.shortcuts[keys] = sc

    @staticmethod
    def _run_shortcut(slot) -> None:
        # ApplicationShortcut also fires while a modal dialog (Shut Down, Tip) is active: never stack on it.
        if QApplication.activeModalWidget() is None:
            slot()

    def _install_shortcuts(self) -> None:
        self._shortcut("Ctrl+K", self.show_palette)
        self._shortcut("Ctrl+Esc", self.show_start_menu)
        self._shortcut("Alt+`", self.show_switcher)
        self._shortcut("Alt+Shift+`", partial(self.show_switcher, -1))
        self._shortcut("Ctrl+W", self._close_active)
        for keys, direction in (("Ctrl+Alt+Left", "left"), ("Ctrl+Alt+Right", "right"),
                                ("Ctrl+Alt+Up", "up"), ("Ctrl+Alt+Down", "down")):
            self._shortcut(keys, partial(self.snap.snap_active, direction))
        self._shortcut("F5", self._refresh_active)
        self._shortcut("Ctrl+E", self._export_active)
        self._shortcut("Ctrl+Q", self.request_shutdown)
        self._shortcut("F1", self.show_about)
        for m in self.registry.with_shortcuts():
            self._shortcut(m.shortcut, partial(self.open_program, m.id))

    def _close_active(self) -> None:
        w = self.desktop.active_program_window()
        if w is not None:
            w.close()   # the normal path: closeEvent applies side effects only if the close is accepted

    def _refresh_active(self) -> None:
        content = self.active_content()
        refresh = getattr(content, "user_refresh", None) or getattr(content, "refresh", None)
        if callable(refresh):
            refresh()

    def _export_active(self) -> None:
        export = getattr(self.active_content(), "export", None)
        if callable(export):
            export()

    # ----- ShellServices -----
    def _set_job_text(self, key: str, text: str) -> None:
        """The tray shows every long job's own words, joined (a snapshot and an Activity load can run together)."""
        prefix = {"snapshot": "Snapshot", "activity": "Activity"}.get(key)
        if text and prefix and prefix.lower() not in text.lower() and not text.startswith("Import"):
            text = f"{prefix} {text}"               # "Snapshot dev: 7/42 sections", "Activity Starting…"
        self._job_texts[key] = text
        self.taskbar.tray.set_job_text(" · ".join(t for t in self._job_texts.values() if t))

    def show_running_job(self, *_args) -> None:
        """The tray's job words were clicked: open the window of what is running."""
        if self.snapshots.running or self._job_texts.get("snapshot"):
            self.navigate("snapshots", RUN_TAB)
        elif self.activity.running or self._job_texts.get("activity"):
            self.open_program("activity")

    def _on_snapshot_done(self, result) -> None:
        """A snapshot ended: say so (a failure keeps the controller's own message box instead)."""
        if result is None or result.status is Status.ERROR:
            return
        cancelled = bool(getattr(result.data, "cancelled", False))
        title = ("Snapshot cancelled" if cancelled else
                 "Snapshot complete" if result.status is Status.OK else "Snapshot finished with gaps")
        self._show_job_balloon(title, result.message or "", "snapshots", RUN_TAB)

    def _on_activity_done(self, result) -> None:
        if result is None or result.status is Status.ERROR:
            return
        data = result.data
        cancelled = bool(getattr(data, "cancelled", False))
        if getattr(data, "kind", "load") == "import":
            title = ("Import cancelled" if cancelled else
                     "History imported" if result.status is Status.OK else "History imported with gaps")
        else:
            title = ("Activity loading cancelled" if cancelled else
                     "Activity updated" if result.status is Status.OK else "Activity updated with gaps")
        self._show_job_balloon(title, result.message or "", "activity")

    def _show_job_balloon(self, title: str, text: str, program_id: str, params=None) -> None:
        """A Win95 tray balloon over the clock, plus the done sound; clicking it opens the job's window."""
        old = self.job_balloon
        if old is not None and isValid(old):
            old.hide()
            old.deleteLater()
        balloon = Balloon(title, text, timeout_ms=8000, parent=self)
        balloon.clicked.connect(partial(self.navigate, program_id, dict(params or {})))
        self.job_balloon = balloon
        tray = self.taskbar.tray
        balloon.show_at(tray.mapToGlobal(QPoint(tray.width(), 0)))
        self.sound.play("refresh_done")

    def _findings_busy(self) -> str | None:
        busy = getattr(self.providers.findings, "busy_text", None)      # live only; demo and offline never backfill
        reason = busy() if callable(busy) else None
        return reason if isinstance(reason, str) else None              # a NOT_CONNECTED stand-in answers a Result

    def _activity_refusal(self) -> str | None:
        return "Local data is being cleared. Try again when it is done." if self.cleaner.clearing else None

    def load_activity(self, account_ids=(), *_args) -> None:
        self.activity.open_loader(tuple(account_ids or ()))

    def update_activity(self, account_ids=(), *_args) -> None:
        self.activity.update(tuple(account_ids or ()))

    def resume_activity(self, account_ids=(), *_args) -> None:
        self.activity.resume(tuple(account_ids or ()))

    def cancel_activity(self, *_args) -> None:
        self.activity.cancel()

    def import_activity_history(self, account_id="", *_args) -> None:
        self.activity.open_import(account_id or "")

    def prune_activity(self, *_args) -> None:
        self.activity.prune()

    def take_snapshot(self, *_args) -> None:
        self.snapshots.open_prompt()

    def resume_snapshot(self, account_ids=(), retry_denied=False, *_args) -> None:
        self.snapshots.resume_accounts(tuple(account_ids or ()), bool(retry_denied))

    def cancel_snapshot(self, *_args) -> None:
        self.snapshots.cancel()

    def clear_local_data(self, *_args) -> None:
        self.cleaner.request()

    def _on_local_data_cleared(self) -> None:
        self.snapshot_finished.emit(None)        # open windows re-read: their data is gone
        self.activity.reset()
        self.activity_finished.emit(None)

    def boot_snapshot_prompt(self, *_args) -> None:
        """Spec §3.5: after the desktop appears, and after the setup wizard if it is showing."""
        wizard = self._wizard
        if wizard is not None and isValid(wizard) and wizard.isVisible():
            wizard.finished.connect(self._after_wizard)
            return
        self.snapshots.maybe_prompt()

    def _after_wizard(self, *_args) -> None:
        QTimer.singleShot(0, self.snapshots.maybe_prompt)

    def open_program(self, program_id: str) -> QWidget | None:
        return self.desktop.open_program(program_id)

    def navigate(self, program_id: str, params) -> None:
        window = self.open_program(program_id)
        content = window.content() if window is not None and isValid(window) else None
        apply = getattr(content, "apply_navigation", None)
        if callable(apply):
            apply(dict(params))

    def show_palette(self) -> None:
        if self.command_palette is not None and isValid(self.command_palette) and self.command_palette.isVisible():
            self.command_palette.query_edit.setFocus()     # already open: keep what the user typed
            return
        if self.start_menu.isVisible():
            self.start_menu.hide()
        if self.command_palette is None:
            searchable = self.providers.is_live or self.providers.is_demo
            self.command_palette = CommandPalette(self, search=self._search_store if searchable else None,
                                                  open_hit=self._open_hit, icon_for=self._program_icon)
        self.command_palette.set_items(build_items(self.registry, self, self.settings, self.desktop))
        self.command_palette.move(self.geometry().center().x() - self.command_palette.width() // 2, self.geometry().top() + 80)
        self.command_palette.show()
        self.command_palette.query_edit.setFocus()

    def _search_store(self, text: str):
        """Ctrl+K store search (2d spec §4.2), on the TaskRunner: it reads the local store only."""
        return self.runner.submit(self.providers.search.search, Scope(), text, self.SEARCH_LIMIT)

    def _open_hit(self, hit, *_args) -> None:
        self.navigate(hit.target_program, dict(hit.payload))

    def _program_icon(self, program_id: str) -> str:
        try:
            return self.registry.get(program_id).icon
        except KeyError:
            return ""

    def show_switcher(self, step: int = 1) -> None:
        """Open the switcher, or cycle it while it is open (Alt+` held: each press reaches this shortcut)."""
        if self.switcher is not None and isValid(self.switcher) and self.switcher.isVisible():
            self.switcher.cycle(step)
            return
        if self.switcher is None:
            self.switcher = WindowSwitcher(self.desktop, self)
        if self.switcher.open() and step < 0:
            self.switcher.cycle(-2)     # open() preselects the next window; backwards starts at the last

    def show_start_menu(self) -> None:
        if self.start_menu.isVisible():  # Ctrl+Esc toggles
            self.start_menu.hide()
            return
        if self.command_palette is not None and isValid(self.command_palette) and self.command_palette.isVisible():
            self.command_palette.hide()
        self.sound.play("menu_popup")
        self.taskbar.start_button.setDown(True)
        popup_above(self.start_menu, self.taskbar.start_button)

    def _start_menu_hidden(self) -> None:
        self.taskbar.start_button.setDown(False)

    def request_shutdown(self) -> None:
        if self.shutdown_dialog is not None and isValid(self.shutdown_dialog):
            self.shutdown_dialog.raise_()
            self.shutdown_dialog.activateWindow()
            return
        dialog = ShutdownDialog(self)
        dialog.finished.connect(self._on_shutdown_finished)
        self.shutdown_dialog = dialog
        self.sound.play("question")
        dialog.open()   # non-blocking: the choice arrives through finished

    def _on_shutdown_finished(self, _result: int) -> None:
        dialog, self.shutdown_dialog = self.shutdown_dialog, None
        if dialog is None or not isValid(dialog):
            return
        choice = dialog.choice
        dialog.deleteLater()
        if choice is not None:
            self.save_window_geometry()
            perform_shutdown(choice, settings=self.settings, sound=self.sound,
                             app=QApplication.instance(), overlay_target=self)

    def preview_screensaver(self, name: str | None = None) -> None:
        """Start a saver now: the named one, or the configured choice when name is None."""
        self.saver.preview(name)

    def show_about(self) -> None:
        if self._about is not None and isValid(self._about) and self._about.isVisible():
            self._about.raise_()
            self._about.activateWindow()
            return
        self._about = AboutDialog(self)
        self._about.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._about.open()

    def open_setup_wizard(self, page=None, *_args) -> None:
        page = page if isinstance(page, str) else None            # QAction.triggered passes checked=False
        if self._wizard is not None and isValid(self._wizard) and self._wizard.isVisible():
            self._wizard.raise_()
            self._wizard.activateWindow()
            return
        from aws95.shell.setup_wizard import SetupWizard
        self._wizard = SetupWizard(self.settings, self.runner, self.providers.accounts, self.sound, self,
                                   shell=self, is_demo=self.providers.is_demo, start_page=page)
        self._wizard.show()

    def check_permissions(self, profile: str, account_id: str, *_args) -> None:
        from aws95.shell.setup_wizard import PermissionsDialog
        key = (profile, account_id)
        old = self._perm_dialogs.get(key)
        if old is not None and isValid(old) and old.isVisible():
            old.raise_()
            old.activateWindow()
            return
        dlg = PermissionsDialog(self.providers.accounts, self.runner, profile, account_id, self,
                                is_demo=self.providers.is_demo)
        self._perm_dialogs = {k: d for k, d in self._perm_dialogs.items() if isValid(d)}
        self._perm_dialogs[key] = dlg
        dlg.show()

    def sign_in_dialog_for(self, profile: str):
        """The visible sign-in dialog for a CLI profile (the shell's or the wizard's), or None."""
        dlg = self._sign_in_dialogs.get(profile)
        return dlg if dlg is not None and isValid(dlg) and dlg.isVisible() else None

    def track_sign_in(self, profile: str, dlg) -> None:
        """A sign-in dialog the setup wizard opened: Accounts hears about its success, and a second Sign In for the
        same profile raises it instead of opening another (2a leftover)."""
        self._sign_in_dialogs = {k: d for k, d in self._sign_in_dialogs.items() if isValid(d)}
        self._sign_in_dialogs[profile] = dlg
        dlg.finished_ok.connect(partial(self._forward_sign_in, profile))

    def _forward_sign_in(self, profile: str, *_args) -> None:
        self.sign_in_succeeded.emit(profile)

    def sign_in(self, profile: str, *_args) -> None:
        from aws95.shell.setup_wizard import SignInDialog
        old = self._sign_in_dialogs.get(profile)
        if old is not None and isValid(old) and old.isVisible():
            old.raise_()
            old.activateWindow()
            return
        dlg = SignInDialog(self.providers.accounts, self.runner, profile, self, is_demo=self.providers.is_demo)
        dlg.finished_ok.connect(self.sign_in_succeeded)
        self._sign_in_dialogs = {k: d for k, d in self._sign_in_dialogs.items() if isValid(d)}
        self._sign_in_dialogs[profile] = dlg
        dlg.show()

    def tile(self, orientation: Literal["horizontal", "vertical"]) -> None:
        self.desktop.tile(orientation)

    def cascade(self) -> None:
        self.desktop.cascade()

    def minimize_all(self) -> None:
        self.desktop.minimize_all()

    def recycle_bin(self):
        return self.desktop.recycle

    def closeEvent(self, event) -> None:  # noqa: N802
        app = QApplication.instance()
        if app is not None and self._tab_filter_installed:
            app.removeEventFilter(self)
            self._tab_filter_installed = False
        self.save_window_geometry()
        self.saver.dismiss()
        # Close every program window so each ProgramBase.closeEvent runs and cancels its tasks; otherwise their
        # handles and the boot's settings outlive a restart (M3-R7).
        self.desktop.closing_all = True          # no per-window close sound, nothing for the Recycle Bin
        try:
            for window in self.desktop.program_windows():
                if isValid(window):
                    window.close()
        finally:
            self.desktop.closing_all = False
        super().closeEvent(event)
