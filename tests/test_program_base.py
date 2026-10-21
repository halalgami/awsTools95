import threading
from decimal import Decimal

import pytest
from PySide6.QtWidgets import QApplication, QToolBar

from aws95.contracts.common import Result, Scope, Status
from aws95.kit.widgets.datagrid import Column, DataGrid
from aws95.kit.widgets.stateview import StateView, ViewState
from aws95.kit.widgets.watermark import Watermark
from aws95.programs import DEFAULT_MANIFESTS
from aws95.programs.common import ProgramBase, ScopeBar, rows_to_csv, rows_to_markdown
from aws95.providers.demo import demo_provider_set
from aws95.shell.window import ProgramWindow

COLS = [Column("name", "Name"), Column("amount", "Amount", "money")]
GLOBAL_KEYS = {"F5", "Ctrl+E", "Ctrl+W", "Ctrl+K", "Ctrl+Q", "F1"}


class Probe(ProgramBase):
    TITLE = "Probe"
    PROGRAM_MENU = "&Probe"
    EXPORT_NAME = "probe.csv"

    def __init__(self, ctx, fn=None):
        super().__init__(ctx)
        self.fn = fn or (lambda scope: Result.ok(()))
        self.grid = DataGrid(COLS)
        self.view = StateView(self.grid)
        self.set_content(self.view)
        self.rendered = []

    def refresh(self):
        self.handle = self.load(self.view, self.fn, self.scope, render=self._render)

    def _render(self, rows):
        self.rendered.append(rows)
        self.grid.set_rows(rows)

    def export_grid(self):
        return self.grid


@pytest.fixture
def make_probe(program_env, qtbot):
    def make(fn=None, providers=None):
        w, ctx = program_env(lambda c: Probe(c, fn), providers)
        qtbot.addWidget(w)
        return w, ctx
    return make


def test_skeleton(make_probe):
    w, _ = make_probe()
    assert [m.title() for m in (w.file_menu, w.edit_menu, w.view_menu, w.program_menu, w.help_menu)] == \
        ["&File", "&Edit", "&View", "&Probe", "&Help"]
    assert isinstance(w.toolbar, QToolBar) and w.refresh_action in w.toolbar.actions()
    assert isinstance(w.scope_bar, ScopeBar)
    for menu in (w.file_menu, w.edit_menu, w.view_menu, w.program_menu, w.help_menu):
        for a in menu.actions():
            assert a.shortcut().toString() not in GLOBAL_KEYS, a.text()   # never shadow a global shortcut
    assert w.refresh_action.iconText() == "Refresh"


def test_first_show_loads_and_renders(make_probe, qtbot):
    w, _ = make_probe(lambda scope: Result.ok(({"name": "a", "amount": Decimal("1.5")},)))
    w.show()
    qtbot.waitUntil(lambda: bool(w.rendered))      # StateView starts in CONTENT, so wait for the real render
    assert w.rendered[0][0]["name"] == "a"


def test_demo_result_watermarks_body_once(make_probe, qtbot):
    w, _ = make_probe(lambda scope: Result.ok(({"name": "a", "amount": None},), is_demo=True))
    w.show()
    qtbot.waitUntil(lambda: w.is_demo)
    w.refresh()
    qtbot.waitUntil(lambda: len(w.rendered) == 2)
    assert len(w.body.findChildren(Watermark)) == 1


def test_not_connected_and_status(make_probe, qtbot):
    w, _ = make_probe(lambda scope: Result.not_connected("Probe data arrives in phase 2."))
    w.show()
    qtbot.waitUntil(lambda: w.view.state is ViewState.NOT_CONNECTED)
    assert w.status.freshness_label.text() == "Not connected"
    assert not w.is_demo


