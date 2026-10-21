"""AWS setup wizard (spec §6.1): QWizard in classic style, non-modal; reaches AWS only through the accounts provider."""
from __future__ import annotations

import os
import shlex
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QLinearGradient, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QDialog, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget, QWizard, QWizardPage)
from shiboken6 import isValid

from aws95.contracts.accounts import CapabilityCheck, Profile, RosterEntry
from aws95.contracts.billing import BILLING_CHECK_NOTE
from aws95.contracts.common import Result, Scope, Status
from aws95.core.sanitize import clean_model_text
from aws95.core.settings import MAX_NICKNAME
from aws95.core.tasks import CancelScope
from aws95.kit.mascot import cloudy_pixmap
from aws95.kit.style import scaled_fixed_font
from aws95.kit.theme import current_theme, theme_bus
from aws95.kit.widgets.watermark import Watermark

PAGE_IDS = ("welcome", "sources", "choose", "permissions", "names", "finish")
MAX_PARALLEL = 4
CLI_URL = "https://aws.amazon.com/cli/"
NEUTRAL = "#c0c0c0"
DEMO_SUFFIX = " [DEMO]"
WELCOME = ("AWS 95 Tools shows your AWS accounts. It only reads. It never changes anything in your accounts. "
           "Nothing leaves this computer except requests to AWS.")


def should_autostart_wizard(settings, providers) -> bool:
    return bool(getattr(providers, "is_live", False)) and not settings.setup_completed \
        and not settings.setup_suppress_autostart and not settings.roster()


def _plain(text: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _short(path: str) -> str:
    home = os.path.expanduser("~")
    return clean_model_text("~" + path[len(home):] if path.startswith(home) else path)


def _copy(text: str, *_args) -> None:
    QGuiApplication.clipboard().setText(text)


def side_pixmap(scale: float = 1.0) -> QPixmap:
    w, h = int(150 * scale), int(360 * scale)
    pix = QPixmap(w, h)
    p = QPainter(pix)
    g = QLinearGradient(0, 0, 0, h)
    g.setColorAt(0, QColor("#000080"))
    g.setColorAt(1, QColor("#1084d0"))
    p.fillRect(pix.rect(), g)
    m = cloudy_pixmap(int(96 * scale))
    p.drawPixmap((w - m.width()) // 2, h // 3, m)
    p.end()
    return pix


class PromptRelay(QObject):
    """Bridges sso's reader-thread callback to the GUI thread: a cross-thread emit is queued by Qt."""
    prompt = Signal(str, str)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)             # parented: its last reference is never dropped on the worker thread

    def emit_prompt(self, url: str, code: str) -> None:
        self.prompt.emit(url, code)


def login_command(profile: str) -> str:
    """The command a user can run themselves. Same quoting rule as aws95.aws.sso.manual_command (the shell may not
    import aws95.aws; tests pin the two together)."""
    if sys.platform == "win32":
        quoted = f'"{profile}"' if any(c in profile for c in ' &^|<>"') else profile
    else:
        quoted = shlex.quote(profile)
    return f"aws sso login --profile {quoted}"


class SignInDialog(QDialog):
    """Non-modal `aws sso login` helper: shows the device URL (and code, when the CLI prints one), Cancel/Close both
    stop the CLI. Copy puts only the login command on the clipboard, and only when the user presses it."""
    finished_ok = Signal(str)

    def __init__(self, accounts, runner, profile: str, parent=None, *, label: str | None = None,
                 is_demo: bool = False) -> None:
        super().__init__(parent)
        self.accounts = accounts
        self.profile = profile
        self.label = label or profile
        self.command = login_command(profile)
        self.setWindowTitle(f"Sign In — {clean_model_text(self.label)}" + (DEMO_SUFFIX if is_demo else ""))
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._loader = _Loader(runner)
        self._relay = PromptRelay(self)
        self._relay.prompt.connect(self._on_prompt)
        self._started = False
        self.info_label = _plain("Finish signing in in your browser…")
        self.detail_label = _plain("")
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.copy_button = QPushButton("&Copy Command")
        self.copy_button.clicked.connect(self._copy)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.close)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        self.close_button.hide()
        lay = QVBoxLayout(self)
        lay.addWidget(self.info_label)
        lay.addWidget(self.detail_label)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.copy_button)
        row.addWidget(self.cancel_button)
        row.addWidget(self.close_button)
        lay.addLayout(row)
        self.setMinimumWidth(460)
        if is_demo:
            self._watermark = Watermark(self)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._started:
            self._started = True
            self._loader.submit(self, self.accounts.sign_in, self.profile, self._relay.emit_prompt,
                                on_done=self._done)

    def _on_prompt(self, url: str, code: str) -> None:
        url, code = clean_model_text(url).strip(), clean_model_text(code).strip()
        if not url.startswith("https://"):
            self.detail_label.setText("The sign-in address was not recognised. Cancel and sign in with the "
                                      "AWS CLI instead.")
            return
        self.detail_label.setText(f"Open {url} and enter code {code}" if code else f"Open {url}")
        self._fit()

    def _fit(self) -> None:
        """Grow to the wrapped text (large fonts); never shrink below what the user dragged to."""
        hint = self.sizeHint()
        self.resize(max(self.width(), hint.width()), max(self.height(), self.layout().heightForWidth(self.width())))

    def _copy(self, *_args) -> None:
        _copy(self.command)

    def _done(self, result: Result) -> None:
        if result.status == Status.OK:
            self.finished_ok.emit(self.label)
            self.close()
            return
        text = clean_model_text(result.message or "Sign-in did not finish.")
        self.info_label.setText(text)
        extra = []
        if result.status == Status.NOT_CONNECTED:
            if CLI_URL not in text:
                extra.append(f"Install the AWS CLI: {CLI_URL}")
            if self.command not in text:
                extra.append(f"Or run: {self.command}")
        self.detail_label.setText("\n".join(extra))
        self._fit()
        self.cancel_button.hide()
        self.close_button.show()

    def text_dump(self) -> str:
        return "\n".join((self.info_label.text(), self.detail_label.text()))

    def done(self, r: int) -> None:
        self._loader.bump()             # cancels the CLI
        super().done(r)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._loader.bump()
        super().closeEvent(event)


