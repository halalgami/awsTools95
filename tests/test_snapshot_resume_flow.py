import dataclasses

from PySide6.QtCore import QSettings

from aws95.contracts.accounts import RosterEntry
from aws95.contracts.common import Result, Status
from aws95.contracts.snapshots import AccountStage, AccountRun, ResumeRequest, SnapshotOutcome
from aws95.core.settings import Settings
from aws95.core.tasks import TaskRunner
from aws95.providers.demo import demo_provider_set
from aws95.shell.snapshot_prompt import SnapshotController

A = "123456789012"


class Snaps:
    def __init__(self):
        self.resumed = []

    def resume_snapshot(self, scope, request=None, *, progress=None, stop=None, cancel=None):
        self.resumed.append(request)
        return Result.ok(SnapshotOutcome((AccountRun(A, 1, "complete", "complete (2/2 sections)"),)),
                         message="dev: complete (2/2 sections)")

    def __getattr__(self, name):
        return getattr(demo_provider_set().snapshots, name)


def make(tmp_path, qtbot, snaps):
    from PySide6.QtWidgets import QWidget
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    settings.set_roster((RosterEntry("dev", A),))
    settings.set_account_billing(A, True)
    runner = TaskRunner()
    parent = QWidget()
    qtbot.addWidget(parent)
    ps = dataclasses.replace(demo_provider_set(), snapshots=snaps, is_demo=False, is_live=True)
    return SnapshotController(settings, runner, ps, parent), runner


def test_resume_accounts_builds_the_request_from_settings(tmp_path, qtbot):
    snaps = Snaps()
    c, runner = make(tmp_path, qtbot, snaps)
    try:
        with qtbot.waitSignal(c.finished, timeout=3000):
            c.resume_accounts(retry_denied=True)
        [req] = snaps.resumed
        assert isinstance(req, ResumeRequest) and req.retry_denied
        assert req.include_billing_for == frozenset({A}) and [e.account_id for e in req.accounts] == [A]
    finally:
        runner.shutdown()


def test_resume_is_refused_while_a_snapshot_runs(tmp_path, qtbot):
    snaps = Snaps()
    c, runner = make(tmp_path, qtbot, snaps)
    try:
        c._handle = type("H", (), {"done": False})()            # a running snapshot
        c.resume_accounts()
        assert snaps.resumed == []
    finally:
        c._handle = None
        runner.shutdown()


def test_prompt_offers_resume_for_resumable_accounts(qtbot):
    from aws95.shell.snapshot_prompt import PromptAccount, SnapshotPrompt
    acct = PromptAccount(RosterEntry("dev", A), "dev", ("us-east-1",), True)
    dlg = SnapshotPrompt("It's been 3 days.", (acct,), (), resumable=("dev",))
    qtbot.addWidget(dlg)
    dlg.show()
    assert dlg.resume_button.isVisible() and "dev" in dlg.resume_button.text()
    with qtbot.waitSignal(dlg.resume_requested, timeout=1000):
        dlg.resume_button.click()
    plain = SnapshotPrompt("It's been 3 days.", (acct,), ())
    qtbot.addWidget(plain)
    plain.show()
    assert not plain.resume_button.isVisible()
