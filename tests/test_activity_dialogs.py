from datetime import date

import pytest
from PySide6.QtCore import QDate

from aws95.contracts.accounts import RosterEntry
from aws95.contracts.activity import ALL_REGIONS, LOAD_WORDING, AccessCheck, HistoryEstimate, TrailInfo, TrailLookup
from aws95.contracts.common import Result, Status
from aws95.shell.activity_dialogs import ESTIMATING, ImportHistoryDialog, JobAccount, LoadHistoryDialog

DEV, PROD = "123456789012", "210987654321"
ACCOUNTS = (JobAccount(RosterEntry("dev", DEV), "dev", ("eu-west-1", "us-east-1")),
            JobAccount(RosterEntry("prod", PROD), "prod", ("us-east-1",)))
KMS = f"arn:aws:kms:us-east-1:{DEV}:key/abcd"
TRAIL = TrailInfo("main", f"arn:aws:cloudtrail:us-east-1:{DEV}:trail/main", "us-east-1", "example-cloudtrail-bucket",
                  "logs", KMS, False, DEV, True, False)
TODAY = date(2026, 10, 2)
ESTIMATES = (HistoryEstimate(DEV, "eu-west-1", {7: 1, 30: 4, 90: 11}),
             HistoryEstimate(DEV, "us-east-1", {7: 2, 30: 6, 90: 17}, at_least=True),
             HistoryEstimate(PROD, "us-east-1", {}, error="Permission denied for cloudtrail:LookupEvents."),
             HistoryEstimate("", ALL_REGIONS, {7: 3, 30: 10, 90: 28}, at_least=True))


@pytest.fixture
def opened(qapp):
    made = []
    yield made
    for w in made:
        w.close()


def test_load_dialog_defaults_to_seven_days_and_emits_choice(opened, qtbot):
    dlg = LoadHistoryDialog(ACCOUNTS)
    opened.append(dlg)
    assert dlg.selected_days() == 7 and not dlg.isModal() and dlg.windowTitle() == "Load Activity History"
    assert "(recommended)" in dlg.day_buttons[7].text() and dlg.estimate_label.text() == ESTIMATING
    assert dlg.wording_label.text() == LOAD_WORDING
    dlg.day_buttons[30].setChecked(True)
    with qtbot.waitSignal(dlg.load_requested) as sig:
        dlg.load_button.click()
    assert sig.args == [30]


def test_load_dialog_shows_estimates_per_region_and_total(opened):
    dlg = LoadHistoryDialog(ACCOUNTS)
    opened.append(dlg)
    dlg.set_estimates(Result.ok(ESTIMATES))
    cells = [[dlg.estimates.item(r, c).text() for c in range(5)] for r in range(dlg.estimates.rowCount())]
    assert cells[0] == ["dev", "eu-west-1", "≈ 1 min", "≈ 4 min", "≈ 11 min"]
    assert cells[1][2] == "≈ 2 min or more" and cells[2][2:] == ["unknown"] * 3
    assert cells[3][:2] == ["All", ALL_REGIONS]
    assert dlg.day_buttons[90].text() == "&90 days — ≈ 28 min or more"
    assert "prod · us-east-1: Permission denied for cloudtrail:LookupEvents." in dlg.estimate_label.text()


def test_load_dialog_survives_a_failed_estimate(opened):
    dlg = LoadHistoryDialog(ACCOUNTS)
    opened.append(dlg)
    dlg.set_estimates(Result.error("Can't reach AWS (cloudtrail). Check your network or VPN."))
    assert dlg.estimates.rowCount() == 0 and "You can still load." in dlg.estimate_label.text()
    assert dlg.load_button.isEnabled()


def import_dialog(opened, qtbot):
    dlg = ImportHistoryDialog(ACCOUNTS, today=TODAY)
    opened.append(dlg)
    dlg.set_trails(Result.ok(TrailLookup(DEV, (TRAIL,))))
    return dlg


def test_import_dialog_plan_and_dates(opened, qtbot):
    dlg = import_dialog(opened, qtbot)
    assert dlg.account_id() == DEV and dlg.reader_combo.currentData() == "dev"
    p = dlg.plan()
    assert p.trail == TRAIL and p.regions == ("eu-west-1", "us-east-1") and p.label == "dev"
    assert (p.start_day, p.end_day) == (date(2026, 9, 2), date(2026, 10, 1))
    assert dlg.to_edit.maximumDate() == QDate(2026, 10, 2)
    dlg.from_edit.setDate(QDate(2026, 10, 1))
    dlg.to_edit.setDate(QDate(2026, 9, 1))
    assert dlg.plan() is None and "start date is after the end date" in dlg.result_label.text()


