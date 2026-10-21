"""Fleet: rule × account matrix with a per-account cost row (spec §7)."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from aws95.contracts.billing import CostPoint
from aws95.contracts.common import Status
from aws95.contracts.findings import FleetCell
from aws95.kit.theme import Theme, current_theme, theme_bus
from aws95.kit.widgets.datagrid import format_value
from aws95.kit.widgets.stateview import StateView
from aws95.programs.common import ProgramBase

COST_ROW = "Cost (MTD)"


COST_NOTE = "Cost row: month-to-date Cost Explorer actuals per account."


class FleetProgram(ProgramBase):
    TITLE = "Fleet"
    PROGRAM_MENU = "F&leet"
    REFRESH_ON_SNAPSHOT = True

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self._cells: list[FleetCell] = []
        self._costs: dict[str, CostPoint] = {}
        self._cost_reason = ""
        self._accounts: list[str] = []
        self._rules: list[str] = []
        self.matrix = QTableWidget(0, 0)
        self.matrix.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.matrix.cellClicked.connect(self.cell_clicked)
        self.matrix.installEventFilter(self)     # macOS item views never emit activated on Return
        self.view = StateView(self.matrix)
        self.primary_view = self.view
        self.cost_label = QLabel(COST_NOTE)
        self.cost_label.setTextFormat(Qt.TextFormat.PlainText)
        self.cost_view = StateView(self.cost_label)
        self.cost_note = QLabel("")                # one plain line when the cost row isn't offered (not a panel)
        self.cost_note.setTextFormat(Qt.TextFormat.PlainText)
        self.cost_note.setWordWrap(True)
        self.cost_note.hide()
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.cost_view)
        lay.addWidget(self.cost_note)
        self.set_content(body)
        # Bound method: Qt drops the connection when this widget is destroyed.
        theme_bus().changed.connect(self._on_theme_changed)
        if self.scope_bar is not None:
            self.scope_bar.labels_changed.connect(self._relabel_headers)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if (obj is self.matrix and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)):
            if not event.isAutoRepeat():
                self.cell_clicked(self.matrix.currentRow(), self.matrix.currentColumn())
            return True
        return super().eventFilter(obj, event)

    def _header_text(self, acct: str) -> str:
        bar = self.scope_bar
        ref = next((r for r in bar.accounts if r.account_id == acct), None) if bar else None
        return bar.label_for(ref)[0] if ref is not None and bar is not None else acct

    def _relabel_headers(self) -> None:
        for col, acct in enumerate(self._accounts):
            item = self.matrix.horizontalHeaderItem(col)
            if item is not None:
                item.setText(self._header_text(acct))

    def accounts(self) -> list[str]:
        return list(self._accounts)

    def rules(self) -> list[str]:
        return list(self._rules)

    def refresh(self) -> None:
        self._clear()
        self.load(self.view, self.ctx.providers.findings.fleet, self.scope, render=self._render_matrix)
        self.load(self.cost_view, self.ctx.providers.billing.breakdown, self.scope, "account",
                  render=self._render_costs, on_result=self._after_costs, status=False)

    def _clear(self) -> None:
        """Drop every old cell so an ERROR or empty result never leaves stale figures (M3-R5)."""
        self._cells, self._costs, self._rules, self._accounts = [], {}, [], []
        self._cost_reason = ""
        self.matrix.clear()
        self.matrix.setRowCount(0)
        self.matrix.setColumnCount(0)
        self.status.set_counts("")
        self.cost_label.setText(COST_NOTE)
        self.cost_note.hide()
        self.cost_view.show()

    @staticmethod
    def _style(item: QTableWidgetItem, cell: FleetCell, t: Theme) -> None:
        if not cell.collected:
            item.setForeground(QColor(t.muted))
        elif cell.open_count:
            item.setForeground(QColor(t.bad))
        else:
            item.setForeground(QColor(t.ok))

    def _render_matrix(self, cells: tuple[FleetCell, ...]) -> None:
        # derive everything first; widgets are mutated only after this block
        new_cells = list(cells)
        rules = list(dict.fromkeys(c.rule for c in new_cells))
        accounts = list(dict.fromkeys(c.account for c in new_cells))
        headers = [self._header_text(a) for a in accounts]
        t = current_theme()
        self._cells, self._rules, self._accounts = new_cells, rules, accounts
        m = self.matrix
        m.clear()
        m.setRowCount(len(rules) + 1)
        m.setColumnCount(len(accounts))
        m.setVerticalHeaderLabels([*rules, COST_ROW])
        for col, (acct, text) in enumerate(zip(accounts, headers)):
            header = QTableWidgetItem(text)
            header.setToolTip(acct)
            m.setHorizontalHeaderItem(col, header)
        for c in new_cells:
            item = QTableWidgetItem(c.symbol)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if not c.collected:
                item.setToolTip("Not collected for this account")
            self._style(item, c, t)
            m.setItem(rules.index(c.rule), accounts.index(c.account), item)
        self._fill_cost_row()
        self.status.set_counts(f"{len(rules)} rules × {len(accounts)} accounts")

    def _on_theme_changed(self, _theme=None) -> None:
        t = current_theme()
        for c in self._cells:
            item = self.matrix.item(self._rules.index(c.rule), self._accounts.index(c.account))
            if item is not None:
                self._style(item, c, t)

    def _render_costs(self, points: tuple[CostPoint, ...]) -> None:
        self._costs = {p.dims.get("account", ""): p for p in points}
        self._fill_cost_row()

    def _after_costs(self, result) -> None:
        self._cost_reason = result.message or ""
        if result.data is None:
            self._costs = {}
        self._fill_cost_row()                       # render ran before the reason was known: tooltips need it
        if result.status is Status.NOT_CONNECTED:
            # Honest, but one line: the cost row isn't offered here; a full "Not connected" panel would take the
            # matrix's room for a secondary figure.
            self.cost_note.setText(f"Cost row: {result.message or 'not available.'}")
            self.cost_view.hide()
            self.cost_note.show()

    def _fill_cost_row(self) -> None:
        row = len(self._rules)
        if self.matrix.rowCount() <= row:
            return
        for col, acct in enumerate(self._accounts):
            p = self._costs.get(acct)
            item = QTableWidgetItem("—" if p is None else f"{format_value(p.amount, 'money')} {p.unit}")
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            missing = f"No cost figure: {self._cost_reason}" if self._cost_reason else "No cost figure from Billing"
            item.setToolTip(missing if p is None else item.text())
            self.matrix.setItem(row, col, item)
        self.matrix.resizeColumnsToContents()     # an exact money figure is never elided

    def cell_clicked(self, row: int, col: int) -> None:
        if not 0 <= col < len(self._accounts):
            return
        acct = self._accounts[col]
        if row == len(self._rules):
            self.navigate("billing")
            return
        if not 0 <= row < len(self._rules):
            return
        rule = self._rules[row]
        cell = next((c for c in self._cells if c.rule == rule and c.account == acct), None)
        if cell is not None and not cell.collected:
            self.status.set_counts(f"Not collected: {rule} in {acct}")
            return
        self.navigate("findings", rule=rule, account=acct)


def make_fleet(ctx) -> FleetProgram:
    return FleetProgram(ctx)
