"""The Run tab (resumable-snapshots spec §7.1): every section of each account's latest snapshot, each account's stage,
and Resume / Cancel. It reads only the store, at most once per REFRESH_MS while a snapshot runs."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from shiboken6 import isValid

from aws95.contracts.billing import BILLING_COST_NOTE
from aws95.contracts.common import Result, Status
from aws95.kit.widgets.datagrid import Column, DataGrid
from aws95.kit.widgets.stateview import StateView

COLUMNS = [Column("account", "Account"), Column("collector", "Section"), Column("region", "Region"),
           Column("state", "State", "status"), Column("rows", "Rows", "number"),
           Column("denials", "Denied", "number"), Column("seconds", "Time (s)", "number"), Column("error", "Error")]
REFRESH_MS = 500


def read_run(snapshots, scope, *, cancel=None) -> Result:
    """Runs on the TaskRunner: the sections and the account stages in one load, so one generation guards both."""
    sections = snapshots.sections(scope, cancel=cancel)
    if sections.status is not Status.OK or sections.data is None:
        return sections
    stages = snapshots.stages(scope, cancel=cancel)
    staged = stages.data if stages.status is Status.OK and stages.data is not None else ()
    return replace(sections, data=(sections.data, staged))


def _plain(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class RunTab(QWidget):
    def __init__(self, program) -> None:
        super().__init__(program)
        self.program = program
        self._stages: tuple = ()
        self._running = False
        self.stage_box = QVBoxLayout()
        self.stage_labels: dict[str, QLabel] = {}
        self.grid = DataGrid(COLUMNS, placeholder="Filter…  e.g. state:timed_out")
        self.view = StateView(self.grid)
        self.view.retry_requested.connect(self.reload)
        self.resume_button = QPushButton("&Resume unfinished sections")
        self.resume_button.setEnabled(False)
        self.resume_button.clicked.connect(self._resume)
        self.retry_denied_box = QCheckBox("Retry &denied sections")
        self.cancel_button = QPushButton("Ca&ncel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        self.cost_note = _plain(BILLING_COST_NOTE)
        self.cost_note.hide()
        buttons = QHBoxLayout()
        for w in (self.resume_button, self.retry_denied_box, self.cancel_button):
            buttons.addWidget(w)
        buttons.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self.stage_box)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.cost_note)
        lay.addLayout(buttons)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.reload)
        connect = getattr(getattr(program.ctx.shell, "snapshot_progress", None), "connect", None)
        if connect is not None:
            connect(self._on_progress)

    # ----- loading -----
    def reload(self, *_args) -> None:
        if not isValid(self):
            return
        p = self.program
        p.load(self.view, read_run, p.ctx.providers.snapshots, p.scope, render=self._render,
               is_empty=self._is_empty, status=False)

    @staticmethod
    def _is_empty(data) -> bool:
        return not data[0]

    def _render(self, data) -> None:
        sections, stages = data
        # Build the rows before touching widgets (M3-R8).
        rows = [{"account": self.program.account_name(s.account), "collector": s.collector, "region": s.region,
                 "state": s.state, "rows": s.rows, "denials": s.denials,
                 "seconds": int((s.ended_at - s.started_at).total_seconds()) if s.started_at and s.ended_at else None,
                 "error": s.error or ""} for s in sections]
        self.grid.set_rows(rows)
        for column in range(3):                 # account, section and region are never elided (large fonts)
            self.grid.view.resizeColumnToContents(column)
        self._render_stages(stages)
        self.program._sync_counts()

    def _render_stages(self, stages) -> None:
        self._stages = tuple(stages)
        wanted = {st.account for st in self._stages}
        for account in [a for a in self.stage_labels if a not in wanted]:
            label = self.stage_labels.pop(account)
            label.hide()
            label.deleteLater()
        for st in self._stages:
            label = self.stage_labels.get(st.account)
            if label is None:
                label = _plain()
                self.stage_box.addWidget(label)
                self.stage_labels[st.account] = label
            label.setText(st.text(self.program.account_name(st.account)))
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        now = datetime.now(timezone.utc)
        can = any(st.can_resume(now) for st in self._stages)
        self.resume_button.setEnabled(can and not self._running and self.program.ctx.providers.is_live)
        self.cost_note.setVisible(can and any(st.billing_unfinished for st in self._stages))
        self.cancel_button.setEnabled(self._running)

    # ----- live updates -----
    def _on_progress(self, text: str) -> None:
        if not isValid(self):
            return
        running = bool(text)
        if running != self._running:
            self._running = running
            self._sync_buttons()
        # While running: at most one re-read per REFRESH_MS. When it stops: one last re-read for the end states.
        if not self._timer.isActive():
            self._timer.start()

    # ----- actions -----
    def _resume(self, *_args) -> None:
        self.program.ctx.shell.resume_snapshot((), self.retry_denied_box.isChecked())

    def _cancel(self, *_args) -> None:
        self.program.ctx.shell.cancel_snapshot()
