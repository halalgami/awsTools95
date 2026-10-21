"""ProgramBase: the shared program-window skeleton (spec §7): menu bar, toolbar + scope, body, status."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import partial
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMenuBar, QToolBar, QVBoxLayout, QWidget
from shiboken6 import isValid

from aws95.contracts.common import Scope, Status
from aws95.core.paths import app_dir
from aws95.core.tasks import CancelScope, TaskHandle
from aws95.kit.icons import icon
from aws95.kit.widgets.datagrid import DataGrid, format_value
from aws95.kit.widgets.stateview import StateView, banner_for
from aws95.kit.widgets.statusstrip import StatusStrip
from aws95.kit.widgets.watermark import Watermark
from aws95.programs.common.export import rows_to_csv, write_text
from aws95.programs.common.scope import ScopeBar
from aws95.shell.context import ProgramContext
from aws95.shell.window import ProgramWindow

DEMO_SUFFIX = " [DEMO]"
_MARK_DIALOG = "aws95MarkDemo"


def default_ask_save_path(parent: QWidget, title: str, default_name: str, filters: str) -> str | None:
    exports = app_dir() / "exports"
    exports.mkdir(mode=0o700, exist_ok=True)
    path, _ = QFileDialog.getSaveFileName(parent, title, str(exports / default_name), filters)
    return path or None


class ProgramBase(QWidget):
    TITLE = "Program"
    PROGRAM_MENU = "&Program"
    REFRESH_ON_SNAPSHOT = False          # data windows re-read the store when a snapshot ends
    EXPORT_NAME = "export.csv"
    ask_save_path: Callable[..., str | None] = staticmethod(default_ask_save_path)
    # The StateView driven by the program's one status-owning load (M3-R23); set by each program.
    primary_view: StateView | None = None
    scope_changed = Signal(object)

    def __init__(self, ctx: ProgramContext, parent: QWidget | None = None, *, scope_bar: bool = True,
                 regions: bool = True) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.is_demo = False
        self._tasks = CancelScope()
        self._generation: dict[int, int] = {}
        self._watermark: Watermark | None = None
        self._loaded_once = False
        self._handles: dict[int, TaskHandle] = {}
        self._last: dict[int, tuple] = {}
        self._failed_views: set[int] = set()
        self._view_results: dict[int, object] = {}

        # menu bar (labels show the global shortcut; the shell owns the binding)
        self.menu_bar = QMenuBar(self)
        self.menu_bar.setNativeMenuBar(False)
        self.file_menu = self.menu_bar.addMenu("&File")
        self.edit_menu = self.menu_bar.addMenu("&Edit")
        self.view_menu = self.menu_bar.addMenu("&View")
        self.program_menu = self.menu_bar.addMenu(self.PROGRAM_MENU)
        self.help_menu = self.menu_bar.addMenu("&Help")
        self.refresh_action = self.file_menu.addAction(icon("run"), "&Refresh\tF5")
        self.refresh_action.setIconText("Refresh")
        self.refresh_action.triggered.connect(self.user_refresh)
        self.export_action = self.file_menu.addAction(icon("folder"), "&Export…\tCtrl+E")
        self.export_action.setIconText("Export")
        self.export_action.triggered.connect(self.export)
        self.file_menu.addSeparator()
        self.close_action = self.file_menu.addAction("&Close\tCtrl+W")
        self.close_action.triggered.connect(self._close_window)
        self.copy_action = self.edit_menu.addAction("&Copy Row")
        self.copy_action.triggered.connect(self.copy_current_row)
        self.status_action = self.view_menu.addAction("&Status Bar")
        self.status_action.setCheckable(True)
        self.status_action.setChecked(True)
        self.about_action = self.help_menu.addAction(icon("help"), "&About AWS 95 Tools")
        self.about_action.triggered.connect(self._show_about)

        self.toolbar = QToolBar(self)
        self.toolbar.setIconSize(QSize(16, 16))
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toolbar.addAction(self.refresh_action)
        self.toolbar.addAction(self.export_action)
        # The scope pickers get their own row: on the action row they overflowed into the toolbar's extension
        # menu at the default window size, out of sight and out of keyboard reach (ruling M3-R21).
        self.scope_bar: ScopeBar | None = None
        self.scope_toolbar: QToolBar | None = None
        if scope_bar:
            self.scope_toolbar = QToolBar(self)
            self.scope_toolbar.setIconSize(self.toolbar.iconSize())
            self.scope_bar = ScopeBar(ctx, self._tasks, self.scope_toolbar, regions=regions)
            self.scope_toolbar.addWidget(self.scope_bar)
            self.scope_bar.scope_changed.connect(self._on_scope_changed)
            self.scope_bar.labels_changed.connect(self._sync_chip)

        self.body = QWidget(self)
        self._body_layout = QVBoxLayout(self.body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self.status = StatusStrip(self)
        self.status_action.toggled.connect(self.status.setVisible)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setMenuBar(self.menu_bar)
        layout.addWidget(self.toolbar)
        if self.scope_toolbar is not None:
            layout.addWidget(self.scope_toolbar)
        layout.addWidget(self.body, 1)
        layout.addWidget(self.status)
        if self.REFRESH_ON_SNAPSHOT:
            connect = getattr(getattr(ctx.shell, "snapshot_finished", None), "connect", None)
            if connect is not None:                  # a shell without the signal (older fakes) just never refreshes
                connect(self._on_snapshot_finished)
            running = getattr(getattr(ctx.shell, "snapshot_progress", None), "connect", None)
            if running is not None:
                running(self._on_snapshot_progress)

    def _on_snapshot_progress(self, text: str) -> None:
        """While a snapshot runs the freshness pane says so; when it ends the pane reads as before (then refresh)."""
        if not isValid(self):
            return
        if text:
            self.status.freshness_label.setText("Snapshot running…")
        elif self.status.last_result is not None:
            self.status.set_result(self.status.last_result)
        else:
            self.status.freshness_label.setText("")

    def _on_snapshot_finished(self, *_args) -> None:
        if isValid(self) and self._loaded_once:
            self.refresh()

    def export_notice(self, grid) -> str | None:
        """Spec §9 / 2a D6: an export of a PARTIAL (or stale, denied, unreachable) result says so in its first row."""
        w = grid
        while w is not None:
            result = self._view_results.get(id(w)) if isinstance(w, StateView) else None
            if result is not None:
                cov = getattr(result, "coverage", None)
                degraded = result.status in (Status.PARTIAL, Status.STALE, Status.DENIED, Status.UNREACHABLE)
                if degraded or (cov is not None and not cov.complete):
                    detail = banner_for(result) or result.message or ""
                    return f"Status: {result.status.value.upper()}" + (f" - {detail}" if detail else "")
                return None
            w = w.parentWidget()
        return None

    # ----- content and scope -----
    def set_content(self, widget: QWidget) -> None:
        self._body_layout.addWidget(widget)

    @property
    def scope(self) -> Scope:
        return self.scope_bar.scope() if self.scope_bar is not None else Scope()

    def refresh(self) -> None:
        """Subclasses (re)load their data for self.scope."""

    def user_refresh(self, *_args) -> None:
        """F5 and File ▸ Refresh. Windows whose refresh can fetch new data from AWS (Activity's Update activity)
        override this; refresh() itself only re-reads, because opening a window never calls AWS."""
        self.refresh()

    def apply_navigation(self, params: Mapping[str, str]) -> None:
        """Subclasses accept cross-program links (e.g. a service name from Billing)."""

    def navigate(self, program_id: str, **params: str) -> None:
        self.ctx.shell.navigate(program_id, params)

    def program_window(self) -> ProgramWindow | None:
        w = self.parentWidget()
        while w is not None:
            if isinstance(w, ProgramWindow):
                return w
            w = w.parentWidget()
        return None

    def _sync_chip(self) -> None:
        win = self.program_window()
        if win is not None and self.scope_bar is not None:
            text, color = self.scope_bar.chip()
            win.set_scope_chip(text, color)

    def _on_scope_changed(self, scope: Scope) -> None:
        self._sync_chip()
        self.scope_changed.emit(scope)
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_chip()
        if not self._loaded_once:
            self._loaded_once = True
            QTimer.singleShot(0, self._first_load)
        elif self.primary_view is not None:
            self.retry_failed(self.primary_view)

    def _first_load(self) -> None:
        if isValid(self):
            self.refresh()

    # ----- loading -----
    def load(self, view: StateView, fn: Callable[..., Any], *args: Any, render: Callable[[Any], None],
             is_empty: Callable[[Any], bool] | None = None, on_result: Callable[[Any], None] | None = None,
             loading_text: str = "Reading…", status: bool = True, **kwargs: Any) -> TaskHandle:
        key = id(view)
        previous = self._handles.get(key)
        if previous is not None and not previous.done:
            previous.cancel()                                   # triage #1: superseded work stops
        generation = self._generation.get(key, 0) + 1
        self._generation[key] = generation
        view.show_loading(loading_text)
        handle = self.ctx.runner.submit(fn, *args, **kwargs)
        self._handles[key] = handle
        self._last[key] = (fn, args, kwargs, dict(render=render, is_empty=is_empty, on_result=on_result,
                                                  loading_text=loading_text, status=status))
        self._failed_views.discard(key)
        self._tasks.track(handle)
        handle.finished.connect(partial(self._on_loaded, view, generation, render, is_empty, on_result, status))
        handle.failed.connect(partial(self._on_failed, view, generation))
        return handle

    def invalidate(self, view: StateView) -> None:
        """Drop any in-flight load for the view: its result can no longer render (M3-R4)."""
        self._generation[id(view)] = self._generation.get(id(view), 0) + 1
        self._failed_views.discard(id(view))        # a message state must never be replayed with the old scope's args
        self._last.pop(id(view), None)
        self._view_results.pop(id(view), None)

    def _current(self, view: StateView, generation: int) -> bool:
        return isValid(self) and isValid(view) and self._generation.get(id(view)) == generation

    def _on_loaded(self, view, generation, render, is_empty, on_result, status, result) -> None:
        if not self._current(view, generation):
            return
        if getattr(result, "is_demo", False):
            self.mark_demo()
        view.apply_result(result, render, is_empty)
        self._view_results[id(view)] = result
        if getattr(result, "status", None) in (Status.ERROR, Status.UNREACHABLE):
            self._failed_views.add(id(view))
        if status:
            self.status.set_result(result)
        if on_result is not None:
            on_result(result)

    def _on_failed(self, view, generation, message: str, details: str) -> None:
        if self._current(view, generation):
            view.show_error(message, details=details or None)
            self._failed_views.add(id(view))

    def retry_failed(self, view: StateView) -> None:
        """Triage #4: when a view whose last load failed is shown again, re-run that load once."""
        key = id(view)
        if key not in self._failed_views or key not in self._last:
            return
        self._failed_views.discard(key)
        fn, args, kwargs, opts = self._last[key]
        self.load(view, fn, *args, **opts, **kwargs)

    def mark_demo(self) -> None:
        self.is_demo = True
        if self._watermark is None:
            self._watermark = Watermark(self.body)
        for dialog in self.findChildren(QDialog):           # dialogs opened before demo status was known
            if isValid(dialog) and dialog.property(_MARK_DIALOG):
                self._apply_demo_mark(dialog)

    def mark_dialog(self, dialog: QDialog) -> QDialog:
        """Register a program dialog: whenever it shows demo data it carries the watermark and [DEMO] (spec §9)."""
        dialog.setProperty(_MARK_DIALOG, True)
        if self.is_demo:
            self._apply_demo_mark(dialog)
        return dialog

    @staticmethod
    def _apply_demo_mark(dialog: QDialog) -> None:
        if not dialog.windowTitle().endswith(DEMO_SUFFIX):
            dialog.setWindowTitle(dialog.windowTitle() + DEMO_SUFFIX)
        if dialog.findChild(Watermark, options=Qt.FindChildOption.FindDirectChildrenOnly) is None:
            Watermark(dialog)

    # ----- export / copy -----
    def export_grid(self) -> DataGrid | None:
        return None

    def export(self) -> bool:
        grid = self.export_grid()
        return self.save_grid(grid, self.EXPORT_NAME, "CSV files (*.csv)",
                              partial(rows_to_csv, notice=self.export_notice(grid)))

    def save_grid(self, grid: DataGrid | None, default_name: str, filters: str,
                  make_text: Callable[[Any, Any], str], parent: QWidget | None = None) -> bool:
        """The one export flow: ask for a path, write the visible rows, record the recent document, report."""
        rows = grid.visible_rows() if grid is not None else []
        if not rows:
            self.status.set_counts("Nothing to export")
            return False
        path = self.ask_save_path(parent or self, f"Export {self.TITLE}", default_name, filters)
        if not path:
            return False
        try:
            write_text(path, make_text(grid.visible_columns(), rows))
        except (OSError, ValueError) as exc:
            self.status.set_counts(f"Export failed: {exc}")
            return False
        try:
            self.ctx.settings.add_recent_document(path)
        except ValueError:            # the file is written; only the recent-documents entry is refused
            self.status.set_counts(f"Exported to {path} (not added to recent documents)")
            return True
        self.status.set_counts(f"Exported to {path}")
        return True

    def copy_current_row(self) -> None:
        grid = self.export_grid()
        row = grid.current_row() if grid is not None else None
        if row is None:
            return
        QApplication.clipboard().setText("\t".join(format_value(row.get(c.key), c.kind)
                                                   for c in grid.visible_columns()))

    # ----- window plumbing -----
    def _close_window(self) -> None:
        win = self.program_window()
        (win or self).close()

    def _show_about(self) -> None:
        self.ctx.shell.show_about()

    def closeEvent(self, event) -> None:  # noqa: N802
        for dialog in self.findChildren(QDialog):       # an orphaned dialog would sit on a cancelled load
            if isValid(dialog):
                dialog.close()
        self._tasks.cancel_all()
        # Release retained handles and the replayable load args: they hold the context, and Qt-side signal
        # connections keep such cycles out of the garbage collector's reach.
        self._handles.clear()
        self._last.clear()
        self._failed_views.clear()
        self._view_results.clear()
        super().closeEvent(event)