class _Loader:
    """Per-page generation + CancelScope + FIFO (spec §6.4)."""

    def __init__(self, runner):
        self._runner = runner
        self.scope = CancelScope()
        self.generation = 0
        self._queue: deque = deque()
        self._running = 0

    def bump(self) -> int:
        self.scope.cancel_all()
        self._queue.clear()
        self._running = 0
        self.generation += 1
        return self.generation

    def submit(self, owner, fn, *args, on_done, limited=False, **kwargs):
        job = (owner, self.generation, fn, args, kwargs, on_done, limited)
        if limited and self._running >= MAX_PARALLEL:
            self._queue.append(job)
            return
        self._start(job)

    def _start(self, job):
        owner, generation, fn, args, kwargs, on_done, limited = job
        if limited:
            self._running += 1
        handle = self._runner.submit(fn, *args, **kwargs)
        self.scope.track(handle)
        handle.finished.connect(partial(self._deliver, owner, generation, on_done, limited))
        handle.failed.connect(partial(self._failed, owner, generation, on_done, limited))

    def _settle(self, generation, limited):
        if limited and generation == self.generation:
            self._running -= 1
            if self._queue:
                self._start(self._queue.popleft())

    def _deliver(self, owner, generation, on_done, limited, result):
        self._settle(generation, limited)
        if isValid(owner) and generation == self.generation:
            on_done(result)

    def _failed(self, owner, generation, on_done, limited, message, _details):
        self._settle(generation, limited)
        if isValid(owner) and generation == self.generation:
            on_done(Result.error(f"Unexpected error: {message.split(':')[0]}"))   # class name only, never its text


class _LoadingPage(QWizardPage):
    """A wizard page that owns a _Loader; every load is cancelled when the page is left or the wizard closes."""

    def __init__(self, wizard: "SetupWizard") -> None:
        super().__init__()
        self.w = wizard
        self.loader = _Loader(wizard.runner)

    def cancel_loads(self) -> None:
        self.loader.bump()

    def cleanupPage(self) -> None:  # noqa: N802
        self.cancel_loads()


class WelcomePage(_LoadingPage):
    def __init__(self, wizard: "SetupWizard") -> None:
        super().__init__(wizard)
        self.setTitle("Welcome")
        self.suppress_box = QCheckBox("&Don't show this at startup")
        self.suppress_box.setChecked(bool(wizard.settings.setup_suppress_autostart))
        lay = QVBoxLayout(self)
        lay.addWidget(_plain(WELCOME))
        lay.addStretch(1)
        lay.addWidget(self.suppress_box)


class SourcesPage(_LoadingPage):
    def __init__(self, wizard: "SetupWizard") -> None:
        super().__init__(wizard)
        self.setTitle("Your AWS settings")
        self.setSubTitle("These are the AWS settings files on this computer.")
        self.profile_count = 0
        self._loaded = False
        self._rows: list[QLabel] = []
        self._extras: list[QWidget] = []
        self.look_again_button = QPushButton("&Look Again")
        self.finish_empty_button = QPushButton("Finish &Without Accounts")
        self.look_again_button.clicked.connect(self._reload)
        self.finish_empty_button.clicked.connect(self._finish_empty)
        self._lay = QVBoxLayout(self)
        self._body = QVBoxLayout()
        self._lay.addLayout(self._body)
        self._lay.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.look_again_button)
        buttons.addWidget(self.finish_empty_button)
        buttons.addStretch(1)
        self._lay.addLayout(buttons)

    def initializePage(self) -> None:  # noqa: N802
        self._reload()

    def _finish_empty(self, *_args) -> None:
        self.w.finish_without_accounts()

    def _reload(self, *_args) -> None:
        self.loader.bump()
        self._loaded = False
        self.profile_count = 0
        self._clear()
        self.completeChanged.emit()
        self.loader.submit(self, self.w.accounts.config_sources, on_done=self._render)

    def _clear(self) -> None:
        for widget in (*self._rows, *self._extras):
            if isValid(widget):
                self._body.removeWidget(widget)
                widget.setParent(None)
                widget.deleteLater()
        self._rows, self._extras = [], []

    def _add(self, widget: QWidget, row: bool = False) -> None:
        (self._rows if row else self._extras).append(widget)
        self._body.addWidget(widget)

    def _render(self, result: Result) -> None:
        self._clear()
        extra: str | None = None
        if result.status == Status.OK and result.data is not None:
            self.profile_count = sum(s.profile_count for s in result.data)
            for s in result.data:
                if s.exists:
                    text = f"✔ {_short(s.path)} — {s.profile_count} profiles"
                else:
                    text = f"✖ {_short(s.path)} — not found"
                self._add(_plain(text), row=True)
        else:
            self.profile_count = 0
            msg = clean_model_text(result.message or "Could not read the AWS settings files.")
            self._add(_plain("✖ " + msg), row=True)
            extra = "Fix the file's permissions or contents, then Look Again."
        if extra:
            self._add(_plain(extra))
        if self.profile_count == 0:
            self._add(_plain("To set up AWS on this computer:"))
            self._guidance("1. Install the AWS CLI:", CLI_URL)
            self._guidance("2. For access keys, run:", "aws configure")
            self._guidance("3. For single sign-on, run:", "aws configure sso")
        self._loaded = True
        self.completeChanged.emit()

    def _guidance(self, label: str, value: str) -> None:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(_plain(f"{label} {value}"), 1)
        button = QPushButton("Copy")
        button.clicked.connect(partial(_copy, value))
        lay.addWidget(button)
        self._add(box)

    def isComplete(self) -> bool:  # noqa: N802
        return self._loaded and self.profile_count > 0

    def text_dump(self) -> str:
        return "\n".join(label.text() for label in self._rows if isValid(label))


@dataclass
class Row:
    profile: Profile
    account: str | None
    state: str                  # checking | ok | failed | never | manual
    message: str = ""
    ticked: bool = False
    note: str = ""
    signin: bool = False
    in_roster: bool = False
    verified_at: datetime | None = None     # when THIS wizard session verified the row OK


COL_USE, COL_NAME, COL_TYPE, COL_REGION, COL_STATUS = range(5)


