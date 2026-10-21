from aws95.programs.activity import make_activity
from aws95.programs.billing import make_billing
from aws95.programs.findings import make_findings
from aws95.providers.demo import demo_provider_set


def test_findings_navigation_quotes_the_target(program_env, qtbot):
    """Review Focus 4."""
    w, _ = program_env(make_findings, demo_provider_set())
    qtbot.addWidget(w)
    w.apply_navigation({"rule": "s3.no_lifecycle", "target": "s3.bucket:my logs:2026"})
    assert w.grid.filter_edit.text() == "rule:s3.no_lifecycle target:'s3.bucket:my logs:2026'"
    assert w.grid.model.set_filter(w.grid.filter_edit.text()) is True       # it parses


def test_activity_navigation_runs_the_principal_filter(program_env, qtbot):
    w, _ = program_env(make_activity, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    w.apply_navigation({"principal": "alice"})
    assert w.tabs.currentWidget() is w.filter_tab and w.filter_tab.principal_edit.text() == "alice"
    assert w.filter_tab.query().principal == "alice"


def test_billing_navigation_filters_to_one_service(program_env, qtbot):
    """Review Focus 4."""
    w, _ = program_env(make_billing, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    page = w.pages["service"]
    qtbot.waitUntil(lambda: bool(w.current_page().grid.visible_rows()), timeout=3000)
    name = page.grid.visible_rows()[0]["service"] if page.grid.visible_rows() else "Amazon EC2"
    w.apply_navigation({"service": name})
    qtbot.waitUntil(lambda: bool(page.grid.visible_rows()), timeout=3000)
    assert w.current_page() is page and {r["service"] for r in page.grid.visible_rows()} == {name}


def test_arn_navigation_fills_the_filter_and_runs_it(program_env, qtbot):
    w, _ = program_env(make_activity, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    w.apply_navigation({"arn": "i-0a1b2c3d4e5f60001", "account": "111111111111"})
    assert w.tabs.currentWidget() is w.filter_tab and w.filter_tab.query().arn_contains == "i-0a1b2c3d4e5f60001"
    qtbot.waitUntil(lambda: w.scope.accounts == ("111111111111",), timeout=3000)   # once the account list loads


def test_arn_navigation_ignores_unknown_account(program_env, qtbot):
    """Review Focus 5."""
    w, _ = program_env(make_activity, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    before = w.scope.accounts
    w.apply_navigation({"arn": "x-1", "account": "999999999999"})
    qtbot.waitUntil(lambda: w.scope_bar.account_combo.count() > 1, timeout=3000)
    qtbot.wait(100)
    assert w.filter_tab.query().arn_contains == "x-1" and w.scope.accounts == before
