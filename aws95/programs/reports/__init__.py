"""Reports: type + scope → preview → save as Markdown/CSV; saved files appear under Start ▸ Documents (spec §7)."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QSplitter, \
    QVBoxLayout, QWidget

from aws95.contracts.common import Result, Scope
from aws95.contracts.reports import RenderedReport, ReportType
from aws95.kit.widgets.stateview import StateView
from aws95.programs.common import ProgramBase, write_text
from aws95.programs.common.export import defang_csv_text

ID_ROLE = int(Qt.ItemDataRole.UserRole) + 1
EMPTY_PREVIEW = "Pick a report on the left to preview it."


def _scope_text(scope: Scope) -> str:
    parts = [f"accounts: {', '.join(scope.accounts) if scope.accounts else 'all'}",
             f"regions: {', '.join(scope.regions) if scope.regions else 'all'}"]
    if scope.time_range is not None:
        tr = scope.time_range
        parts.append(f"time range: {tr.start.astimezone(timezone.utc).isoformat()} → "
                     f"{tr.end.astimezone(timezone.utc).isoformat()}")
    return "; ".join(parts)


def with_provenance(markdown: str, report_type: str, scope: Scope, result: Result,
                    now: datetime | None = None) -> str:
    """Prepend a provenance block under the title line; the provider's own text is left untouched (M3-R16)."""
    status = result.status.name + (f" ({result.message})" if result.message else "")
    lines = [f"> Report type: {report_type}", f"> Scope: {_scope_text(scope)}", f"> Result status: {status}",
             f"> Generated: {(now or datetime.now(timezone.utc)).strftime('%Y-%m-%d %H:%M:%S')} UTC"]
    block = "\n>\n".join(lines)                # a bare '>' keeps each line a separate paragraph when rendered
    head, _sep, rest = markdown.partition("\n")
    if head.startswith("# "):
        return f"{head}\n\n{block}\n\n{rest.lstrip(chr(10))}"
    return f"{block}\n\n{markdown}"


