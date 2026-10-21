import re
from datetime import date, timedelta

import pytest
from PySide6.QtCore import Qt

from aws95.kit.widgets.stateview import ViewState
from aws95.programs.findings import make_findings
from aws95.providers.demo import demo_provider_set


@pytest.fixture
def demo(program_env, qtbot):
    w, ctx = program_env(make_findings, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: len(w.grid.visible_rows()) == 13)    # StateView starts in CONTENT: wait for rows
    return w, ctx


def test_empty_mode(program_env, qtbot):
    w, _ = program_env(make_findings)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.view.state is ViewState.NOT_CONNECTED)


def test_open_findings_columns_and_seeded_marker(demo):
    w, _ = demo
    rows = w.grid.visible_rows()
    assert len(rows) == 13 and {r["severity"] for r in rows} == {"critical", "high", "medium", "low"}
    seeded = [r for r in rows if r["rule"] == "cloudtrail-off"][0]
    assert seeded["first_seen"].endswith(" (seeded)") and seeded["age"] == 300
    assert w.severity_buttons["critical"].text() == "Critical (3)"


def test_severity_chip_filters(demo):
    w, _ = demo
    w.severity_buttons["critical"].click()
    assert all(r["severity"] != "critical" for r in w.grid.visible_rows())
    assert len(w.grid.visible_rows()) == 10
    w.severity_buttons["critical"].click()
    assert len(w.grid.visible_rows()) == 13


def test_state_tabs(demo, qtbot):
    w, _ = demo
    w.set_state("accepted")
    qtbot.waitUntil(lambda: w.current_state() == "accepted" and w.view.state is ViewState.CONTENT
                    and len(w.grid.visible_rows()) == 2)
    reasons = {r["reason"] for r in w.grid.visible_rows()}
    assert "Migration scheduled for Q4" in reasons
    never = [r for r in w.grid.visible_rows() if r["rid"] == "break-glass"][0]
    assert never["muted_until"] == "never"
    w.set_state("resolved")
    qtbot.waitUntil(lambda: len(w.grid.visible_rows()) == 2 and w.current_state() == "resolved")


def test_navigation_from_fleet_sets_filter(demo, qtbot):
    w, _ = demo
    w.apply_navigation({"rule": "sg-open-ssh", "account": "222222222222"})
    qtbot.waitUntil(lambda: w.current_state() == "open" and w.grid.filter_edit.text() ==
                    "rule:sg-open-ssh account:222222222222" and len(w.grid.visible_rows()) == 1
                    and w.view.state is ViewState.CONTENT)


def test_row_activation_opens_resources(demo):
    w, ctx = demo
    row = [r for r in w.grid.visible_rows() if r["rid"] == "demo-orders-db"][0]
    w.grid.row_activated.emit(row)
    assert ctx.shell.calls[-1] == ("navigate", ("resources", {"rtype": "rds.instance", "filter": "rid:demo-orders-db"}))


def test_accept_dialog_needs_selection_and_cannot_save_in_demo(demo, qtbot):
    w, _ = demo
    assert w.open_mute_dialog() is None
    assert "Select a finding" in w.status.counts_label.text()
    w.grid.view.selectRow(0)
    dlg = w.open_mute_dialog()
    qtbot.addWidget(dlg)
    assert dlg.isVisible() and not dlg.isModal()
    assert dlg.scope_resource.isChecked()
    assert dlg.expiry_edit.date().toPython() == date.today() + timedelta(days=90)
    assert not dlg.save_button.isEnabled() and "demo mode" in dlg.save_button.toolTip()
    qtbot.keyClick(dlg, Qt.Key.Key_Escape)
    assert not dlg.isVisible()


def test_rules_dialog_lists_rules(demo, qtbot):
    w, _ = demo
    dlg = w.open_rules_dialog()
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: len(dlg.table.visible_rows()) == 12)
    rows = dlg.table.visible_rows()
    assert [r["enabled"] for r in rows if r["id"] == "budget-missing"] == ["No"]
    assert not dlg.save_button.isEnabled()


def test_seed_and_backfill_dialogs(demo, qtbot):
    w, _ = demo
    w.grid.view.selectRow(0)                       # findings-tools: Seed opens from the selected finding
    seed, backfill = w.open_seed_dialog(), w.open_backfill_dialog()
    for dlg in (seed, backfill):
        qtbot.addWidget(dlg)
        assert dlg.isVisible()
    assert seed.save_button.isEnabled() and backfill.start_button.isEnabled()


def _letter(text):
    m = re.search(r"(?<!&)&([A-Za-z])", text.replace("&&", ""))
    return m.group(1).lower() if m else None


def test_no_duplicate_mnemonics(demo, qtbot):
    w, _ = demo
    bar = [_letter(a.text()) for a in w.menu_bar.actions()]
    assert len(bar) == len(set(bar)), bar
    own = [_letter(a.text()) for a in w.program_menu.actions()]
    assert not (set(own) - {None}) & set(bar), own
    assert len(own) == len(set(own)), own


def test_dialog_mnemonics_are_unique(demo, qtbot):
    from PySide6.QtWidgets import QAbstractButton, QLabel
    w, _ = demo
    w.grid.view.selectRow(0)
    for dlg in (w.open_mute_dialog(), w.open_seed_dialog(), w.open_backfill_dialog()):
        qtbot.addWidget(dlg)
        texts = [b.text() for b in dlg.findChildren(QAbstractButton)] + [lb.text() for lb in dlg.findChildren(QLabel)]
        letters = [_letter(t) for t in texts if _letter(t)]
        assert len(letters) == len(set(letters)), (type(dlg).__name__, letters)


