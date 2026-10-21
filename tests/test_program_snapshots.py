import re

import pytest
import shiboken6

from aws95.contracts.snapshots import DiffEntry

from aws95.kit.widgets.stateview import ViewState
from aws95.programs.snapshots import make_snapshots

from aws95.providers.demo import demo_provider_set


@pytest.fixture
def demo(program_env, qtbot):
    w, ctx = program_env(make_snapshots, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: len(w.history.visible_rows()) == 6)
    return w, ctx


def test_empty_mode(program_env, qtbot):
    w, _ = program_env(make_snapshots)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.history_view.state is ViewState.NOT_CONNECTED)


def test_history_and_health(demo, qtbot):
    w, _ = demo
    ids = [r["id"] for r in w.history.visible_rows()]
    assert ids == [103, 102, 101, 203, 202, 201]
    assert w.select_snapshot(203)
    statuses = {r["collector"]: r["status"] for r in w.health.visible_rows()}
    assert statuses["iam"] == "denied"


def test_compare_previous_opens_diff(demo, qtbot):
    w, _ = demo
    w.select_snapshot(203)
    dlg = w.compare_previous()
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: dlg.tabs.tabText(3) == "Excluded (3)")
    titles = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert titles == ["Added (0)", "Removed (0)", "Changed (1)", "Excluded (3)"]
    reasons = [r["reason"] for r in dlg.grids["excluded"].visible_rows()]
    assert all(reasons) and "collector failed: iam (AccessDenied)" in reasons
    changed = dlg.grids["changed"].visible_rows()[0]
    assert changed["deltas"] == "memory_mb: 512 → 1024"
    assert not dlg.isModal()


def test_compare_needs_previous(demo):
    w, _ = demo
    w.select_snapshot(101)
    assert w.compare_previous() is None
    assert "No earlier snapshot" in w.status.counts_label.text()


def test_diff_combo_reload(demo, qtbot):
    w, _ = demo
    w.select_snapshot(103)
    dlg = w.compare_previous()
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: dlg.tabs.tabText(2) != "Changed")
    dlg.from_combo.setCurrentIndex(dlg.from_combo.findData(101))
    qtbot.waitUntil(lambda: dlg.tabs.tabText(0) == "Added (2)")


def test_trends_metric_and_restated(demo, qtbot):
    w, _ = demo
    w.tabs.setCurrentIndex(1)
    qtbot.waitUntil(lambda: w.metric_combo.count() == 4)
    w.metric_combo.setCurrentIndex(w.metric_combo.findText("cost.mtd"))
    qtbot.waitUntil(lambda: w.trend_view.state is ViewState.CONTENT and
                    any(r["restated"] == "restated" for r in w.trend.visible_rows()))
    assert w.chart.point_count == 8
    money_cols = [c for c in w.trend.columns if c.key == "value"]
    assert money_cols[0].kind == "money"


def _letter(text):
    m = re.search(r"(?<!&)&([A-Za-z])", text.replace("&&", ""))
    return m.group(1).lower() if m else None


def test_no_duplicate_mnemonics(demo):
    w, _ = demo
    bar = [_letter(a.text()) for a in w.menu_bar.actions()]
    assert len(bar) == len(set(bar)), bar
    own = [_letter(a.text()) for a in w.program_menu.actions()]
    assert not (set(own) - {None}) & set(bar), own
    # always-visible window mnemonics (button, picker labels) clash with neither the bar nor each other
    from PySide6.QtWidgets import QLabel
    window = [_letter(w.compare_button.text())] + [_letter(lb.text()) for lb in w.findChildren(QLabel)]
    window = [x for x in window if x]
    assert len(window) == len(set(window)), window
    assert not set(window) & set(bar), window


def test_status_counts_describe_history(demo, qtbot):
    w, _ = demo
    qtbot.waitUntil(lambda: w.status.counts_label.text() == "6 of 6 snapshots")
    w.history.set_filter_text("status:partial")
    qtbot.waitUntil(lambda: w.status.counts_label.text() == "1 of 6 snapshots")


def test_refresh_clears_old_history_and_health(demo, qtbot):
    w, _ = demo
    w.select_snapshot(203)
    assert w.health.visible_rows()
    w.refresh()
    assert w.history.visible_rows() == [] and w.health.visible_rows() == []
    qtbot.waitUntil(lambda: len(w.history.visible_rows()) == 6)


def test_excluded_reason_never_blank(demo, qtbot):
    w, _ = demo
    w.select_snapshot(203)
    dlg = w.compare_previous()
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: dlg.view.state is ViewState.CONTENT and dlg.tabs.tabText(3) == "Excluded (3)")
    dlg._render((DiffEntry("excluded", "ec2", "r", "i-1", reason=None), DiffEntry("excluded", "ec2", "r", "i-2", reason="  ")))
    assert [r["reason"] for r in dlg.grids["excluded"].visible_rows()] == ["reason not reported"] * 2


def test_diff_clears_on_reload(demo, qtbot):
    w, _ = demo
    w.select_snapshot(103)
    dlg = w.compare_previous()
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: dlg.tabs.tabText(0) == "Added (1)" or dlg.tabs.tabText(0) == "Added (0)")
    dlg.from_combo.setCurrentIndex(dlg.from_combo.findData(101))
    assert dlg.tabs.tabText(0) == "Added" and dlg.grids["added"].visible_rows() == []
    qtbot.waitUntil(lambda: dlg.tabs.tabText(0) == "Added (2)")