class ChoosePage(_LoadingPage):
    def __init__(self, wizard: "SetupWizard") -> None:
        super().__init__(wizard)
        self.setTitle("Choose your accounts")
        self.setSubTitle("Tick the profiles to use. Each AWS account is used once.")
        self.rows: list[Row] = []
        self._listed = False
        self._defaults_applied = False
        self._rendering = False
        self._roster_held: dict[str, str] = {}
        self._sign_in_dialog: SignInDialog | None = None
        self._sign_in_owned = False        # False when the shell's own dialog for the profile was reused
        self.suppress_box = QCheckBox("&Don't show this at startup")
        self.suppress_box.setChecked(bool(wizard.settings.setup_suppress_autostart))
        self.finish_empty_button = QPushButton("Finish &Without Accounts")
        self.finish_empty_button.clicked.connect(self._finish_empty)
        self.status_label = _plain("")
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Use", "Profile", "Type", "Region", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.Stretch)
        for col in (COL_USE, COL_NAME, COL_TYPE, COL_REGION):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self._item_changed)
        lay = QVBoxLayout(self)
        lay.addWidget(self.status_label)
        lay.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(self.suppress_box)
        bottom.addStretch(1)
        bottom.addWidget(self.finish_empty_button)
        lay.addLayout(bottom)

    # ----- loading -----
    def cancel_loads(self) -> None:
        super().cancel_loads()
        self.hide_sign_in_dialog()

    def initializePage(self) -> None:  # noqa: N802
        self.cancel_loads()
        self.rows = []
        self._listed = False
        self._defaults_applied = False
        self.status_label.setText("Checking your profiles…")
        self._rebuild()
        self.loader.submit(self, self.w.accounts.list_profiles, on_done=self._listed_profiles)
        self.completeChanged.emit()

    def _finish_empty(self, *_args) -> None:
        self.w.finish_without_accounts()

    def _listed_profiles(self, result: Result) -> None:
        if result.status != Status.OK or result.data is None:
            msg = clean_model_text(result.message or "Could not list the AWS profiles.")
            self.status_label.setText("✖ " + msg)
            self._listed = True
            self.completeChanged.emit()
            return
        self.status_label.setText("")
        roster = {e.profile: e.account_id for e in self.w.settings.roster()}
        profiles = list(result.data)
        self._roster_held = {}
        if self.w.only_new:
            self._roster_held = {acc: name for name, acc in roster.items()}
            profiles = [p for p in profiles if p.name not in roster]
        for p in profiles:
            in_roster = p.name in roster
            account = (p.verified.account_id if p.verified else None) or roster.get(p.name)
            state = {"auto": "checking", "manual": "manual", "never": "never"}.get(p.verify, "never")
            message = f"— {clean_model_text(p.issue or '')}" if state == "never" else ""
            self.rows.append(Row(p, account if in_roster else None, state, message, ticked=in_roster,
                                 in_roster=in_roster))
        self._listed = True
        self._rebuild()
        for row in self.rows:
            if row.state == "checking":
                self._start_verify(row)
        self._maybe_apply_defaults()
        self.completeChanged.emit()

    def _start_verify(self, row: Row) -> None:
        self.loader.submit(self, self.w.accounts.verify_profile, Scope(), row.profile.name,
                           on_done=partial(self._verified, row.profile.name), limited=True)

    def _row(self, name: str) -> Row:
        return next(r for r in self.rows if r.profile.name == name)

    def _verified(self, name: str, result: Result) -> None:
        row = self._row(name)
        row.signin = False
        if result.status == Status.OK and result.data is not None:
            row.state = "ok"
            row.account = result.data.account_id
            row.message = ""
            row.verified_at = self.w.clock()
        else:
            row.state = "failed"
            row.verified_at = None
            if result.status == Status.NOT_CONNECTED and row.profile.sign_in_profile:
                row.signin = True
                row.message = "⚠ SSO sign-in needed"
            else:
                row.message = "✖ " + clean_model_text(result.message or "Could not verify this profile.")
            if row.in_roster:
                row.message = "⚠ Couldn't check now: " + clean_model_text(result.message or "not available")
        if row.state == "ok" and row.ticked:
            holder = self._holder(row)
            if holder is not None:          # a re-verify moved this row onto an account another row holds
                row.ticked = False
                row.note = f"○ Same account as {holder}"
        if self._defaults_applied:
            self._apply_default(row)
        self._render_row(self.rows.index(row))
        if not self._defaults_applied:
            self._maybe_apply_defaults()
        self.completeChanged.emit()

    def _maybe_apply_defaults(self) -> None:
        if self._defaults_applied or not self._listed or any(r.state == "checking" for r in self.rows):
            return
        self._defaults_applied = True
        for row in self.rows:
            self._apply_default(row)
        self._rebuild()

    def _holder(self, row: Row) -> str | None:
        for other in self.rows:
            if other is not row and other.ticked and other.account and other.account == row.account:
                return other.profile.name
        return self._roster_held.get(row.account or "")

    def _apply_default(self, row: Row) -> None:
        if row.ticked or row.state != "ok" or not row.account:
            return
        holder = self._holder(row)
        if holder is None:
            row.ticked = True
            row.note = ""
        else:
            row.note = f"○ Same account as {holder}"
        if self.rows and row in self.rows:
            self._render_row(self.rows.index(row))

    # ----- rendering -----
    def _rebuild(self) -> None:
        self._rendering = True
        try:
            self.table.setRowCount(len(self.rows))
        finally:
            self._rendering = False
        for i in range(len(self.rows)):
            self._render_row(i)

    def _status_text(self, row: Row) -> str:
        if row.state == "checking":
            return "Checking…"
        if row.state == "manual":
            return "Click Verify (uses an external credential helper)"
        if row.state == "unchecked":
            return "○ Not checked"
        if row.state == "ok":
            return row.note or f"✔ {row.account}"
        return row.message

    def _render_row(self, i: int) -> None:
        row = self.rows[i]
        p = row.profile
        self._rendering = True
        try:
            use = QTableWidgetItem()
            flags = Qt.ItemFlag.ItemIsEnabled
            if self.can_tick(p.name):
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            use.setFlags(flags)
            use.setCheckState(Qt.CheckState.Checked if row.ticked else Qt.CheckState.Unchecked)
            self.table.setItem(i, COL_USE, use)
            for col, text in ((COL_NAME, p.name), (COL_TYPE, p.kind), (COL_REGION, p.region or "")):
                item = QTableWidgetItem(clean_model_text(text))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(i, col, item)
            text = self._status_text(row)
            button = None
            if row.state in ("manual", "unchecked"):
                button = QPushButton("Verify")
                button.clicked.connect(partial(self._verify_clicked, p.name))
            elif row.signin:
                button = QPushButton("Sign In…")
                button.clicked.connect(partial(self._sign_in_clicked, p.name))
            if button is None:
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(i, COL_STATUS, item)
                self.table.removeCellWidget(i, COL_STATUS)
            else:
                box = QWidget()
                lay = QHBoxLayout(box)
                lay.setContentsMargins(2, 0, 2, 0)
                lay.addWidget(_plain(text), 1)
                lay.addWidget(button)
                self.table.setItem(i, COL_STATUS, QTableWidgetItem())
                self.table.setCellWidget(i, COL_STATUS, box)
        finally:
            self._rendering = False
        self.table.resizeRowToContents(i)

    # ----- ticking -----
    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._rendering or item.column() != COL_USE:
            return
        row = self.rows[item.row()]
        self.set_ticked(row.profile.name, item.checkState() == Qt.CheckState.Checked)

    def can_tick(self, name: str) -> bool:
        row = self._row(name)
        return row.state == "ok" or row.in_roster

    def set_ticked(self, name: str, value: bool) -> None:
        row = self._row(name)
        if value and (not self.can_tick(name) or not row.account):
            self._render_row(self.rows.index(row))
            return
        if value:
            held = self._roster_held.get(row.account or "")
            if held is not None:
                row.note = f"○ Same account as {held}"
                self._render_row(self.rows.index(row))
                return
        row.ticked = value
        if value:
            row.note = ""
            for other in self.rows:
                if other is not row and other.ticked and other.account == row.account:
                    other.ticked = False
                    other.note = f"○ Same account as {name}"
                    self._render_row(self.rows.index(other))
        self._render_row(self.rows.index(row))
        self.completeChanged.emit()

    def validatePage(self) -> bool:  # noqa: N802
        """Next: probes must not outlive the page. Unfinished checks become 'Not checked' with a Verify button."""
        self.cancel_loads()
        self._defaults_applied = True
        for i, row in enumerate(self.rows):
            if row.state == "checking":
                row.state = "unchecked"
                self._render_row(i)
        return True

    def verify_available(self, name: str) -> bool:
        return self._row(name).state in ("manual", "unchecked")

    def _verify_clicked(self, name: str, *_args) -> None:
        self.verify(name)

    def verify(self, name: str) -> None:
        row = self._row(name)
        row.state = "checking"
        row.signin = False
        self._render_row(self.rows.index(row))
        self._start_verify(row)

    # ----- sign in -----
    def _sign_in_clicked(self, name: str, *_args) -> None:
        self.sign_in(name)

    def sign_in_available(self, name: str) -> bool:
        row = self._row(name)
        return row.signin and bool(row.profile.sign_in_profile)

    def sign_in(self, name: str) -> None:
        row = self._row(name)
        profile = row.profile.sign_in_profile or name
        self.hide_sign_in_dialog()
        shell = self.w.shell
        find = getattr(shell, "sign_in_dialog_for", None) if shell is not None else None
        existing = find(profile) if callable(find) else None
        if existing is not None:                    # one sign-in per profile: reuse the one Accounts already opened
            existing.finished_ok.connect(partial(self._signed_in_as, name))
            self._sign_in_dialog, self._sign_in_owned = existing, False
            existing.raise_()
            existing.activateWindow()
            return
        dlg = SignInDialog(self.w.accounts, self.w.runner, profile, self.w, label=name, is_demo=self.w.is_demo)
        dlg.finished_ok.connect(self._signed_in)
        self._sign_in_dialog, self._sign_in_owned = dlg, True
        track = getattr(shell, "track_sign_in", None) if shell is not None else None
        if callable(track):                         # the shell tells Accounts and reuses this dialog for the profile
            track(profile, dlg)
        dlg.show()
        dlg.raise_()

    def _signed_in_as(self, name: str, *_args) -> None:
        self._signed_in(name)

    def _signed_in(self, name: str) -> None:
        if isValid(self) and any(r.profile.name == name for r in self.rows):
            self.verify(name)

    def hide_sign_in_dialog(self) -> None:
        if self._sign_in_owned and self._sign_in_dialog is not None and isValid(self._sign_in_dialog):
            self._sign_in_dialog.close()            # never close a dialog the shell owns
        self._sign_in_dialog, self._sign_in_owned = None, False

    def sign_in_dialog_text(self) -> str:
        dlg = self._sign_in_dialog
        return dlg.text_dump() if dlg is not None and isValid(dlg) else ""

    def unprobeable(self) -> dict[str, str]:
        """Chosen rows the Permissions page must not probe, with the reason (spec §4.1: no MFA, no unverified helper)."""
        out: dict[str, str] = {}
        for r in self.rows:
            if not (r.ticked and r.account):
                continue
            if r.profile.verify == "never":
                out[r.profile.name] = clean_model_text(r.profile.issue or "This profile can't be used yet.")
            elif r.profile.verify == "manual" and r.state == "manual":
                out[r.profile.name] = "Verify this profile first (it uses an external credential helper)."
        return out

    # ----- queries -----
    def all_settled(self) -> bool:
        return self._listed and self._defaults_applied_or_empty() and not any(r.state == "checking" for r in self.rows)

    def _defaults_applied_or_empty(self) -> bool:
        return self._defaults_applied or not self.rows

    def ticked(self) -> tuple[str, ...]:
        return tuple(r.profile.name for r in self.rows if r.ticked)

    def names(self) -> tuple[str, ...]:
        return tuple(r.profile.name for r in self.rows)

    def row_status(self, name: str) -> str:
        return self._status_text(self._row(name))

    def verified_chosen(self) -> tuple[tuple[RosterEntry, datetime], ...]:
        """Chosen rows this session verified OK, with when. A roster row that could not be checked is left out."""
        return tuple((RosterEntry(r.profile.name, r.account), r.verified_at) for r in self.rows
                     if r.ticked and r.account and r.state == "ok" and r.verified_at is not None)

    def failed_chosen(self) -> tuple[str, ...]:
        return tuple(r.profile.name for r in self.rows if r.ticked and r.state == "failed")

    def chosen(self) -> tuple[RosterEntry, ...]:
        return tuple(RosterEntry(r.profile.name, r.account) for r in self.rows if r.ticked and r.account)

    def isComplete(self) -> bool:  # noqa: N802
        return any(r.ticked for r in self.rows)


