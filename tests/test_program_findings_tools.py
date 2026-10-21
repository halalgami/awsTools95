from datetime import date

import pytest
from PySide6.QtCore import QSettings

from aws95.core.settings import Settings
from aws95.programs.findings import make_findings
from aws95.providers.demo import demo_provider_set
from aws95.providers.live import live_provider_set
from tests.store_seed import PINNED, ROSTER, seed_store


@pytest.fixture
def live(tmp_path, program_env, qtbot):
    store = seed_store(tmp_path / "aws95.db")
    settings = Settings(QSettings(str(tmp_path / "f.ini"), QSettings.Format.IniFormat))
    settings.set_roster(ROSTER)
    ps = live_provider_set(settings, store=store, clock=lambda: PINNED)
    w, ctx = program_env(make_findings, ps)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: bool(w.grid.visible_rows()), timeout=3000)
    yield w, ctx, store
    store.close()


def test_rules_editor_disables_and_resets(live, qtbot):
    w, _, store = live
    dlg = w.open_rules_dialog()
    qtbot.waitUntil(lambda: bool(dlg.table.visible_rows()), timeout=3000)
    dlg.select_rule("s3.no_lifecycle")
    dlg.enabled_box.setChecked(False)
    dlg.save_button.click()
    qtbot.waitUntil(lambda: dlg.status_label.text().startswith("Re-checked"), timeout=3000)
    assert store.rule_overrides()["s3.no_lifecycle"]["disabled"] is True
    qtbot.waitUntil(lambda: not [r for r in w.grid.visible_rows() if r["rule"] == "s3.no_lifecycle"], timeout=3000)
    dlg.reset_button.click()
    qtbot.waitUntil(lambda: store.rule_overrides() == {}, timeout=3000)


def test_rules_editor_shows_param_errors(live, qtbot):
    w, _, store = live
    dlg = w.open_rules_dialog()
    qtbot.waitUntil(lambda: bool(dlg.table.visible_rows()), timeout=3000)
    dlg.select_rule("ec2.sg_open_admin_port")
    dlg.params_table.item(0, 1).setText('"22"')
    dlg.save_button.click()
    qtbot.waitUntil(lambda: "ports" in dlg.error_label.text(), timeout=3000)
    dlg.params_table.item(0, 1).setText("[22,")
    dlg.save_button.click()
    assert "isn't valid JSON" in dlg.error_label.text() and store.rule_overrides() == {}


def test_tuned_column(live, qtbot):
    w, ctx, _ = live
    from aws95.contracts.common import Scope
    ctx.providers.findings.set_rule(Scope(), "iam.user_no_mfa", enabled=True, severity="low", params=None)
    w.refresh()
    qtbot.waitUntil(lambda: any(r["tuned"] == "tuned" for r in w.grid.visible_rows()), timeout=3000)


def test_seed_dialog(live, qtbot):
    w, _, store = live
    index = next(i for i, r in enumerate(w.grid.visible_rows()) if r["rule"] == "iam.user_no_mfa")
    w.grid.view.selectRow(index)
    dlg = w.open_seed_dialog()
    assert not dlg.remove_button.isVisible()
    dlg.date_edit.setDate(dlg.date_edit.date().addYears(1))           # not earlier → refused
    dlg.save_button.click()
    qtbot.waitUntil(lambda: "must be before" in dlg.error_label.text(), timeout=3000)
    from PySide6.QtCore import QDate
    dlg.date_edit.setDate(QDate(2026, 1, 1))
    dlg.note_edit.setText("Q2 audit")
    dlg.save_button.click()
    qtbot.waitUntil(lambda: any("(seeded)" in r["first_seen"] for r in w.grid.visible_rows()), timeout=3000)


def test_backfill_dialog_progress(live, qtbot):
    w, _, _ = live
    dlg = w.open_backfill_dialog()
    from PySide6.QtCore import QDate
    dlg.since_edit.setDate(QDate(2026, 1, 1))
    dlg.start_button.click()
    qtbot.waitUntil(lambda: dlg.progress_label.text().startswith("Backfilled 3 snapshots"), timeout=5000)
    assert dlg.start_button.isEnabled() and not dlg.cancel_button.isEnabled()


def test_seed_needs_a_selection(program_env, qtbot):
    w, _ = program_env(make_findings, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    assert w.open_seed_dialog() is None
