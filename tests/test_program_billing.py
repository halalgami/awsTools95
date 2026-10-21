import dataclasses
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from aws95.contracts.billing import CostPoint, CostSummary
from aws95.contracts.common import Result
from aws95.kit.widgets.stateview import ViewState
from aws95.programs.billing import BillingProgram, make_billing
from aws95.providers.demo import demo_provider_set

KEYS = ["service", "account", "region", "usage_type", "trend", "daily", "free_tier", "uncovered"]


@pytest.fixture
def demo(program_env, qtbot):
    w, ctx = program_env(make_billing, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.mtd_label.text() != "—" and len(w.pages["service"].grid.visible_rows()) == 10)
    return w, ctx


def test_empty_mode(program_env, qtbot):
    w, _ = program_env(make_billing)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.kpi_view.state is ViewState.NOT_CONNECTED)
    qtbot.waitUntil(lambda: w.current_page().view.state is ViewState.NOT_CONNECTED)


def test_tabs_in_spec_order(demo):
    w, _ = demo
    assert list(w.pages) == KEYS
    assert [w.tabs.tabText(i) for i in range(w.tabs.count())] == [
        "By service", "By account", "By region", "By usage type", "Trend", "Daily", "Free tier", "Uncovered spend"]


def test_money_is_exact_everywhere(demo, tmp_path, monkeypatch):
    w, _ = demo
    assert w.mtd_label.text() == "412.3415 USD"
    assert w.forecast_label.text() == "655.10 USD" and w.last_month_label.text() == "598.42 USD"
    rows = {r["service"]: r for r in w.pages["service"].grid.visible_rows()}
    s3 = rows["Amazon Simple Storage Service"]
    assert s3["amount"] == Decimal("38.912")
    col = [c.key for c in w.pages["service"].grid.columns].index("amount")
    idx = [r["service"] for r in w.pages["service"].grid.visible_rows()].index("Amazon Simple Storage Service")
    assert w.pages["service"].grid.model.data(w.pages["service"].grid.model.index(idx, col)) == "38.912"
    target = tmp_path / "b.csv"
    monkeypatch.setattr(BillingProgram, "ask_save_path", staticmethod(lambda *a: str(target)))
    assert w.export()
    assert "38.912" in target.read_text() and "72.917" in target.read_text()


def test_huge_amount_not_rounded(program_env, qtbot):
    class Big:
        def summary(self, scope, *, cancel=None):
            return Result.ok(CostSummary(Decimal("12345678901.123456"), None, Decimal("0.001"), "USD",
                                         datetime(2026, 10, 2, tzinfo=timezone.utc)))

        def __getattr__(self, name):
            return getattr(demo_provider_set().billing, name)

    w, _ = program_env(make_billing, dataclasses.replace(demo_provider_set(), billing=Big()))
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.mtd_label.text() != "—")
    assert w.mtd_label.text() == "12,345,678,901.123456 USD"
    assert w.forecast_label.text() == "—" and w.last_month_label.text() == "0.001 USD"


def test_pages_load_lazily_and_charts_plot(demo, qtbot):
    w, _ = demo
    assert w.pages["trend"].dirty                  # never shown yet, so never loaded
    w.select_tab("trend")
    qtbot.waitUntil(lambda: w.pages["trend"].chart.point_count == 13)
    w.select_tab("daily")
    qtbot.waitUntil(lambda: w.pages["daily"].chart is not None and w.pages["daily"].chart.point_count == 30)


def test_free_tier_has_no_percent_column(demo, qtbot):
    w, _ = demo
    w.select_tab("free_tier")
    qtbot.waitUntil(lambda: bool(w.pages["free_tier"].grid.visible_rows()))
    keys = [c.key for c in w.pages["free_tier"].grid.columns]
    assert keys == ["service", "metric", "actual", "limit", "forecast", "unit", "period"]


def test_double_click_service_navigates(demo, qtbot):
    w, ctx = demo
    grid = w.pages["service"].grid
    grid.row_activated.emit(grid.visible_rows()[0])
    assert ctx.shell.calls[-1] == ("navigate", ("resources", {"service": grid.visible_rows()[0]["service"]}))


def test_refresh_marks_pages_dirty(demo, qtbot):
    w, _ = demo
    w.select_tab("region")
    qtbot.waitUntil(lambda: bool(w.pages["region"].grid.visible_rows()))
    w.refresh()
    assert w.pages["account"].dirty
    assert w.mtd_label.text() == "—" and not w.pages["region"].grid.visible_rows()   # M3-R5: cleared at load start
    qtbot.waitUntil(lambda: bool(w.pages["region"].grid.visible_rows()) and w.mtd_label.text() != "—")

