import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QWidget

from aws95.contracts.accounts import Profile, RosterEntry
from aws95.contracts.common import Result
from aws95.contracts.snapshots import AccountAge, AccountRun, SnapshotOutcome, SnapshotProgress
from aws95.core.settings import Settings
from aws95.core.tasks import TaskRunner
from aws95.shell.snapshot_prompt import LocalDataCleaner, SnapshotController
from tests.test_snapshot_prompt import DEV, NOW, ROSTER, env, providers  # noqa: F401 - env is a fixture


def test_clear_refused_while_snapshot_runs(env, qtbot, make_store):
    """Review Focus 4."""
    _, make = env
    store = make_store()
    store.set_meta("k", "v")
    prov = providers([AccountAge(DEV, None)], store=store)
    prov.snapshots.gate = threading.Event()
    c = make(prov)
    cleaner = LocalDataCleaner(c.parent(), c._runner, prov, c)
    c.take(SimpleNamespace(accounts=ROSTER))
    qtbot.waitUntil(lambda: c.running, timeout=3000)
    cleaner.request()
    assert cleaner.confirm_box is None and "snapshot is running" in cleaner.last_notice
    with qtbot.waitSignal(c.finished, timeout=3000):
        prov.snapshots.gate.set()
    cleaner.request()
    box = cleaner.confirm_box
    assert box is not None and box.isVisible()
    with qtbot.waitSignal(cleaner.cleared, timeout=3000):
        box.buttons[0].click()                           # Delete
    assert not Path(store.path).exists() or store.get_meta("k") is None


def test_clear_never_in_demo(env, qtbot):
    _, make = env
    prov = providers([], live=False, demo=True)
    c = make(prov)
    cleaner = LocalDataCleaner(c.parent(), c._runner, prov, c)
    cleaner.request()
    assert cleaner.confirm_box is None and "Demo mode" in cleaner.last_notice


def test_clear_rechecks_when_the_user_confirms(env, qtbot, make_store):
    """Review I6: a snapshot that starts while the non-modal confirmation is open still blocks the delete."""
    _, make = env
    store = make_store()
    store.set_meta("k", "v")
    prov = providers([AccountAge(DEV, None)], store=store)
    prov.snapshots.gate = threading.Event()
    c = make(prov)
    cleaner = LocalDataCleaner(c.parent(), c._runner, prov, c)
    cleaner.request()
    box = cleaner.confirm_box
    c.take(SimpleNamespace(accounts=ROSTER))
    qtbot.waitUntil(lambda: c.running, timeout=3000)
    box.buttons[0].click()                               # Delete, but a snapshot is now running
    assert not cleaner.clearing and "snapshot is running" in cleaner.last_notice
    assert store.get_meta("k") == "v"
    with qtbot.waitSignal(c.finished, timeout=3000):
        prov.snapshots.gate.set()


class SlowStore:
    def __init__(self):
        self.gate = threading.Event()

    def clear(self):
        self.gate.wait(5)


def test_no_snapshot_starts_while_clearing(env, qtbot):
    _, make = env
    slow = SlowStore()
    prov = providers([AccountAge(DEV, None)], store=slow)
    c = make(prov)
    cleaner = LocalDataCleaner(c.parent(), c._runner, prov, c)
    cleaner.request()
    cleaner.confirm_box.buttons[0].click()
    qtbot.waitUntil(lambda: cleaner.clearing, timeout=3000)
    c.take(SimpleNamespace(accounts=ROSTER))
    qtbot.wait(50)
    assert prov.snapshots.taken == [] and not c.running
    with qtbot.waitSignal(cleaner.cleared, timeout=3000):
        slow.gate.set()
    assert not cleaner.clearing


def test_busy_checks_refuse_the_clear(env, qtbot):
    """The 2c hook: an Activity job refuses the clear in its own words, at request and again at confirmation."""
    _, make = env
    prov = providers([AccountAge(DEV, None)], store=SlowStore())
    c = make(prov)
    cleaner = LocalDataCleaner(c.parent(), c._runner, prov, c)
    reason = ["Activity history is loading. Cancel it or wait, then try again."]
    cleaner.add_busy_check(lambda: reason[0])
    cleaner.request()
    assert cleaner.confirm_box is None and cleaner.last_notice == reason[0]
    reason[0] = None
    cleaner.request()
    box = cleaner.confirm_box
    reason[0] = "Activity import is running."
    box.buttons[0].click()
    assert not cleaner.clearing and cleaner.last_notice == "Activity import is running."