def test_partial_result_shows_banner_and_coverage(make_probe, qtbot):
    from aws95.contracts.common import Coverage
    w, _ = make_probe(lambda scope: Result(Status.PARTIAL, ({"name": "a", "amount": None},),
                                           Coverage(("us-east-1", "eu-west-1"), ("us-east-1",), 0)))
    w.show()
    qtbot.waitUntil(lambda: bool(w.rendered))      # StateView starts in CONTENT, so wait for the real render
    assert w.view.banner_text and "1 of 2 regions" in w.view.banner_text
    assert w.status.coverage_label.text().startswith("1/2 regions")


def test_failure_shows_error_with_details(make_probe, qtbot):
    def boom(scope):
        raise RuntimeError("engine fell over")
    w, _ = make_probe(boom)
    w.show()
    qtbot.waitUntil(lambda: w.view.state is ViewState.ERROR)
    assert "engine fell over" in w.view._body.text()


def test_stale_result_is_dropped(make_probe, qtbot):
    gate = threading.Event()
    started = threading.Event()
    calls = []

    def fn(scope):
        calls.append(scope)
        started.set()
        if len(calls) == 1:
            gate.wait(5)
            return Result.ok(({"name": "stale", "amount": None},))
        return Result.ok(({"name": "fresh", "amount": None},))

    w, _ = make_probe(fn)
    w.refresh()
    first = w.handle
    assert started.wait(5)            # the first call must be in flight before the second starts (pool order)
    w.refresh()
    qtbot.waitUntil(lambda: w.view.state is ViewState.CONTENT)
    gate.set()
    qtbot.waitUntil(lambda: first.done)
    QApplication.processEvents()
    assert [r[0]["name"] for r in w.rendered] == ["fresh"]


def test_close_cancels_and_drops_late_result(program_env, qtbot):
    gate = threading.Event()

    def slow(scope):
        gate.wait(5)
        return Result.ok(({"name": "late", "amount": None},))

    content, ctx = program_env(lambda c: Probe(c, slow))
    win = ProgramWindow(DEFAULT_MANIFESTS[0], content, ctx.settings)
    qtbot.addWidget(win)
    content.refresh()
    handle = content.handle
    win.close()                       # QMdiSubWindow closes its widget first
    assert handle.token.cancelled
    gate.set()
    qtbot.waitUntil(lambda: handle.done)
    QApplication.processEvents()
    assert content.rendered == []


def test_export_writes_exact_csv_and_records_document(make_probe, qtbot, tmp_path, monkeypatch):
    rows = ({"name": "=HYPERLINK(1)", "amount": Decimal("1.2345")}, {"name": "credit", "amount": Decimal("-12.50")})
    w, ctx = make_probe(lambda scope: Result.ok(rows))
    target = tmp_path / "out.csv"
    monkeypatch.setattr(Probe, "ask_save_path", staticmethod(lambda *a: str(target)))
    w.show()
    qtbot.waitUntil(lambda: bool(w.rendered))      # StateView starts in CONTENT, so wait for the real render
    assert w.export() is True
    text = target.read_text(encoding="utf-8")
    assert text.splitlines() == ["Name,Amount", "'=HYPERLINK(1),1.2345", "credit,-12.50"]
    assert ctx.settings.recent_documents()[0] == str(target)


def test_export_cancelled_or_empty(make_probe, qtbot, monkeypatch):
    w, _ = make_probe(lambda scope: Result.ok(()))
    monkeypatch.setattr(Probe, "ask_save_path", staticmethod(lambda *a: None))
    assert w.export() is False


def test_rows_to_csv_defangs_formulas_only_in_text():
    cols = [Column("a", "A"), Column("n", "N", "money")]
    out = rows_to_csv(cols, [{"a": "+1", "n": Decimal("-3")}, {"a": "@x", "n": None}, {"a": "-y", "n": 5}])
    assert out.splitlines() == ["A,N", "'+1,-3", "'@x,", "'-y,5"]


def test_rows_to_markdown_escapes():
    md = rows_to_markdown("T", [Column("a", "A")], [{"a": "x|y\nz"}])
    assert "x\\|y z" in md and md.startswith("# T")


