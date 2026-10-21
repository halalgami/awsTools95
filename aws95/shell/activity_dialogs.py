"""Activity job dialogs (first-drop spec §5.1, §5.4), owned by the shell like the snapshot prompt: Load Activity
History (the first-use choice with estimates) and Import History… (S3). Both are non-modal and never reach AWS
themselves: they emit signals that ActivityController turns into provider calls. Provider text is plain text."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QComboBox, QDateEdit, QDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit, QPushButton,
                               QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from aws95.contracts.accounts import RosterEntry
from aws95.contracts.activity import (ALL_REGIONS, DEFAULT_HISTORY_DAYS, HISTORY_DAYS, LOAD_WORDING, ImportPlan,
                                      TrailInfo)
from aws95.contracts.common import Result, Status


@dataclass(frozen=True)
class JobAccount:
    entry: RosterEntry
    label: str
    regions: tuple[str, ...]


ESTIMATING = "Estimating from the last hour of activity…"
ESTIMATE_NOTE = "Estimates assume about 50 events per request at about 2 requests a second."
CHOICE_TEXT = {7: "&7 days (recommended)", 30: "&30 days", 90: "&90 days"}
RANGE_NOTE = ("Days are UTC. The import runs in the background, newest day first; progress shows in the status bar "
              "and the tray.")
FINDING_TRAIL = "Finding the account's CloudTrail trail…"


def _plain(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _qdate(d: date) -> QDate:
    return QDate(d.year, d.month, d.day)


def _fit(dialog: QDialog) -> None:
    """Grow to the wrapped text and the table (large fonts); never shrink below what the user dragged to."""
    layout = dialog.layout()
    layout.activate()
    width = max(dialog.width(), dialog.sizeHint().width())
    dialog.resize(width, max(dialog.height(), layout.heightForWidth(width)))


def _fit_table(table: QTableWidget) -> None:
    """Every estimate row and column visible: the table asks for exactly the room its contents need."""
    table.resizeColumnsToContents()
    frame = 2 * table.frameWidth()
    width = table.verticalHeader().width() + sum(table.columnWidth(c) for c in range(table.columnCount())) + frame
    height = table.horizontalHeader().height() + sum(table.rowHeight(r) for r in range(table.rowCount())) + frame
    table.setMinimumSize(width, height)


class LoadHistoryDialog(QDialog):
    load_requested = Signal(int)                 # the chosen number of days

    def __init__(self, accounts: Sequence[JobAccount], parent: QWidget | None = None, *,
                 days: int = DEFAULT_HISTORY_DAYS) -> None:
        super().__init__(parent)
        self.setWindowTitle("Load Activity History")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._accounts = list(accounts)
        names = ", ".join(a.label for a in self._accounts)
        self.headline_label = _plain(f"Choose how much activity history to load for {names}.", self)
        self.choice_box = QGroupBox("History", self)
        choices = QVBoxLayout(self.choice_box)
        self.day_group = QButtonGroup(self)
        self.day_buttons: dict[int, QRadioButton] = {}
        for d in HISTORY_DAYS:
            button = QRadioButton(CHOICE_TEXT[d], self.choice_box)
            self.day_group.addButton(button, d)
            choices.addWidget(button)
            self.day_buttons[d] = button
        self.day_buttons.get(days, self.day_buttons[DEFAULT_HISTORY_DAYS]).setChecked(True)
        self.estimates = QTableWidget(0, 2 + len(HISTORY_DAYS), self)
        self.estimates.setHorizontalHeaderLabels(["Account", "Region", *(f"{d} days" for d in HISTORY_DAYS)])
        self.estimates.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.estimates.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.estimates.verticalHeader().setVisible(False)
        self.estimates.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.estimates.horizontalHeader().setStretchLastSection(True)
        self.estimate_label = _plain(ESTIMATING, self)
        self.wording_label = _plain(LOAD_WORDING, self)
        self.load_button = QPushButton("&Load", self)
        self.load_button.setDefault(True)
        self.cancel_button = QPushButton("Cancel", self)
        self.load_button.clicked.connect(self._load)
        self.cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.load_button)
        buttons.addWidget(self.cancel_button)
        lay = QVBoxLayout(self)
        for w in (self.headline_label, self.choice_box, self.estimates, self.estimate_label, self.wording_label):
            lay.addWidget(w)
        lay.addLayout(buttons)
        self.resize(600, 460)

    def selected_days(self) -> int:
        return self.day_group.checkedId()

    def set_estimates(self, result: Result) -> None:
        self.estimates.setRowCount(0)
        if result.status is not Status.OK or result.data is None:
            self.estimate_label.setText(f"No estimate: {result.message or 'the sample failed.'} You can still load.")
            if self.isVisible():
                _fit(self)
            return
        labels = {a.entry.account_id: a.label for a in self._accounts}
        problems, total = [], None
        for est in result.data:
            name = labels.get(est.account, est.account) if est.account else "All"
            row = self.estimates.rowCount()
            self.estimates.insertRow(row)
            for col, text in enumerate([name, est.region, *(est.text(d) for d in HISTORY_DAYS)]):
                self.estimates.setItem(row, col, QTableWidgetItem(text))
            if est.error:
                problems.append(f"{name} · {est.region}: {est.error}")
            if est.region == ALL_REGIONS:
                total = est
        for d, button in self.day_buttons.items():
            button.setText(f"{CHOICE_TEXT[d]} — {total.text(d)}" if total is not None else CHOICE_TEXT[d])
        self.estimate_label.setText("\n".join([ESTIMATE_NOTE, *problems]))
        _fit_table(self.estimates)
        if self.isVisible():
            _fit(self)

    def _load(self, *_args) -> None:
        self.load_requested.emit(self.selected_days())
        self.accept()

    def text_dump(self) -> str:
        cells = [self.estimates.item(r, c).text() for r in range(self.estimates.rowCount())
                 for c in range(self.estimates.columnCount()) if self.estimates.item(r, c) is not None]
        return "\n".join([self.headline_label.text(), *(b.text() for b in self.day_buttons.values()), *cells,
                          self.estimate_label.text(), self.wording_label.text()])

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        _fit(self)
        self.activateWindow()
        self.load_button.setFocus()


class ImportHistoryDialog(QDialog):
    trails_requested = Signal(str)               # account id: find its trails (describe_trails)
    check_requested = Signal(object)             # ImportPlan: probe access
    import_requested = Signal(object)            # ImportPlan: start the background import

    def __init__(self, accounts: Sequence[JobAccount], parent: QWidget | None = None, *, today: date,
                 account_id: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import History")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._accounts = list(accounts)
        self._trails: tuple[TrailInfo, ...] = ()
        self._pending: ImportPlan | None = None
        self._checked: ImportPlan | None = None
        self.account_combo = QComboBox(self)
        self.reader_combo = QComboBox(self)
        for a in self._accounts:
            self.account_combo.addItem(a.label, a.entry.account_id)
            self.reader_combo.addItem(f"{a.label} ({a.entry.profile})", a.entry.profile)
        start = self.account_combo.findData(account_id) if account_id else 0
        self.account_combo.setCurrentIndex(max(0, start))
        self.reader_combo.setCurrentIndex(max(0, start))
        self.trail_combo = QComboBox(self)
        self.trail_combo.setEnabled(False)
        self.trail_label = _plain(FINDING_TRAIL, self)
        self.note_label = _plain("", self)
        self.note_label.setVisible(False)
        self.from_edit = QDateEdit(_qdate(today - timedelta(days=30)), self)     # 30 days, ending yesterday
        self.to_edit = QDateEdit(_qdate(today - timedelta(days=1)), self)
        for edit in (self.from_edit, self.to_edit):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
            edit.setMaximumDate(_qdate(today))
        self.range_label = _plain(RANGE_NOTE, self)
        self.check_button = QPushButton("C&heck Access", self)
        self.result_label = _plain("", self)
        self.policy_view = QPlainTextEdit(self)
        self.policy_view.setReadOnly(True)
        self.policy_view.setVisible(False)
        self.copy_button = QPushButton("&Copy Policy Lines", self)
        self.copy_button.setVisible(False)
        self.import_button = QPushButton("&Import", self)
        self.import_button.setEnabled(False)
        self.close_button = QPushButton("Close", self)

        form = QFormLayout()
        for text, widget in (("&Account:", self.account_combo), ("&Trail:", self.trail_combo),
                             ("&Read the files with:", self.reader_combo), ("&From:", self.from_edit),
                             ("T&o:", self.to_edit)):
            label = QLabel(text, self)
            label.setBuddy(widget)
            form.addRow(label, widget)
        buttons = QHBoxLayout()
        buttons.addWidget(self.check_button)
        buttons.addWidget(self.copy_button)
        buttons.addStretch(1)
        buttons.addWidget(self.import_button)
        buttons.addWidget(self.close_button)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        for w in (self.trail_label, self.note_label, self.range_label, self.result_label, self.policy_view):
            lay.addWidget(w)
        lay.addLayout(buttons)
        self.resize(620, 520)

        self.account_combo.currentIndexChanged.connect(self._on_account)
        self.trail_combo.currentIndexChanged.connect(self._on_trail)
        self.reader_combo.currentIndexChanged.connect(self._invalidate)
        self.from_edit.dateChanged.connect(self._invalidate)
        self.to_edit.dateChanged.connect(self._invalidate)
        self.check_button.clicked.connect(self._check)
        self.copy_button.clicked.connect(self._copy)
        self.import_button.clicked.connect(self._import)
        self.close_button.clicked.connect(self.reject)

    # ----- state -----
    def account_id(self) -> str:
        return str(self.account_combo.currentData() or "")

    def _account(self) -> JobAccount | None:
        return next((a for a in self._accounts if a.entry.account_id == self.account_id()), None)

    def _trail(self) -> TrailInfo | None:
        index = self.trail_combo.currentIndex()
        return self._trails[index] if 0 <= index < len(self._trails) else None

    def plan(self) -> ImportPlan | None:
        account, trail = self._account(), self._trail()
        start, end = self.from_edit.date().toPython(), self.to_edit.date().toPython()
        if start > end:
            self.result_label.setText("The start date is after the end date.")
            return None
        if account is None or trail is None:
            return None
        return ImportPlan(account.entry, str(self.reader_combo.currentData() or account.entry.profile), trail,
                          start, end, account.regions, account.label)

    def _invalidate(self, *_args) -> None:
        self._checked = None
        self.import_button.setEnabled(False)
        self.plan()

    # ----- answers from the controller -----
    def set_trails(self, result: Result) -> None:
        if result.status is not Status.OK or result.data is None:
            self.trail_label.setText(result.message or "The trails couldn't be read.")
            return
        lookup = result.data
        if lookup.account != self.account_id():
            return                                         # a late answer for an account no longer chosen
        self._trails = lookup.trails
        blocked = self.trail_combo.blockSignals(True)
        try:
            self.trail_combo.clear()
            for t in lookup.trails:
                self.trail_combo.addItem(t.name, t.arn)
        finally:
            self.trail_combo.blockSignals(blocked)
        self.trail_combo.setEnabled(bool(lookup.trails))
        self.trail_label.setText(lookup.trails[0].describe() if lookup.trails else "")
        self.note_label.setText(lookup.note or "")
        self.note_label.setVisible(bool(lookup.note))
        self._invalidate()
        if self.isVisible():
            _fit(self)

    def set_check(self, result: Result) -> None:
        self.check_button.setEnabled(True)
        pending, self._pending = self._pending, None
        if result.status is not Status.OK or result.data is None:
            self.result_label.setText(result.message or "The access check failed.")
            self._show_policy(None)
            return
        check = result.data
        self.result_label.setText(check.message)
        self._show_policy(check.policy)
        self._checked = pending if check.ok and pending is not None and pending == self.plan() else None
        self.import_button.setEnabled(self._checked is not None)

    def _show_policy(self, policy: str | None) -> None:
        self.policy_view.setPlainText(policy or "")
        self.policy_view.setVisible(bool(policy))
        self.copy_button.setVisible(bool(policy))
        if self.isVisible():
            _fit(self)

    # ----- user actions -----
    def _on_account(self, *_args) -> None:
        self._trails = ()
        blocked = self.trail_combo.blockSignals(True)
        try:
            self.trail_combo.clear()
        finally:
            self.trail_combo.blockSignals(blocked)
        self.trail_combo.setEnabled(False)
        self.trail_label.setText(FINDING_TRAIL)
        self.note_label.setVisible(False)
        account = self._account()
        if account is not None:
            self.reader_combo.setCurrentIndex(max(0, self.reader_combo.findData(account.entry.profile)))
        self._invalidate()
        self.trails_requested.emit(self.account_id())

    def _on_trail(self, *_args) -> None:
        trail = self._trail()
        self.trail_label.setText(trail.describe() if trail is not None else "")
        self._invalidate()

    def _check(self, *_args) -> None:
        plan = self.plan()
        if plan is None:
            return
        self._pending = plan
        self.check_button.setEnabled(False)
        self.result_label.setText("Checking access…")
        self.check_requested.emit(plan)

    def _copy(self, *_args) -> None:
        QApplication.clipboard().setText(self.policy_view.toPlainText())     # only when the user presses Copy

    def _import(self, *_args) -> None:
        if self._checked is None or self._checked != self.plan():
            self.import_button.setEnabled(False)
            return
        self.import_requested.emit(self._checked)
        self.accept()

    def text_dump(self) -> str:
        return "\n".join([self.trail_label.text(), self.note_label.text(), self.range_label.text(),
                          self.result_label.text(), self.policy_view.toPlainText()])

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        _fit(self)
        self.activateWindow()
        self.account_combo.setFocus()
