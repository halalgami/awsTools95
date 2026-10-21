import re

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QLabel

from aws95.kit.widgets.stateview import ViewState
from aws95.programs.activity.dialogs import EventDialog
from aws95.programs.activity import ActivityProgram, make_activity
from aws95.providers.demo import demo_provider_set


@pytest.fixture
def demo(program_env, qtbot):
    w, ctx = program_env(make_activity, demo_provider_set())
    qtbot.addWidget(w)
    w.set_range("90d")
    w.show()
    return w, ctx


def test_tab_order(demo):
    w, _ = demo
    assert [w.tabs.tabText(i) for i in range(w.tabs.count())] == ["Overview", "Timeline", "Watchlist", "Filter"]


def test_watchlist_sorted_by_risk_then_time(demo, qtbot):
    w, _ = demo
    w.tabs.setCurrentWidget(w.watchlist)
    qtbot.waitUntil(lambda: len(w.watchlist.grid.visible_rows()) > 0)
    rows = w.watchlist.grid.visible_rows()
    assert len(rows) == 53 and all(r["risk"] >= 1 for r in rows)
    keys = [(-r["risk"], -r["time"].timestamp()) for r in rows]
    assert keys == sorted(keys)
    assert rows[0]["risk"] == 3 and rows[0]["rule"] == "root-activity"
    assert w.status.counts_label.text() == "53 of 53 events"


def test_watchlist_return_opens_event(demo, qtbot):
    w, _ = demo
    w.tabs.setCurrentWidget(w.watchlist)
    qtbot.waitUntil(lambda: len(w.watchlist.grid.visible_rows()) > 0)
    w.watchlist.grid.view.selectRow(0)
    w.watchlist.grid.view.setFocus()
    QTest.keyClick(w.watchlist.grid.view, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: w.findChild(EventDialog) is not None)
    assert w.findChild(EventDialog).windowTitle() == f"Event {w.watchlist.grid.visible_rows()[0]['event_id']} [DEMO]"


def test_filter_human_and_service(demo, qtbot):
    w, _ = demo
    w.tabs.setCurrentWidget(w.filter_tab)
    f = w.filter_tab
    assert not f.export_csv_button.isEnabled()
    f.service_edit.setText("iam.amazonaws.com")
    f.human_box.setChecked(True)
    f.run_button.click()
    qtbot.waitUntil(lambda: len(f.grid.visible_rows()) > 0)
    rows = f.grid.visible_rows()
    assert all(r["service"] == "iam.amazonaws.com" and r["human"] == "Yes" for r in rows)
    assert f.export_csv_button.isEnabled() and f.export_md_button.isEnabled()


def test_filter_no_match_is_empty_not_error(demo, qtbot):
    w, _ = demo
    w.tabs.setCurrentWidget(w.filter_tab)
    f = w.filter_tab
    f.principal_edit.setText("nobody-at-all")
    f.run_button.click()
    qtbot.waitUntil(lambda: f.view.state is ViewState.EMPTY and f.view._body.text() == "Nothing here.")
    assert not f.export_csv_button.isEnabled()


def test_filter_clears_old_results_when_run_starts(demo, qtbot):
    w, _ = demo
    w.tabs.setCurrentWidget(w.filter_tab)
    f = w.filter_tab
    f.risk_box.setChecked(True)
    f.run_button.click()
    qtbot.waitUntil(lambda: len(f.grid.visible_rows()) > 0)
    f.principal_edit.setText("nobody-at-all")
    f.run_button.click()
    assert f.grid.visible_rows() == [] and not f.export_csv_button.isEnabled()


def test_filter_does_not_rerun_on_range_change(demo, qtbot):
    w, _ = demo
    w.tabs.setCurrentWidget(w.filter_tab)
    f = w.filter_tab
    f.risk_box.setChecked(True)
    f.run_button.click()
    qtbot.waitUntil(lambda: len(f.grid.visible_rows()) > 0)
    before = f.grid.visible_rows()
    w.set_range("24h")
    assert not f.dirty
    assert "press Run" in w.status.counts_label.text()
    assert f.grid.visible_rows() == before
    assert not f.export_csv_button.isEnabled() and not f.export_md_button.isEnabled()   # stale: never exportable
    assert f.export_csv() is False and f.export_markdown() is False
    w.tabs.setCurrentWidget(w.watchlist)
    w.tabs.setCurrentWidget(f)
    assert "press Run" in w.status.counts_label.text()
    f.run_button.click()
    qtbot.waitUntil(lambda: "press Run" not in w.status.counts_label.text())
    qtbot.waitUntil(lambda: f.export_csv_button.isEnabled() and f.export_md_button.isEnabled())


def test_stale_filter_results_never_export_via_file_menu(demo, qtbot, tmp_path, monkeypatch):
    """File > Export / Ctrl+E on the Filter tab goes through the same provenance guard as its buttons (M3-R16)."""
    w, _ = demo
    w.tabs.setCurrentWidget(w.filter_tab)
    f = w.filter_tab
    out = tmp_path / "stale.csv"
    monkeypatch.setattr(ActivityProgram, "ask_save_path", staticmethod(lambda *a: str(out)))
    assert w.export() is False and not out.exists()         # nothing run yet: no provenance
    f.risk_box.setChecked(True)
    f.run_button.click()
    qtbot.waitUntil(lambda: len(f.grid.visible_rows()) > 0 and f.export_csv_button.isEnabled())
    w.set_range("24h")
    assert f.export_grid() is None
    assert w.export() is False and not out.exists()
    f.run_button.click()
    qtbot.waitUntil(lambda: f.export_csv_button.isEnabled())
    assert w.export() is True
    assert out.read_text().startswith("Time (UTC),Risk,Rule")


