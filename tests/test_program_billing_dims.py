from aws95.programs.billing import make_billing
from aws95.programs.fleet import make_fleet
from aws95.providers.demo import demo_provider_set


def open_tab(program_env, qtbot, key):
    w, ctx = program_env(make_billing, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    w.select_tab(key)
    qtbot.waitUntil(lambda: bool(w.pages[key].grid.visible_rows()), timeout=3000)
    return w, ctx


def test_region_and_usage_tabs_show_accounts(program_env, qtbot):
    for key in ("region", "usage_type"):
        w, _ = open_tab(program_env, qtbot, key)
        rows = w.pages[key].grid.visible_rows()
        assert all(r["account"] and r[key] for r in rows)


def test_uncovered_tab_shows_reason_and_navigates(program_env, qtbot):
    w, ctx = open_tab(program_env, qtbot, "uncovered")
    rows = w.pages["uncovered"].grid.visible_rows()
    assert all(r["reason"] for r in rows)
    w._open_service(rows[0])
    assert ctx.shell.calls[-1][0] == "navigate"


def test_fleet_missing_cost_tooltip_names_the_reason(program_env, qtbot):
    from aws95.contracts.common import Result, Status
    import dataclasses
    ps = demo_provider_set()

    class Billing:
        def breakdown(self, scope, dimension="service", *, cancel=None):
            from aws95.contracts.billing import CostPoint
            from decimal import Decimal
            return Result(Status.PARTIAL, (CostPoint("2026-10", Decimal("1"), "USD", {"account": "111111111111"}),),
                          None, None, "Billing isn't enabled for: 222222222222.")

        def __getattr__(self, name):
            return getattr(ps.billing, name)
    w, _ = program_env(make_fleet, dataclasses.replace(ps, billing=Billing()))
    qtbot.addWidget(w)
    w.show()
    def tips():
        row = w.matrix.rowCount() - 1
        return [w.matrix.item(row, c).toolTip() for c in range(w.matrix.columnCount()) if w.matrix.item(row, c)]
    qtbot.waitUntil(lambda: any("Billing isn't enabled for" in t for t in tips()), timeout=3000)


def test_account_and_reason_columns_are_never_elided(program_env, qtbot):
    w, _ = open_tab(program_env, qtbot, "uncovered")
    view = w.pages["uncovered"].grid.view
    fm = view.fontMetrics()
    assert view.columnWidth(0) >= fm.horizontalAdvance("222222222222")
    assert view.columnWidth(2) >= fm.horizontalAdvance("Not a resource service")
