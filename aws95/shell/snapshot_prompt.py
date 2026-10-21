"""The refresh prompt (first-drop spec §3.5, F1). Snapshots are manual: when the newest snapshot of any roster
account is more than a day old (or missing), the app ASKS - at boot after the desktop (and after the setup wizard)
and when a data window opens - at most once per session. The same dialog backs every Take Snapshot command.

2c extension point: SnapshotController.register_option(PromptOption(...)) adds a checkbox such as "Also update
Activity"; its on_accept(request) runs when the user presses Take Snapshot with the box ticked."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import partial

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget)
from shiboken6 import isValid

from aws95.contracts.accounts import RosterEntry
from aws95.contracts.billing import BILLING_COST_NOTE
from aws95.contracts.common import Result, Scope, Status
from aws95.contracts.snapshots import STALE_AFTER, AccountAge, ResumeRequest, SnapshotProgress, SnapshotRequest
from aws95.core.cancel import CancelToken
from aws95.kit.widgets.dialogs import Icon, MessageBox95
from aws95.store import Store

DATA_PROGRAMS = frozenset({"resources", "findings", "fleet", "snapshots", "billing", "activity"})
GLOBAL_NOTE = "+ global services"
CLEAR_TEXT = ("Delete aws95.db? It holds your snapshots, findings, accepted findings and activity history. "
              "Your AWS accounts and your settings are not touched. This can't be undone.")


@dataclass(frozen=True)
class PromptOption:
    key: str
    text: str
    visible: Callable[[], bool]
    on_accept: Callable[[SnapshotRequest], None]
    default: bool = True


@dataclass(frozen=True)
class PromptAccount:
    entry: RosterEntry
    label: str
    regions: tuple[str, ...]
    billing: bool


def snapshot_regions(saved: Sequence[str], profile_region: str | None) -> tuple[str, ...]:
    """Spec F5: the account's default regions; with none set, the profile's region plus us-east-1."""
    if saved:
        return tuple(dict.fromkeys(saved))
    return tuple(dict.fromkeys([profile_region or "us-east-1", "us-east-1"]))


def stale_ages(ages: Sequence[AccountAge], now: datetime) -> list[AccountAge]:
    return [a for a in ages if a.taken_at is None or now - a.taken_at > STALE_AFTER]


def headline(stale: Sequence[AccountAge], labels: Mapping[str, str], now: datetime) -> str:
    if not stale:
        return "Take a new snapshot of your accounts?"
    names = ", ".join(labels.get(a.account_id, a.account_id) for a in stale)
    dated = [a.taken_at for a in stale if a.taken_at is not None]
    if not dated:
        return f"You haven't taken a snapshot yet ({names}). Take one now?"
    days = max((now - t).days for t in dated)
    return f"It's been {days} day{'s' if days != 1 else ''} since your last snapshot ({names}). Take one now?"


def gather(providers, roster: Sequence[RosterEntry], *, cancel=None) -> tuple[Result, Result, Result]:
    """Runs on the TaskRunner: snapshot ages and stages from the store, and profile regions from the AWS config
    files. None of it reaches AWS."""
    scope = Scope(accounts=tuple(e.account_id for e in roster))
    return (providers.snapshots.ages(scope, cancel=cancel), providers.accounts.list_profiles(cancel=cancel),
            providers.snapshots.stages(scope, cancel=cancel))


def _plain(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _notice(parent: QWidget, title: str, text: str, icon: Icon = Icon.INFO) -> MessageBox95:
    box = MessageBox95(parent, title, text, icon)
    box.setModal(False)
    box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    box.show()
    return box


class SnapshotPrompt(QDialog):
    take_requested = Signal(object, object)       # ({account: include billing}, {option key: ticked})
    change_regions_requested = Signal()
    resume_requested = Signal()

    def __init__(self, headline_text: str, accounts: Sequence[PromptAccount], options: Sequence[PromptOption],
                 parent: QWidget | None = None, *, busy_text: str | None = None,
                 resumable: Sequence[str] = ()) -> None:
        super().__init__(parent)
        self.setWindowTitle("Take Snapshot")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.headline_label = _plain(headline_text, self)
        self.covers_box = QGroupBox("Covers", self)
        self.covers_layout = QVBoxLayout(self.covers_box)
        self.billing_box = QGroupBox("Include Billing for", self)
        billing_layout = QVBoxLayout(self.billing_box)
        self.billing_checks: dict[str, QCheckBox] = {}
        for acct in accounts:
            box = QCheckBox(acct.label.replace("&", "&&"), self.billing_box)
            box.setChecked(acct.billing)
            billing_layout.addWidget(box)
            self.billing_checks[acct.entry.account_id] = box
        self.cost_note = _plain(BILLING_COST_NOTE, self.billing_box)
        billing_layout.addWidget(self.cost_note)
        self.option_checks: dict[str, QCheckBox] = {}
        for opt in options:
            if opt.visible():
                box = QCheckBox(opt.text, self)
                box.setChecked(opt.default)
                self.option_checks[opt.key] = box
        self.busy_label = _plain("", self)
        self.take_button = QPushButton("&Take Snapshot", self)
        self.take_button.setDefault(True)
        self.regions_button = QPushButton("Change &regions…", self)
        self.later_button = QPushButton("&Not now", self)
        # Resumable-snapshots spec §6: finish the unfinished sections of the same snapshot instead of starting over.
        self.resume_button = QPushButton(
            f"R&esume unfinished ({', '.join(resumable)})" if resumable else "R&esume unfinished", self)
        self.resume_button.setVisible(bool(resumable))
        self.resume_button.clicked.connect(self._resume)
        self.take_button.clicked.connect(self._take)
        self.regions_button.clicked.connect(self.change_regions_requested)
        self.later_button.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        for b in (self.resume_button, self.take_button, self.regions_button, self.later_button):
            buttons.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addWidget(self.headline_label)
        lay.addWidget(self.covers_box)
        lay.addWidget(self.billing_box)
        for box in self.option_checks.values():
            lay.addWidget(box)
        lay.addWidget(self.busy_label)
        lay.addLayout(buttons)
        self._accounts: list[PromptAccount] = []
        self.set_accounts(accounts)
        self.set_busy(busy_text)

    def set_accounts(self, accounts: Sequence[PromptAccount]) -> None:
        while self.covers_layout.count():
            item = self.covers_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._accounts = list(accounts)
        for acct in self._accounts:
            self.covers_layout.addWidget(_plain(f"{acct.label}: {', '.join(acct.regions)} {GLOBAL_NOTE}",
                                                self.covers_box))
        if self.isVisible():
            self._fit()

    def set_busy(self, text: str | None) -> None:
        self.busy_label.setText(f"A snapshot is already running: {text}" if text else "")
        self.busy_label.setVisible(bool(text))
        self.take_button.setEnabled(not text)
        self.resume_button.setEnabled(not text)
        if self.isVisible():
            self._fit()

    def _fit(self) -> None:
        """Grow to the wrapped text (large fonts); never shrink below what the user dragged to."""
        layout = self.layout()
        layout.activate()
        width = max(self.width(), self.sizeHint().width())
        self.resize(width, max(self.height(), layout.heightForWidth(width)))

    def billing_choices(self) -> dict[str, bool]:
        return {acct: box.isChecked() for acct, box in self.billing_checks.items()}

    def option_states(self) -> dict[str, bool]:
        return {key: box.isChecked() for key, box in self.option_checks.items()}

    def _take(self, *_args) -> None:
        self.take_requested.emit(self.billing_choices(), self.option_states())
        self.accept()

    def _resume(self, *_args) -> None:
        self.resume_requested.emit()
        self.accept()

    def text_dump(self) -> str:
        covers = [self.covers_layout.itemAt(i).widget().text() for i in range(self.covers_layout.count())
                  if self.covers_layout.itemAt(i).widget() is not None]
        return "\n".join([self.headline_label.text(), *covers,
                          *(b.text().replace("&&", "&") for b in self.billing_checks.values()),
                          self.cost_note.text(), *(b.text() for b in self.option_checks.values()),
                          self.busy_label.text()])

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._fit()
        self.activateWindow()
        (self.take_button if self.take_button.isEnabled() else self.later_button).setFocus()


class RegionsDialog(QDialog):
    saved = Signal(str)

    def __init__(self, settings, accounts: Sequence[PromptAccount], parent: QWidget | None = None) -> None:
        from aws95.programs.common.scope import REGIONS
        super().__init__(parent)
        self.setWindowTitle("Change regions")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._settings = settings
        self._regions = {a.entry.account_id: a.regions for a in accounts}
        self.account_combo = QComboBox(self)
        for acct in accounts:
            self.account_combo.addItem(f"{acct.label} ({acct.entry.account_id})", acct.entry.account_id)
        self.region_list = QListWidget(self)
        for code in REGIONS:
            item = QListWidgetItem(code)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.region_list.addItem(item)
        self.note = _plain("Snapshots cover these regions plus global services. Saved for this account.", self)
        self.error_label = _plain("", self)
        self.save_button = QPushButton("&Save", self)
        self.save_button.setDefault(True)
        cancel = QPushButton("Cancel", self)
        self.save_button.clicked.connect(self._save)
        cancel.clicked.connect(self.reject)
        form = QFormLayout()
        form.addRow("&Account:", self.account_combo)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.save_button)
        buttons.addWidget(cancel)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.region_list, 1)
        lay.addWidget(self.note)
        lay.addWidget(self.error_label)
        lay.addLayout(buttons)
        self.account_combo.currentIndexChanged.connect(self._load)
        self._load()

    def _load(self, *_args) -> None:
        chosen = set(self._regions.get(self.account_combo.currentData(), ()))
        for i in range(self.region_list.count()):
            item = self.region_list.item(i)
            item.setCheckState(Qt.CheckState.Checked if item.text() in chosen else Qt.CheckState.Unchecked)
        self.error_label.setText("")

    def _save(self, *_args) -> None:
        account = self.account_combo.currentData()
        regions = [self.region_list.item(i).text() for i in range(self.region_list.count())
                   if self.region_list.item(i).checkState() == Qt.CheckState.Checked]
        if not regions:
            self.error_label.setText("Pick at least one region.")
            return
        self._settings.set_account_regions(account, regions)
        self._regions[account] = tuple(regions)
        self.saved.emit(account)
        self.accept()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.activateWindow()
        self.region_list.setFocus()


class SnapshotController(QObject):
    progress = Signal(str)
    finished = Signal(object)
    running_changed = Signal(bool)
    _relay = Signal(object)          # SnapshotProgress from a worker thread, queued to the GUI thread

    def __init__(self, settings, runner, providers, parent: QWidget, *,
                 clock: Callable[[], datetime] | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._runner = runner
        self._providers = providers
        self._parent = parent
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._options: list[PromptOption] = []
        self._asked = False
        self._checking = False
        self._prompt: SnapshotPrompt | None = None
        self._regions_dialog: RegionsDialog | None = None
        self._handle = None
        self._stop: CancelToken | None = None
        self._busy_text: str | None = None
        self._accounts: list[PromptAccount] = []
        self._refusals: list[Callable[[], str | None]] = []
        self._relay.connect(self._on_progress)

    # ----- state -----
    @property
    def running(self) -> bool:
        return self._handle is not None and not self._handle.done

    @property
    def checking(self) -> bool:
        return self._checking

    @property
    def prompt(self) -> SnapshotPrompt | None:
        return self._prompt if self._prompt is not None and isValid(self._prompt) else None

    @property
    def regions_dialog(self) -> RegionsDialog | None:
        d = self._regions_dialog
        return d if d is not None and isValid(d) else None

    def register_option(self, option: PromptOption) -> None:
        self._options.append(option)

    def add_refusal(self, check: Callable[[], str | None]) -> None:
        """Something that must not overlap a snapshot (Clear Local Data while it deletes the store) refuses it with
        its own words: `check()` returns the reason, or None when a snapshot may start."""
        self._refusals.append(check)

    # ----- asking -----
    def maybe_prompt(self, *_args) -> None:
        """Boot and data-window trigger: ask only when an account is stale or has none, once per session."""
        self._check(force=False)

    def open_prompt(self, *_args) -> None:
        """Take Snapshot from the toolbar, Start menu or Ctrl+K: always opens the dialog (live mode only)."""
        if not self._providers.is_live:
            _notice(self._parent, "Take Snapshot",
                    "Snapshots need live mode. Demo and offline mode never contact AWS.")
            return
        self._check(force=True)

    def _check(self, force: bool) -> None:
        if not self._providers.is_live or self._checking:
            return
        if not force and (self._asked or self.running):
            return
        if self.prompt is not None and self.prompt.isVisible():
            self.prompt.raise_()
            self.prompt.activateWindow()
            return
        roster = self._settings.roster()
        if not roster:
            if force:
                _notice(self._parent, "Take Snapshot",
                        "Add an account first: Start ▸ Settings ▸ AWS Setup Wizard…")
            return
        self._checking = True
        handle = self._runner.submit(gather, self._providers, roster)
        handle.finished.connect(partial(self._on_gathered, roster, force))
        handle.failed.connect(self._on_gather_failed)

    def _on_gather_failed(self, message: str, _details: str = "") -> None:
        self._checking = False

    def _account(self, entry: RosterEntry, profile) -> PromptAccount:
        named = self._settings.account_label(entry.account_id)
        label = named[0] if named else entry.profile
        regions = snapshot_regions(self._settings.account_regions(entry.account_id),
                                   profile.region if profile is not None else None)
        return PromptAccount(entry, label, regions, self._settings.account_billing(entry.account_id))

    def _on_gathered(self, roster, force: bool, payload) -> None:
        self._checking = False
        ages, profiles, stages = payload
        if ages.status is not Status.OK or ages.data is None:
            if force:
                _notice(self._parent, "Take Snapshot", ages.message or "The local data could not be read.",
                        Icon.ERROR)
            return
        now = self._clock()
        stale = stale_ages(ages.data, now)
        if not force and not stale:
            return
        by_name = {p.name: p for p in (profiles.data or ())} if profiles.status is Status.OK else {}
        self._accounts = [self._account(e, by_name.get(e.profile)) for e in roster]
        labels = {a.entry.account_id: a.label for a in self._accounts}
        if not force:
            self._asked = True
        by_stage = {st.account: st for st in (stages.data or ())} if stages.status is Status.OK else {}
        resumable = tuple(a.label for a in self._accounts
                          if a.entry.account_id in by_stage and by_stage[a.entry.account_id].can_resume(now))
        dlg = SnapshotPrompt(headline(stale, labels, now), self._accounts, self._options, self._parent,
                             busy_text=self._busy_text if self.running else None, resumable=resumable)
        dlg.take_requested.connect(self._on_take)
        dlg.resume_requested.connect(self._on_resume_from_prompt)
        dlg.change_regions_requested.connect(self._open_regions)
        self._prompt = dlg
        dlg.show()

    def _open_regions(self, *_args) -> None:
        if self.regions_dialog is not None:
            self.regions_dialog.raise_()
            return
        dlg = RegionsDialog(self._settings, self._accounts, self.prompt or self._parent)
        dlg.saved.connect(self._on_regions_saved)
        self._regions_dialog = dlg
        dlg.show()

    def _on_regions_saved(self, account_id: str) -> None:
        saved = self._settings.account_regions(account_id)
        self._accounts = [replace(a, regions=saved) if a.entry.account_id == account_id and saved else a
                          for a in self._accounts]
        if self.prompt is not None:
            self.prompt.set_accounts(self._accounts)

    # ----- taking -----
    def _on_take(self, billing: dict, options: dict) -> None:
        for account_id, on in billing.items():
            self._settings.set_account_billing(account_id, on)
        request = SnapshotRequest(tuple(a.entry for a in self._accounts),
                                  frozenset(a for a, on in billing.items() if on),
                                  {a.entry.account_id: a.regions for a in self._accounts},
                                  {a.entry.account_id: a.label for a in self._accounts})
        self.take(request)
        for opt in self._options:
            if options.get(opt.key):
                opt.on_accept(request)

    def take(self, request) -> None:
        self._start(self._providers.snapshots.take_snapshot, request, "Take Snapshot", "Starting the snapshot…")

    def resume(self, request) -> None:
        self._start(self._providers.snapshots.resume_snapshot, request, "Resume Snapshot", "Resuming the snapshot…")

    def resume_accounts(self, account_ids: Sequence[str] = (), retry_denied: bool = False) -> None:
        """Resume Snapshot from the Run tab, the prompt, the Start menu or Ctrl+K (resumable-snapshots spec §6).
        Billing sections run only for accounts that opted in."""
        if not self._providers.is_live:
            _notice(self._parent, "Resume Snapshot",
                    "Snapshots need live mode. Demo and offline mode never contact AWS.")
            return
        roster = [e for e in self._settings.roster() if not account_ids or e.account_id in account_ids]
        if not roster:
            _notice(self._parent, "Resume Snapshot", "Add an account first: Start ▸ Settings ▸ AWS Setup Wizard…")
            return
        labels = {}
        for e in roster:
            named = self._settings.account_label(e.account_id)
            labels[e.account_id] = named[0] if named else e.profile
        billing = frozenset(e.account_id for e in roster if self._settings.account_billing(e.account_id))
        self.resume(ResumeRequest(tuple(roster), bool(retry_denied), billing, labels))

    def _on_resume_from_prompt(self, *_args) -> None:
        self.resume_accounts()

    def _start(self, fn, request, title: str, starting: str) -> None:
        if self.running:
            _notice(self._parent, title, "A snapshot is already running.")
            return
        for check in self._refusals:
            reason = check()
            if reason:
                _notice(self._parent, title, reason)
                return
        self._stop = CancelToken()
        self._busy_text = starting
        self._handle = self._runner.submit(fn, Scope(), request, progress=self._relay.emit, stop=self._stop)
        self._handle.finished.connect(self._on_finished)
        self._handle.failed.connect(self._on_failed)
        self.running_changed.emit(True)
        self.progress.emit(self._busy_text)

    def cancel(self, *_args) -> None:
        if self._stop is not None and self.running:
            self._stop.cancel()
            self.progress.emit("Stopping the snapshot…")

    def _on_progress(self, p: SnapshotProgress) -> None:
        if not self.running:
            return
        self._busy_text = p.text
        self.progress.emit(p.text)
        if self.prompt is not None:
            self.prompt.set_busy(p.text)

    def _settle(self) -> None:
        self._handle = None
        self._stop = None
        self._busy_text = None
        self.running_changed.emit(False)
        self.progress.emit("")
        if self.prompt is not None:
            self.prompt.set_busy(None)

    def _on_finished(self, result) -> None:
        self._settle()
        self.finished.emit(result)
        if getattr(result, "status", None) is Status.ERROR:
            _notice(self._parent, "Snapshot", result.message or "The snapshot failed.", Icon.ERROR)

    def _on_failed(self, message: str, _details: str = "") -> None:
        self._settle()
        self.finished.emit(Result.error(message))
        _notice(self._parent, "Snapshot", f"The snapshot failed: {message}", Icon.ERROR)


class LocalDataCleaner(QObject):
    """Settings ▸ Clear Local Data… (spec §3.3): deletes aws95.db after a confirmation. Never in demo mode, never while
    a snapshot (or a job registered with add_busy_check) runs - checked again when the user confirms, because the
    confirmation is non-modal - and while it deletes, `clearing` is True and the snapshot controller refuses to start
    (review I6). 2c registers its Activity jobs with add_busy_check and checks `clearing` before starting one."""
    cleared = Signal()

    def __init__(self, parent: QWidget, runner, providers, controller: SnapshotController,
                 store_factory: Callable[[], Store] = Store) -> None:
        super().__init__(parent)
        self._parent = parent
        self._runner = runner
        self._providers = providers
        self._controller = controller
        self._store_factory = store_factory
        self._box: MessageBox95 | None = None
        self._busy_checks: list[Callable[[], str | None]] = []
        self._clearing = False
        self.last_notice = ""
        controller.add_refusal(self._refusal)

    @property
    def clearing(self) -> bool:
        """True from the confirmed Delete until the store is gone (or the delete failed)."""
        return self._clearing

    def add_busy_check(self, check: Callable[[], str | None]) -> None:
        """Another long job (2c: an Activity load or import) refuses the clear with its own words while it runs:
        `check()` returns the reason, or None when clearing is fine."""
        self._busy_checks.append(check)

    def _refusal(self) -> str | None:
        return "Local data is being cleared. Try again when it is done." if self._clearing else None

    def _busy(self) -> str | None:
        if self._controller.running:
            return "A snapshot is running. Wait for it to finish, then try again."
        for check in self._busy_checks:
            reason = check()
            if reason:
                return reason
        return None

    @property
    def confirm_box(self) -> MessageBox95 | None:
        return self._box if self._box is not None and isValid(self._box) else None

    def _say(self, text: str, icon: Icon = Icon.INFO) -> None:
        self.last_notice = text
        _notice(self._parent, "Clear Local Data", text, icon)

    def request(self, *_args) -> None:
        if self._providers.is_demo:
            self._say("Demo mode never writes local data, so there is nothing to clear.")
            return
        busy = self._busy()
        if busy:
            self._say(busy)
            return
        if self._clearing:
            self._say("Local data is already being cleared.")
            return
        if self.confirm_box is not None:
            self.confirm_box.raise_()
            return
        box = MessageBox95(self._parent, "Clear Local Data", CLEAR_TEXT, Icon.QUESTION, ("&Delete", "&Cancel"),
                           default=1)
        box.setModal(False)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.accepted.connect(partial(self._confirmed, box))
        self._box = box
        box.show()

    def _confirmed(self, box: MessageBox95, *_args) -> None:
        if not isValid(box) or box.choice != 0:
            return
        busy = self._busy()             # again: the box is non-modal, so a job may have started since it opened
        if busy:
            self._say(busy)
            return
        self._clearing = True
        store = self._providers.store if self._providers.store is not None else self._store_factory()
        handle = self._runner.submit(store.clear)
        handle.finished.connect(self._done)
        handle.failed.connect(self._failed)

    def _done(self, *_args) -> None:
        self._clearing = False
        self.cleared.emit()
        self._say("Local data deleted. Your AWS accounts were not touched.")

    def _failed(self, message: str, _details: str = "") -> None:
        self._clearing = False
        self._say(f"The local data could not be deleted: {message}", Icon.ERROR)
