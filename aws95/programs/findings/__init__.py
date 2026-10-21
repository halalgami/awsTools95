"""Findings: severity chips, Open / Resolved / Accepted, age, seeded markers, dialogs (spec §7)."""
from __future__ import annotations

import shlex
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from functools import partial

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QTabBar, QToolButton, QVBoxLayout, QWidget
from shiboken6 import isValid

from aws95.contracts.common import Result, Scope, Status
from aws95.contracts.findings import Finding
from aws95.core.cancel import CancelToken
from aws95.kit.widgets.datagrid import Column, DataGrid
from aws95.kit.widgets.stateview import StateView
from aws95.programs.common import ProgramBase
from aws95.programs.common.paging import fetch_all_pages
from aws95.programs.findings.dialogs import BackfillDialog, MuteDialog, RulesDialog, SeedDialog

SEVERITIES = ("critical", "high", "medium", "low")
STATES = (("open", "Open"), ("resolved", "Resolved"), ("accepted", "Accepted (muted)"))
COLUMNS = [Column("severity", "Severity", "status"), Column("rule", "Rule", width=-1), Column("tuned", "Tuned", width=-1),
           Column("account", "Account", width=-1), Column("region", "Region"),
           Column("age", "Age (days)", "number", -1), Column("first_seen", "First seen", width=-1),
           Column("target", "Target", width=-1), Column("muted_until", "Muted until", width=-1), Column("reason", "Reason")]


def fetch_findings(provider, scope: Scope, state: str, *, cancel: CancelToken | None = None) -> Result:
    return fetch_all_pages(partial(provider.list_findings, scope, state, cancel=cancel), cancel=cancel)


def finding_row(f: Finding) -> dict:
    seen = f.first_seen.date().isoformat() + (" (seeded)" if f.seeded else "")
    mute = f.mute
    until = "" if mute is None else ("never" if mute.expires_at is None else mute.expires_at.date().isoformat())
    return {"severity": f.severity, "rule": f.rule, "target": f"{f.rtype}:{f.rid}", "rtype": f.rtype, "rid": f.rid,
            "account": f.account, "region": f.region, "age": f.age_days, "first_seen": seen,
            "muted_until": until, "reason": mute.reason if mute else "", "tuned": "tuned" if f.tuned else "",
            "finding": f}