GLYPHS = {Status.OK: "✔", Status.DENIED: "✖", Status.UNREACHABLE: "⚠", Status.NOT_CONNECTED: "○", Status.ERROR: "✖"}
BILLING_NOT_CHECKED = f"○ Billing: Not checked ({BILLING_CHECK_NOTE})."
GAP_STATUSES = (Status.DENIED, Status.UNREACHABLE, Status.ERROR)


def _check_text(c: CapabilityCheck) -> str:
    if not c.available:
        return f"… {clean_model_text(c.label)}: arrives in a later update"
    text = f"{GLYPHS.get(c.status, '✖')} {clean_model_text(c.label)}: {clean_model_text(c.message)}"
    if c.missing_actions:
        text += "\n   Needs: " + ", ".join(clean_model_text(a) for a in c.missing_actions)
    return text


class PermissionsPanel(QWidget):
    """The permission checks for (profile, account) targets; read-only calls through the accounts provider."""

    def __init__(self, accounts, runner, targets: list[tuple[str, str]], parent=None, *,
                 skipped: list[tuple[str, str, str]] = ()) -> None:
        super().__init__(parent)
        self.accounts = accounts
        self.targets = list(targets)
        self.skipped = list(skipped)        # (profile, account, reason): shown as not checked, never probed
        self.loader = _Loader(runner)
        self.results: dict[str, tuple[CapabilityCheck, ...] | None] = {}
        self._rows: dict[str, list[QLabel]] = {}
        self._billing_rows: dict[str, QLabel] = {}
        self._boxes: dict[str, QVBoxLayout] = {}
        self._pending: set[str] = set()
        self._run_billing = False
        self._failed: set[str] = set()
        self._late: dict[str, tuple[str, CapabilityCheck | None]] = {}
        self.audit_note = _plain("Checks are read-only calls. A denied check is recorded in your account's CloudTrail "
                                 "as AccessDenied, which your security team may see.")
        self.billing_box = QCheckBox(f"Also check Bi&lling access ({BILLING_CHECK_NOTE})")
        self.policy_button = QPushButton("Show Required &Policy…")
        self.again_button = QPushButton("Check &Again")
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        for profile, account in self.targets:
            group = QGroupBox(f"{clean_model_text(profile)} ({clean_model_text(account)})")
            box = QVBoxLayout(group)
            self._boxes[profile] = box
            inner_lay.addWidget(group)
        for profile, account, reason in self.skipped:
            group = QGroupBox(f"{clean_model_text(profile)} ({clean_model_text(account)})")
            box = QVBoxLayout(group)
            self._boxes[profile] = box
            self._rows[profile] = []
            inner_lay.addWidget(group)
            self._add_row(profile, f"○ Not checked: {clean_model_text(reason)}")
        inner_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setMinimumHeight(self.fontMetrics().height() * 11)      # one account's checks fit at any font scale
        buttons = QHBoxLayout()
        buttons.addWidget(self.policy_button)
        buttons.addWidget(self.again_button)
        buttons.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.audit_note)
        lay.addWidget(self.billing_box)
        lay.addWidget(scroll, 1)
        lay.addLayout(buttons)
        self.again_button.clicked.connect(self._again)
        self.billing_box.toggled.connect(self._billing_toggled)

    def cancel_loads(self) -> None:
        self.loader.bump()
        self._pending.clear()
        self._late = {}

    def _again(self, *_args) -> None:
        self.run()

    def _clear_rows(self, profile: str) -> None:
        for label in self._rows.get(profile, []):
            if isValid(label):
                self._boxes[profile].removeWidget(label)
                label.setParent(None)
                label.deleteLater()
        self._rows[profile] = []
        self._billing_rows.pop(profile, None)

    def _add_row(self, profile: str, text: str) -> QLabel:
        label = _plain(text)
        self._rows[profile].append(label)
        self._boxes[profile].addWidget(label)
        return label

    def run(self) -> None:
        self.loader.bump()
        self._pending = {p for p, _ in self.targets}
        include_billing = self._run_billing = self.billing_box.isChecked()
        self._late = {}
        self._failed = set()
        for profile, _account in self.targets:
            self.results[profile] = None
            self._clear_rows(profile)
            self._add_row(profile, "Checking…")
            self.loader.submit(self, self.accounts.check_permissions, Scope(), profile, include_billing,
                               on_done=partial(self._done, profile), limited=True)

    def _done(self, profile: str, result: Result) -> None:
        self._pending.discard(profile)
        self._clear_rows(profile)
        if result.status != Status.OK or result.data is None:
            self.results[profile] = None
            self._failed.add(profile)
            msg = clean_model_text(result.message or "The permission check did not complete.")
            self._add_row(profile, f"{GLYPHS.get(result.status, '✖')} {msg}")
            return
        self.results[profile] = tuple(result.data)
        for c in result.data:
            label = self._add_row(profile, _check_text(c))
            if c.capability == "billing":
                self._billing_rows[profile] = label
        checked = self.billing_box.isChecked()
        if self._run_billing and not checked:          # unticked while this run was in flight
            self._set_billing(profile, BILLING_NOT_CHECKED, None)
        elif checked and not self._run_billing and profile in self._late:
            self._set_billing(profile, *self._late.pop(profile))

    def _billing_toggled(self, checked: bool) -> None:
        for profile, _account in self.targets:
            if checked:
                self.loader.submit(self, self.accounts.check_permissions, Scope(), profile, True, only_billing=True,
                                   on_done=partial(self._billing_done, profile), limited=True)
            else:
                self._set_billing(profile, BILLING_NOT_CHECKED, None)

    def _billing_done(self, profile: str, result: Result) -> None:
        if not self.billing_box.isChecked():           # unticked since this was asked: drop it
            return
        check = next((c for c in (result.data or ()) if c.capability == "billing"), None) \
            if result.status == Status.OK else None
        if check is not None:
            text = _check_text(check)
        else:
            msg = clean_model_text(result.message or "The billing check did not complete.")
            text = f"{GLYPHS.get(result.status, '✖')} Billing: {msg}"
        if profile in self._pending:                   # the full run has not landed yet: apply it after
            self._late[profile] = (text, check)
            return
        self._set_billing(profile, text, check)

    def _set_billing(self, profile: str, text: str, check: CapabilityCheck | None) -> None:
        label = self._billing_rows.get(profile)
        if label is None or not isValid(label):
            if self.results.get(profile) is None:      # failed or cancelled run: no capability list to attach to,
                return                                 # so the billing result is dropped on purpose (Check Again redoes it)
            label = self._add_row(profile, text)
            self._billing_rows[profile] = label
        label.setText(text)
        rows = self.results.get(profile)
        if rows is not None:
            kept = tuple(c for c in rows if c.capability != "billing")
            self.results[profile] = kept + ((check,) if check is not None else ())

    def gaps(self) -> list[tuple[str, int, bool]]:
        """(label, accounts affected, arriving later) for every DENIED/UNREACHABLE/ERROR capability."""
        counts: dict[str, int] = {}
        later: dict[str, bool] = {}
        for profile, rows in self.results.items():
            if rows is None:
                if profile in self._failed:
                    counts["Permission check"] = counts.get("Permission check", 0) + 1
                continue                                # cancelled or unfinished: see unchecked(), not a gap
            for c in rows:
                if c.status in GAP_STATUSES:
                    counts[c.label] = counts.get(c.label, 0) + 1
                    later[c.label] = later.get(c.label, True) and "arriving later" in c.message
        return [(label, n, later.get(label, False)) for label, n in counts.items()]

    def unchecked(self) -> int:
        """Targets whose check never finished (cancelled on leaving the page); failures are gaps instead."""
        return len(self.skipped) + sum(1 for p, rows in self.results.items() if rows is None and p not in self._failed)

    def text_dump(self) -> str:
        return "\n".join(label.text() for rows in self._rows.values() for label in rows if isValid(label))