def test_uncovered_and_charts(demo, qtbot):
    w, _ = demo
    assert w.pages["trend"].chart is not None and w.pages["service"].chart.bar_count == 10
    w.select_tab("uncovered")
    qtbot.waitUntil(lambda: bool(w.pages["uncovered"].grid.visible_rows()))      # services the map leaves uncovered
    services = {r["service"] for r in w.pages["uncovered"].grid.visible_rows()}
    assert "Tax" in services and "Amazon Route 53" not in services                  # a collector covers it now
    grid = w.pages["uncovered"].grid
    row = grid.visible_rows()[0]
    grid.row_activated.emit(row)
    assert w.ctx.shell.calls[-1] == ("navigate", ("resources", {"service": row["service"]}))


def test_reload_error_clears_stale_rows_and_chart(program_env, qtbot, tmp_path, monkeypatch):
    class Flaky:
        fail = False

        def breakdown(self, scope, dimension="service", *, cancel=None):
            return Result.error("boom") if self.fail else demo_provider_set().billing.breakdown(scope, dimension)

        def __getattr__(self, name):
            return getattr(demo_provider_set().billing, name)

    flaky = Flaky()
    w, _ = program_env(make_billing, dataclasses.replace(demo_provider_set(), billing=flaky))
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: bool(w.pages["service"].grid.visible_rows()))
    assert w.pages["service"].chart.bar_count == 10
    flaky.fail = True
    w.refresh()
    qtbot.waitUntil(lambda: w.pages["service"].view.state is ViewState.ERROR)
    assert not w.pages["service"].grid.visible_rows() and w.pages["service"].chart.bar_count == 0
    monkeypatch.setattr(BillingProgram, "ask_save_path", staticmethod(lambda *a: str(tmp_path / "x.csv")))
    assert not w.export() and not (tmp_path / "x.csv").exists()


def test_no_duplicate_mnemonics(demo):
    import re
    w, _ = demo

    def letter(text):
        m = re.search(r"(?<!&)&([A-Za-z])", text.replace("&&", ""))
        return m.group(1).lower() if m else None

    bar = [letter(a.text()) for a in w.menu_bar.actions()]
    assert len(bar) == len(set(bar)), bar
    own = [letter(a.text()) for a in w.program_menu.actions()]
    assert None not in own and len(own) == len(set(own)), own
    assert not set(own) & set(bar), own


def test_bar_labels_are_ordinals_matching_the_hash_column(demo):
    w, _ = demo
    page = w.pages["service"]
    rows = page.grid.visible_rows()
    assert [c.key for c in page.grid.columns][:2] == ["n", "service"]
    assert list(page.chart.labels) == [str(r["n"]) for r in rows] == [str(i) for i in range(1, 11)]


def _trend_with_bad_period(program_env, qtbot):
    class Bad:
        def trend(self, scope, granularity="monthly", *, cancel=None):
            return Result.ok((CostPoint("2026/10", Decimal("1.5"), "USD"),))


        def __getattr__(self, name):
            return getattr(demo_provider_set().billing, name)

    w, _ = program_env(make_billing, dataclasses.replace(demo_provider_set(), billing=Bad()))
    qtbot.addWidget(w)
    w.show()
    return w


def test_render_failure_leaves_nothing_exportable(program_env, qtbot, tmp_path, monkeypatch):
    w = _trend_with_bad_period(program_env, qtbot)
    qtbot.waitUntil(lambda: w.mtd_label.text() != "—")
    w.select_tab("trend")
    qtbot.waitUntil(lambda: w.pages["trend"].view.state is ViewState.ERROR)
    assert not w.pages["trend"].grid.visible_rows() and w.pages["trend"].chart.point_count == 0
    monkeypatch.setattr(BillingProgram, "ask_save_path", staticmethod(lambda *a: str(tmp_path / "x.csv")))
    assert not w.export() and not (tmp_path / "x.csv").exists()


def test_status_count_follows_visible_tab(demo, qtbot):
    w, _ = demo
    assert w.status.counts_label.text() == "10 rows"
    w.select_tab("trend")
    qtbot.waitUntil(lambda: w.status.counts_label.text() == "13 rows")
    w.select_tab("service")
    assert w.status.counts_label.text() == "10 rows"