def test_diff_window_closes_with_program(demo, qtbot):
    w, _ = demo
    w.select_snapshot(203)
    dlg = w.compare_previous()
    assert dlg.isVisible()
    w.close()
    assert not dlg.isVisible()


def test_trend_swap_deletes_old_grid_and_keeps_export(demo, qtbot, tmp_path):
    w, _ = demo
    w.tabs.setCurrentIndex(1)
    qtbot.waitUntil(lambda: w.metric_combo.count() == 4)
    first = w.trend
    w.metric_combo.setCurrentIndex(w.metric_combo.findText("s3.buckets"))
    qtbot.waitUntil(lambda: w.trend is not first)
    qtbot.waitUntil(lambda: not shiboken6.isValid(first))
    qtbot.waitUntil(lambda: len(w.trend.visible_rows()) == 16)
    assert w.trend.columns[3].kind == "number"
    assert w.export_grid() is w.trend
    out = tmp_path / "t.csv"
    w.ask_save_path = lambda *a: str(out)
    assert w.export() and "restated" in out.read_text()
    assert w.status.counts_label.text() == "Exported to " + str(out)
    w.metric_combo.setCurrentIndex(w.metric_combo.findText("cost.mtd"))
    qtbot.waitUntil(lambda: w.trend.columns[3].kind == "money")


def test_chart_follows_account_and_trends_status(demo, qtbot):
    w, _ = demo
    w.tabs.setCurrentIndex(1)
    qtbot.waitUntil(lambda: w.metric_combo.count() == 4)
    w.metric_combo.setCurrentIndex(0)
    qtbot.waitUntil(lambda: w.series_account_combo.count() == 2 and w.chart.point_count == 8)
    assert w.series_account_combo.currentText() == "111111111111"
    w.series_account_combo.setCurrentIndex(1)
    assert w.chart.point_count == 8
    qtbot.waitUntil(lambda: w.status.counts_label.text() == "16 of 16 points")
    w.tabs.setCurrentIndex(0)
    assert w.status.counts_label.text() == "6 of 6 snapshots"


def test_chart_latest_per_period_and_marks_restated(demo, qtbot):
    w, _ = demo
    w.tabs.setCurrentIndex(1)
    qtbot.waitUntil(lambda: w.metric_combo.count() == 4)
    w.metric_combo.setCurrentIndex(w.metric_combo.findText("cost.mtd"))
    qtbot.waitUntil(lambda: len(w._points) > 0 and w.chart.point_count > 0)
    mine = [p for p in w._points if p.dims.get("account") == "111111111111"]
    assert w.chart.point_count == len({p.period for p in mine})
    assert w.chart.marked_count == 1
    assert len(w.trend.visible_rows()) == len(w._points)         # the grid keeps every observation


def test_diff_invalid_order_says_so(demo, qtbot, monkeypatch):
    w, ctx = demo
    w.select_snapshot(103)
    dlg = w.compare_previous()
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: dlg.tabs.tabText(0).startswith("Added ("))
    calls = []
    real = ctx.providers.snapshots.diff
    monkeypatch.setattr(type(ctx.providers.snapshots), "diff",
                        lambda self, *a, **k: calls.append(a) or real(*a, **k))
    dlg.from_combo.setCurrentIndex(dlg.from_combo.findData(103))          # From == To
    assert dlg.view.state is ViewState.EMPTY and not calls
    dlg.from_combo.setCurrentIndex(dlg.from_combo.findData(103))
    dlg.to_combo.setCurrentIndex(dlg.to_combo.findData(101))              # reversed
    assert dlg.view.state is ViewState.EMPTY and not calls
    dlg.to_combo.setCurrentIndex(dlg.to_combo.findData(103))
    dlg.from_combo.setCurrentIndex(dlg.from_combo.findData(101))          # valid again
    qtbot.waitUntil(lambda: dlg.tabs.tabText(0) == "Added (2)")
    assert calls


def test_tab_change_retries_failed_history(program_env, qtbot):
    from aws95.contracts.common import Result
    w, _ = program_env(make_snapshots)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.history_view.state is ViewState.NOT_CONNECTED)
    calls = []

    def failing(scope, *, cancel=None):
        calls.append(1)
        return Result.error("boom")

    w.load(w.history_view, failing, w.scope, render=lambda rows: None)
    qtbot.waitUntil(lambda: len(calls) == 1, timeout=3000)
    qtbot.wait(30)
    w.tabs.setCurrentIndex(1)
    qtbot.wait(30)
    w.tabs.setCurrentIndex(0)                     # back to History: the failed load re-runs
    qtbot.waitUntil(lambda: len(calls) == 2, timeout=3000)
    qtbot.wait(30)
    w.tabs.setCurrentIndex(1)
    w.tabs.setCurrentIndex(0)                     # it failed again, so coming back retries exactly once more
    qtbot.waitUntil(lambda: len(calls) == 3, timeout=3000)
    qtbot.wait(50)
    assert len(calls) == 3
