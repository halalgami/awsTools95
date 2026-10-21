import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QWidget

from aws95.contracts.accounts import Profile, RosterEntry
from aws95.contracts.activity import (ALL_REGIONS, AccessCheck, AccountHistory, AccountLoad, CoverageSpan,
                                      HistoryEstimate, HistoryOutcome, HistoryProgress, HistoryStatus, RegionHistory,
                                      TrailInfo, TrailLookup)
from aws95.contracts.common import Result
from aws95.contracts.snapshots import SnapshotRequest
from aws95.core.settings import Settings
from aws95.core.tasks import TaskRunner
from aws95.shell.activity_jobs import CLEAR_BUSY, LIVE_ONLY, ActivityController
from aws95.shell.snapshot_prompt import LocalDataCleaner, SnapshotController
from aws95.store import EventRow

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
DEV, PROD = "123456789012", "210987654321"
ROSTER = (RosterEntry("dev", DEV), RosterEntry("prod", PROD))
TRAIL = TrailInfo("main", f"arn:aws:cloudtrail:us-east-1:{DEV}:trail/main", "us-east-1", "example-cloudtrail-bucket",
                  "", None, False, DEV, True, False)


class StubActivity:
    def __init__(self, has_history=False):
        self.has, self.calls, self.gate = has_history, [], None

    def history_status(self, scope, *, cancel=None):
        spans = (CoverageSpan(NOW - timedelta(days=7), NOW),) if self.has else ()
        return Result.ok(HistoryStatus((AccountHistory(DEV, 7, (RegionHistory("us-east-1", spans),)),)))

    def estimate_history(self, scope, request=None, *, cancel=None):
        self.calls.append(("estimate", request))
        return Result.ok((HistoryEstimate(DEV, "eu-west-1", {7: 1, 30: 4, 90: 11}),
                          HistoryEstimate("", ALL_REGIONS, {7: 3, 30: 9, 90: 27})))

    def load_history(self, scope, request=None, *, progress=None, stop=None, cancel=None):
        self.calls.append(("load", request))
        if progress is not None:
            progress(HistoryProgress("load", "dev", 1, 7, "eu-west-1"))
        if self.gate is not None:
            self.gate.wait(5)
        self.has = True
        return Result.ok(HistoryOutcome("load", (AccountLoad(DEV, "complete", 3),)))

    def find_trails(self, scope, account=None, regions=(), *, cancel=None):
        self.calls.append(("trails", account, regions))
        return Result.ok(TrailLookup(account.account_id, (TRAIL,)))

    def check_import(self, scope, plan=None, *, cancel=None):
        self.calls.append(("check", plan))
        return Result.ok(AccessCheck(True, "Access checked: this profile can list and read the trail's log files."))

    def import_history(self, scope, plan=None, *, progress=None, stop=None, cancel=None):
        self.calls.append(("import", plan))
        return Result.ok(HistoryOutcome("import", (AccountLoad(DEV, "complete", 5),)))


class StubAccounts:
    def list_profiles(self, scope=None, *, cancel=None):
        return Result.ok((Profile("dev", "profile", "eu-west-1"), Profile("prod", "profile", None)))


def providers(*, live=True, demo=False, has_history=False, store=None):
    return SimpleNamespace(is_live=live, is_demo=demo, activity=StubActivity(has_history), accounts=StubAccounts(),
                           store=store)


@pytest.fixture
def env(tmp_path, qapp, qtbot):
    settings = Settings(QSettings(str(tmp_path / "a.ini"), QSettings.Format.IniFormat))
    settings.set_roster(ROSTER)
    settings.set_account_label(DEV, "dev", "#2e8b57")
    runner = TaskRunner()
    parent = QWidget()
    qtbot.addWidget(parent)
    made = []

    def make(prov):
        c = ActivityController(settings, runner, prov, parent, clock=lambda: NOW)
        made.append(c)
        return c
    yield settings, make
    for c in made:
        for dlg in (c.loader, c.importer):
            if dlg is not None:
                dlg.close()
    runner.shutdown()


def test_loader_estimates_then_loads_the_chosen_days(env, qtbot):
    _, make = env
    prov = providers()
    c = make(prov)
    c.open_loader()
    qtbot.waitUntil(lambda: c.loader is not None and c.loader.estimates.rowCount() == 2, timeout=3000)
    (kind, req), = [x for x in prov.activity.calls if x[0] == "estimate"]
    assert dict(req.regions_for) == {DEV: ("eu-west-1", "us-east-1"), PROD: ("us-east-1",)}
    assert dict(req.labels) == {DEV: "dev", PROD: "prod"} and req.retention_days == 90
    c.loader.day_buttons[30].setChecked(True)
    with qtbot.waitSignal(c.finished, timeout=3000):
        c.loader.load_button.click()
    load = next(r for k, r in prov.activity.calls if k == "load")
    assert load.days == 30 and load.mode == "load"
    qtbot.waitUntil(lambda: c.has_history, timeout=3000)


@pytest.mark.parametrize("live,demo", [(False, True), (False, False)])
def test_never_in_demo_or_offline(env, qtbot, live, demo):
    _, make = env
    prov = providers(live=live, demo=demo)
    c = make(prov)
    c.open_loader()
    assert c.last_notice == LIVE_ONLY and c.loader is None
    c.update()                                    # F5 in a demo/offline window only re-reads: no notice, no call
    c.resume()
    c.open_import()
    c.prune()
    assert prov.activity.calls == []