def test_import_needs_a_passing_check_for_the_same_plan(opened, qtbot):
    dlg = import_dialog(opened, qtbot)
    assert not dlg.import_button.isEnabled()
    with qtbot.waitSignal(dlg.check_requested) as sig:
        dlg.check_button.click()
    assert sig.args[0] == dlg.plan()
    dlg.set_check(Result.ok(AccessCheck(True, "Access checked: this profile can list and read the trail's log files.")))
    assert dlg.import_button.isEnabled() and not dlg.policy_view.isVisible()
    dlg.reader_combo.setCurrentIndex(1)                            # anything changes: check again
    assert not dlg.import_button.isEnabled()
    dlg.check_button.click()
    dlg.set_check(Result.ok(AccessCheck(True, "ok")))
    with qtbot.waitSignal(dlg.import_requested) as sig:
        dlg.import_button.click()
    assert sig.args[0].reader_profile == "prod"


def test_failed_check_shows_policy_lines_as_plain_text(opened, qtbot):
    dlg = import_dialog(opened, qtbot)
    dlg.show()
    dlg.check_button.click()
    dlg.set_check(Result.ok(AccessCheck(False, "The trail's log files are encrypted …", '[{"Action": "kms:Decrypt"}]')))
    assert not dlg.import_button.isEnabled() and dlg.policy_view.isVisible() and dlg.copy_button.isVisible()
    assert dlg.policy_view.toPlainText() == '[{"Action": "kms:Decrypt"}]' and dlg.policy_view.isReadOnly()
    dlg.copy_button.click()
    from PySide6.QtWidgets import QApplication
    assert QApplication.clipboard().text() == '[{"Action": "kms:Decrypt"}]'


def test_account_change_asks_for_its_trails_and_ignores_stale_answers(opened, qtbot):
    dlg = import_dialog(opened, qtbot)
    with qtbot.waitSignal(dlg.trails_requested) as sig:
        dlg.account_combo.setCurrentIndex(1)
    assert sig.args == [PROD] and dlg.plan() is None and dlg.reader_combo.currentData() == "prod"
    dlg.set_trails(Result.ok(TrailLookup(DEV, (TRAIL,))))           # a late answer for the previous account
    assert dlg.trail_combo.count() == 0
    dlg.set_trails(Result.ok(TrailLookup(PROD, (), "This account has no CloudTrail trail…")))
    assert dlg.note_label.text().startswith("This account has no CloudTrail trail") and dlg.plan() is None


def test_trails_failure_is_said_plainly(opened, qtbot):
    dlg = ImportHistoryDialog(ACCOUNTS, today=TODAY)
    opened.append(dlg)
    dlg.set_trails(Result(Status.DENIED, message="Permission denied for cloudtrail:DescribeTrails."))
    assert dlg.trail_label.text() == "Permission denied for cloudtrail:DescribeTrails." and dlg.plan() is None


def _squeezed(dlg):
    """Wrapped labels shorter than their text needs, or form rows/controls shorter than their own minimum."""
    from PySide6.QtWidgets import QComboBox, QDateEdit, QLabel, QPushButton, QRadioButton
    bad = [w for w in dlg.findChildren(QLabel)
           if w.isVisible() and w.wordWrap() and w.height() < w.heightForWidth(w.width())]
    bad += [w for kind in (QComboBox, QDateEdit, QPushButton, QRadioButton) for w in dlg.findChildren(kind)
            if w.isVisible() and w.height() < w.minimumSizeHint().height()]
    return bad


def test_dialogs_fit_their_text_at_dark_150(qapp, qtbot):
    """Dark and 150 % font are Important: nothing in either job dialog may be squeezed or clipped."""
    from aws95.contracts.activity import AccessCheck, TrailLookup
    from aws95.kit import theme as th
    from aws95.kit.style import apply_theme
    apply_theme(qapp, th.DARK, font_scale=150)
    try:
        load = LoadHistoryDialog(ACCOUNTS)
        qtbot.addWidget(load)
        load.set_estimates(Result.ok(ESTIMATES))
        load.show()
        qtbot.waitExposed(load)
        assert _squeezed(load) == []
        table = load.estimates
        rows = sum(table.rowHeight(r) for r in range(table.rowCount()))
        assert table.viewport().height() >= rows                     # every estimate row is visible
        imp = ImportHistoryDialog(ACCOUNTS, today=TODAY)
        qtbot.addWidget(imp)
        imp.set_trails(Result.ok(TrailLookup(DEV, (TRAIL,), "This is an organisation trail. " * 4)))
        imp.show()
        qtbot.waitExposed(imp)
        imp.check_button.click()
        imp.set_check(Result.ok(AccessCheck(False, "The trail's log files are encrypted. " * 4, "[\n]")))
        qtbot.wait(50)
        assert _squeezed(imp) == []
    finally:
        apply_theme(qapp, th.LIGHT, font_scale=100)
