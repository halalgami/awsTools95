"""Billing: Cost Explorer actuals only (spec §7, D-decision). Every figure comes from a Result; no view math."""
from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QPushButton, QSplitter, QTabWidget, QVBoxLayout,
                               QWidget)

from aws95.contracts.billing import BILLING_COST_NOTE, BILLING_NOT_ENABLED, CostPoint, CostSummary, FreeTierUsage
from aws95.contracts.common import Scope
from aws95.kit.widgets.bevel import BevelPanel
from aws95.kit.widgets.charts import BarChart, LineChart
from aws95.kit.widgets.datagrid import Column, DataGrid, format_value
from aws95.kit.widgets.stateview import StateView
from aws95.programs.common import ProgramBase

POINT_COLUMNS = {
    "service": [Column("n", "#", "number", width=32), Column("service", "Service", width=-1), Column("period", "Period"), Column("amount", "Amount", "money", -1),
                Column("unit", "Unit", width=60)],
    "account": [Column("account", "Account", width=-1), Column("period", "Period"), Column("amount", "Amount", "money", -1),
                Column("unit", "Unit", width=60)],
    "region": [Column("account", "Account", width=-1), Column("region", "Region", width=-1), Column("period", "Period"),
               Column("amount", "Amount", "money", -1), Column("unit", "Unit", width=60)],
    "usage_type": [Column("account", "Account", width=-1), Column("usage_type", "Usage type", width=-1), Column("period", "Period"),
                   Column("amount", "Amount", "money", -1), Column("unit", "Unit", width=60)],
    "trend": [Column("period", "Month"), Column("amount", "Amount", "money", -1), Column("unit", "Unit", width=60)],
    "daily": [Column("period", "Day"), Column("amount", "Amount", "money", -1), Column("unit", "Unit", width=60)],
    "uncovered": [Column("account", "Account", width=-1), Column("service", "Service", width=-1),
                  Column("reason", "Reason", width=-1),
                  Column("period", "Period"), Column("amount", "Amount", "money", -1), Column("unit", "Unit", width=60)],
}
FREE_TIER_COLUMNS = [Column("service", "Service"), Column("metric", "Metric"), Column("actual", "Actual", "number"),
                     Column("limit", "Limit", "number"), Column("forecast", "Forecast", "number"),
                     Column("unit", "Unit", width=60), Column("period", "Period")]
TABS = (("service", "By service"), ("account", "By account"), ("region", "By region"),
        ("usage_type", "By usage type"), ("trend", "Trend"), ("daily", "Daily"), ("free_tier", "Free tier"),
        ("uncovered", "Uncovered spend"))
# Program-menu labels: unique mnemonics that avoid the menu bar letters F, E, V, B, H.
MENU_LABELS = {"service": "By &service", "account": "By &account", "region": "By re&gion",
               "usage_type": "By &usage type", "trend": "&Trend", "daily": "&Daily",
               "free_tier": "Free t&ier", "uncovered": "Unc&overed spend"}
NOTES = {
    "uncovered": "Services with spend that no AWS 95 collector inventories yet. Tax, support and similar lines are listed "
                 "as not a resource service.",
    "region": "This month to date, from Cost Explorer.",
    "usage_type": "This month to date, from Cost Explorer.",
    "trend": "Thirteen months of Cost Explorer actuals.",
    "daily": "This month's Cost Explorer actuals, one row per day.",
}


def period_start(period: str) -> datetime:
    parts = [int(p) for p in period.split("-")]
    return datetime(parts[0], parts[1], parts[2] if len(parts) > 2 else 1, tzinfo=timezone.utc)


def money(amount, currency: str) -> str:
    return "—" if amount is None else f"{format_value(amount, 'money')} {currency}"


def _plain(text: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


@dataclass
class _Spec:
    fetch: Callable[[Scope], object]
    rows: Callable[[tuple], list[dict]]


class CostPage(QWidget):
    def __init__(self, key: str, columns, chart: BarChart | LineChart | None, note: str | None) -> None:
        super().__init__()
        self.key = key
        self.dirty = True
        self.grid = DataGrid(columns)
        self.chart = chart
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 0, 0)
        if note:
            note_label = _plain(note, inner)
            note_label.setWordWrap(True)          # a long note must never set the window's minimum width
            lay.addWidget(note_label)
        if chart is not None:
            split = QSplitter(Qt.Orientation.Vertical)
            split.addWidget(chart)
            split.addWidget(self.grid)
            lay.addWidget(split)
        else:
            lay.addWidget(self.grid)
        self.view = StateView(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.view)

    def close_chart(self) -> None:
        if self.chart is not None:
            self.chart.close()


