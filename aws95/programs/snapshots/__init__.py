"""Snapshots: history with collector health, Diff window, Trends with restatements (spec §7), and the Run tab
(resumable-snapshots spec §7.1)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPushButton, QSplitter, QTabWidget, QVBoxLayout,
                               QWidget)
from shiboken6 import isValid

from aws95.contracts.snapshots import MetricPoint, Snapshot
from aws95.kit.icons import icon
from aws95.kit.widgets.charts import LineChart
from aws95.kit.widgets.datagrid import Column, DataGrid
from aws95.kit.widgets.stateview import StateView, ViewState
from aws95.programs.common import ProgramBase
from aws95.programs.snapshots.diff import DiffDialog
from aws95.programs.snapshots.run_tab import RunTab

HISTORY = [Column("id", "#", "number"), Column("account", "Account", width=-1), Column("taken_at", "Taken", "date", -1),
           Column("status", "Status", "status"), Column("regions", "Regions", width=-1), Column("sections", "Sections"),
           Column("tool_version", "Tool"),
           Column("caller_arn", "Caller")]
HEALTH = [Column("collector", "Collector"), Column("status", "Status", "status"), Column("regions", "Regions covered", width=-1),
          Column("denied", "Denied", "number"), Column("error", "Error"), Column("duration_ms", "ms", "number")]
PICK = "Select a snapshot to see its collectors."


def trend_columns(money: bool) -> list[Column]:
    return [Column("observed_at", "Observed", "date", -1), Column("account", "Account"), Column("period", "Period"),
            Column("value", "Value", "money" if money else "number"), Column("unit", "Unit"),
            Column("restated", "Restated")]


class SnapshotsProgram(ProgramBase):
    TITLE = "Snapshots"
    PROGRAM_MENU = "&Snapshots"
    REFRESH_ON_SNAPSHOT = True
    EXPORT_NAME = "snapshots.csv"

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self._snapshots: list[Snapshot] = []
        self._points: list[MetricPoint] = []

        # History tab
        self.history = DataGrid(HISTORY, placeholder="Filter…  e.g. status:partial")
        self.history_view = StateView(self.history)
        self.primary_view = self.history_view
        self.history.current_row_changed.connect(self._on_snapshot)
        self.history.model.filter_applied.connect(self._sync_counts)
        self.health = DataGrid(HEALTH)
        self.health_view = StateView(self.health)
        self.health_view.show_empty(PICK)
        self.history_view.retry_requested.connect(self.refresh)
        self.compare_button = QPushButton("&Compare with previous…")
        self.compare_button.clicked.connect(self.compare_previous)
        history = QWidget()
        hl = QVBoxLayout(history)
        hl.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.history_view)
        split.addWidget(self.health_view)
        hl.addWidget(split, 1)
        hl.addWidget(self.compare_button, 0, Qt.AlignmentFlag.AlignLeft)

        # Trends tab
        self.metric_combo = QComboBox()
        self.series_account_combo = QComboBox()
        self.metric_combo.currentIndexChanged.connect(self._load_series)
        self.series_account_combo.currentIndexChanged.connect(self._plot)
        self.chart = LineChart()
        self.trend = DataGrid(trend_columns(False))
        self.trend.model.filter_applied.connect(self._sync_counts)
        self._trend_split = QSplitter(Qt.Orientation.Vertical)
        self._trend_split.addWidget(self.chart)
        self._trend_split.addWidget(self.trend)
        self.trend_view = StateView(self._trend_split)
        self.trend_view.show_empty("Pick a metric.")
        self.trend_view.retry_requested.connect(self._load_names)
        trends = QWidget()
        tl = QVBoxLayout(trends)
        tl.setContentsMargins(0, 0, 0, 0)
        pickers = QHBoxLayout()
        metric_label = QLabel("&Metric:")
        account_label = QLabel("&Plot account:")
        metric_label.setBuddy(self.metric_combo)
        account_label.setBuddy(self.series_account_combo)
        for label in (metric_label, account_label):
            label.setTextFormat(Qt.TextFormat.PlainText)
        pickers.addWidget(metric_label)
        pickers.addWidget(self.metric_combo, 1)
        pickers.addWidget(account_label)
        pickers.addWidget(self.series_account_combo)
        tl.addLayout(pickers)
        tl.addWidget(self.trend_view, 1)

        self.tabs = QTabWidget()
        self.tabs.addTab(history, "History")
        self.tabs.addTab(trends, "Trends")
        self.run_tab = RunTab(self)
        self.tabs.addTab(self.run_tab, "Run")
        self.tabs.currentChanged.connect(self._on_tab)
        self.set_content(self.tabs)
        self.program_menu.addAction("&Compare with Previous…").triggered.connect(self.compare_previous)
        self.take_action = QAction(icon("camera"), "&Take Snapshot…", self)
        self.take_action.setIconText("Take Snapshot")
        self.take_action.triggered.connect(self._take_snapshot)
        self.cancel_action = QAction("Ca&ncel Snapshot", self)
        self.cancel_action.triggered.connect(self._cancel_snapshot)
        self.toolbar.addAction(self.take_action)
        self.program_menu.addAction(self.take_action)
        self.resume_action = QAction("Res&ume Snapshot", self)
        self.resume_action.triggered.connect(self._resume_snapshot)
        self.program_menu.addAction(self.resume_action)
        self.program_menu.addAction(self.cancel_action)
        connect = getattr(getattr(ctx.shell, "snapshot_progress", None), "connect", None)
        if connect is not None:
            connect(self._on_snapshot_progress)

    # ----- taking a snapshot -----
    def _take_snapshot(self, *_args) -> None:
        self.ctx.shell.take_snapshot()

    def apply_navigation(self, params) -> None:
        if params.get("tab") == "run":
            self.tabs.setCurrentWidget(self.run_tab)

    def _resume_snapshot(self, *_args) -> None:
        self.ctx.shell.resume_snapshot((), False)

    def account_name(self, account_id: str) -> str:
        named = self.ctx.settings.account_label(account_id)
        return named[0] if named else account_id

    def _cancel_snapshot(self, *_args) -> None:
        self.ctx.shell.cancel_snapshot()

    def _on_snapshot_progress(self, text: str) -> None:
        if not isValid(self):
            return
        if text:
            self.status.set_counts(text)
        else:
            self._sync_counts()


    # ----- status -----
    def _sync_counts(self, *_args) -> None:
        if self.tabs.currentIndex() == 1:
            model, noun = self.trend.model, "points"
        elif self.tabs.currentWidget() is self.run_tab:
            model, noun = self.run_tab.grid.model, "sections"
        else:
            model, noun = self.history.model, "snapshots"
        total = model.total_count
        self.status.set_counts(f"{model.shown_count} of {total} {noun}" if total else "")

    # ----- history -----
    def refresh(self) -> None:
        self._reset_history()
        self.load(self.history_view, self.ctx.providers.snapshots.list_snapshots, self.scope,
                  render=self._render_history)
        if self.tabs.currentIndex() == 1:
            self._reset_series()
            self._load_names()
        self.run_tab.grid.set_rows([])
        self.run_tab.reload()

    def _reset_history(self) -> None:
        """Clear replaced content when a load starts (M3-R5)."""
        self._snapshots = []
        self.history.set_rows([])
        self._on_snapshot(None)

    def _render_history(self, snapshots: tuple[Snapshot, ...]) -> None:
        rows = [{"id": s.id, "account": s.account, "taken_at": s.taken_at, "status": s.status,
                 "regions": ", ".join(s.regions),
                 "sections": f"{s.sections_done}/{s.sections_total}" if s.sections_total else "", "tool_version": s.tool_version, "caller_arn": s.caller_arn, "snapshot": s} for s in snapshots]
        self._snapshots = list(snapshots)
        self.history.set_rows(rows)
        self._sync_counts()

    def select_snapshot(self, snapshot_id: int) -> bool:
        for i, row in enumerate(self.history.visible_rows()):
            if row["id"] == snapshot_id:
                self.history.view.selectRow(i)
                return True
        return False

    def _selected(self) -> Snapshot | None:
        row = self.history.current_row()
        return row["snapshot"] if row else None

    def _on_snapshot(self, row) -> None:
        self.invalidate(self.health_view)                   # M3-R4: nothing in flight may land after this
        s: Snapshot | None = row["snapshot"] if row else None
        if s is None:
            self.health.set_rows([])
            self.health_view.show_empty(PICK)
            return
        self.health.set_rows([{"collector": h.collector, "status": h.status.value,
                               "regions": f"{len(h.coverage.regions_covered)}/{len(h.coverage.regions_requested)}",
                               "denied": h.coverage.denials, "error": h.error or "", "duration_ms": h.duration_ms}
                              for h in s.collectors])
        self.health_view.show_content(None)

    def compare_previous(self) -> DiffDialog | None:
        s = self._selected()
        if s is None:
            self.status.set_counts("Select a snapshot first")
            return None
        same = sorted((x for x in self._snapshots if x.account == s.account), key=lambda x: x.taken_at)
        earlier = [x for x in same if x.taken_at < s.taken_at]
        if not earlier:
            self.status.set_counts("No earlier snapshot for this account")
            return None
        dialog = self.mark_dialog(DiffDialog(self, same, earlier[-1].id, s.id, self))
        dialog.show()
        return dialog

    # ----- trends -----
    def _on_tab(self, index: int) -> None:
        if index == 1 and self.metric_combo.count() == 0:
            self._load_names()
        elif self.tabs.widget(index) is self.run_tab:
            self.retry_failed(self.run_tab.view)
        else:
            self.retry_failed(self.trend_view if index == 1 else self.history_view)
        self._sync_counts()

    def _load_names(self) -> None:
        self.load(self.trend_view, self.ctx.providers.snapshots.metric_names, self.scope,
                  render=self._render_names, on_result=self._after_names, loading_text="Reading metrics…",
                  status=False)

    def _render_names(self, names: tuple[str, ...]) -> None:
        keep = self.metric_combo.currentText()
        self.metric_combo.blockSignals(True)
        try:
            self.metric_combo.clear()
            self.metric_combo.addItems(list(names))
            self.metric_combo.setCurrentIndex(self.metric_combo.findText(keep) if keep else -1)
        finally:
            self.metric_combo.blockSignals(False)

    def _after_names(self, _result) -> None:
        if self.trend_view.state is not ViewState.CONTENT:
            return
        if self.metric_combo.currentIndex() >= 0:
            self._load_series(0)
        else:
            self.trend_view.show_empty("Pick a metric.")

    def _reset_series(self) -> None:
        self._points = []
        self.trend.set_rows([])
        self.chart.set_series([])
        self.series_account_combo.blockSignals(True)
        try:
            self.series_account_combo.clear()
        finally:
            self.series_account_combo.blockSignals(False)

    def _load_series(self, _index: int) -> None:
        name = self.metric_combo.currentText()
        if name:
            self._reset_series()
            self.load(self.trend_view, self.ctx.providers.snapshots.metric_series, self.scope, name,
                      render=self._render_series, status=False)

    def _render_series(self, points: tuple[MetricPoint, ...]) -> None:
        # Build everything before touching widgets (M3-R8).
        money = bool(points) and all(p.unit == "USD" for p in points)
        rows = [{"observed_at": p.observed_at, "account": p.dims.get("account", ""), "period": p.period,
                 "value": p.value, "unit": p.unit or "", "restated": "restated" if p.restated else ""}
                for p in points]
        accounts = list(dict.fromkeys(p.dims.get("account", "") for p in points))
        grid = DataGrid(trend_columns(money))
        grid.model.filter_applied.connect(self._sync_counts)
        # Columns are fixed at construction, so a metric with another unit gets a fresh grid.
        old, split = self.trend, self._trend_split
        split.replaceWidget(split.indexOf(old), grid)
        self.trend = grid
        old.hide()
        old.setParent(None)
        old.deleteLater()
        self._points = list(points)
        grid.set_rows(rows)
        grid.view.resizeColumnToContents(3)               # figures are never elided; tooltip holds the exact value
        self.series_account_combo.blockSignals(True)
        try:
            self.series_account_combo.clear()
            self.series_account_combo.addItems(accounts)
        finally:
            self.series_account_combo.blockSignals(False)
        self._plot()
        self._sync_counts()

    def _plot(self, *_args) -> None:
        """Latest observation per period for the chosen account (selection only); restated points are marked."""
        account = self.series_account_combo.currentText()
        latest: dict[object, MetricPoint] = {}
        unperiodic: list[MetricPoint] = []
        for p in self._points:
            if p.dims.get("account", "") != account:
                continue
            if p.period is None:
                unperiodic.append(p)
            elif p.period not in latest or p.observed_at > latest[p.period].observed_at:
                latest[p.period] = p
        chosen = sorted([*latest.values(), *unperiodic], key=lambda p: p.observed_at)
        self.chart.set_series([(p.observed_at, p.value) for p in chosen],
                              marked=[i for i, p in enumerate(chosen) if p.restated])

    def export_grid(self) -> DataGrid:
        if self.tabs.currentWidget() is self.run_tab:
            return self.run_tab.grid
        return self.trend if self.tabs.currentIndex() == 1 else self.history

    def closeEvent(self, event) -> None:  # noqa: N802
        self.chart.close()          # a pyqtgraph widget destroyed as a plain child leaks and later segfaults
        super().closeEvent(event)


def make_snapshots(ctx) -> SnapshotsProgram:
    return SnapshotsProgram(ctx)
