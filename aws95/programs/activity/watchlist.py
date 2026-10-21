"""Watchlist tab: every event at risk 1 or above, worst and newest first."""
from __future__ import annotations

from functools import partial

from PySide6.QtWidgets import QVBoxLayout, QWidget

from aws95.contracts.activity import ActivityQuery
from aws95.kit.widgets.datagrid import DataGrid
from aws95.kit.widgets.stateview import StateView
from aws95.programs.activity.common import EVENT_COLUMNS, event_row, fetch_all_events


class WatchlistTab(QWidget):
    def __init__(self, program) -> None:
        super().__init__()
        self.program = program
        self.dirty = True
        self.grid = DataGrid(EVENT_COLUMNS, placeholder="Filter\u2026  e.g. rule:root-activity account:1111*")
        self.grid.row_activated.connect(program.open_event_row)
        self.grid.model.filter_applied.connect(partial(program.sync_counts, self))
        self.view = StateView(self.grid)
        self.primary_view = self.view
        self.view.retry_requested.connect(self.reload)
        QVBoxLayout(self).addWidget(self.view)

    def reload(self) -> None:
        self.dirty = False
        self.grid.set_rows([])                      # the old rows must not sit under a new load (M3-R5)
        self.program.load(self.view, fetch_all_events, self.program.ctx.providers.activity, self.program.scope,
                          ActivityQuery(risk_min=1), render=self._render)

    def _render(self, events) -> None:
        # ordering only: risk descending, then newest first
        rows = sorted((event_row(e) for e in events), key=lambda r: (-r["risk"], -r["time"].timestamp()))
        self.grid.set_rows(rows)

    def export_grid(self) -> DataGrid:
        return self.grid