def test_update_without_history_opens_the_loader_and_with_history_updates(env, qtbot):
    _, make = env
    prov = providers()
    c = make(prov)
    c.update((DEV,))
    qtbot.waitUntil(lambda: c.loader is not None, timeout=3000)
    c.loader.close()
    prov.activity.has = True
    c.refresh_history_flag()
    qtbot.waitUntil(lambda: c.has_history, timeout=3000)
    with qtbot.waitSignal(c.finished, timeout=3000):
        c.update((DEV,))
    load = next(r for k, r in prov.activity.calls if k == "load")
    assert load.mode == "update" and [e.account_id for e in load.accounts] == [DEV]


def test_one_job_at_a_time_progress_and_cancel(env, qtbot):
    _, make = env
    prov = providers(has_history=True)
    prov.activity.gate = threading.Event()
    c = make(prov)
    texts, states = [], []
    c.progress.connect(texts.append)
    c.running_changed.connect(states.append)
    c.resume()
    qtbot.waitUntil(lambda: "Activity dev: 1/7 days · eu-west-1" in texts, timeout=3000)
    assert c.running and c.busy_text() == CLEAR_BUSY
    c.open_loader()
    assert "already" in c.last_notice and c.loader is None
    c.cancel()
    assert texts[-1] == "Stopping…"
    with qtbot.waitSignal(c.finished, timeout=3000):
        prov.activity.gate.set()
    assert not c.running and texts[-1] == "" and states == [True, False] and c.busy_text() is None


def test_prompt_option_visible_only_with_history_and_updates_on_accept(env, qtbot):
    _, make = env
    prov = providers()
    c = make(prov)
    option = c.prompt_option()
    assert option.key == "activity" and option.text == "Also update Activity" and not option.visible()
    prov.activity.has = True
    c.refresh_history_flag()
    qtbot.waitUntil(c.option_visible, timeout=3000)
    with qtbot.waitSignal(c.finished, timeout=3000):
        option.on_accept(SnapshotRequest(ROSTER[:1]))
    assert next(r for k, r in prov.activity.calls if k == "load").mode == "update"


def test_import_flow(env, qtbot):
    _, make = env
    prov = providers()
    c = make(prov)
    c.open_import(DEV)
    qtbot.waitUntil(lambda: c.importer is not None and c.importer.trail_combo.count() == 1, timeout=3000)
    assert ("trails", ROSTER[0], ("eu-west-1", "us-east-1")) in prov.activity.calls
    dlg = c.importer
    dlg.reader_combo.setCurrentIndex(1)                            # org trail: read with another roster profile
    dlg.check_button.click()
    qtbot.waitUntil(dlg.import_button.isEnabled, timeout=3000)
    with qtbot.waitSignal(c.finished, timeout=3000):
        dlg.import_button.click()
    plan = next(call[1] for call in prov.activity.calls if call[0] == "import")
    assert plan.reader_profile == "prod" and plan.account == ROSTER[0]


def test_clear_local_data_refused_while_activity_runs(env, qtbot):
    settings, make = env
    prov = providers(has_history=True)
    prov.activity.gate = threading.Event()
    c = make(prov)
    snapshots = SnapshotController(settings, c._runner, prov, c.parent())
    cleaner = LocalDataCleaner(c.parent(), c._runner, prov, snapshots)
    cleaner.add_busy_check(c.busy_text)
    c.resume()
    qtbot.waitUntil(lambda: c.running, timeout=3000)
    cleaner.request()
    assert cleaner.confirm_box is None and cleaner.last_notice == CLEAR_BUSY
    with qtbot.waitSignal(c.finished, timeout=3000):
        prov.activity.gate.set()


def test_no_job_starts_while_local_data_is_cleared(env, qtbot):
    """Review I6: the 2b cleaner sets `clearing` while it deletes the store; no Activity job may start then."""
    settings, make = env
    prov = providers(has_history=True)
    c = make(prov)
    snapshots = SnapshotController(settings, c._runner, prov, c.parent())
    cleaner = LocalDataCleaner(c.parent(), c._runner, prov, snapshots)
    reason = "Local data is being cleared. Try again when it is done."

    def refusal():
        return reason if cleaner.clearing else None
    c.add_refusal(refusal)
    cleaner._clearing = True                                      # as between the confirmed Delete and its end
    c.resume()
    qtbot.waitUntil(lambda: c.last_notice == reason, timeout=3000)
    assert not c.running and not [k for k, _ in prov.activity.calls if k == "load"]
    cleaner._clearing = False
    with qtbot.waitSignal(c.finished, timeout=3000):
        c.resume()


def test_prune_uses_retention(env, qtbot, make_store):
    settings, make = env
    store = make_store("p.db")
    store.insert_events([EventRow("old", DEV, "us-east-1", NOW - timedelta(days=40), "IAMUser", "alice", True,
                                  "iam.amazonaws.com", "CreateUser", False, None, None, None, (), 3, None, "lookup",
                                  "{}")])
    settings.activity_retention_days = 30
    c = make(providers(store=store))
    c.prune()
    qtbot.waitUntil(lambda: store.raw_event("old") is None, timeout=3000)
