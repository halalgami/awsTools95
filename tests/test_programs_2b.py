import csv
import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from aws95.contracts.common import Coverage, Freshness, Page, Result, Scope, Status
from aws95.contracts.findings import Finding
from aws95.kit.widgets import statusstrip
from aws95.kit.widgets.stateview import ViewState
from aws95.programs.billing import make_billing
from aws95.programs.findings import make_findings
from aws95.programs.snapshots import make_snapshots
from aws95.providers.demo import demo_provider_set
from aws95.providers.empty import empty_provider_set

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


def test_snapshot_freshness_text():
    r = Result.ok((), freshness=Freshness(NOW - timedelta(hours=2), "snapshot"))
    assert statusstrip.freshness_text(r, NOW) == "Snapshot 2h ago"
    assert statusstrip.freshness_text(Result.ok((), freshness=Freshness(NOW, "attempt")), NOW) == "Synced just now"


def _finding(rid="b1", mute=None):
    return Finding("123456789012", "s3.no_lifecycle", "s3.bucket", "global", rid, "medium", NOW, False, "open", 3,
                   {}, mute)


class PartialFindings:
    def __init__(self):
        self.accepted = []
        self.unaccepted = []

    def list_findings(self, scope, state="open", cursor=None, *, cancel=None):
        return Result(Status.PARTIAL, Page((_finding(),)), Coverage(("us-east-1", "eu-west-1"), ("us-east-1",)),
                      None, "Not collected in eu-west-1.")

    def rules(self, scope, *, cancel=None):
        return Result.ok(())

    def fleet(self, scope, *, cancel=None):
        return Result.ok(())

    def accept(self, scope, finding=None, target="resource", reason="", expires_at=None, *, cancel=None):
        self.accepted.append((finding.rid, target, reason, expires_at))
        if not reason:
            return Result.error("Give a reason for accepting this finding.")
        return Result.ok(None)

    def unaccept(self, scope, mute_id=0, *, cancel=None):
        self.unaccepted.append(mute_id)
        return Result.ok(None)


@pytest.fixture
def findings(program_env, qtbot):
    stub = PartialFindings()
    w, ctx = program_env(make_findings, dataclasses.replace(empty_provider_set(), findings=stub))
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: len(w.grid.visible_rows()) == 1)
    return w, ctx, stub


