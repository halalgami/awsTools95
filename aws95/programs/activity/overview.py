"""Overview tab: events over time, top principals/actions/services, human vs service."""
from __future__ import annotations

from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QGroupBox, QLabel, QVBoxLayout, QWidget

from aws95.kit.widgets.charts import LineChart
from aws95.kit.widgets.datagrid import Column, DataGrid
from aws95.kit.widgets.stateview import StateView

TOP_COLUMNS = [Column("key", "Name"), Column("count", "Events", "number")]


def _boxed(title: str, view: QWidget) -> QGroupBox:
    box = QGroupBox(title)
    QVBoxLayout(box).addWidget(view)
    return box


class OverviewTab(QWidget):
    def __init__(self, program) -> None:
        super().__init__()
        self.program = program
        self.dirty = True
        self.chart = LineChart()
        self.series_view = StateView(self.chart)
        self.primary_view = self.series_view
        self.principals = DataGrid(TOP_COLUMNS, placeholder="Filter…  e.g. ci-deploy")
        self.actions = DataGrid(TOP_COLUMNS, placeholder="Filter…  e.g. AssumeRole")
        self.services = DataGrid(TOP_COLUMNS, placeholder="Filter…  e.g. s3")
        self.principals_view = StateView(self.principals)
        self.actions_view = StateView(self.actions)
        self.services_view = StateView(self.services)
        self.actors_label = QLabel("")
        self.actors_label.setTextFormat(Qt.TextFormat.PlainText)
        self.actors_view = StateView(self.actors_label)
        grid = QGridLayout(self)
        grid.addWidget(_boxed("Events over time", self.series_view), 0, 0, 1, 3)
        grid.addWidget(_boxed("Top principals", self.principals_view), 1, 0)
        grid.addWidget(_boxed("Top actions", self.actions_view), 1, 1)
        grid.addWidget(_boxed("Top services", self.services_view), 1, 2)
        grid.addWidget(_boxed("Human vs service", self.actors_view), 2, 0, 1, 3)
        grid.setRowStretch(0, 3)
        grid.setRowStretch(1, 2)
        for view in (self.series_view, self.principals_view, self.actions_view, self.services_view, self.actors_view):
            view.retry_requested.connect(self.reload)

    def _clear(self) -> None:
        self.chart.set_series([])
        for grid in (self.principals, self.actions, self.services):
            grid.set_rows([])
        self.actors_label.setText("")

    def reload(self) -> None:
        self.dirty = False
        self._clear()
        p, provider, scope = self.program, self.program.ctx.providers.activity, self.program.scope
        # the series load owns the status bar; the secondary loads stay quiet (M3-R10)
        p.load(self.series_view, provider.time_series, scope, p.bucket(), render=self._render_series,
               is_empty=self._series_empty)
        for view, grid, dimension in ((self.principals_view, self.principals, "principal"),
                                      (self.actions_view, self.actions, "action"),
                                      (self.services_view, self.services, "service")):
            p.load(view, provider.top, scope, dimension, 10, render=partial(self._render_top, grid), status=False)
        p.load(self.actors_view, provider.top, scope, "actor_kind", 2, render=self._render_actors, status=False)

    @staticmethod
    def _series_empty(bucketed) -> bool:
        return not bucketed.series

    def _render_series(self, bucketed) -> None:
        self.chart.set_series(list(bucketed.series))

    @staticmethod
    def _render_top(grid: DataGrid, pairs) -> None:
        grid.set_rows([{"key": k, "count": n} for k, n in pairs])

    def _render_actors(self, pairs) -> None:
        counts = dict(pairs)
        self.actors_label.setText(f"Human: {counts.get('human', 0):,}   Service: {counts.get('service', 0):,}")

    def export_grid(self) -> DataGrid:
        return self.principals
