"""Activity Log (ctwatch's home): Overview, Timeline, Watchlist, Filter; event viewer; history coverage (spec §5)."""
from __future__ import annotations

import dataclasses
import time
from functools import partial
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QDateTime, QTimeZone
from PySide6.QtWidgets import QComboBox, QDateTimeEdit, QLabel, QTabWidget, QToolBar, QVBoxLayout, QWidget
from shiboken6 import isValid

from aws95.contracts.common import Scope, TimeRange
from aws95.programs.activity.common import RANGES
from aws95.programs.activity.dialogs import EventDialog, RetentionDialog
from aws95.programs.activity.history import HistoryDialog, HistoryPanel
from aws95.programs.activity.filter_tab import FilterTab
from aws95.programs.activity.overview import OverviewTab
from aws95.programs.activity.timeline import TimelineTab
from aws95.programs.activity.watchlist import WatchlistTab
from aws95.programs.common import ProgramBase

DEFAULT_RANGE = "7d"
STALE_NOTE = "Range or scope changed \u2014 press Run to update the results"
LIVE_REFRESH_S = 15.0           # while a job runs, the views re-read at most this often


class ActivityProgram(ProgramBase):
    TITLE = "Activity Log"
    PROGRAM_MENU = "Ac&tivity"      # &A belongs to the scope bar's Account picker
    EXPORT_NAME = "activity.csv"
    clock = staticmethod(lambda: datetime.now(timezone.utc))
    monotonic = staticmethod(time.monotonic)

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self.range_combo = QComboBox()
        for key, title, _delta in RANGES:
            self.range_combo.addItem(title, key)
        self.range_combo.setCurrentIndex(self.range_combo.findData(DEFAULT_RANGE))
        self._previous_key = DEFAULT_RANGE
        now = QDateTime.fromSecsSinceEpoch(int(self.clock().timestamp()), QTimeZone.utc())
        self.from_edit = QDateTimeEdit(now.addDays(-7))
        self.to_edit = QDateTimeEdit(now)
        for edit in (self.from_edit, self.to_edit):
            edit.setTimeZone(QTimeZone.utc())
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd HH:mm")
            edit.dateTimeChanged.connect(self._on_custom_changed)
        # The range picker gets its own row: beside the scope bar it overflows at larger font scales (ruling
        # M3-R21). Custom's From/To need ~780 px at 150% font scale, more than one row holds at the default size,
        # so they get a further row that exists only while Custom is selected (ruling M3-R25).
        bar = self.scope_toolbar
        self.range_toolbar = QToolBar(self)
        self.range_toolbar.setIconSize(bar.iconSize())
        range_label = QLabel("Ra&nge:")
        range_label.setBuddy(self.range_combo)
        self.range_toolbar.addWidget(range_label)
        self.range_toolbar.addWidget(self.range_combo)
        self.custom_toolbar = QToolBar(self)
        self.custom_toolbar.setIconSize(bar.iconSize())
        for widget in (QLabel("From:"), self.from_edit, QLabel("To:"), self.to_edit):
            self.custom_toolbar.addWidget(widget)
        layout = self.layout()
        layout.insertWidget(layout.indexOf(bar) + 1, self.range_toolbar)
        layout.insertWidget(layout.indexOf(self.range_toolbar) + 1, self.custom_toolbar)
        self.custom_toolbar.setVisible(False)
        self.range_combo.currentIndexChanged.connect(self._on_range)

        self._pending_account: str | None = None
        if self.scope_bar is not None:
            self.scope_bar.labels_changed.connect(self._apply_pending_account)
        self.tabs = QTabWidget()
        self.overview = OverviewTab(self)
        self.timeline = TimelineTab(self)
        self.add_tab(self.overview, "Overview")
        self.watchlist = WatchlistTab(self)
        self.filter_tab = FilterTab(self)
        self.add_tab(self.timeline, "Timeline")
        self.add_tab(self.watchlist, "Watchlist")
        self.add_tab(self.filter_tab, "Filter")
        self.load_action = self.program_menu.addAction("Loa&d Activity History…")
        self.load_action.triggered.connect(self._load_history)
        self.resume_action = self.program_menu.addAction("&Resume Loading")
        self.resume_action.triggered.connect(self._resume)
        self.cancel_action = self.program_menu.addAction("&Cancel Loading")
        self.cancel_action.triggered.connect(self._cancel)
        self.import_action = self.program_menu.addAction("&Import History…")
        self.import_action.triggered.connect(self._import_history)
        self.retention_action = self.program_menu.addAction("&Keep Activity For…")
        self.retention_action.triggered.connect(self.open_retention)
        self.program_menu.addSeparator()
        self.program_menu.addAction("History C&overage…").triggered.connect(self.open_history)
        self.program_menu.addAction("&Watchlist").triggered.connect(partial(self.tabs.setCurrentWidget, self.watchlist))
        self.program_menu.addAction("Fi&lter").triggered.connect(partial(self.tabs.setCurrentWidget, self.filter_tab))
        self.tabs.currentChanged.connect(self._on_tab)
        self.history_panel = HistoryPanel(self)
        body = QWidget(self)
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.history_panel)
        column.addWidget(self.tabs, 1)
        self.set_content(body)
        self._last_live_refresh = float("-inf")
        for name, slot in (("activity_progress", self._on_activity_progress),
                           ("activity_finished", self._on_activity_finished)):
            signal = getattr(ctx.shell, name, None)
            if hasattr(signal, "connect"):
                signal.connect(slot)

    @property
    def primary_view(self):
        return self.tabs.currentWidget().primary_view

    def closeEvent(self, event) -> None:  # noqa: N802
        # A pyqtgraph widget destroyed as a plain child (never close()d) leaks and later segfaults.
        self.overview.chart.close()
        super().closeEvent(event)

    def scope_account_ids(self) -> tuple[str, ...]:
        return tuple(self.scope.accounts)

    def user_refresh(self, *_args) -> None:
        """F5: in live mode, Update activity (only what is newer than the stored coverage), then re-read."""
        if self.ctx.providers.is_live:
            self.ctx.shell.update_activity(self.scope_account_ids())
        self.refresh()

    def _load_history(self, *_args) -> None:
        self.ctx.shell.load_activity(self.scope_account_ids())

    def _resume(self, *_args) -> None:
        self.ctx.shell.resume_activity(self.scope_account_ids())

    def _cancel(self, *_args) -> None:
        self.ctx.shell.cancel_activity()

    def _import_history(self, *_args) -> None:
        ids = self.scope_account_ids()
        self.ctx.shell.import_activity_history(ids[0] if ids else "")

    def open_retention(self, *_args) -> RetentionDialog:
        dialog = self.mark_dialog(RetentionDialog(self, self))
        dialog.show()
        return dialog

    def _on_activity_progress(self, text: str) -> None:
        if not isValid(self):
            return
        if not text:
            self._show_counts(self.tabs.currentWidget())
            return
        self.status.set_counts(text)
        now = self.monotonic()
        if self._loaded_once and now - self._last_live_refresh >= LIVE_REFRESH_S:
            self._last_live_refresh = now
            self.refresh()                            # the newest events appear while the load runs

    def _on_activity_finished(self, *_args) -> None:
        if isValid(self) and self._loaded_once:
            self.refresh()

    # ----- tabs -----
    def add_tab(self, page: QWidget, title: str) -> None:
        self.tabs.addTab(page, title)

    def _on_tab(self, _index: int) -> None:
        page = self.tabs.currentWidget()
        if getattr(page, "dirty", False):
            page.reload()
        elif getattr(page, "primary_view", None) is not None:
            self.retry_failed(page.primary_view)
        self._show_counts(page)

    def refresh(self) -> None:
        self.history_panel.reload()
        for i in range(self.tabs.count()):
            self.tabs.widget(i).dirty = True
        # The Filter tab runs only on demand: its results stay as the user's last query, flagged stale.
        self.filter_tab.dirty = False
        self.filter_tab.mark_stale()
        current = self.tabs.currentWidget()
        if current is self.filter_tab:
            self._show_counts(current)
        else:
            current.reload()

    def sync_counts(self, page: QWidget, shown: int, total: int) -> None:
        """Counts describe the visible event grid (M3-R8); only the tab on screen reports."""
        if page is self.tabs.currentWidget():
            if getattr(page, "stale", False):
                self.status.set_counts(STALE_NOTE)
            else:
                self.status.set_counts(f"{shown} of {total} events" if total else "")

    def _show_counts(self, page: QWidget) -> None:
        if getattr(page, "stale", False):
            self.status.set_counts(STALE_NOTE)
            return
        grid = getattr(page, "events", None) or getattr(page, "grid", None)
        model = getattr(grid, "model", None)
        if model is None:
            self.status.set_counts("")
        else:
            self.sync_counts(page, model.shown_count, model.total_count)

    # ----- time range -----
    def _set_custom_visible(self, on: bool) -> None:
        self.custom_toolbar.setVisible(on)

    def set_range(self, key: str) -> None:
        self.range_combo.setCurrentIndex(self.range_combo.findData(key))

    def _on_range(self, index: int) -> None:
        custom = self.range_combo.currentData() == "custom"
        if custom:
            self._seed_custom(self._previous_key)
        self._previous_key = self.range_combo.currentData()
        self._set_custom_visible(custom)
        self.refresh()

    def _seed_custom(self, key: str) -> None:
        """Start Custom from the preset range the user was looking at, seconds zeroed (the editors hide them)."""
        delta = next((d for k, _t, d in RANGES if k == key and d is not None), None)
        if delta is None:
            return                                  # Custom to Custom: keep the user's own range
        end = self.clock().replace(second=0, microsecond=0)
        for edit, moment in ((self.from_edit, end - delta), (self.to_edit, end)):
            blocked = edit.blockSignals(True)       # one refresh follows from the range change, not three
            try:
                edit.setDateTime(QDateTime.fromSecsSinceEpoch(int(moment.timestamp()), QTimeZone.utc()))
            finally:
                edit.blockSignals(blocked)

    def _on_custom_changed(self, *_args) -> None:
        if self.range_combo.currentData() == "custom":
            self.refresh()

    def time_range(self) -> TimeRange:
        key = self.range_combo.currentData()
        if key == "custom":
            start = datetime.fromtimestamp(self.from_edit.dateTime().toSecsSinceEpoch(), timezone.utc)
            end = datetime.fromtimestamp(self.to_edit.dateTime().toSecsSinceEpoch(), timezone.utc)
            return TimeRange(min(start, end), max(start, end))
        delta = next(d for k, _t, d in RANGES if k == key)
        end = self.clock()
        return TimeRange(end - delta, end)

    def bucket(self) -> str:
        tr = self.time_range()
        return "hour" if tr.end - tr.start <= timedelta(hours=48) else "day"

    @property
    def scope(self) -> Scope:
        return dataclasses.replace(super().scope, time_range=self.time_range())

    # ----- events -----
    def open_event(self, event_id: str) -> EventDialog:
        dialog = self.mark_dialog(EventDialog(self, event_id, self))
        dialog.show()
        return dialog

    def open_history(self, *_args) -> HistoryDialog:
        dialog = self.mark_dialog(HistoryDialog(self, self))
        dialog.show()
        return dialog

    def apply_navigation(self, params) -> None:
        """Ctrl+K principal hits and Resources ▸ Related ▸ Open in Activity Log: the Filter tab, run for them."""
        principal, arn, account = params.get("principal"), params.get("arn"), params.get("account")
        if not principal and not arn:
            return
        if account and self.scope_bar is not None and not self.scope_bar.set_account(account):
            self._pending_account = account              # the account list may still be loading: try once more then
        if principal:
            self.filter_tab.principal_edit.setText(principal)
        if arn:
            self.filter_tab.arn_edit.setText(arn)
        self.tabs.setCurrentWidget(self.filter_tab)
        self.filter_tab.reload()

    def _apply_pending_account(self, *_args) -> None:
        """Navigation arrived before the account list loaded. An account not in the list is dropped (scope unchanged)."""
        account, self._pending_account = self._pending_account, None
        if account and self.scope_bar is not None:
            self.scope_bar.set_account(account)

    def account_name(self, account_id: str) -> str:
        named = self.ctx.settings.account_label(account_id)
        return named[0] if named else account_id

    def open_event_row(self, row) -> None:
        self.open_event(row["event_id"])

    def export_grid(self):
        return getattr(self.tabs.currentWidget(), "export_grid", lambda: None)()

    def export(self) -> bool:
        if self.tabs.currentWidget() is self.filter_tab:   # File > Export routes through the tab's guarded flow
            return self.filter_tab.export_csv()
        return super().export()


def make_activity(ctx) -> ActivityProgram:
    return ActivityProgram(ctx)
