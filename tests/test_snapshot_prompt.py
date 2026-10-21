import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QWidget

from aws95.contracts.accounts import Profile, RosterEntry
from aws95.contracts.billing import BILLING_COST_NOTE
from aws95.contracts.common import Result
from aws95.contracts.snapshots import AccountAge, AccountRun, SnapshotOutcome, SnapshotProgress
from aws95.core.settings import Settings
from aws95.core.tasks import TaskRunner
from aws95.shell.snapshot_prompt import PromptOption, SnapshotController, headline, snapshot_regions, stale_ages

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
DEV, PROD = "123456789012", "210987654321"
ROSTER = (RosterEntry("dev", DEV), RosterEntry("prod", PROD))


class StubSnapshots:
    def __init__(self, ages):
        self.ages_list = ages
        self.asked = 0
        self.taken = []
        self.gate = None

    def ages(self, scope, *, cancel=None):
        self.asked += 1
        return Result.ok(tuple(self.ages_list))

    def stages(self, scope, *, cancel=None):
        return Result.ok(())

    def take_snapshot(self, scope, request=None, *, progress=None, stop=None, cancel=None):
        self.taken.append(request)
        if progress is not None:
            progress(SnapshotProgress(DEV, "dev", 1, 19, "eu-west-1"))
        if self.gate is not None:
            self.gate.wait(5)
        return Result.ok(SnapshotOutcome((AccountRun(DEV, 1, "complete"),)))


class StubAccounts:
    def list_profiles(self, scope=None, *, cancel=None):
        return Result.ok((Profile("dev", "profile", "eu-west-1"), Profile("prod", "profile", None)))


def providers(ages, *, live=True, demo=False, store=None):
    return SimpleNamespace(is_live=live, is_demo=demo, snapshots=StubSnapshots(ages), accounts=StubAccounts(),
                           store=store)


@pytest.fixture
def env(tmp_path, qapp, qtbot):
    settings = Settings(QSettings(str(tmp_path / "p.ini"), QSettings.Format.IniFormat))
    settings.set_roster(ROSTER)
    runner = TaskRunner()
    parent = QWidget()
    qtbot.addWidget(parent)

    def make(prov):
        return SnapshotController(settings, runner, prov, parent, clock=lambda: NOW)
    yield settings, make
    runner.shutdown()


def _prompt(qtbot, c):
    qtbot.waitUntil(lambda: c.prompt is not None and c.prompt.isVisible(), timeout=3000)
    return c.prompt


def _settled(qtbot, c):
    qtbot.waitUntil(lambda: not c.checking, timeout=3000)


def test_regions_rule():
    assert snapshot_regions((), "eu-west-1") == ("eu-west-1", "us-east-1")
    assert snapshot_regions((), None) == ("us-east-1",)
    assert snapshot_regions(("ap-south-1",), "eu-west-1") == ("ap-south-1",)


def test_headlines():
    labels = {DEV: "dev", PROD: "prod"}
    assert headline(stale_ages([AccountAge(DEV, None), AccountAge(PROD, None)], NOW), labels, NOW) == \
        "You haven't taken a snapshot yet (dev, prod). Take one now?"
    old = [AccountAge(DEV, NOW - timedelta(days=3)), AccountAge(PROD, NOW - timedelta(hours=2))]
    assert headline(stale_ages(old, NOW), labels, NOW) == \
        "It's been 3 days since your last snapshot (dev). Take one now?"
    assert headline([], labels, NOW) == "Take a new snapshot of your accounts?"


def test_prompt_for_a_stale_account_shows_regions_and_billing(env, qtbot):
    settings, make = env
    c = make(providers([AccountAge(DEV, NOW - timedelta(days=3)), AccountAge(PROD, NOW - timedelta(hours=2))]))
    c.maybe_prompt()
    text = _prompt(qtbot, c).text_dump()
    assert "It's been 3 days since your last snapshot (dev)." in text
    assert "dev: eu-west-1, us-east-1 + global services" in text
    assert "prod: us-east-1 + global services" in text
    assert BILLING_COST_NOTE in text


def test_prompt_for_a_missing_snapshot(env, qtbot):
    _, make = env
    c = make(providers([AccountAge(DEV, None), AccountAge(PROD, None)]))
    c.maybe_prompt()
    assert "You haven't taken a snapshot yet (dev, prod)." in _prompt(qtbot, c).text_dump()


def test_fresh_accounts_are_not_prompted(env, qtbot):
    _, make = env
    c = make(providers([AccountAge(DEV, NOW - timedelta(hours=1)), AccountAge(PROD, NOW - timedelta(hours=1))]))
    c.maybe_prompt()
    _settled(qtbot, c)
    assert c.prompt is None


def test_once_per_session_unless_asked(env, qtbot):
    _, make = env
    c = make(providers([AccountAge(DEV, None), AccountAge(PROD, None)]))
    c.maybe_prompt()
    _prompt(qtbot, c).later_button.click()
    qtbot.waitUntil(lambda: c.prompt is None or not c.prompt.isVisible(), timeout=3000)
    c.maybe_prompt()
    _settled(qtbot, c)
    assert c.prompt is None or not c.prompt.isVisible()
    c.open_prompt()                                      # on demand: always
    assert _prompt(qtbot, c).isVisible()


def test_not_now_takes_nothing(env, qtbot):
    _, make = env
    prov = providers([AccountAge(DEV, None), AccountAge(PROD, None)])
    c = make(prov)
    c.maybe_prompt()
    _prompt(qtbot, c).later_button.click()
    qtbot.wait(50)
    assert prov.snapshots.taken == []