def _point_rows(*dims: str, ordinal: bool = False):
    def rows(points: tuple[CostPoint, ...]) -> list[dict]:
        out = []
        for i, p in enumerate(points, 1):
            row = {"period": p.period, "amount": p.amount, "unit": p.unit, "point": p}
            if ordinal:
                row["n"] = i
            for dim in dims:
                row[dim] = p.dims.get(dim, "")
            out.append(row)
        return out
    return rows


def _free_tier_rows(items: tuple[FreeTierUsage, ...]) -> list[dict]:
    return [{"service": f.service, "metric": f.metric, "actual": f.actual, "limit": f.limit,
             "forecast": f.forecast, "unit": f.unit, "period": f.period} for f in items]


class BillingProgram(ProgramBase):
    TITLE = "Billing"
    PROGRAM_MENU = "&Billing"
    REFRESH_ON_SNAPSHOT = True
    EXPORT_NAME = "billing.csv"

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        billing = ctx.providers.billing

        kpis = BevelPanel("raised")
        grid = QGridLayout(kpis)
        self.mtd_label, self.forecast_label, self.last_month_label = (_plain("—"), _plain("—"), _plain("—"))
        for col, (title, label) in enumerate((("Month to date (incl. today)", self.mtd_label), ("Forecast (month end)", self.forecast_label),
                                              ("Last month", self.last_month_label))):
            grid.addWidget(_plain(title), 0, col)
            f = label.font()
            f.setBold(True)
            f.setPointSizeF(f.pointSizeF() * 1.4)
            label.setFont(f)
            grid.addWidget(label, 1, col)
        kpi_note = _plain("From your latest snapshot with Billing. Month to date includes today, which is still accruing. "
                          "The month-end forecast adds AWS's forecast from tomorrow to the end of the month; on the "
                          "last day of a month there is none, so it shows —.")
        kpi_note.setWordWrap(True)
        grid.addWidget(kpi_note, 2, 0, 1, 3)
        self.kpi_view = StateView(kpis)

        self._specs: dict[str, _Spec] = {
            "service": _Spec(partial(billing.breakdown, dimension="service"), _point_rows("service", ordinal=True)),
            "account": _Spec(partial(billing.breakdown, dimension="account"), _point_rows("account")),
            "region": _Spec(partial(billing.breakdown, dimension="region"), _point_rows("account", "region")),
            "usage_type": _Spec(partial(billing.breakdown, dimension="usage_type"), _point_rows("account", "usage_type")),
            "trend": _Spec(partial(billing.trend, granularity="monthly"), _point_rows()),
            "daily": _Spec(partial(billing.trend, granularity="daily"), _point_rows()),
            "free_tier": _Spec(billing.free_tier, _free_tier_rows),
            "uncovered": _Spec(billing.uncovered_spend, _point_rows("account", "service", "reason")),
        }
        self.tabs = QTabWidget()
        self.pages: dict[str, CostPage] = {}
        for key, title in TABS:
            chart = LineChart() if key in ("trend", "daily") else BarChart() if key == "service" else None
            columns = FREE_TIER_COLUMNS if key == "free_tier" else POINT_COLUMNS[key]
            page = CostPage(key, columns, chart, NOTES.get(key))
            self.pages[key] = page
            self.tabs.addTab(page, title)
        for key in ("service", "uncovered"):
            self.pages[key].grid.row_activated.connect(self._open_service)
        self.tabs.currentChanged.connect(self._on_tab)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.kpi_view)
        # Spec §2.1: the cost is stated beside the control, before anything runs. Enabling only saves the choice
        # and opens the snapshot prompt, which states it again.
        self.enable_button = QPushButton("&Enable Billing…")
        self.enable_button.clicked.connect(self._enable_billing)
        self.enable_note = _plain(BILLING_COST_NOTE)
        self.enable_note.setWordWrap(True)
        enable_row = QHBoxLayout()
        enable_row.addWidget(self.enable_button, 0, Qt.AlignmentFlag.AlignLeft)
        enable_row.addWidget(self.enable_note, 1)
        for w in (self.enable_button, self.enable_note):
            w.hide()
        lay.addLayout(enable_row)
        lay.addWidget(self.tabs, 1)
        self.set_content(body)
        for key, _title in TABS:
            self.program_menu.addAction(MENU_LABELS[key]).triggered.connect(partial(self.select_tab, key))

    # ----- loading -----
    def refresh(self) -> None:
        for label in (self.mtd_label, self.forecast_label, self.last_month_label):
            label.setText("—")                       # stale figures never sit beside an error
        self.mtd_label.setToolTip("")
        for page in self.pages.values():
            self.invalidate(page.view)               # a late result of the previous scope must not land
            self._clear_page(page)
        # One status owner per program (M3-R23): the visible page owns the status bar, not the KPI summary.
        self.load(self.kpi_view, self.ctx.providers.billing.summary, self.scope, render=self._render_summary,
                  status=False, on_result=self._after_summary)
        for page in self.pages.values():
            page.dirty = True
        self._load_page(self.current_page())

    def _after_summary(self, result) -> None:
        missing = BILLING_NOT_ENABLED.split(" for ")[0] in (result.message or "")    # "Billing isn't enabled"
        for w in (self.enable_button, self.enable_note):
            w.setVisible(missing and not self.is_demo)

    def _enable_billing(self, *_args) -> None:
        accounts = self.scope.accounts or tuple(e.account_id for e in self.ctx.settings.roster())
        for account_id in accounts:
            self.ctx.settings.set_account_billing(account_id, True)
        self.status.set_counts("Billing will be included in the next snapshot.")
        self.ctx.shell.take_snapshot()

    def _render_summary(self, s: CostSummary) -> None:
        self.mtd_label.setText(money(s.mtd, s.currency))
        self.forecast_label.setText(money(s.forecast, s.currency))
        self.last_month_label.setText(money(s.last_month, s.currency))
        self.mtd_label.setToolTip(f"As of {s.as_of.isoformat()}")

    @property
    def primary_view(self) -> StateView:
        return self.current_page().view            # the visible page owns the status bar

    def current_page(self) -> CostPage:
        return self.tabs.currentWidget()

    def select_tab(self, key: str) -> None:
        self.tabs.setCurrentWidget(self.pages[key])

    def _on_tab(self, _index: int) -> None:
        page = self.current_page()
        if page.dirty:
            self._load_page(page)
        else:
            self._show_count(page)
            self.retry_failed(page.view)

    @staticmethod
    def _clear_page(page: CostPage) -> None:
        page.grid.set_rows([])
        if page.chart is not None and page.chart.plotItem is None:      # closed with the window
            return
        if isinstance(page.chart, LineChart):
            page.chart.set_series([])
        elif isinstance(page.chart, BarChart):
            page.chart.set_data([], [])

    def _load_page(self, page: CostPage) -> None:
        page.dirty = False
        self._clear_page(page)                       # M3-R5: an empty or failed reload leaves nothing exportable
        if page is self.current_page():
            self._show_count(page)
        spec = self._specs[page.key]
        self.load(page.view, spec.fetch, self.scope, render=partial(self._render_page, page))

    def _render_page(self, page: CostPage, data) -> None:
        try:
            rows = self._specs[page.key].rows(data)
            # Everything derived is built before any widget is touched: a bad row must not leave a half render.
            if isinstance(page.chart, LineChart):
                series = [(period_start(r["period"]), r["amount"]) for r in rows]
            elif isinstance(page.chart, BarChart):
                bars = ([str(r["n"]) for r in rows], [r["amount"] for r in rows])   # ordinals match the "#" column
            page.grid.set_rows(rows)
            if isinstance(page.chart, LineChart):
                page.chart.set_series(series)
            elif isinstance(page.chart, BarChart):
                page.chart.set_data(*bars)
        except Exception:
            self._clear_page(page)               # StateView shows ERROR; nothing stays exportable
            raise
        if page is self.current_page():
            self._show_count(page)

    def _show_count(self, page: CostPage) -> None:
        self.status.set_counts(f"{len(page.grid.visible_rows())} rows")

    # ----- interaction -----
    def apply_navigation(self, params) -> None:
        """Ctrl+K service hit (2d spec §4.3): By service, filtered to that one service."""
        service = params.get("service")
        if not service:
            return
        page = self.pages["service"]
        page.grid.set_filter_text(f"service:{shlex.quote(service)}")
        self.select_tab("service")

    def _open_service(self, row) -> None:
        service = row.get("service")
        if service:
            self.navigate("resources", service=service)

    def export_grid(self) -> DataGrid:
        return self.current_page().grid

    def closeEvent(self, event) -> None:  # noqa: N802
        # A pyqtgraph widget destroyed as a plain child (never close()d) leaks and later segfaults.
        for page in self.pages.values():
            page.close_chart()
        super().closeEvent(event)


def make_billing(ctx) -> BillingProgram:
    return BillingProgram(ctx)
