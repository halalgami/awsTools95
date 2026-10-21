"""Filter tab: an on-demand query over the activity log, with Markdown and CSV export."""
from __future__ import annotations

from datetime import timezone
from functools import partial

from PySide6.QtWidgets import QCheckBox, QFormLayout, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from aws95.contracts.activity import ActivityQuery
from aws95.contracts.common import Scope
from aws95.kit.widgets.datagrid import DataGrid
from aws95.kit.widgets.stateview import StateView
from aws95.programs.activity.common import EVENT_COLUMNS, event_row, fetch_all_events
from aws95.programs.common import rows_to_csv, rows_to_markdown

PROMPT = "Set any filters and press Run."


def _text(edit: QLineEdit) -> str | None:
    return edit.text().strip() or None


class FilterTab(QWidget):
    def __init__(self, program) -> None:
        super().__init__()
        self.program = program
        self.dirty = False                  # runs on demand, never auto-reloads
        self.stale = False                  # the range or scope changed since the rows were produced
        self._pending: list[str] = []       # provenance of the run in flight (query, range, scope)
        self._provenance: list[str] | None = None   # provenance of the rows on screen, once the run finished
        self.principal_edit, self.service_edit = QLineEdit(), QLineEdit()
        self.action_edit, self.arn_edit = QLineEdit(), QLineEdit()
        self.service_edit.setPlaceholderText("e.g. iam.amazonaws.com")
        self.arn_edit.setPlaceholderText("part of a resource ARN")
        self.human_box = QCheckBox("Hu&mans only")
        self.risk_box = QCheckBox("&Risky only (risk \u2265 1)")
        self.run_button = QPushButton("R&un")
        self.run_button.setDefault(True)
        self.run_button.clicked.connect(self.reload)
        self.export_md_button = QPushButton("E&xport Markdown\u2026")
        self.export_csv_button = QPushButton("Export &CSV\u2026")
        self.export_md_button.clicked.connect(self.export_markdown)
        self.export_csv_button.clicked.connect(self.export_csv)
        self.grid = DataGrid(EVENT_COLUMNS)
        self.grid.row_activated.connect(program.open_event_row)
        self.grid.model.filter_applied.connect(partial(program.sync_counts, self))
        self.view = StateView(self.grid)
        self.primary_view = self.view
        self.view.retry_requested.connect(self.reload)
        self.view.show_empty(PROMPT)
        self._set_exports(False)

        form = QFormLayout()
        for label, edit in (("&Principal:", self.principal_edit), ("&Service:", self.service_edit),
                            ("Act&ion:", self.action_edit), ("ARN c&ontains:", self.arn_edit)):
            form.addRow(label, edit)
        form.addRow(self.human_box)
        form.addRow(self.risk_box)
        buttons = QHBoxLayout()
        for b in (self.run_button, self.export_md_button, self.export_csv_button):
            buttons.addWidget(b)
        buttons.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addLayout(buttons)
        lay.addWidget(self.view, 1)

    def query(self) -> ActivityQuery:
        return ActivityQuery(principal=_text(self.principal_edit), service=_text(self.service_edit),
                             action=_text(self.action_edit), arn_contains=_text(self.arn_edit),
                             human_only=self.human_box.isChecked(), risk_min=1 if self.risk_box.isChecked() else 0)

    def reload(self) -> None:
        self.stale = False
        self._provenance = None
        self.program.status.set_counts("")
        self._set_exports(False)
        self.grid.set_rows([])                      # the old rows must not sit under a new load (M3-R5)
        scope, query = self.program.scope, self.query()
        self._pending = self._describe(scope, query)    # captured now, not read from the controls at export time
        self.program.load(self.view, fetch_all_events, self.program.ctx.providers.activity, scope, query,
                          render=self._render, on_result=self._on_result)

    @staticmethod
    def _describe(scope: Scope, query: ActivityQuery) -> list[str]:
        lines = [f"{label}: {value}" for label, value in (
            ("Principal", query.principal), ("Service", query.service), ("Action", query.action),
            ("ARN contains", query.arn_contains)) if value]
        if query.human_only:
            lines.append("Humans only: yes")
        if query.risk_min:
            lines.append(f"Risky only: risk >= {query.risk_min}")
        if not lines:
            lines.append("Query: no filters")
        tr = scope.time_range
        if tr is not None:
            fmt = "%Y-%m-%d %H:%M:%S"
            lines.append(f"Range (UTC): {tr.start.astimezone(timezone.utc).strftime(fmt)} -> "
                         f"{tr.end.astimezone(timezone.utc).strftime(fmt)}")
        lines.append("Accounts: " + (", ".join(scope.accounts) or "All accounts"))
        lines.append("Regions: " + (", ".join(scope.regions) or "All regions"))
        return lines

    def _render(self, events) -> None:
        self.grid.set_rows([event_row(e) for e in events])

    def _on_result(self, result) -> None:
        if not self.grid.visible_rows() and not self.grid.model.total_count:
            return                                  # empty, error or not connected: nothing to export
        status = f"Result status: {result.status.value}"
        self._provenance = [*self._pending, status + (f" ({result.message})" if result.message else "")]
        self._set_exports(not self.stale)

    def mark_stale(self) -> None:
        """The range or scope changed: only a tab that holds results has anything to flag, and none is exportable."""
        if self._provenance is None:
            return
        self.stale = True
        self._set_exports(False)

    def _set_exports(self, on: bool) -> None:
        self.export_md_button.setEnabled(on)
        self.export_csv_button.setEnabled(on)

    def exportable(self) -> bool:
        return not self.stale and self._provenance is not None

    def export_grid(self) -> DataGrid | None:
        """None while the rows on screen are stale or have no provenance: they are never exportable (M3-R16)."""
        return self.grid if self.exportable() else None

    def _save(self, default_name: str, filters: str, make_text) -> bool:
        return self.program.save_grid(self.export_grid(), default_name, filters, make_text, parent=self)

    def export_markdown(self) -> bool:
        return self._save("activity-filter.md", "Markdown (*.md)",
                          partial(rows_to_markdown, "Activity filter", preamble=self._provenance or ()))

    def export_csv(self) -> bool:
        return self._save("activity-filter.csv", "CSV files (*.csv)", rows_to_csv)
