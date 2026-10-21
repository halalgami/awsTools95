"""Findings dialogs (spec §7): Accept, and the findings tools (findings-tools spec §6): Rules…, Seed, Backfill."""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton, QRadioButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)
from shiboken6 import isValid

from aws95.contracts.common import Scope, Status
from aws95.contracts.findings import Finding
from aws95.core.cancel import CancelToken
from aws95.kit.widgets.datagrid import Column, DataGrid
from aws95.kit.widgets.stateview import StateView

PHASE2 = "Not in this release."
DEMO_NO_WRITE = "Accepting findings isn't available in demo mode."
DEFAULT_MUTE_DAYS = 90


def _plain(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class _Phase2Dialog(QDialog):
    NOTE = ""
    focus_widget: QWidget | None = None

    def __init__(self, title: str, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.form = QFormLayout()
        self.note_label = _plain(self.NOTE, self)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setEnabled(False)
        self.save_button.setToolTip(PHASE2)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(self.form)
        lay.addWidget(self.note_label)
        lay.addWidget(buttons)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.activateWindow()
        (self.focus_widget or self).setFocus()


class MuteDialog(_Phase2Dialog):
    NOTE = "Accepting is saved on this computer only. Nothing is written to AWS."

    def __init__(self, finding: Finding, parent: QWidget | None = None, *,
                 on_save: Callable[[str, str, datetime | None], None] | None = None) -> None:
        super().__init__("Accept finding", parent)
        self.form.addRow("Finding:", _plain(f"{finding.rule} on {finding.rtype}:{finding.rid}", self))
        self.scope_rule = QRadioButton("Every resource for this &rule", self)
        self.scope_account = QRadioButton(f"This rule in a&ccount {finding.account}", self)
        self.scope_resource = QRadioButton("Only this r&esource", self)
        self.scope_resource.setChecked(True)
        for b in (self.scope_rule, self.scope_account, self.scope_resource):
            self.form.addRow(b)
        self.reason_edit = QLineEdit(self)
        self.reason_edit.setPlaceholderText("Why is this accepted?")
        self.form.addRow("Re&ason:", self.reason_edit)
        self.expiry_edit = QDateEdit(QDate(date.today() + timedelta(days=DEFAULT_MUTE_DAYS)), self)
        self.expiry_edit.setCalendarPopup(True)
        self.never_box = QCheckBox("&Never expires", self)
        self.never_box.toggled.connect(self.expiry_edit.setDisabled)
        self.focus_widget = self.reason_edit
        self.form.addRow("E&xpires:", self.expiry_edit)
        self.form.addRow(self.never_box)
        self.error_label = _plain("", self)
        self.form.addRow(self.error_label)
        self._on_save = on_save
        if on_save is not None:
            self.save_button.setEnabled(True)
            self.save_button.setToolTip("")
            self.save_button.clicked.connect(self._save)
        else:
            self.save_button.setToolTip(DEMO_NO_WRITE)

    def target(self) -> str:
        if self.scope_rule.isChecked():
            return "rule"
        if self.scope_account.isChecked():
            return "account"
        return "resource"

    def expires_at(self) -> datetime | None:
        if self.never_box.isChecked():
            return None
        d = self.expiry_edit.date()
        return datetime(d.year(), d.month(), d.day(), 23, 59, 59, tzinfo=timezone.utc)

    def _save(self, *_args) -> None:
        self.error_label.setText("")
        if self._on_save is not None:
            self._on_save(self.target(), self.reason_edit.text(), self.expires_at())

    def show_error(self, text: str) -> None:
        self.error_label.setText(text)


SEVERITIES = ("critical", "high", "medium", "low")
LOCAL_NOTE = "Changes are saved on this computer only and re-check your latest snapshots. Nothing is written to AWS."


def _submit(program, fn, *args, done, failed, **kwargs):
    handle = program.ctx.runner.submit(fn, *args, **kwargs)
    program._tasks.track(handle)
    handle.finished.connect(done)
    handle.failed.connect(failed)
    return handle


class _FocusOnShow:
    """Dialogs take focus and put it on their first input when shown (CLAUDE.md dialog rules)."""
    focus_widget: QWidget | None = None

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.activateWindow()
        (self.focus_widget or self).setFocus()


class RulesDialog(_FocusOnShow, QDialog):
    COLUMNS = [Column("id", "Rule", width=-1), Column("title", "Title", width=-1), Column("severity", "Severity"),
               Column("enabled", "Enabled"), Column("tuned", "Tuned"), Column("source", "Source")]

    def __init__(self, program, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.program = program
        self.setWindowTitle("Rules")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._rules: dict[str, object] = {}
        self._current: str | None = None
        self.table = DataGrid(self.COLUMNS)
        self.view = StateView(self.table)
        self.table.current_row_changed.connect(self._on_row)
        self.focus_widget = self.table.filter_edit
        self.enabled_box = QCheckBox("&Enabled", self)
        self.severity_combo = QComboBox(self)
        self.severity_combo.addItems(SEVERITIES)
        self.severity_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.shipped_label = _plain("", self)
        self.params_table = QTableWidget(0, 3, self)
        self.params_table.setHorizontalHeaderLabels(["Name", "Value", "Shipped"])
        self.params_table.horizontalHeader().setStretchLastSection(True)
        self.params_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.params_table.verticalHeader().setVisible(False)
        self.error_label = _plain("", self)
        self.status_label = _plain("", self)
        self.save_button = QPushButton("&Save", self)
        self.reset_button = QPushButton("Reset to s&hipped", self)
        close = QPushButton("&Close", self)
        self.save_button.clicked.connect(self._save)
        self.reset_button.clicked.connect(self._reset)
        close.clicked.connect(self.close)
        editor = QFormLayout()
        editor.addRow(self.enabled_box)
        sev = QHBoxLayout()
        sev.addWidget(self.severity_combo)
        sev.addWidget(self.shipped_label, 1)
        severity_label = QLabel("Se&verity:", self)
        severity_label.setBuddy(self.severity_combo)
        editor.addRow(severity_label, sev)
        editor.addRow("Parameters:", self.params_table)
        buttons = QHBoxLayout()
        for b in (self.save_button, self.reset_button):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.view, 2)
        lay.addLayout(editor)
        lay.addWidget(self.error_label)
        lay.addWidget(_plain(LOCAL_NOTE, self))
        lay.addWidget(self.status_label)
        lay.addLayout(buttons)
        self._set_editing(False)
        self.resize(820, 620)
        self.reload()

    def reload(self, *_args) -> None:
        self.program.load(self.view, self.program.ctx.providers.findings.rules, Scope(), render=self._render,
                          status=False)

    def _render(self, rules) -> None:
        self._rules = {r.id: r for r in rules}
        self.table.set_rows([{"id": r.id, "title": r.title, "severity": r.severity,
                              "enabled": "Yes" if r.enabled else "No", "tuned": "tuned" if r.tuned else "",
                              "source": r.source} for r in rules])
        if self._current:
            self.select_rule(self._current)

    def select_rule(self, rule_id: str) -> None:
        for i, row in enumerate(self.table.visible_rows()):
            if row["id"] == rule_id:
                self.table.view.selectRow(i)
                self._on_row(row)
                return

    def _set_editing(self, on: bool) -> None:
        for w in (self.enabled_box, self.severity_combo, self.params_table, self.save_button, self.reset_button):
            w.setEnabled(on)

    def _on_row(self, row) -> None:
        rule = self._rules.get(row["id"]) if row else None
        self._current = rule.id if rule else None
        self.error_label.setText("")
        self._set_editing(rule is not None)
        if rule is None:
            return
        self.enabled_box.setChecked(rule.enabled)
        self.severity_combo.setCurrentText(rule.severity)
        self.shipped_label.setText(f"(shipped: {rule.default_severity})")
        self.params_table.setRowCount(0)
        for name in sorted(rule.default_params):
            r = self.params_table.rowCount()
            self.params_table.insertRow(r)
            key = QTableWidgetItem(name)
            key.setFlags(key.flags() & ~Qt.ItemFlag.ItemIsEditable)
            shipped = QTableWidgetItem(json.dumps(rule.default_params[name]))
            shipped.setFlags(shipped.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.params_table.setItem(r, 0, key)
            self.params_table.setItem(r, 1, QTableWidgetItem(json.dumps(rule.params.get(name))))
            self.params_table.setItem(r, 2, shipped)
        self.reset_button.setEnabled(rule.tuned)

    def _params(self) -> dict | None:
        out = {}
        for r in range(self.params_table.rowCount()):
            name, text = self.params_table.item(r, 0).text(), self.params_table.item(r, 1).text()
            try:
                out[name] = json.loads(text)
            except ValueError:
                self.error_label.setText(f"{name} isn't valid JSON.")
                return None
        return out

    def _save(self, *_args) -> None:
        self.error_label.setText("")
        params = self._params()
        if params is None or self._current is None:
            return
        self._busy(True)
        _submit(self.program, self.program.ctx.providers.findings.set_rule, Scope(), self._current,
                enabled=self.enabled_box.isChecked(), severity=self.severity_combo.currentText(), params=params,
                done=self._done, failed=self._failed)

    def _reset(self, *_args) -> None:
        if self._current is None:
            return
        self._busy(True)
        _submit(self.program, self.program.ctx.providers.findings.reset_rule, Scope(), self._current,
                done=self._done, failed=self._failed)

    def _busy(self, on: bool) -> None:
        self.save_button.setEnabled(not on)
        self.reset_button.setEnabled(not on)

    def _done(self, result) -> None:
        if not isValid(self):
            return
        self._busy(False)
        if result.status is Status.OK:
            self.status_label.setText(result.message or "")
            self.reload()
            self.program.refresh()
        else:
            self.error_label.setText(result.message or "The change wasn't saved.")

    def _failed(self, message: str, _details: str = "") -> None:
        if isValid(self):
            self._busy(False)
            self.error_label.setText(message)


class SeedDialog(_FocusOnShow, QDialog):
    def __init__(self, program, finding: Finding, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.program, self.finding = program, finding
        self.setWindowTitle("Seed finding")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        seen = finding.first_seen.date()
        self.date_edit = QDateEdit(QDate(seen.year, seen.month, seen.day).addDays(-30), self)
        self.date_edit.setCalendarPopup(True)
        self.focus_widget = self.date_edit
        self.note_edit = QLineEdit(self)
        self.note_edit.setPlaceholderText("Where it was found earlier, e.g. Q2 audit")
        self.error_label = _plain("", self)
        self.save_button = QPushButton("&Save", self)
        self.remove_button = QPushButton("&Remove seed", self)
        close = QPushButton("&Close", self)
        self.remove_button.setVisible(finding.seeded)
        self.save_button.clicked.connect(self._save)
        self.remove_button.clicked.connect(self._remove)
        close.clicked.connect(self.close)
        form = QFormLayout()
        form.addRow("Finding:", _plain(f"{finding.rule} on {finding.rtype}:{finding.rid} ({finding.account})", self))
        form.addRow("Now first seen:", _plain(seen.isoformat() + (" (seeded)" if finding.seeded else ""), self))
        form.addRow("&First seen:", self.date_edit)
        form.addRow("&Note:", self.note_edit)
        buttons = QHBoxLayout()
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.error_label)
        lay.addWidget(_plain("Seeds are saved on this computer only. Nothing is written to AWS.", self))
        lay.addLayout(buttons)

    def _save(self, *_args) -> None:
        d = self.date_edit.date()
        _submit(self.program, self.program.ctx.providers.findings.seed, Scope(), self.finding,
                date(d.year(), d.month(), d.day()), self.note_edit.text(), done=self._done, failed=self._failed)

    def _remove(self, *_args) -> None:
        _submit(self.program, self.program.ctx.providers.findings.unseed, Scope(), self.finding,
                done=self._done, failed=self._failed)

    def _done(self, result) -> None:
        if not isValid(self):
            return
        if result.status is Status.OK:
            self.program.refresh()
            self.close()
        else:
            self.error_label.setText(result.message or "The seed wasn't saved.")

    def _failed(self, message: str, _details: str = "") -> None:
        if isValid(self):
            self.error_label.setText(message)


class BackfillDialog(_FocusOnShow, QDialog):
    _relay = Signal(int, int)

    def __init__(self, program, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.program = program
        self.setWindowTitle("Backfill findings")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._stop: CancelToken | None = None
        self.since_edit = QDateEdit(QDate(date.today() - timedelta(days=30)), self)
        self.since_edit.setCalendarPopup(True)
        self.focus_widget = self.since_edit
        self.progress_label = _plain("", self)
        self.start_button = QPushButton("&Start", self)
        self.cancel_button = QPushButton("Ca&ncel", self)
        self.cancel_button.setEnabled(False)
        close = QPushButton("&Close", self)
        self.start_button.clicked.connect(self._start)
        self.cancel_button.clicked.connect(self._cancel)
        close.clicked.connect(self.close)
        self._relay.connect(self._on_progress)
        form = QFormLayout()
        form.addRow("S&ince:", self.since_edit)
        form.addRow("Accounts:", _plain(", ".join(program.scope.accounts) or "All accounts in view", self))
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(_plain("Re-runs today's rules on stored snapshots. Nothing is read from AWS.", self))
        lay.addWidget(self.progress_label)
        lay.addLayout(buttons)

    def _start(self, *_args) -> None:
        d = self.since_edit.date()
        self._stop = CancelToken()
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_label.setText("Starting…")
        _submit(self.program, self.program.ctx.providers.findings.backfill, self.program.scope,
                date(d.year(), d.month(), d.day()), progress=self._relay.emit, stop=self._stop,
                done=self._done, failed=self._failed)

    def _on_progress(self, done: int, total: int) -> None:
        if isValid(self):
            self.progress_label.setText(f"{done} of {total} snapshots")

    def _cancel(self, *_args) -> None:
        if self._stop is not None:
            self._stop.cancel()
            self.progress_label.setText("Stopping…")

    def _done(self, result) -> None:
        if not isValid(self):
            return
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress_label.setText(result.message or "")
        if result.status is Status.OK:
            self.program.refresh()

    def _failed(self, message: str, _details: str = "") -> None:
        if isValid(self):
            self.start_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self.progress_label.setText(message)