class PermissionsPage(_LoadingPage):
    def __init__(self, wizard: "SetupWizard") -> None:
        super().__init__(wizard)
        self.setTitle("Check permissions")
        self.setSubTitle("This checks what AWS 95 Tools can read in each account.")
        self.panel: PermissionsPanel | None = None
        self._lay = QVBoxLayout(self)

    def initializePage(self) -> None:  # noqa: N802
        self.w._finish_empty = False
        self.cancel_loads()
        old = self.panel
        if old is not None and isValid(old):
            self._lay.removeWidget(old)
            old.setParent(None)
            old.deleteLater()
        skip = self.w.choose_page.unprobeable()
        chosen = self.w.choose_page.chosen()
        targets = [(e.profile, e.account_id) for e in chosen if e.profile not in skip]
        skipped = [(e.profile, e.account_id, skip[e.profile]) for e in chosen if e.profile in skip]
        self.panel = PermissionsPanel(self.w.accounts, self.w.runner, targets, skipped=skipped)
        self.panel.policy_button.clicked.connect(self._show_policy)
        self._lay.addWidget(self.panel)
        self.panel.run()

    def _show_policy(self, *_args) -> None:
        self.w.show_policy()

    def cancel_loads(self) -> None:
        super().cancel_loads()
        if self.panel is not None and isValid(self.panel):
            self.panel.cancel_loads()

    def validatePage(self) -> bool:  # noqa: N802
        self.cancel_loads()
        return True

    def nextId(self) -> int:  # noqa: N802
        if self.w.skip_names():
            return PAGE_IDS.index("finish")
        return super().nextId()

    def isComplete(self) -> bool:  # noqa: N802
        return True