def test_partial_csv_export_carries_a_status_row(findings, tmp_path):
    w, _, _ = findings
    path = tmp_path / "f.csv"
    w.ask_save_path = lambda *a, **k: str(path)
    assert w.export()
    first = next(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    assert first[0].startswith("Status: PARTIAL") and "eu-west-1" in first[0]


def test_ok_csv_export_has_no_status_row(program_env, qtbot, tmp_path):
    w, _ = program_env(make_findings, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: len(w.grid.visible_rows()) > 0)
    path = tmp_path / "ok.csv"
    w.ask_save_path = lambda *a, **k: str(path)
    assert w.export()
    assert next(csv.reader(path.read_text(encoding="utf-8").splitlines()))[0] == "Severity"


def test_accept_dialog_saves_through_the_provider(findings, qtbot):
    w, _, stub = findings
    w.grid.view.selectRow(0)
    dlg = w.open_mute_dialog()
    assert dlg.save_button.isEnabled()
    dlg.never_box.setChecked(True)
    dlg.save_button.click()                              # no reason: the provider refuses, the dialog says why
    qtbot.waitUntil(lambda: "reason" in dlg.error_label.text(), timeout=3000)
    dlg.reason_edit.setText("accepted risk")
    dlg.scope_rule.setChecked(True)
    dlg.save_button.click()
    qtbot.waitUntil(lambda: len(stub.accepted) == 2, timeout=3000)
    assert stub.accepted[1] == ("b1", "rule", "accepted risk", None)


def test_accept_expiry_is_end_of_the_chosen_day_utc(findings):
    w, _, _ = findings
    w.grid.view.selectRow(0)
    dlg = w.open_mute_dialog()
    exp = dlg.expires_at()
    assert exp.tzinfo is timezone.utc and (exp.hour, exp.minute, exp.second) == (23, 59, 59)


def test_unaccept_needs_an_accepted_finding(findings, qtbot):
    w, _, stub = findings
    w.grid.view.selectRow(0)
    w.unaccept_selected()
    assert stub.unaccepted == [] and "accepted finding" in w.status.counts_label.text()


def test_demo_accept_cannot_save(program_env, qtbot):
    w, _ = program_env(make_findings, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: len(w.grid.visible_rows()) > 0)
    w.grid.view.selectRow(0)
    dlg = w.open_mute_dialog()
    assert not dlg.save_button.isEnabled() and "demo mode" in dlg.save_button.toolTip()


def test_windows_refresh_after_a_snapshot(program_env, qtbot):
    w, ctx = program_env(make_snapshots, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: len(w.history.visible_rows()) > 0)
    calls = []
    w.refresh = lambda: calls.append(1)
    ctx.shell.snapshot_finished.emit(None)
    assert calls == [1]


def test_snapshots_toolbar_and_progress(program_env, qtbot):
    w, ctx = program_env(make_snapshots, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    w.take_action.trigger()
    w.cancel_action.trigger()
    assert ("take_snapshot", ()) in ctx.shell.calls and ("cancel_snapshot", ()) in ctx.shell.calls
    ctx.shell.snapshot_progress.emit("dev: 7/19 collectors · me-south-1")
    assert w.status.counts_label.text() == "dev: 7/19 collectors · me-south-1"


def test_billing_offers_enable_when_not_enabled(program_env, qtbot):
    from aws95.contracts.billing import BILLING_NOT_ENABLED

    class Off:
        def __getattr__(self, name):
            return lambda *a, **k: Result.not_connected(BILLING_NOT_ENABLED)

    w, ctx = program_env(make_billing, dataclasses.replace(empty_provider_set(), billing=Off()))
    ctx.settings.set_roster(())
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.enable_button.isVisible(), timeout=3000)
    w.enable_button.click()
    assert ("take_snapshot", ()) in ctx.shell.calls


def test_billing_hides_enable_when_enabled(program_env, qtbot):
    w, _ = program_env(make_billing, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.kpi_view.state is ViewState.CONTENT, timeout=3000)
    assert not w.enable_button.isVisible()


def test_every_billed_control_states_its_cost(program_env, qtbot):
    """Spec §2.1: every control that can cause a billed call shows its cost in visible text, before anything runs.
    (My Accounts' checkbox and the wizard's probe are pinned in their own test files.)"""
    from aws95.contracts.accounts import RosterEntry
    from aws95.contracts.billing import BILLING_COST_NOTE, BILLING_NOT_ENABLED
    from aws95.shell.snapshot_prompt import PromptAccount, SnapshotPrompt

    acct = PromptAccount(RosterEntry("dev", "123456789012"), "dev", ("us-east-1",), False)
    dlg = SnapshotPrompt("It's been 3 days since your last snapshot (dev). Take one now?", (acct,), ())
    qtbot.addWidget(dlg)
    dlg.show()
    assert dlg.cost_note.isVisible() and dlg.cost_note.text() == BILLING_COST_NOTE
    assert dlg.cost_note.parent() is dlg.billing_checks["123456789012"].parent()     # beside the checkboxes

    class Off:
        def __getattr__(self, name):
            return lambda *a, **k: Result.not_connected(BILLING_NOT_ENABLED)

    w, _ = program_env(make_billing, dataclasses.replace(empty_provider_set(), billing=Off()))
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.enable_button.isVisible(), timeout=3000)
    assert w.enable_note.isVisible() and w.enable_note.text() == BILLING_COST_NOTE


def test_data_windows_say_a_snapshot_is_running(program_env, qtbot):
    """While a snapshot runs, every data window's freshness pane says so; when it ends the pane reads as before."""
    from aws95.programs.findings import make_findings as findings_factory
    w, ctx = program_env(findings_factory, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.status.freshness_label.text() != "", timeout=3000)
    before = w.status.freshness_label.text()
    ctx.shell.snapshot_progress.emit("dev: 7/19 collectors")
    assert w.status.freshness_label.text() == "Snapshot running…"
    ctx.shell.snapshot_progress.emit("")
    assert w.status.freshness_label.text() == before
