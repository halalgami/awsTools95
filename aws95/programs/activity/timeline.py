"""Timeline tab: principals on the left, what the selected one did on the right."""
from __future__ import annotations

from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from aws95.contracts.activity import ActivityQuery
from aws95.kit.widgets.datagrid import Column, DataGrid
from aws95.kit.widgets.stateview import StateView
from aws95.programs.activity.common import EVENT_COLUMNS, event_row, fetch_all_events

PRINCIPAL_COLUMNS = [Column("kind", "Kind"), Column("name", "Principal"), Column("type", "Type"),
                     Column("count", "Events", "number")]
PICK = "Pick a principal to see what they did."


class TimelineTab(QWidget):
    def __init__(self, program) -> None:
        super().__init__()
        self.program = program
        self.dirty = True
        self.principals = DataGrid(PRINCIPAL_COLUMNS, placeholder="Filter…  e.g. kind:Human")
        self.principals_view = StateView(self.principals)
        self.primary_view = self.principals_view
        self.principals.current_row_changed.connect(self._on_principal)
        self.events = DataGrid(EVENT_COLUMNS)
        self.events_view = StateView(self.events)
        self.events_view.show_empty(PICK)
        self.events.row_activated.connect(program.open_event_row)
        self.events.model.filter_applied.connect(partial(program.sync_counts, self))
        self.principals_view.retry_requested.connect(self.reload)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.principals_view)
        split.addWidget(self.events_view)
        split.setStretchFactor(1, 3)
        QVBoxLayout(self).addWidget(split)

    def reload(self) -> None:
        self.dirty = False
        self.program.invalidate(self.events_view)
        self.events.set_rows([])
        self.events_view.show_empty(PICK)
        self.principals.set_rows([])
        self.program.load(self.principals_view, self.program.ctx.providers.activity.principals, self.program.scope,
                          render=self._render_principals)

    def _render_principals(self, rows) -> None:
        self.principals.set_rows([{"kind": "Human" if p.is_human else "Service", "name": p.principal_name,
                                   "type": p.principal_type, "count": p.count} for p in rows])

    def select_principal(self, name: str) -> bool:
        model = self.principals.model
        for i, row in enumerate(self.principals.visible_rows()):
            if row["name"] == name:
                while model.canFetchMore() and i >= model.rowCount():     # same paging as Accounts.select_profile
                    model.fetchMore()
                self.principals.view.selectRow(i)
                return True
        return False

    def _on_principal(self, row) -> None:
        if row is None:
            return
        self.events.set_rows([])
        self.program.load(self.events_view, fetch_all_events, self.program.ctx.providers.activity,
                          self.program.scope, ActivityQuery(principal=row["name"]), render=self._render_events,
                          status=False)             # the principals list owns the status bar (M3-R23)

    def _render_events(self, events) -> None:
        rows = [event_row(e) for e in sorted(events, key=lambda e: e.time_utc, reverse=True)]
        self.events.set_rows(rows)

    def export_grid(self) -> DataGrid:
        return self.events
