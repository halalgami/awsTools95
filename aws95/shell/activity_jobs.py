"""ActivityController (first-drop spec §5): the shell's owner of Activity jobs — Load Activity History (first use,
with estimates), Resume Loading, Update Activity (F5 and the snapshot prompt's "Also update Activity"), Import
History… and Cancel. One job at a time; progress goes to the tray and every Activity window's status bar. Nothing
here runs in demo or offline mode, and nothing reaches AWS unless the user asked."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from functools import partial

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QWidget
from shiboken6 import isValid

from aws95.contracts.accounts import RosterEntry
from aws95.contracts.activity import HistoryProgress, HistoryRequest, LoadMode, activity_regions
from aws95.contracts.common import Result, Scope, Status
from aws95.core.cancel import CancelToken
from aws95.kit.widgets.dialogs import Icon, MessageBox95
from aws95.shell.activity_dialogs import ImportHistoryDialog, JobAccount, LoadHistoryDialog
from aws95.shell.snapshot_prompt import PromptOption

LIVE_ONLY = "Activity history needs live mode. Demo and offline mode never contact AWS."
NO_ACCOUNTS_TEXT = "Add an account first: Start ▸ Settings ▸ AWS Setup Wizard…"
CLEAR_BUSY = "Activity is loading. Wait for it to finish (or cancel it), then try again."


def gather_profiles(providers, *, cancel=None) -> Result:
    """Runs on the TaskRunner: profile regions from the AWS config files. No AWS call."""
    return providers.accounts.list_profiles(cancel=cancel)


class ActivityController(QObject):
    progress = Signal(str)
    finished = Signal(object)
    running_changed = Signal(bool)
    _relay = Signal(object)          # HistoryProgress from a worker thread, queued to the GUI thread

    def __init__(self, settings, runner, providers, parent: QWidget, *,
                 clock: Callable[[], datetime] | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._runner = runner
        self._providers = providers
        self._parent = parent
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._handle = None
        self._stop: CancelToken | None = None
        self._has_history = False
        self._loader: LoadHistoryDialog | None = None
        self._importer: ImportHistoryDialog | None = None
        self.last_notice = ""
        self._refusals: list[Callable[[], str | None]] = []
        self._relay.connect(self._on_progress)

    # ----- state -----
    @property
    def running(self) -> bool:
        return self._handle is not None and not self._handle.done

    @property
    def has_history(self) -> bool:
        return self._has_history

    @property
    def loader(self) -> LoadHistoryDialog | None:
        return self._loader if self._loader is not None and isValid(self._loader) else None

    @property
    def importer(self) -> ImportHistoryDialog | None:
        return self._importer if self._importer is not None and isValid(self._importer) else None

    def busy_text(self) -> str | None:
        return CLEAR_BUSY if self.running else None

    def add_refusal(self, check: Callable[[], str | None]) -> None:
        """Something that must not overlap an Activity job (Clear Local Data while it deletes the store, review I6)
        refuses it with its own words: `check()` returns the reason, or None when a job may start."""
        self._refusals.append(check)

    def _notice(self, text: str, icon: Icon = Icon.INFO) -> None:
        self.last_notice = text
        box = MessageBox95(self._parent, "Activity", text, icon)
        box.setModal(False)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.show()

    def _live(self) -> bool:
        if not self._providers.is_live:
            self._notice(LIVE_ONLY)
            return False
        return True

    def _idle(self) -> bool:
        if self.running:
            self._notice("Activity is already loading or importing. Wait for it to finish, or cancel it.")
            return False
        return True

    # ----- accounts and requests -----
    def _with_accounts(self, account_ids: Sequence[str], then: Callable[[list[JobAccount]], None]) -> None:
        wanted = set(account_ids)
        roster = [e for e in self._settings.roster() if not wanted or e.account_id in wanted]
        if not roster:
            self._notice(NO_ACCOUNTS_TEXT)
            return
        handle = self._runner.submit(gather_profiles, self._providers)
        handle.finished.connect(partial(self._on_profiles, roster, then))
        handle.failed.connect(partial(self._on_profiles_failed, roster, then))

    def _on_profiles(self, roster: list[RosterEntry], then, result) -> None:
        ok = getattr(result, "status", None) is Status.OK
        by_name = {p.name: p for p in (result.data or ())} if ok else {}
        then([self._job_account(e, by_name.get(e.profile)) for e in roster])

    def _on_profiles_failed(self, roster: list[RosterEntry], then, _message: str, _details: str = "") -> None:
        then([self._job_account(e, None) for e in roster])

    def _job_account(self, entry: RosterEntry, profile) -> JobAccount:
        named = self._settings.account_label(entry.account_id)
        label = named[0] if named else entry.profile
        regions = activity_regions(self._settings.account_regions(entry.account_id),
                                   profile.region if profile is not None else None)
        return JobAccount(entry, label, regions)

    def _request(self, accounts: list[JobAccount], *, days: int | None = None, mode: LoadMode = "load") -> HistoryRequest:
        return HistoryRequest(tuple(a.entry for a in accounts), {a.entry.account_id: a.regions for a in accounts},
                              days, mode, {a.entry.account_id: a.label for a in accounts},
                              self._settings.activity_retention_days)

    # ----- first use: Load Activity History… -----
    def open_loader(self, account_ids: Sequence[str] = (), *_args) -> None:
        if not self._live() or not self._idle():
            return
        if self.loader is not None:
            self.loader.raise_()
            self.loader.activateWindow()
            return
        self._with_accounts(account_ids, self._show_loader)

    def _show_loader(self, accounts: list[JobAccount]) -> None:
        dlg = LoadHistoryDialog(accounts, self._parent)
        dlg.load_requested.connect(partial(self._on_load_chosen, accounts))
        self._loader = dlg
        dlg.show()
        handle = self._runner.submit(self._providers.activity.estimate_history, Scope(), self._request(accounts))
        handle.finished.connect(partial(self._on_estimates, dlg))
        handle.failed.connect(partial(self._on_estimates_failed, dlg))

    def _on_estimates(self, dlg: LoadHistoryDialog, result) -> None:
        if isValid(dlg):
            dlg.set_estimates(result)

    def _on_estimates_failed(self, dlg: LoadHistoryDialog, message: str, _details: str = "") -> None:
        if isValid(dlg):
            dlg.set_estimates(Result.error(message))

    def _on_load_chosen(self, accounts: list[JobAccount], days: int) -> None:
        self._start(self._providers.activity.load_history, self._request(accounts, days=days))

    # ----- resume and update -----
    def resume(self, account_ids: Sequence[str] = (), *_args) -> None:
        if not self._providers.is_live or not self._idle():
            return
        self._with_accounts(account_ids, self._start_resume)

    def _start_resume(self, accounts: list[JobAccount]) -> None:
        self._start(self._providers.activity.load_history, self._request(accounts))

    def update(self, account_ids: Sequence[str] = (), *_args) -> None:
        """F5 in Activity and the prompt's "Also update Activity": only what is newer than the stored coverage."""
        if not self._providers.is_live:
            return                                   # demo and offline windows only re-read
        if not self._has_history:
            self.open_loader(account_ids)
            return
        if not self._idle():
            return
        self._with_accounts(account_ids, self._start_update)

    def _start_update(self, accounts: list[JobAccount]) -> None:
        self._start(self._providers.activity.load_history, self._request(accounts, mode="update"))

    # ----- Import History… -----
    def open_import(self, account_id: str = "", *_args) -> None:
        if not self._live():
            return
        if self.importer is not None:
            self.importer.raise_()
            self.importer.activateWindow()
            return
        self._with_accounts((), partial(self._show_importer, account_id))

    def _show_importer(self, account_id: str, accounts: list[JobAccount]) -> None:
        dlg = ImportHistoryDialog(accounts, self._parent, today=self._clock().date(), account_id=account_id or None)
        dlg.trails_requested.connect(partial(self._find_trails, dlg, accounts))
        dlg.check_requested.connect(partial(self._check, dlg))
        dlg.import_requested.connect(self._on_import_chosen)
        self._importer = dlg
        dlg.show()
        self._find_trails(dlg, accounts, dlg.account_id())

    def _find_trails(self, dlg: ImportHistoryDialog, accounts: list[JobAccount], account_id: str) -> None:
        account = next((a for a in accounts if a.entry.account_id == account_id), None)
        if account is None:
            return
        handle = self._runner.submit(self._providers.activity.find_trails, Scope(accounts=(account_id,)),
                                     account.entry, account.regions)
        handle.finished.connect(partial(self._deliver, dlg, "set_trails"))
        handle.failed.connect(partial(self._deliver_failure, dlg, "set_trails"))

    def _check(self, dlg: ImportHistoryDialog, plan) -> None:
        handle = self._runner.submit(self._providers.activity.check_import, Scope(), plan)
        handle.finished.connect(partial(self._deliver, dlg, "set_check"))
        handle.failed.connect(partial(self._deliver_failure, dlg, "set_check"))

    @staticmethod
    def _deliver(dlg: ImportHistoryDialog, method: str, result) -> None:
        if isValid(dlg):
            getattr(dlg, method)(result)

    @staticmethod
    def _deliver_failure(dlg: ImportHistoryDialog, method: str, message: str, _details: str = "") -> None:
        if isValid(dlg):
            getattr(dlg, method)(Result.error(message))

    def _on_import_chosen(self, plan) -> None:
        if self._idle():
            self._start(self._providers.activity.import_history, plan)

    # ----- running a job -----
    def _start(self, fn: Callable, arg) -> None:
        if not self._idle():
            return
        for check in self._refusals:
            reason = check()
            if reason:
                self._notice(reason)
                return
        self._stop = CancelToken()
        self._handle = self._runner.submit(fn, Scope(), arg, progress=self._relay.emit, stop=self._stop)
        self._handle.finished.connect(self._on_finished)
        self._handle.failed.connect(self._on_failed)
        self.running_changed.emit(True)
        self.progress.emit("Starting…")

    def cancel(self, *_args) -> None:
        if self._stop is not None and self.running:
            self._stop.cancel()
            self.progress.emit("Stopping…")

    def _on_progress(self, p: HistoryProgress) -> None:
        if self.running:
            self.progress.emit(p.text)

    def _settle(self) -> None:
        self._handle = None
        self._stop = None
        self.running_changed.emit(False)
        self.progress.emit("")

    def _on_finished(self, result) -> None:
        self._settle()
        self.finished.emit(result)
        self.refresh_history_flag()
        if getattr(result, "status", None) is Status.ERROR:
            self._notice(result.message or "Loading activity failed.", Icon.ERROR)

    def _on_failed(self, message: str, _details: str = "") -> None:
        self._settle()
        self.finished.emit(Result.error(message))
        self._notice(f"Loading activity failed: {message}", Icon.ERROR)

    # ----- shared state -----
    def refresh_history_flag(self, *_args) -> None:
        if not self._providers.is_live:
            return
        roster = self._settings.roster()
        if not roster:
            self._has_history = False
            return
        handle = self._runner.submit(self._providers.activity.history_status,
                                     Scope(accounts=tuple(e.account_id for e in roster)))
        handle.finished.connect(self._on_history)

    def _on_history(self, result) -> None:
        data = getattr(result, "data", None)
        self._has_history = bool(getattr(result, "status", None) is Status.OK and data is not None and data.has_history)

    def prune(self, *_args) -> None:
        """Retention ("Keep activity for N days", spec §3.3): drop looked-up events older than that."""
        store = self._providers.store
        if not self._providers.is_live or store is None or self.running:
            return
        cut = self._clock() - timedelta(days=self._settings.activity_retention_days)
        self._runner.submit(store.prune_events, cut)

    def start_session(self, *_args) -> None:
        self.refresh_history_flag()
        self.prune()

    def reset(self, *_args) -> None:
        self._has_history = False

    # ----- 2b's prompt: "Also update Activity" -----
    def option_visible(self) -> bool:
        return bool(self._providers.is_live and self._has_history and not self.running)

    def _on_snapshot_accept(self, request) -> None:
        self.update(tuple(e.account_id for e in request.accounts))

    def prompt_option(self) -> PromptOption:
        return PromptOption("activity", "Also update Activity", self.option_visible, self._on_snapshot_accept)