class FindingsProgram(ProgramBase):
    TITLE = "Findings"
    PROGRAM_MENU = "F&indings"
    REFRESH_ON_SNAPSHOT = True
    EXPORT_NAME = "findings.csv"

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self._rows: list[dict] = []
        self._nav_filter: str | None = None
        self._navigating = False

        chips = QHBoxLayout()
        self.severity_buttons: dict[str, QToolButton] = {}
        for sev in SEVERITIES:
            b = QToolButton()
            b.setCheckable(True)
            b.setChecked(True)
            b.setText(sev.title())
            b.toggled.connect(self._apply_severity)
            chips.addWidget(b)
            self.severity_buttons[sev] = b
        chips.addStretch(1)

        self.state_tabs = QTabBar()
        for _, title in STATES:
            self.state_tabs.addTab(title)
        self.state_tabs.currentChanged.connect(self._on_state_tab)

        self.grid = DataGrid(COLUMNS, placeholder="Filter…  e.g. rule:sg-open-ssh account:2222* region:eu-*")
        self.grid.row_activated.connect(self._open_resource)
        self.grid.model.filter_applied.connect(self._on_filter_applied)
        self.view = StateView(self.grid)
        self.primary_view = self.view

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(chips)
        lay.addWidget(self.state_tabs)
        lay.addWidget(self.view, 1)
        self.set_content(body)
        self._accept_dialog: MuteDialog | None = None

        menu = self.program_menu
        menu.addAction("&Accept…").triggered.connect(self.open_mute_dialog)
        menu.addAction("&Un-accept").triggered.connect(self.unaccept_selected)
        menu.addAction("&Rules…").triggered.connect(self.open_rules_dialog)
        menu.addAction("&Seed finding…").triggered.connect(self.open_seed_dialog)
        menu.addAction("&Backfill…").triggered.connect(self.open_backfill_dialog)

    # ----- state -----
    def current_state(self) -> str:
        return STATES[self.state_tabs.currentIndex()][0]

    def set_state(self, state: str) -> None:
        index = [s for s, _ in STATES].index(state)
        if index == self.state_tabs.currentIndex():
            self.refresh()
        else:
            self.state_tabs.setCurrentIndex(index)

    def _on_state_tab(self, _index: int) -> None:
        if not self._navigating and self._nav_filter is not None:
            if self.grid.filter_edit.text() == self._nav_filter:     # a filter navigation set must not leak to tabs
                self.grid.set_filter_text("")
            self._nav_filter = None
        self.refresh()

    def refresh(self) -> None:
        self._reset()
        self.load(self.view, fetch_findings, self.ctx.providers.findings, self.scope, self.current_state(),
                  render=self._render)

    def _reset(self) -> None:
        """Clear replaced content when a load starts, so an empty or failed result never leaves old rows (M3-R5)."""
        self._rows = []
        self.grid.set_rows([])
        for sev, b in self.severity_buttons.items():
            b.setText(sev.title())

    def _render(self, findings) -> None:
        rows = [finding_row(f) for f in findings]             # build everything before touching widgets
        counts = Counter(r["severity"] for r in rows)
        self._rows = rows
        for sev, b in self.severity_buttons.items():
            b.setText(f"{sev.title()} ({counts.get(sev, 0)})")
        self._apply_severity()

    def _apply_severity(self, *_args) -> None:
        wanted = {s for s, b in self.severity_buttons.items() if b.isChecked()}
        shown = [r for r in self._rows if r["severity"] in wanted]
        self.grid.set_rows(shown)
        if self._rows and not shown:
            self.status.set_counts(f"{len(self._rows)} findings hidden by severity filter")

    def _on_filter_applied(self, shown: int, total: int) -> None:
        if total == 0:
            self.status.set_counts("")
            return
        self.status.set_counts(f"{shown} of {total} findings")

    # ----- selection, navigation -----
    def selected_finding(self) -> Finding | None:
        row = self.grid.current_row()
        return row["finding"] if row else None

    def apply_navigation(self, params: Mapping[str, str]) -> None:
        parts = [f"{k}:{shlex.quote(params[k])}" for k in ("rule", "account", "target") if params.get(k)]
        text = " ".join(parts)
        self._navigating = True
        try:
            self.grid.set_filter_text(text)
            self._nav_filter = text or None
            self.set_state("open")
        finally:
            self._navigating = False

    def _open_resource(self, row) -> None:
        self.navigate("resources", rtype=row["rtype"], filter=f"rid:{row['rid']}")

    def export_grid(self) -> DataGrid:
        return self.grid

    # ----- dialogs -----
    def _show(self, dialog):
        self.mark_dialog(dialog)
        dialog.show()
        return dialog

    def open_mute_dialog(self, *_args) -> MuteDialog | None:
        finding = self.selected_finding()
        if finding is None:
            self.status.set_counts("Select a finding to accept")
            return None
        save = None if self.is_demo else partial(self._accept, finding)
        self._accept_dialog = MuteDialog(finding, self, on_save=save)
        self._accept_dialog.finished.connect(self._on_accept_dialog_finished)
        return self._show(self._accept_dialog)

    def _on_accept_dialog_finished(self, *_args) -> None:
        # The dialog deletes itself on close; drop our wrapper after its own signal has returned (Qt lessons).
        QTimer.singleShot(0, self, self._forget_accept_dialog)

    def _forget_accept_dialog(self) -> None:
        dialog = self._accept_dialog
        if dialog is None or not isValid(dialog) or not dialog.isVisible():
            self._accept_dialog = None

    def _accept(self, finding: Finding, target: str, reason: str, expires_at: datetime | None) -> None:
        handle = self.ctx.runner.submit(self.ctx.providers.findings.accept, self.scope, finding, target, reason,
                                        expires_at)
        self._tasks.track(handle)
        handle.finished.connect(partial(self._accepted, self._accept_dialog))
        handle.failed.connect(partial(self._accept_failed, self._accept_dialog))

    def _accepted(self, dialog, result) -> None:
        if not isValid(self):
            return
        if result.status is Status.OK:
            if dialog is not None and isValid(dialog):
                dialog.close()
            self.status.set_counts("Accepted. It now shows under Accepted.")
            self.refresh()
        elif dialog is not None and isValid(dialog):
            dialog.show_error(result.message or "This finding could not be accepted.")

    def _accept_failed(self, dialog, message: str, _details: str = "") -> None:
        if dialog is not None and isValid(dialog):
            dialog.show_error(message)

    def unaccept_selected(self, *_args) -> None:
        finding = self.selected_finding()
        if finding is None or finding.mute is None:
            self.status.set_counts("Select an accepted finding to un-accept")
            return
        if self.is_demo:
            self.status.set_counts("Un-accept isn't available in demo mode.")
            return
        handle = self.ctx.runner.submit(self.ctx.providers.findings.unaccept, self.scope, finding.mute.id)
        self._tasks.track(handle)
        handle.finished.connect(self._unaccepted)

    def _unaccepted(self, result) -> None:
        if not isValid(self):
            return
        if result.status is Status.OK:
            self.status.set_counts("Un-accepted. It shows under Open again.")
            self.refresh()
        else:
            self.status.set_counts(result.message or "Un-accept failed.")

    def open_rules_dialog(self) -> RulesDialog:
        return self._show(RulesDialog(self, self))

    def open_seed_dialog(self, *_args) -> SeedDialog | None:
        finding = self.selected_finding()
        if finding is None:
            self.status.set_counts("Select a finding first")
            return None
        return self._show(SeedDialog(self, finding, self))

    def open_backfill_dialog(self, *_args) -> BackfillDialog:
        return self._show(BackfillDialog(self, self))


def make_findings(ctx) -> FindingsProgram:
    return FindingsProgram(ctx)