def test_copy_current_row(make_probe, qtbot):
    w, _ = make_probe(lambda scope: Result.ok(({"name": "a", "amount": Decimal("2.50")},)))
    w.show()
    qtbot.waitUntil(lambda: bool(w.rendered))      # StateView starts in CONTENT, so wait for the real render
    w.grid.view.selectRow(0)
    w.copy_current_row()
    assert QApplication.clipboard().text() == "a\t2.50"


def test_scope_bar_loads_demo_accounts_and_chip(make_probe, qtbot):
    w, ctx = make_probe(providers=demo_provider_set())
    bar = w.scope_bar
    qtbot.waitUntil(lambda: bar.account_combo.count() == 3)
    assert bar.scope() == Scope()
    assert bar.chip() == ("All accounts", "rainbow")
    assert bar.set_account("111111111111")
    assert bar.scope().accounts == ("111111111111",)
    assert bar.chip() == ("lab-sandbox", "#ffd34d")
    ctx.settings.set_account_label("111111111111", "Lab", "#00ff00")
    assert bar.chip() == ("Lab", "#00ff00")
    assert "Lab" in bar.account_combo.currentText()
    bar.set_regions(["eu-west-1", "us-east-1"])
    assert set(bar.scope().regions) == {"eu-west-1", "us-east-1"}
    assert bar.region_button.text() == "2 regions"
    bar.set_regions([])
    assert bar.scope().regions == () and bar.region_button.text() == "All regions"


def test_scope_bar_preselects_active_account(program_env, qtbot):
    def factory(ctx):
        ctx.settings.active_account = "222222222222"
        return Probe(ctx)
    w, _ = program_env(factory, demo_provider_set())
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: w.scope_bar.account_combo.count() == 3)
    assert w.scope_bar.scope().accounts == ("222222222222",)


def test_scope_change_refreshes(make_probe, qtbot):
    seen = []
    w, _ = make_probe(lambda scope: (seen.append(scope), Result.ok(()))[1], demo_provider_set())
    w.show()
    qtbot.waitUntil(lambda: w.scope_bar.account_combo.count() == 3)
    qtbot.waitUntil(lambda: len(seen) >= 1)
    w.scope_bar.set_account("222222222222")
    qtbot.waitUntil(lambda: seen[-1].accounts == ("222222222222",))


def test_navigate_delegates_to_shell(make_probe):
    w, ctx = make_probe()
    w.navigate("resources", service="Amazon Simple Storage Service")
    assert ("navigate", ("resources", {"service": "Amazon Simple Storage Service"})) in ctx.shell.calls


def test_export_failures_are_reported(make_probe, qtbot, tmp_path, monkeypatch):
    w, ctx = make_probe(lambda scope: Result.ok(({"name": "a", "amount": Decimal("1")},)))
    w.show()
    qtbot.waitUntil(lambda: bool(w.rendered))
    monkeypatch.setattr(Probe, "ask_save_path", staticmethod(lambda *a: str(tmp_path / "missing" / "o.csv")))
    assert w.export() is False
    assert "Export failed" in w.status.counts_label.text()
    odd = tmp_path / "a\nb.csv"
    monkeypatch.setattr(Probe, "ask_save_path", staticmethod(lambda *a: str(odd)))
    assert w.export() is True
    assert odd.exists() and ctx.settings.recent_documents() == ()
    assert "not added" in w.status.counts_label.text()
    monkeypatch.setattr(Probe, "ask_save_path", staticmethod(lambda *a: str(tmp_path / "nul\x00.csv")))
    assert w.export() is False
    assert "Export failed" in w.status.counts_label.text()


def test_export_cancelled_with_rows(make_probe, qtbot, monkeypatch):
    w, ctx = make_probe(lambda scope: Result.ok(({"name": "a", "amount": Decimal("1")},)))
    w.show()
    qtbot.waitUntil(lambda: bool(w.rendered))
    monkeypatch.setattr(Probe, "ask_save_path", staticmethod(lambda *a: None))
    assert w.export() is False
    assert ctx.settings.recent_documents() == ()