def test_refresh_clears_stale_rows_and_chips_on_failure(demo, qtbot):
    w, ctx = demo
    from aws95.contracts.common import Result

    class Boom:
        def list_findings(self, *a, **k):
            return Result.error("boom")

    object.__setattr__(ctx.providers, "findings", Boom())
    w.refresh()
    qtbot.waitUntil(lambda: w.view.state is ViewState.ERROR)
    assert w.grid.visible_rows() == []
    assert w.severity_buttons["critical"].text() == "Critical"
    assert w.status.counts_label.text() == ""
    assert w.export() is False


# ----- fix round 1 -----
from datetime import datetime  # noqa: E402

from aws95.contracts.common import Coverage, Page, Result, Status  # noqa: E402
from aws95.contracts.findings import Finding  # noqa: E402


def _finding(i, severity="high", state="open"):
    return Finding("111111111111", "rule-x", "ec2.instance", "us-east-1", f"i-{i}", severity,
                   datetime(2026, 1, 1), False, state, 5)


class PagedFindings:
    def __init__(self, pages, statuses=None):
        self.pages, self.statuses = pages, statuses or {}

    def list_findings(self, scope, state="open", cursor=None, *, cancel=None):
        i = int(cursor or 0)
        nxt = str(i + 1) if i + 1 < len(self.pages) else None
        cov = Coverage(("r",), (), 1) if self.statuses.get(i) is Status.PARTIAL else None
        return Result(self.statuses.get(i, Status.OK), Page(tuple(self.pages[i]), nxt), cov)


def _with_provider(program_env, qtbot, fake):
    w, ctx = program_env(make_findings, demo_provider_set())
    object.__setattr__(ctx.providers, "findings", fake)
    qtbot.addWidget(w)
    w.show()
    return w, ctx


def test_two_page_provider_shows_all_findings(program_env, qtbot):
    fake = PagedFindings([[_finding(1, "critical"), _finding(2)], [_finding(3, "critical"), _finding(4, "low")]])
    w, _ = _with_provider(program_env, qtbot, fake)
    qtbot.waitUntil(lambda: len(w.grid.visible_rows()) == 4)
    assert w.severity_buttons["critical"].text() == "Critical (2)"
    assert w.severity_buttons["low"].text() == "Low (1)"


def test_partial_second_page_gives_banner(program_env, qtbot):
    fake = PagedFindings([[_finding(1)], [_finding(2)]], {1: Status.PARTIAL})
    w, _ = _with_provider(program_env, qtbot, fake)
    qtbot.waitUntil(lambda: len(w.grid.visible_rows()) == 2)
    assert w.view.state is ViewState.CONTENT and w.view.banner_text


def test_manual_tab_change_clears_navigation_filter(demo, qtbot):
    w, _ = demo
    w.apply_navigation({"rule": "sg-open-ssh", "account": "222222222222"})
    qtbot.waitUntil(lambda: len(w.grid.visible_rows()) == 1)
    w.state_tabs.setCurrentIndex(2)
    qtbot.waitUntil(lambda: w.current_state() == "accepted" and len(w.grid.visible_rows()) == 2)
    assert w.grid.filter_edit.text() == ""


def test_user_typed_filter_survives_tab_change(demo, qtbot):
    w, _ = demo
    w.apply_navigation({"rule": "sg-open-ssh"})
    w.grid.set_filter_text("severity:high")
    w.state_tabs.setCurrentIndex(1)
    qtbot.waitUntil(lambda: w.current_state() == "resolved")
    assert w.grid.filter_edit.text() == "severity:high"


def test_empty_result_after_content_clears_rows_and_chips(demo, qtbot):
    w, ctx = demo
    object.__setattr__(ctx.providers, "findings", PagedFindings([[]]))
    w.refresh()
    qtbot.waitUntil(lambda: w.view.state is ViewState.EMPTY)
    assert w.grid.visible_rows() == [] and w.severity_buttons["critical"].text() == "Critical"
    assert w.export() is False


def test_rapid_tab_switching_ends_on_last_tab(demo, qtbot):
    w, _ = demo
    for i in (1, 2, 0, 2):
        w.state_tabs.setCurrentIndex(i)
    qtbot.waitUntil(lambda: w.view.state is ViewState.CONTENT and len(w.grid.visible_rows()) == 2)
    qtbot.wait(100)
    assert w.current_state() == "accepted"
    assert {r["rid"] for r in w.grid.visible_rows()} >= {"break-glass"}


def test_navigation_during_inflight_load(program_env, qtbot):
    w, _ = program_env(make_findings, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    w.refresh()                                   # a load is in flight
    w.apply_navigation({"rule": "sg-open-ssh", "account": "222222222222"})
    qtbot.waitUntil(lambda: len(w.grid.visible_rows()) == 1)
    qtbot.wait(100)
    assert len(w.grid.visible_rows()) == 1
    assert w.grid.filter_edit.text() == "rule:sg-open-ssh account:222222222222"


def test_all_chips_off_reports_hidden_findings(demo):
    w, _ = demo
    for b in w.severity_buttons.values():
        b.click()
    assert w.grid.visible_rows() == []
    assert w.status.counts_label.text() == "13 findings hidden by severity filter"


def test_dialog_focus_goes_to_first_input(demo, qtbot):
    w, _ = demo
    w.grid.view.selectRow(0)
    dlg = w.open_seed_dialog()
    qtbot.addWidget(dlg)
    assert dlg.focus_widget is dlg.date_edit


def test_closing_program_closes_rules_dialog(demo, qtbot):
    from shiboken6 import isValid
    w, _ = demo
    dlg = w.open_rules_dialog()
    w.close()
    qtbot.waitUntil(lambda: not isValid(dlg) or not dlg.isVisible())