def test_fresh_filter_tab_has_no_stale_note(demo, qtbot):
    w, _ = demo
    w.refresh()
    w.tabs.setCurrentWidget(w.filter_tab)
    assert not w.filter_tab.stale and w.status.counts_label.text() == ""
    assert w.filter_tab.view._body.text() == "Set any filters and press Run."


def test_markdown_carries_provenance_of_the_run(demo, qtbot, tmp_path, monkeypatch):
    # The range is relative to the clock and the export prints it to the second: with the real clock, a second
    # boundary between the run and the assertion made the two ranges differ (a rare full-suite flake). Pin it.
    from datetime import datetime, timezone
    pinned = datetime.now(timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(ActivityProgram, "clock", staticmethod(lambda: pinned))
    w, _ = demo
    w.tabs.setCurrentWidget(w.filter_tab)
    f = w.filter_tab
    w.scope_bar.set_account("111111111111")
    f.service_edit.setText("iam.amazonaws.com")
    f.risk_box.setChecked(True)
    f.run_button.click()
    qtbot.waitUntil(lambda: len(f.grid.visible_rows()) > 0 and f.export_md_button.isEnabled())
    tr = w.time_range()
    # controls change after the run; the export must still describe the run
    f.service_edit.setText("changed.amazonaws.com")
    f.risk_box.setChecked(False)
    md = tmp_path / "p.md"
    monkeypatch.setattr(ActivityProgram, "ask_save_path", staticmethod(lambda *a: str(md)))
    assert f.export_markdown()
    text = md.read_text()
    head = text.split("| Time")[0]
    assert "- Service: iam.amazonaws.com" in head and "changed.amazonaws" not in text
    assert "- Risky only: risk >= 1" in head
    assert f"- Range (UTC): {tr.start:%Y-%m-%d %H:%M:%S} -> {tr.end:%Y-%m-%d %H:%M:%S}" in head
    assert "- Accounts: 111111111111" in head and "- Regions: All regions" in head
    assert "- Result status: ok" in head
    csv = tmp_path / "p.csv"
    monkeypatch.setattr(ActivityProgram, "ask_save_path", staticmethod(lambda *a: str(csv)))
    assert f.export_csv()
    assert csv.read_text().startswith("Time (UTC),Risk,Rule")


def test_time_column_not_elided(demo, qtbot):
    w, _ = demo
    w.tabs.setCurrentWidget(w.watchlist)
    qtbot.waitUntil(lambda: len(w.watchlist.grid.visible_rows()) > 0)
    view = w.watchlist.grid.view
    assert view.columnWidth(0) >= view.fontMetrics().horizontalAdvance("2026-10-03 12:34") + 12


def test_filter_exports(demo, qtbot, tmp_path, monkeypatch):
    w, ctx = demo
    w.tabs.setCurrentWidget(w.filter_tab)
    f = w.filter_tab
    f.risk_box.setChecked(True)
    f.run_button.click()
    qtbot.waitUntil(lambda: len(f.grid.visible_rows()) > 0)
    md, csv_path = tmp_path / "f.md", tmp_path / "f.csv"
    paths = iter([str(md), str(csv_path)])
    monkeypatch.setattr(ActivityProgram, "ask_save_path", staticmethod(lambda *a: next(paths)))
    assert f.export_markdown() and f.export_csv()
    assert md.read_text().startswith("# Activity filter")
    assert csv_path.read_text().splitlines()[0].startswith("Time (UTC),Risk,Rule")
    assert ctx.settings.recent_documents()[:2] == (str(csv_path), str(md))


def test_filter_export_failure_reports_in_status_bar(demo, qtbot, tmp_path, monkeypatch):
    w, ctx = demo
    w.tabs.setCurrentWidget(w.filter_tab)
    f = w.filter_tab
    f.risk_box.setChecked(True)
    f.run_button.click()
    qtbot.waitUntil(lambda: len(f.grid.visible_rows()) > 0)
    bad = str(tmp_path / "missing-dir" / "f.csv")
    monkeypatch.setattr(ActivityProgram, "ask_save_path", staticmethod(lambda *a: bad))
    assert f.export_csv() is False
    assert "Export failed" in w.status.counts_label.text()
    monkeypatch.setattr(ActivityProgram, "ask_save_path", staticmethod(lambda *a: None))
    assert f.export_markdown() is False


def test_history_dialog(demo, qtbot):
    w, _ = demo
    dlg = w.open_history()
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: "Denied change attempts: covered." in dlg.status_label.text())
    text = dlg.status_label.text()
    assert "111111111111: looked up" in text and "imported" in text and not dlg.isModal()
    assert "111111111111 · me-south-1:" in text


def _letter(text):
    m = re.search(r"(?<!&)&([A-Za-z])", text.replace("&&", ""))
    return m.group(1).lower() if m else None


def test_mnemonics_unique_across_all_tabs(demo):
    w, _ = demo
    bar = [_letter(a.text()) for a in w.menu_bar.actions()]
    own = [x for x in (_letter(a.text()) for a in w.program_menu.actions()) if x]
    assert len(own) == len(set(own)), own
    assert not set(own) & set(bar), own
    texts = [lb.text() for lb in w.findChildren(QLabel)] + [b.text() for b in w.findChildren(QAbstractButton)]
    window = [x for x in map(_letter, texts) if x]
    assert len(window) == len(set(window)), window
    assert not set(window) & set(bar), window
    assert {"p", "s", "i", "o", "m", "r", "u", "x", "c"} <= set(window)