def test_set_regions_unchanged_does_not_emit(make_probe):
    w, _ = make_probe()
    seen = []
    w.scope_bar.scope_changed.connect(seen.append)
    w.scope_bar.set_regions(["us-east-1"])
    w.scope_bar.set_regions(["us-east-1"])
    assert len(seen) == 1
    w.scope_bar._select_all_regions()
    w.scope_bar._select_all_regions()
    assert len(seen) == 2


def test_decimal_cells_have_no_scientific_notation():
    cols = [Column("n", "N", "money")]
    rows = [{"n": Decimal("1E-8")}, {"n": Decimal("1E+2")}]
    assert rows_to_csv(cols, rows).splitlines() == ["N", "0.00000001", "100"]
    assert "0.00000001" in rows_to_markdown("T", cols, rows) and "| 100 |" in rows_to_markdown("T", cols, rows)


def test_invalidate_drops_in_flight_result(make_probe, qtbot):
    gate = threading.Event()
    w, _ = make_probe(lambda scope: (gate.wait(5), Result.ok(({"name": "late", "amount": None},)))[1])
    w.refresh()
    w.invalidate(w.view)
    gate.set()
    qtbot.waitUntil(lambda: w.handle.done)
    QApplication.processEvents()
    assert w.rendered == []


def test_close_closes_child_dialogs(make_probe):
    from shiboken6 import isValid
    from PySide6.QtWidgets import QDialog
    w, _ = make_probe()
    w.show()
    dlg = QDialog(w)
    dlg.show()
    w.close()
    assert not isValid(dlg) or not dlg.isVisible()


def test_load_status_false_leaves_status_bar(make_probe, qtbot):
    w, _ = make_probe()
    w.show()
    qtbot.waitUntil(lambda: w.view.state is ViewState.EMPTY)
    w.status.coverage_label.setText("marker")
    done = []
    w.load(w.view, lambda scope: Result.ok(()), w.scope, render=lambda r: None, status=False,
               on_result=done.append)
    qtbot.waitUntil(lambda: bool(done))
    assert w.status.coverage_label.text() == "marker"
    w.load(w.view, lambda scope: Result.ok(()), w.scope, render=lambda r: None, on_result=done.append)
    qtbot.waitUntil(lambda: len(done) == 2)
    assert w.status.coverage_label.text() != "marker"


def test_second_load_cancels_first(make_probe, qtbot):
    gate = threading.Event()
    w, _ = make_probe()
    rendered = []

    def slow(scope, *, cancel=None):
        gate.wait(2)
        return Result.ok(("first",))

    def fast(scope, *, cancel=None):
        return Result.ok(("second",))

    h1 = w.load(w.view, slow, w.scope, render=rendered.append)
    w.load(w.view, fast, w.scope, render=rendered.append)
    assert h1.token.cancelled
    gate.set()
    qtbot.waitUntil(lambda: rendered == [("second",)], timeout=3000)
    qtbot.wait(50)
    assert rendered == [("second",)]


def test_failed_view_retries_once(make_probe, qtbot):
    calls = []

    def flaky(scope, *, cancel=None):
        calls.append(1)
        return Result.error("boom") if len(calls) == 1 else Result.ok(())

    w, _ = make_probe()
    w.load(w.view, flaky, w.scope, render=lambda rows: None)
    qtbot.waitUntil(lambda: len(calls) == 1, timeout=3000)
    qtbot.wait(20)
    w.retry_failed(w.view)
    qtbot.waitUntil(lambda: len(calls) == 2, timeout=3000)
    qtbot.wait(20)
    w.retry_failed(w.view)                       # the last load succeeded: no retry
    qtbot.wait(50)
    assert len(calls) == 2


def _flaky(calls, fail_first=True, raises=False):
    def fn(scope, *, cancel=None):
        calls.append(1)
        if len(calls) == 1 and fail_first:
            if raises:
                raise RuntimeError("boom")
            return Result.error("boom")
        return Result.ok(())
    return fn