class ReportsProgram(ProgramBase):
    TITLE = "Reports"
    PROGRAM_MENU = "&Reports"
    clock = staticmethod(lambda: datetime.now(timezone.utc))      # injectable, like ActivityProgram.clock

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self._types: dict[str, ReportType] = {}
        self._rendered: RenderedReport | None = None
        self._markdown: str = ""
        self._report_id: str | None = None
        self.types = QListWidget()
        self.types.currentItemChanged.connect(self._on_type)
        self.types_view = StateView(self.types)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.preview_view = StateView(self.preview)
        self.preview_view.show_empty(EMPTY_PREVIEW)
        self.save_md_button = QPushButton("Save as &Markdown…")
        self.save_csv_button = QPushButton("Save as C&SV…")
        self.save_md_button.clicked.connect(self.save_markdown)
        self.save_csv_button.clicked.connect(self.save_csv)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.preview_view, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.save_md_button)
        buttons.addWidget(self.save_csv_button)
        rl.addLayout(buttons)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.types_view)
        split.addWidget(right)
        split.setStretchFactor(1, 3)
        self.set_content(split)
        # mnemonics: bar F/E/V/R/H, scope bar A/G, buttons M/S, so the menu items take D/C
        self.save_md_action = self.program_menu.addAction("Save as Mark&down…")
        self.save_md_action.triggered.connect(self.save_markdown)
        self.save_csv_action = self.program_menu.addAction("Save as &CSV…")
        self.save_csv_action.triggered.connect(self.save_csv)
        self._sync_buttons()

    # ----- loading -----
    def _clear_preview(self) -> None:
        self.invalidate(self.preview_view)       # an in-flight render for the old scope/selection must not land
        self._rendered = None
        self._markdown = ""
        self.preview.clear()
        self._sync_buttons()

    @property
    def primary_view(self) -> StateView:
        return self.preview_view if self._report_id else self.types_view    # see refresh(): one status owner

    def refresh(self) -> None:
        self._clear_preview()
        self.preview_view.show_empty(EMPTY_PREVIEW)
        self._report_follows = False
        # One status owner (M3-R23): a selected report's render owns the status bar. The type list writes it only
        # when no report render follows from it (nothing selected, the selection gone, or the list failed).
        self.load(self.types_view, self.ctx.providers.reports.report_types, self.scope, render=self._render_types,
                  on_result=self._after_types, status=False)

    def _render_types(self, types: tuple[ReportType, ...]) -> None:
        self._types = {t.id: t for t in types}
        self.types.blockSignals(True)
        try:
            self.types.clear()
            for t in types:
                item = QListWidgetItem(t.title)
                item.setData(ID_ROLE, t.id)
                self.types.addItem(item)
        finally:
            self.types.blockSignals(False)
        if self._report_id:
            self._report_follows = self.select_report(self._report_id)

    def _after_types(self, result: Result) -> None:
        if not self._report_follows:
            self.status.set_result(result)

    def select_report(self, report_id: str) -> bool:
        for i in range(self.types.count()):
            item = self.types.item(i)
            if item.data(ID_ROLE) == report_id:
                if self.types.currentItem() is item:
                    self._render_report(report_id)
                else:
                    self.types.setCurrentItem(item)
                return True
        return False

    def _on_type(self, current, _previous) -> None:
        if current is not None:
            self._render_report(current.data(ID_ROLE))

    def _render_report(self, report_id: str) -> None:
        self._report_id = report_id
        scope = self.scope
        self._clear_preview()                    # the previous report is never left showing under a new selection
        self.load(self.preview_view, self.ctx.providers.reports.render, scope, report_id,
                  render=self._show_report, on_result=partial(self._on_report_result, scope, report_id),
                  loading_text="Rendering…")

    def _show_report(self, report: RenderedReport) -> None:
        self._rendered = report
        self._markdown = report.markdown
        self.preview.setPlainText(report.markdown)

    def _on_report_result(self, scope: Scope, report_id: str, result: Result) -> None:
        if self._rendered is not None and result.data is self._rendered:
            self._markdown = with_provenance(self._rendered.markdown, report_id, scope, result, self.clock())
            self.preview.setPlainText(self._markdown)
        else:
            self._rendered = None
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        t = self._types.get(self._report_id or "")
        r = self._rendered
        md = r is not None and t is not None and "md" in t.formats
        csv = r is not None and t is not None and "csv" in t.formats and r.csv is not None
        for widget in (self.save_md_button, self.save_md_action):
            widget.setEnabled(md)
        for widget in (self.save_csv_button, self.save_csv_action):
            widget.setEnabled(csv)

    # ----- saving -----
    def _save(self, ext: str, filters: str, text: str) -> bool:
        stem = re.sub(r"[^A-Za-z0-9_-]", "_", self._report_id or "report")
        path = self.ask_save_path(self, "Save report", f"{stem}.{ext}", filters)
        if not path:
            return False
        try:
            write_text(path, text)
        except (OSError, ValueError) as exc:
            self.status.set_counts(f"Save failed: {exc}")
            return False
        try:
            self.ctx.settings.add_recent_document(path)
        except ValueError:            # the file is written; only the recent-documents entry is refused
            self.status.set_counts(f"Saved {path} (not added to recent documents)")
            return True
        self.status.set_counts(f"Saved {path}")
        return True

    def export(self) -> bool:
        if self.save_md_button.isEnabled():          # Markdown carries the provenance block, so it is preferred
            return self.save_markdown()
        if self.save_csv_button.isEnabled():
            return self.save_csv()
        self.status.set_counts("Nothing to export")
        return False

    def save_markdown(self) -> bool:
        if not self.save_md_button.isEnabled() or self._rendered is None:
            return False
        return self._save("md", "Markdown (*.md)", self._markdown)

    def save_csv(self) -> bool:
        if not self.save_csv_button.isEnabled() or self._rendered is None or self._rendered.csv is None:
            return False
        return self._save("csv", "CSV files (*.csv)", defang_csv_text(self._rendered.csv))    # M3-R24


def make_reports(ctx) -> ReportsProgram:
    return ReportsProgram(ctx)