class PolicyDialog(QDialog):
    """The generated least-privilege policy: copy it or save it. Non-modal; the Save dialog is the one allowed modal."""
    ask_save_path = staticmethod(lambda parent: QFileDialog.getSaveFileName(
        parent, "Save policy", "aws95-policy.json", "JSON (*.json)")[0])

    def __init__(self, accounts, runner, parent=None, *, is_demo: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("Required policy" + (DEMO_SUFFIX if is_demo else ""))
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.accounts = accounts
        self.loader = _Loader(runner)
        self.advice = _plain("Recommended: attach the generated policy below. It allows only what AWS 95 Tools reads. "
                             "If you prefer an AWS-managed policy, ViewOnlyAccess covers most features; "
                             "SecurityAudit covers the security checks.")
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(scaled_fixed_font())
        self.text.setPlainText("Loading…")
        self.copy_button = QPushButton("&Copy")
        self.save_button = QPushButton("&Save As…")
        self.error_label = _plain("")
        _style_error(self.error_label)
        self.copy_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_clicked)
        self.save_button.clicked.connect(self._save_clicked)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.save_button)
        lay = QVBoxLayout(self)
        lay.addWidget(self.advice)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.error_label)
        lay.addLayout(buttons)
        self.resize(560, 420)
        self._policy: str | None = None
        if is_demo:
            self._watermark = Watermark(self)
        self.loader.submit(self, accounts.required_policy, on_done=self._loaded)

    def _loaded(self, result: Result) -> None:
        if result.status == Status.OK and isinstance(result.data, str):
            self._policy = result.data
            self.text.setPlainText(result.data)
            self.copy_button.setEnabled(True)
            self.save_button.setEnabled(True)
        else:
            self.text.setPlainText("")
            self.error_label.setText(clean_model_text(result.message or "The policy could not be generated."))

    def _copy_clicked(self, *_args) -> None:
        if self._policy is not None:
            _copy(self._policy)

    def _save_clicked(self, *_args) -> None:
        if self._policy is None:
            return
        path = self.ask_save_path(self)
        if not path:
            return
        try:
            Path(path).write_text(self._policy, encoding="utf-8")
        except (OSError, ValueError) as e:
            self.error_label.setText(f"Couldn't save: {clean_model_text(getattr(e, 'strerror', None) or str(e))}")
            return
        self.error_label.setText("")

    def done(self, r: int) -> None:
        self.loader.bump()
        super().done(r)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.loader.bump()
        super().closeEvent(event)


