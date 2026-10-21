"""One status-owning load per program: secondary results never write the status bar (M3-R10b, ruling M3-R23)."""
import dataclasses
import time

from aws95.contracts.common import Coverage, Freshness, Scope
from aws95.kit.widgets.stateview import ViewState
from aws95.programs.accounts import make_accounts
from aws95.programs.activity import make_activity
from aws95.programs.billing import make_billing
from aws95.programs.findings import make_findings
from aws95.programs.reports import make_reports
from aws95.programs.resources import make_resources
from aws95.providers.demo import demo_provider_set

MARK = "SECONDARY-SOURCE"
MARK_COVERAGE = Coverage(denials=7)


class _Marked:
    """Wraps a provider: the named methods answer late, with a freshness and coverage nobody else reports."""

    def __init__(self, inner, *names: str, delay: float = 0.05) -> None:
        self._inner = inner
        self._names = set(names)
        self._delay = delay

    def __getattr__(self, name):
        fn = getattr(self._inner, name)
        if name not in self._names:
            return fn

        def marked(*args, **kwargs):
            result = fn(*args, **kwargs)
            time.sleep(self._delay)                 # land after the primary load
            return dataclasses.replace(result, freshness=Freshness(None, MARK), coverage=MARK_COVERAGE)
        return marked


def _status_clean(w) -> bool:
    return MARK not in w.status.freshness_label.text() and "7 denied" not in w.status.coverage_label.text()


def _open(program_env, qtbot, factory, **marked):
    ps = demo_provider_set()
    ps = dataclasses.replace(ps, **{k: _Marked(getattr(ps, k), *v) for k, v in marked.items()})
    w, _ = program_env(factory, ps)
    qtbot.addWidget(w)
    w.show()
    return w


def test_billing_kpi_summary_does_not_own_status(program_env, qtbot):
    w = _open(program_env, qtbot, make_billing, billing=("summary",))
    qtbot.waitUntil(lambda: w.mtd_label.text() != "—" and len(w.pages["service"].grid.visible_rows()) == 10)
    qtbot.wait(100)
    assert w.status.freshness_label.text() and _status_clean(w)


def test_accounts_health_and_verify_do_not_own_status(program_env, qtbot):
    w = _open(program_env, qtbot, make_accounts, accounts=("collector_health", "verify_profile"))
    qtbot.waitUntil(lambda: len(w.profiles.visible_rows()) == 3)
    owner = w.status.freshness_label.text()
    w.select_profile("lab")
    qtbot.waitUntil(lambda: len(w.health.visible_rows()) > 0)
    w.verify_button.click()
    qtbot.waitUntil(lambda: w.verify_label.text().startswith("Verified"))
    qtbot.wait(100)
    assert _status_clean(w) and w.status.freshness_label.text() == owner


def test_findings_rules_dialog_does_not_own_status(program_env, qtbot):
    w = _open(program_env, qtbot, make_findings, findings=("rules",))
    qtbot.waitUntil(lambda: len(w.grid.visible_rows()) == 11)
    dlg = w.open_rules_dialog()
    qtbot.waitUntil(lambda: len(dlg.table.visible_rows()) == 10)
    qtbot.wait(100)
    assert _status_clean(w)
    dlg.close()


def test_activity_event_dialog_and_timeline_events_do_not_own_status(program_env, qtbot):
    w = _open(program_env, qtbot, make_activity, activity=("raw_event", "events"))
    w.set_range("90d")
    w.tabs.setCurrentWidget(w.timeline)
    qtbot.waitUntil(lambda: len(w.timeline.principals.visible_rows()) > 0)
    assert w.timeline.select_principal("ops-alice")
    qtbot.waitUntil(lambda: len(w.timeline.events.visible_rows()) > 1)
    dlg = w.open_event(w.timeline.events.visible_rows()[0]["event_id"])
    qtbot.waitUntil(lambda: dlg.view.state is ViewState.CONTENT)       # the load started LOADING in __init__
    qtbot.wait(100)
    assert w.status.freshness_label.text() and _status_clean(w)
    dlg.close()


def test_reports_type_list_owns_status_only_while_no_report_is_selected(program_env, qtbot):
    w = _open(program_env, qtbot, make_reports, reports=("report_types",))
    qtbot.waitUntil(lambda: w.types.count() > 0)
    qtbot.waitUntil(lambda: MARK in w.status.freshness_label.text())     # nothing selected: the list reports
    w.types.setCurrentRow(0)
    qtbot.waitUntil(lambda: bool(w.preview.toPlainText()))
    qtbot.waitUntil(lambda: _status_clean(w))
    w.refresh()                                     # a report is selected: its render owns the status bar
    qtbot.waitUntil(lambda: bool(w.preview.toPlainText()))
    qtbot.wait(100)
    assert _status_clean(w)


class _Slow:
    """Delays one method, leaving its result untouched (the grid load stays in flight)."""

    def __init__(self, inner, name: str, delay: float) -> None:
        self._inner, self._name, self._delay = inner, name, delay

    def __getattr__(self, name):
        fn = getattr(self._inner, name)
        if name != self._name:
            return fn

        def slow(*args, **kwargs):
            time.sleep(self._delay)
            return fn(*args, **kwargs)
        return slow


def test_resources_type_tree_does_not_own_status_the_grid_does(program_env, qtbot):
    """Ruling M3-R26: the grid load owns the status bar; the type tree (different freshness/coverage) never does."""
    ps = demo_provider_set()
    resources = _Slow(_Marked(ps.resources, "resource_types", delay=0), "list_resources", 0.3)
    w, _ = program_env(make_resources, dataclasses.replace(ps, resources=resources))
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.tree.topLevelItemCount() > 0)
    assert w.grid_view.state is not ViewState.CONTENT or not w.grid.visible_rows()     # grid still in flight
    assert _status_clean(w)
    qtbot.waitUntil(lambda: len(w.grid.visible_rows()) > 0, timeout=5000)
    assert w.status.freshness_label.text() and _status_clean(w)


def test_marked_wrapper_really_marks():
    """Guard for the wrapper itself: a marked call carries the marker freshness and coverage."""
    ps = demo_provider_set()
    marked = _Marked(ps.billing, "summary", delay=0)
    result = marked.summary(Scope())
    assert result.freshness.source == MARK and result.coverage == MARK_COVERAGE
    assert marked.breakdown(Scope()).freshness.source != MARK