def test_invalidate_clears_retry(make_probe, qtbot):
    calls = []
    w, _ = make_probe()
    w.load(w.view, _flaky(calls), w.scope, render=lambda rows: None)
    qtbot.waitUntil(lambda: w.view.state is ViewState.ERROR, timeout=3000)
    w.invalidate(w.view)
    w.retry_failed(w.view)
    qtbot.wait(50)
    assert len(calls) == 1


@pytest.mark.parametrize("raises", [False, True])
def test_reshow_retries_failed_view_once(make_probe, qtbot, raises):
    calls = []
    w, _ = make_probe()
    w.refresh = lambda: None                      # keep the first-show auto load out of the way
    w.primary_view = w.view
    w.show()
    qtbot.wait(20)
    w.load(w.view, _flaky(calls, raises=raises), w.scope, render=lambda rows: None)
    qtbot.waitUntil(lambda: w.view.state is ViewState.ERROR, timeout=3000)
    assert id(w.view) in w._failed_views
    w.hide()
    w.show()                                      # showEvent -> retry_failed(primary_view)
    qtbot.waitUntil(lambda: len(calls) == 2, timeout=3000)
    qtbot.wait(30)
    w.hide()
    w.show()
    qtbot.wait(50)
    assert len(calls) == 2                        # the retry succeeded: no further retry


def test_close_event_clears_load_state(make_probe, qtbot):
    calls = []
    w, _ = make_probe()
    w.load(w.view, _flaky(calls), w.scope, render=lambda rows: None)
    qtbot.waitUntil(lambda: id(w.view) in w._failed_views, timeout=3000)
    assert w._handles and w._last
    w.close()
    assert not w._handles and not w._last and not w._failed_views


def test_scope_bar_reloads_profiles_when_roster_changes(make_probe, qtbot):
    import dataclasses

    from aws95.contracts.accounts import RosterEntry
    from aws95.providers.empty import empty_provider_set

    calls = []

    class CountingAccounts:
        def list_profiles(self, scope=None, *, cancel=None):
            calls.append(1)
            return Result.ok(())

    providers = dataclasses.replace(empty_provider_set(), accounts=CountingAccounts())
    w, ctx = make_probe(providers=providers)
    qtbot.waitUntil(lambda: len(calls) == 1)
    ctx.settings.set_roster((RosterEntry("dev", "123456789012"),))
    qtbot.waitUntil(lambda: len(calls) == 2)


def test_scope_bar_ignores_a_stale_profile_reload(make_probe, qtbot):
    """2a leftover: a slow, older list_profiles must not overwrite the answer of a newer reload."""
    import dataclasses
    ps = demo_provider_set()
    profiles = ps.accounts.list_profiles().data
    gate = threading.Event()
    calls = []

    class Gated:
        def __getattr__(self, name):
            return getattr(ps.accounts, name)

        def list_profiles(self, scope=None, *, cancel=None):
            calls.append(1)
            if len(calls) == 2:                     # the first reload after construction: slow, old answer
                gate.wait(5)
                return Result.ok(profiles)
            return Result.ok(tuple(p for p in profiles if p.verified and p.verified.account_id == "111111111111"))

    w, ctx = make_probe(providers=dataclasses.replace(ps, accounts=Gated(), is_demo=False))
    bar = w.scope_bar
    qtbot.waitUntil(lambda: len(calls) >= 1 and bar.account_combo.count() >= 2, timeout=3000)
    ctx.settings.set_roster(())                     # reload #2 (slow, stale)
    ctx.settings.set_roster(())                     # reload #3 (fast, newest)
    qtbot.waitUntil(lambda: len(calls) >= 3 and bar.account_combo.count() == 2, timeout=3000)
    gate.set()
    qtbot.wait(200)
    assert bar.account_combo.count() == 2           # the stale answer (two accounts) never lands
