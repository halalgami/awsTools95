"""M4 Task 3: the user-facing backlog carried over from M1-M3."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from aws95.contracts.common import Result, Scope, Status
from aws95.providers.demo import demo_provider_set, store

A, B = "111111111111", "222222222222"
NOW = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def ps(monkeypatch):
    monkeypatch.setattr(store, "now", lambda: NOW)
    return demo_provider_set()


# ----- 1. demo fidelity -----
def _sum(points):
    return sum((p.amount for p in points), Decimal(0))


def test_demo_billing_summary_honours_account_scope(ps):
    assert ps.billing.summary(Scope(accounts=(A,))).data.mtd == Decimal("301.15")
    assert ps.billing.summary(Scope(accounts=(B,))).data.mtd == Decimal("111.1915")
    assert ps.billing.summary(Scope(accounts=(A, B))).data.mtd == Decimal("412.3415")
    assert ps.billing.summary(Scope()).data.mtd == Decimal("412.3415")
    a, b = (ps.billing.summary(Scope(accounts=(x,))).data for x in (A, B))
    whole = ps.billing.summary(Scope()).data
    assert a.mtd + b.mtd == whole.mtd and a.forecast + b.forecast == whole.forecast
    assert a.last_month + b.last_month == whole.last_month


@pytest.mark.parametrize("dimension", ["service", "region", "usage_type"])
def test_demo_billing_breakdowns_are_per_account_and_exact(ps, dimension):
    mtd = {acct: ps.billing.summary(Scope(accounts=(acct,))).data.mtd for acct in (A, B)}
    for acct in (A, B):
        points = ps.billing.breakdown(Scope(accounts=(acct,)), dimension).data
        assert points and _sum(points) == mtd[acct], (dimension, acct)
    both = ps.billing.breakdown(Scope(), dimension).data
    assert _sum(both) == Decimal("412.3415")
    assert ps.billing.breakdown(Scope(accounts=(A, B)), dimension).data == both


def test_demo_billing_account_breakdown_and_trend_honour_scope(ps):
    pts = ps.billing.breakdown(Scope(accounts=(B,)), "account").data
    assert [(p.dims["account"], p.amount) for p in pts] == [(B, Decimal("111.1915"))]
    for gran, n in (("monthly", 13), ("daily", 30)):
        whole = ps.billing.trend(Scope(), gran).data
        a, b = (ps.billing.trend(Scope(accounts=(x,)), gran).data for x in (A, B))
        assert len(a) == len(b) == len(whole) == n
        assert [x.amount + y.amount for x, y in zip(a, b)] == [w.amount for w in whole]
        assert a != whole
    assert ps.billing.trend(Scope(accounts=(A,)), "monthly").data[-1].amount == Decimal("301.15")


def test_demo_billing_unknown_account_is_an_error_not_zero(ps):
    r = ps.billing.summary(Scope(accounts=("999999999999",)))
    assert r.status is Status.ERROR and r.is_demo


@pytest.mark.parametrize("call", [
    lambda b, sc: b.summary(sc),
    lambda b, sc: b.breakdown(sc, "service"),
    lambda b, sc: b.breakdown(sc, "account"),
    lambda b, sc: b.trend(sc, "monthly"),
    lambda b, sc: b.trend(sc, "daily"),
    lambda b, sc: b.free_tier(sc),
    lambda b, sc: b.uncovered_spend(sc),
], ids=["summary", "breakdown-service", "breakdown-account", "trend-monthly", "trend-daily", "free_tier", "uncovered"])
def test_every_demo_billing_method_errors_for_an_unknown_account(ps, call):
    r = call(ps.billing, Scope(accounts=("999999999999",)))
    assert r.status is Status.ERROR and r.is_demo and r.data is None
    known = call(ps.billing, Scope(accounts=(A,)))
    assert known.status is Status.OK and known.data


def test_demo_fleet_honours_region_scope(ps):
    whole = {(c.rule, c.account): c.open_count for c in ps.findings.fleet(Scope()).data}
    eu = {(c.rule, c.account): c.open_count for c in ps.findings.fleet(Scope(regions=("eu-west-1",))).data}
    assert eu[("sg-open-ssh", A)] == 1 and whole[("sg-open-ssh", B)] == 1 and eu[("sg-open-ssh", B)] == 0
    assert sum(eu.values()) < sum(whole.values())


def test_demo_insights_payload_is_derived_from_scoped_demo_data(ps):
    whole = ps.insights.preview_payload(Scope(), "insights").data
    one = ps.insights.preview_payload(Scope(accounts=(A,)), "insights").data
    assert whole["analysis"] == "insights" and whole["accounts_in_scope"] == 2 and one["accounts_in_scope"] == 1
    assert whole["cost_mtd_usd"] == "412.3415" and one["cost_mtd_usd"] == "301.15"
    assert whole["cost_last_month_usd"] == "598.42"
    open_ = ps.findings.list_findings(Scope(), "open").data.items
    assert sum(whole["open_findings_by_severity"].values()) == len(open_)
    assert sum(one["open_findings_by_severity"].values()) < len(open_)
    assert sum(whole["resource_counts"].values()) == 23 and sum(one["resource_counts"].values()) < 23
    assert whole["activity_events_30d"] == ps.activity.events(Scope()).data.total
    assert one["activity_events_30d"] < whole["activity_events_30d"]
    assert whole["regions_in_scope"] == 3
    assert whole["cost_scope"] == one["cost_scope"] == "accounts (all regions)"


# ----- 2. AI output sanitising -----
def test_insights_strips_bidi_and_controls(program_env, qtbot):
    import dataclasses

    from aws95.contracts.insights import InsightReport, InsightSection
    from aws95.programs.insights import make_insights

    ps = demo_provider_set()
    bidi = "".join(map(chr, [0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069]))
    ctl = "\x00\x01\x07\x1b\x7f\x85\x9f"

    class Fake:
        def preview_payload(self, scope, kind="insights", *, cancel=None):
            return ps.insights.preview_payload(scope, kind, cancel=cancel)

        def analyze(self, scope, kind="insights", payload=None, *, cancel=None):
            report = InsightReport(kind="insights", model=f"mo{bidi}del{ctl}-x", sections=(
                InsightSection(f"Ti{bidi}tle{ctl}", (f"bul{ctl}let{bidi} one", "keep\ttab\nand newline")),))
            return Result.ok(report, is_demo=True)

    w, _ = program_env(make_insights, dataclasses.replace(ps, insights=Fake()))
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: '"analysis": "insights"' in w.preview.toPlainText())
    w.consent_box.setChecked(True)
    w.analyze()
    qtbot.waitUntil(lambda: "Title" in w.results.toPlainText())
    text = w.results.toPlainText()
    assert text == "Title\n• bullet one\n• keep\ttab\nand newline"
    assert w.model_label.text() == "Model: model-x"


from aws95.core.sanitize import clean_model_text  # noqa: E402


def test_clean_model_text_keeps_newline_and_tab_only():
    assert clean_model_text("a\u202eb\x00c\x85d\te\nf\rg") == "abcd\te\nfg"


# ----- 3. shell close plays no per-window sounds -----
class _RecSound:
    def __init__(self):
        self.played = []

    def play(self, e):
        self.played.append(e)
        return True

    def preview(self, e):
        return True


def test_shell_close_plays_no_window_close_sounds_and_fills_no_bin(tmp_path, qapp, qtbot):
    from PySide6.QtCore import QSettings

    from aws95.core.settings import Settings
    from aws95.core.tasks import TaskRunner
    from aws95.programs import DEFAULT_MANIFESTS
    from aws95.providers.empty import empty_provider_set
    from aws95.shell.registry import Registry
    from aws95.shell.shell import ShellWindow

    runner, sound = TaskRunner(), _RecSound()
    win = ShellWindow(Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)), runner,
                      empty_provider_set(), sound, Registry(DEFAULT_MANIFESTS), idle_clock=lambda: 0.0)
    qtbot.addWidget(win)
    win.resize(1200, 800)
    win.show()
    for pid in ("resources", "billing", "findings"):
        win.open_program(pid)
    assert len(win.desktop.program_windows()) == 3
    sound.played.clear()
    desktop = win.desktop
    win.close()
    assert sound.played.count("window_close") == 0
    assert desktop.recycle.items() == ()
    runner.shutdown()


def test_single_window_close_still_sounds_and_recycles(tmp_path, qapp, qtbot):
    from PySide6.QtCore import QSettings

    from aws95.core.settings import Settings
    from aws95.core.tasks import TaskRunner
    from aws95.programs import DEFAULT_MANIFESTS
    from aws95.providers.empty import empty_provider_set
    from aws95.shell.registry import Registry
    from aws95.shell.shell import ShellWindow

    runner, sound = TaskRunner(), _RecSound()
    win = ShellWindow(Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)), runner,
                      empty_provider_set(), sound, Registry(DEFAULT_MANIFESTS), idle_clock=lambda: 0.0)
    qtbot.addWidget(win)
    win.show()
    w = win.open_program("billing")
    w.close()
    assert sound.played.count("window_close") == 1 and len(win.desktop.recycle.items()) == 1
    assert win.desktop.closing_all is False       # only the shell's own close raises the flag
    win.close()
    runner.shutdown()


# ----- 9. restart does not close the shell twice -----
def test_teardown_does_not_close_an_already_closed_window_again(qapp, monkeypatch):
    from PySide6.QtWidgets import QWidget

    import aws95.app as app_mod

    seen = []
    real = app_mod.close_top_level_windows
    monkeypatch.setattr(app_mod, "close_top_level_windows", lambda skip=(): (seen.append(tuple(skip)), real(skip))[1])
    closes = []

    class Win(QWidget):
        def closeEvent(self, event):  # noqa: N802
            closes.append(1)
            super().closeEvent(event)

    w = Win()
    w.show()
    app_mod._teardown(qapp, w)
    assert closes == [1] and seen == [(w,)]
    app_mod._teardown(qapp, None)
    assert seen[-1] == ()


# ----- 4. Resources primary_view -----
def test_resources_primary_view_is_the_grid_when_one_follows_the_tree(program_env, qtbot):
    from aws95.programs.resources import make_resources

    w, _ = program_env(make_resources, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.grid.model.rowCount() > 0)
    assert w.primary_view is w.grid_view
    assert w.status.freshness_label.text()


def test_resources_primary_view_stays_the_tree_when_no_grid_follows(program_env, qtbot):
    from aws95.kit.widgets.stateview import ViewState
    from aws95.programs.resources import make_resources

    w, _ = program_env(make_resources)           # empty providers: not connected, no grid load follows
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.tree_view.state is ViewState.NOT_CONNECTED)
    assert w.primary_view is w.tree_view
    assert w.grid_view.state is not ViewState.NOT_CONNECTED


# ----- 6. Activity Timeline select_principal pages like Accounts select_profile -----
def test_timeline_select_principal_beyond_the_first_batch(program_env, qtbot, monkeypatch):
    import dataclasses

    from aws95.contracts.activity import PrincipalSummary
    from aws95.kit.widgets.datagrid import RowTableModel
    from aws95.programs.activity import make_activity

    ps = demo_provider_set()

    class Many:
        def __getattr__(self, name):
            return getattr(ps.activity, name)

        def principals(self, scope, *, cancel=None):
            rows = tuple(PrincipalSummary("IAMUser", f"user-{i:03d}", True, 300 - i) for i in range(200))
            return Result.ok(rows, is_demo=True)

    monkeypatch.setattr(RowTableModel, "FETCH_BATCH", 50)
    w, _ = program_env(make_activity, dataclasses.replace(ps, activity=Many()))
    qtbot.addWidget(w)
    w.show()
    w.tabs.setCurrentWidget(w.timeline)
    grid = w.timeline.principals
    qtbot.waitUntil(lambda: len(grid.visible_rows()) == 200)
    assert grid.model.rowCount() < 200
    assert w.timeline.select_principal("user-199") is True
    assert grid.current_row()["name"] == "user-199"
    assert w.timeline.select_principal("nobody-here") is False


# ----- 7. charts are closed with their program -----
def test_activity_and_snapshots_charts_close_with_their_program(program_env, qtbot):
    from aws95.programs.activity import make_activity
    from aws95.programs.snapshots import make_snapshots

    for make, chart in ((make_activity, lambda w: w.overview.chart), (make_snapshots, lambda w: w.chart)):
        w, _ = program_env(make, demo_provider_set())
        qtbot.addWidget(w)
        w.show()
        c = chart(w)
        assert c.plotItem is not None
        assert w.close()
        assert c.plotItem is None, make.__name__


# ----- 10. DataGrid Return with no current row passes through -----
def test_datagrid_return_without_current_row_is_not_swallowed(qtbot):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from aws95.kit.widgets.datagrid import Column, DataGrid

    grid = DataGrid([Column("a", "A")])
    qtbot.addWidget(grid)
    grid.set_rows([])
    activated = []
    grid.row_activated.connect(activated.append)
    press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    assert grid.eventFilter(grid.view, press) is False and activated == []
    grid.set_rows([{"a": "x"}])
    grid.view.selectRow(0)
    assert grid.eventFilter(grid.view, press) is True and len(activated) == 1


# ----- 8. the cleanup fixture no longer swallows everything -----
def test_cleanup_fixture_catches_only_runtime_error():
    import inspect

    import conftest

    src = inspect.getsource(conftest._cleanup_toplevel_widgets)
    assert "except RuntimeError" in src and "except Exception" not in src


@pytest.mark.parametrize("cp", [0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x061C, 0x2060, 0xFEFF,
                                0x202A, 0x202E, 0x2066, 0x2069])
def test_clean_model_text_strips_invisible_format_characters(cp):
    # LRM 200E, RLM 200F, ALM 061C, ZWSP 200B, WJ 2060, BOM FEFF
    assert clean_model_text(f"a{chr(cp)}b") == "ab"


def test_clean_model_text_keeps_arabic_letters():
    word = "\u0645\u0631\u0627\u0628\u062d\u0629"     # the Arabic word for Murabaha
    assert clean_model_text(word) == word
    assert clean_model_text(f"x {word}{chr(0x200F)} y") == f"x {word} y"


def test_no_raw_invisible_or_bidi_characters_in_source():
    import pathlib
    import re
    bad = re.compile("[\u200b-\u200f\u061c\u2060\ufeff\u202a-\u202e\u2066-\u2069]")
    root = pathlib.Path(__file__).resolve().parent.parent
    hits = [f"{p.relative_to(root)}:{i}" for d in ("aws95", "tests", "tools") for p in (root / d).rglob("*.py")
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if bad.search(line)]
    assert hits == []