class PermissionsDialog(QDialog):
    """Re-run the checks for one account from the Accounts window. Never writes settings."""

    def __init__(self, accounts, runner, profile: str, account_id: str, parent=None, *, is_demo: bool = False) -> None:
        super().__init__(parent)
        self.accounts, self.runner, self.is_demo = accounts, runner, is_demo
        self.setWindowTitle(f"Permissions — {clean_model_text(profile)}" + (DEMO_SUFFIX if is_demo else ""))
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.panel = PermissionsPanel(accounts, runner, [(profile, account_id)])
        self.panel.policy_button.clicked.connect(self._show_policy)
        self._policy: PolicyDialog | None = None
        self._started = False
        lay = QVBoxLayout(self)
        lay.addWidget(self.panel)
        self.resize(520, 380)
        if is_demo:
            self._watermark = Watermark(self)

    def _show_policy(self, *_args) -> None:
        if self._policy is not None and isValid(self._policy):
            self._policy.show()
            self._policy.raise_()
            self._policy.activateWindow()
            return
        self._policy = PolicyDialog(self.accounts, self.runner, self, is_demo=self.is_demo)
        self._policy.show()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._started:
            self._started = True
            self.panel.run()

    def done(self, r: int) -> None:
        self.panel.cancel_loads()
        super().done(r)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.panel.cancel_loads()
        super().closeEvent(event)


def _style_error(label: QLabel) -> None:
    pal = label.palette()
    pal.setColor(QPalette.ColorRole.WindowText, QColor(current_theme().bad))
    label.setPalette(pal)


class NamesPage(QWizardPage):
    # Qt's own dialog, parented to the wizard window: the native macOS panel drops the wizard behind the main window.
    ask_color = staticmethod(lambda initial, parent: QColorDialog.getColor(
        QColor(initial), parent.window(), "Account colour", QColorDialog.ColorDialogOption.DontUseNativeDialog))

    def __init__(self, wizard: "SetupWizard") -> None:
        super().__init__()
        self.w = wizard
        self.setTitle("Name your accounts")
        self.setSubTitle("Give each account a short nickname and a colour.")
        self._rows: list[dict] = []
        self._memory: dict[str, tuple[str, str]] = {}
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Profile", "Account", "Nickname", "Colour"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for col in (0, 1, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        lay = QVBoxLayout(self)
        lay.addWidget(self.table, 1)

    def initializePage(self) -> None:  # noqa: N802
        for row in self._rows:                                  # remember edits across Back/Next
            self._memory[row["account"]] = (row["edit"].text(), row["colour"])
        self._rows = []
        chosen = self.w.choose_page.chosen()
        self.table.setRowCount(len(chosen))
        for i, entry in enumerate(chosen):
            nick, colour = self._memory.get(entry.account_id) or self.w.settings.account_label(entry.account_id) \
                or (entry.profile, NEUTRAL)
            for col, text in ((0, entry.profile), (1, entry.account_id)):
                item = QTableWidgetItem(clean_model_text(text))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(i, col, item)
            edit = QLineEdit(nick)
            edit.setMaxLength(MAX_NICKNAME)
            edit.textChanged.connect(self._text_changed)
            button = QPushButton()
            button.clicked.connect(partial(self._pick, entry.account_id))
            self.table.setCellWidget(i, 2, edit)
            self.table.setCellWidget(i, 3, button)
            row = {"profile": entry.profile, "account": entry.account_id, "edit": edit, "button": button,
                   "colour": colour}
            self._rows.append(row)
            self._paint(row)
        self.completeChanged.emit()

    def _paint(self, row: dict) -> None:
        pix = QPixmap(24, 12)
        pix.fill(QColor(row["colour"]))
        row["button"].setIcon(QIcon(pix))
        row["button"].setText(row["colour"])

    def _text_changed(self, *_args) -> None:
        self.completeChanged.emit()

    def _pick(self, account: str, *_args) -> None:
        row = next(r for r in self._rows if r["account"] == account)
        picked = self.ask_color(row["colour"], self)
        top = self.window()
        if isValid(top):                    # the dialog's close activates the main window: bring the wizard back
            top.raise_()
            top.activateWindow()
        if picked is not None and picked.isValid():
            row["colour"] = picked.name()
            self._paint(row)

    def set_nickname(self, profile: str, text: str) -> None:
        next(r for r in self._rows if r["profile"] == profile)["edit"].setText(text)

    def labels(self) -> dict[str, tuple[str, str]]:
        return {r["account"]: (r["edit"].text().strip(), r["colour"]) for r in self._rows}

    def isComplete(self) -> bool:  # noqa: N802
        return all(1 <= len(r["edit"].text().strip()) <= MAX_NICKNAME for r in self._rows)


class FinishPage(QWizardPage):
    def __init__(self, wizard: "SetupWizard") -> None:
        super().__init__()
        self.w = wizard
        self.setTitle("Finish")
        self.setFinalPage(True)
        self.summary = _plain("")
        self.error_label = _plain("")
        _style_error(self.error_label)
        self.open_accounts_box = QCheckBox("&Open the Accounts window")
        self.open_accounts_box.setChecked(True)
        lay = QVBoxLayout(self)
        lay.addWidget(self.summary)
        lay.addWidget(self.error_label)
        lay.addStretch(1)
        lay.addWidget(self.open_accounts_box)
        theme_bus().changed.connect(self._on_theme)

    def _on_theme(self, _theme) -> None:
        _style_error(self.error_label)

    def initializePage(self) -> None:  # noqa: N802
        self.error_label.setText("")
        w = self.w
        self.open_accounts_box.setVisible(not (w.is_demo or w._finish_empty))
        if w.is_demo:
            text = "Demo mode: nothing was saved."
        elif w._finish_empty:
            text = "No accounts added. You can run this wizard again from Start ▸ Settings."
        else:
            text = f"{len(w.choose_page.chosen())} account(s) ready."
            panel = w.permissions_page.panel
            gaps = panel.gaps() if panel is not None else []
            if panel is not None and panel.unchecked():
                text += f" Not checked: {panel.unchecked()} account(s)."
            if gaps:
                parts = [f"{clean_model_text(label)} on {n} account(s)" + (" (feature arrives later)" if later else "")
                         for label, n, later in gaps]
                text += " Permission gaps: " + "; ".join(parts)
            for old, new, account in w.replaced():
                text += f" {clean_model_text(old)} replaced by {clean_model_text(new)} for account {account}."
        self.summary.setText(text)