def test_take_saves_billing_choices_and_builds_the_request(env, qtbot):
    settings, make = env
    settings.set_account_billing(PROD, True)
    prov = providers([AccountAge(DEV, None), AccountAge(PROD, None)])
    c = make(prov)
    c.maybe_prompt()
    p = _prompt(qtbot, c)
    assert p.billing_checks[DEV].isChecked() is False and p.billing_checks[PROD].isChecked() is True
    p.billing_checks[DEV].setChecked(True)
    p.billing_checks[PROD].setChecked(False)
    with qtbot.waitSignal(c.finished, timeout=3000):
        p.take_button.click()
    assert settings.account_billing(DEV) is True and settings.account_billing(PROD) is False
    [req] = prov.snapshots.taken
    assert req.include_billing_for == frozenset({DEV})
    assert dict(req.regions_for) == {DEV: ("eu-west-1", "us-east-1"), PROD: ("us-east-1",)}
    assert dict(req.labels) == {DEV: "dev", PROD: "prod"} and req.accounts == ROSTER


def test_change_regions_saves_per_account(env, qtbot):
    settings, make = env
    c = make(providers([AccountAge(DEV, None), AccountAge(PROD, None)]))
    c.maybe_prompt()
    _prompt(qtbot, c).regions_button.click()
    qtbot.waitUntil(lambda: c.regions_dialog is not None and c.regions_dialog.isVisible(), timeout=3000)
    d = c.regions_dialog
    d.account_combo.setCurrentIndex(1)                   # prod
    for i in range(d.region_list.count()):
        d.region_list.item(i).setCheckState(Qt.CheckState.Unchecked)
    d.save_button.click()
    assert d.error_label.text() == "Pick at least one region." and settings.account_regions(PROD) == ()
    for i in range(d.region_list.count()):
        if d.region_list.item(i).text() == "me-south-1":
            d.region_list.item(i).setCheckState(Qt.CheckState.Checked)
    d.save_button.click()
    assert settings.account_regions(PROD) == ("me-south-1",)
    assert "prod: me-south-1 + global services" in c.prompt.text_dump()


@pytest.mark.parametrize("live,demo", [(False, True), (False, False)])
def test_never_in_demo_or_offline(env, qtbot, live, demo):
    _, make = env
    prov = providers([AccountAge(DEV, None)], live=live, demo=demo)
    c = make(prov)
    c.maybe_prompt()
    qtbot.wait(50)
    assert prov.snapshots.asked == 0 and c.prompt is None


def test_progress_relay_and_finish(env, qtbot):
    _, make = env
    prov = providers([AccountAge(DEV, None), AccountAge(PROD, None)])
    prov.snapshots.gate = threading.Event()
    c = make(prov)
    texts = []
    c.progress.connect(texts.append)
    c.take(SimpleNamespace(accounts=ROSTER))
    qtbot.waitUntil(lambda: "dev: 1/19 sections · eu-west-1" in texts, timeout=3000)
    assert c.running
    with qtbot.waitSignal(c.finished, timeout=3000):
        prov.snapshots.gate.set()
    assert not c.running and texts[-1] == ""


def test_option_extension_point(env, qtbot):
    _, make = env
    prov = providers([AccountAge(DEV, None), AccountAge(PROD, None)])
    c = make(prov)
    calls = []
    c.register_option(PromptOption("activity", "Also update Activity", lambda: True, calls.append))
    c.register_option(PromptOption("hidden", "Never shown", lambda: False, calls.append))
    c.maybe_prompt()
    p = _prompt(qtbot, c)
    assert "Also update Activity" in p.text_dump() and "Never shown" not in p.text_dump()
    with qtbot.waitSignal(c.finished, timeout=3000):
        p.take_button.click()
    assert calls == [prov.snapshots.taken[0]]


def test_take_honours_a_refusal(env, qtbot):
    """Review I6: while something refuses (Clear Local Data is deleting the store), no snapshot starts."""
    _, make = env
    prov = providers([AccountAge(DEV, None)])
    c = make(prov)
    reason = ["Local data is being cleared. Try again when it is done."]
    c.add_refusal(lambda: reason[0])
    c.take(SimpleNamespace(accounts=ROSTER))
    qtbot.wait(50)
    assert prov.snapshots.taken == [] and not c.running
    reason[0] = None
    with qtbot.waitSignal(c.finished, timeout=3000):
        c.take(SimpleNamespace(accounts=ROSTER))
    assert len(prov.snapshots.taken) == 1


def test_prompt_fits_wrapped_text_at_dark_150(qapp, qtbot):
    """Dark and 150 % font are Important: the wrapped cost note must never be squeezed or clipped."""
    from aws95.kit import theme as th
    from aws95.kit.style import apply_theme
    from aws95.shell.snapshot_prompt import PromptAccount, SnapshotPrompt
    apply_theme(qapp, th.DARK, font_scale=150)
    try:
        accounts = (PromptAccount(RosterEntry("dev", "123456789012"), "dev", ("eu-west-1", "us-east-1"), False),
                    PromptAccount(RosterEntry("prod", "210987654321"), "prod", ("me-south-1", "us-east-1"), True))
        dlg = SnapshotPrompt("It's been 3 days since your last snapshot (dev, prod). Take one now?", accounts, ())
        qtbot.addWidget(dlg)
        dlg.show()
        qtbot.waitExposed(dlg)
        box = dlg.billing_box
        assert box.height() >= box.heightForWidth(box.width())
        note = dlg.cost_note
        assert note.height() >= note.heightForWidth(note.width())
    finally:
        apply_theme(qapp, th.LIGHT, font_scale=100)
