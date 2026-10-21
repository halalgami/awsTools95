"""Resources ▸ inspector ▸ Related (activity-polish spec §3): stored Activity events whose ARNs name the selected
resource, for that resource's account only. Local store only; nothing reaches AWS."""
from __future__ import annotations

from functools import partial

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from aws95.contracts.activity import ActivityQuery
from aws95.contracts.common import Scope, Status
from aws95.contracts.paging import fetch_all_pages
from aws95.kit.widgets.datagrid import Column, DataGrid
from aws95.kit.widgets.stateview import StateView

LIMIT = 200
NOTE = ("Matched on the resource ID in each event's ARNs. CloudTrail doesn't record a resource for every event, "
        "so this list can be incomplete.")
CAPPED = "Showing the newest 200."
EMPTY = "No stored events name this resource."
PICK = "Select a resource to see its activity."
COLUMNS = [Column("time", "Time (UTC)", "date", -1), Column("principal", "Principal", width=-1),
           Column("action", "Action", width=-1), Column("account", "Account", width=-1), Column("region", "Region"),
           Column("risk", "Risk", "number")]


def fetch_related(activity, account: str, rid: str, *, cancel=None):
    """Runs on the TaskRunner."""
    return fetch_all_pages(partial(activity.events, Scope(accounts=(account,)), ActivityQuery(arn_contains=rid),
                                   cancel=cancel), cancel=cancel, limit=LIMIT)


class RelatedPanel(QWidget):
    def __init__(self, program) -> None:
        super().__init__()
        self.program = program
        self._target: tuple[str, str] | None = None
        self.note = QLabel(NOTE)
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        self.note.setWordWrap(True)
        self.grid = DataGrid(COLUMNS)
        self.view = StateView(self.grid)
        self.view.show_empty(PICK)
        self.open_button = QPushButton("&Open in Activity Log")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open)
        buttons = QHBoxLayout()
        buttons.addWidget(self.open_button)
        buttons.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.note)
        lay.addWidget(self.view, 1)
        lay.addLayout(buttons)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """A hidden tab must not widen the inspector (the grid's own hint would squeeze the Resources list)."""
        return QSize(120, 120)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(240, 240)

    def show_for(self, row) -> None:
        p = self.program
        p.invalidate(self.view)                          # anything in flight for the previous resource is dropped
        self.grid.set_rows([])
        self.note.setText(NOTE)
        if row is None:
            self._target = None
            self.open_button.setEnabled(False)
            self.view.show_empty(PICK)
            return
        self._target = (row.account, row.rid)
        self.open_button.setEnabled(True)
        p.load(self.view, fetch_related, p.ctx.providers.activity, row.account, row.rid, render=self._render,
               is_empty=lambda events: not events, on_result=self._after, status=False)

    def _render(self, events) -> None:
        ordered = sorted(events, key=lambda e: e.time_utc, reverse=True)[:LIMIT]
        self.grid.set_rows([{"time": e.time_utc, "principal": e.principal_name, "action": e.name,
                             "account": e.account, "region": e.region, "risk": e.risk_level,
                             "arns": " ".join(e.resource_arns)} for e in ordered])
        self.note.setText(f"{NOTE} {CAPPED}" if len(events) >= LIMIT else NOTE)

    def _after(self, result) -> None:
        if result.status is Status.OK and not result.data:
            self.view.show_empty(EMPTY)

    def _open(self, *_args) -> None:
        if self._target is not None:
            account, rid = self._target
            self.program.navigate("activity", arn=rid, account=account)