class SetupWizard(QWizard):
    clock = staticmethod(lambda: datetime.now(timezone.utc))

    def __init__(self, settings, runner, accounts, sound, parent=None, *, shell=None, is_demo=False,
                 start_page=None, only_new=False) -> None:
        super().__init__(parent)
        self.settings = settings
        self.runner = runner
        self.accounts = accounts
        self.sound = sound
        self.shell = shell
        self.is_demo = is_demo
        self.only_new = only_new
        self._finish_empty = False
        self.setWizardStyle(QWizard.WizardStyle.ClassicStyle)
        self.setButtonText(QWizard.WizardButton.CancelButton, "&Cancel")
        self.setPixmap(QWizard.WizardPixmap.WatermarkPixmap, side_pixmap(settings.font_scale / 100))
        self.setWindowTitle("AWS Setup Wizard" + (DEMO_SUFFIX if is_demo else ""))
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.welcome_page = WelcomePage(self)
        self.sources_page = SourcesPage(self)
        self.choose_page = ChoosePage(self)
        self._pages = [self.welcome_page, self.sources_page, self.choose_page]
        self.permissions_page = PermissionsPage(self)
        self.names_page = NamesPage(self)
        self.finish_page = FinishPage(self)
        self._pages.append(self.permissions_page)
        pages = (self.welcome_page, self.sources_page, self.choose_page,
                 self.permissions_page, self.names_page, self.finish_page)
        for i, page in enumerate(pages):
            self.setPage(i, page)
        if isinstance(start_page, str) and start_page in PAGE_IDS:
            self.setStartId(PAGE_IDS.index(start_page))
        self.welcome_page.suppress_box.toggled.connect(partial(self._sync_box, self.choose_page.suppress_box))
        self.choose_page.suppress_box.toggled.connect(partial(self._sync_box, self.welcome_page.suppress_box))
        if is_demo:
            self._watermark = Watermark(self)

    @staticmethod
    def _sync_box(other: QCheckBox, checked: bool) -> None:
        other.blockSignals(True)
        other.setChecked(checked)
        other.blockSignals(False)

    def finish_without_accounts(self) -> None:
        self._finish_empty = True
        self.setCurrentId(PAGE_IDS.index("finish"))      # the Finish page shows the summary; its button accepts

    def skip_names(self) -> bool:
        return self._finish_empty or not self.choose_page.chosen()

    def show_policy(self) -> PolicyDialog:
        dlg = PolicyDialog(self.accounts, self.runner, self, is_demo=self.is_demo)
        dlg.show()
        return dlg

    def accept(self) -> None:
        if self.is_demo:
            return super().accept()                                   # demo never writes settings (spec §6.1)
        entries, labels = self._payload()
        try:
            self.settings.validate_roster(entries)
            for account, (nick, colour) in labels.items():
                if not nick.strip() or len(nick.strip()) > MAX_NICKNAME:
                    raise ValueError(f"The nickname for {account} must be 1 to {MAX_NICKNAME} characters.")
                if not QColor(colour).isValid():
                    raise ValueError(f"The colour for {account} is not valid.")
        except ValueError as exc:
            self.finish_page.error_label.setText(clean_model_text(str(exc)))
            return                                                    # wizard stays open; nothing written
        for account, (nick, colour) in labels.items():
            self.settings.set_account_label(account, nick.strip(), colour)
        self.settings.set_roster(entries)
        if not self._finish_empty:
            held = {e.profile: e.account_id for e in entries}
            for entry, at in self.choose_page.verified_chosen():     # remembered so My Accounts opens already checked
                if held.get(entry.profile) == entry.account_id:
                    self.settings.record_verified(entry.profile, entry.account_id, at)
            for name in self.choose_page.failed_chosen():           # a failed check drops its old entry, as in Accounts
                self.settings.forget_verified(name)
        # Spec §6.2: "known" = seen by the last wizard run or rescan, so a rescan only offers genuinely new profiles.
        self.settings.set_known_profiles([*self.settings.known_profiles(), *self.choose_page.names()])
        self.settings.setup_completed = True
        self.settings.setup_suppress_autostart = self.welcome_page.suppress_box.isChecked()
        self.sound.play("refresh_done")
        if self.finish_page.open_accounts_box.isChecked() and self.shell is not None and entries:
            self.shell.open_program("accounts")
        super().accept()

    def _payload(self) -> tuple[tuple[RosterEntry, ...], dict[str, tuple[str, str]]]:
        if self._finish_empty:
            return tuple(self.settings.roster()), {}
        chosen = self.choose_page.chosen()
        kept = tuple(e for e in self.settings.roster() if e.profile not in set(self.choose_page.names())
                     and e.account_id not in {c.account_id for c in chosen})
        accounts = {c.account_id for c in chosen}
        labels = {a: v for a, v in self.names_page.labels().items() if a in accounts}
        return kept + chosen, labels

    def replaced(self) -> list[tuple[str, str, str]]:
        """Off-page roster entries dropped because a newly chosen profile holds the same account."""
        if self._finish_empty:
            return []
        on_page = set(self.choose_page.names())
        by_account = {c.account_id: c.profile for c in self.choose_page.chosen()}
        return [(e.profile, by_account[e.account_id], e.account_id) for e in self.settings.roster()
                if e.profile not in on_page and e.account_id in by_account]

    def _cancel_loads(self) -> None:
        for page in self._pages:
            page.cancel_loads()
        self.choose_page.hide_sign_in_dialog()

    def reject(self) -> None:
        if not self.is_demo:
            self.settings.setup_suppress_autostart = self.welcome_page.suppress_box.isChecked()
        super().reject()

    def done(self, r: int) -> None:
        self._cancel_loads()
        super().done(r)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._cancel_loads()
        super().closeEvent(event)
