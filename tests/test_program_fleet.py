import dataclasses
import re

import pytest
from PySide6.QtGui import QColor

from aws95.contracts.common import Result
from aws95.kit.theme import DARK, LIGHT, current_theme, set_current
from aws95.kit.widgets.stateview import ViewState
from aws95.programs.fleet import make_fleet
from aws95.providers.demo import demo_provider_set

A, B = "111111111111", "222222222222"


@pytest.fixture
def demo(program_env, qtbot):
    w, ctx = program_env(make_fleet, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: len(w.rules()) == 10 and w.cost_view.state is ViewState.CONTENT
                    and w.matrix.item(w.matrix.rowCount() - 1, 0) is not None)
    return w, ctx


def _cell(w, rule, account):
    return w.matrix.item(w.rules().index(rule), w.accounts().index(account))


def test_empty_mode(program_env, qtbot):
    w, _ = program_env(make_fleet)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.view.state is ViewState.NOT_CONNECTED)


def test_matrix_symbols(demo):
    w, _ = demo
    assert w.accounts() == [A, B] and len(w.rules()) == 10
    assert _cell(w, "ec2-public-ip", A).text() == "✗"
    assert _cell(w, "budget-missing", A).text() == "✓"
    assert _cell(w, "iam-user-no-mfa", B).text() == "?"
    assert _cell(w, "iam-user-no-mfa", B).toolTip() == "Not collected for this account"
    assert w.matrix.verticalHeaderItem(w.matrix.rowCount() - 1).text() == "Cost (MTD)"


def test_cost_row_is_exact_provider_figure(demo):
    w, _ = demo
    assert w.matrix.item(w.matrix.rowCount() - 1, 0).text() == "301.15 USD"


def test_click_cells(demo):
    w, ctx = demo
    w.cell_clicked(w.rules().index("sg-open-ssh"), w.accounts().index(B))
    assert ctx.shell.calls[-1] == ("navigate", ("findings", {"rule": "sg-open-ssh", "account": B}))
    w.cell_clicked(w.rules().index("iam-user-no-mfa"), w.accounts().index(B))
    assert "Not collected: iam-user-no-mfa in" in w.status.counts_label.text()
    w.cell_clicked(w.matrix.rowCount() - 1, 0)
    assert ctx.shell.calls[-1] == ("navigate", ("billing", {}))


def test_billing_not_connected_keeps_matrix(program_env, qtbot):
    from aws95.providers.empty import empty_provider_set
    ps = dataclasses.replace(demo_provider_set(), billing=empty_provider_set().billing)
    w, _ = program_env(make_fleet, ps)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.view.state is ViewState.CONTENT and w.cost_note.isVisible() and len(w.rules()) == 10)
    assert w.cost_view.isHidden()                  # one plain line, not a full "Not connected" panel
    assert w.matrix.item(w.matrix.rowCount() - 1, 0).text() == "—"


def test_refresh_error_clears_stale_cells(program_env, qtbot):
    class Flaky:
        def __init__(self, inner):
            self.inner, self.fail = inner, False

        def fleet(self, scope, *, cancel=None):
            return Result.error("boom") if self.fail else self.inner.fleet(scope, cancel=cancel)

    ps = demo_provider_set()
    flaky = Flaky(ps.findings)
    w, _ = program_env(make_fleet, dataclasses.replace(ps, findings=flaky))
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: len(w.rules()) == 10)
    flaky.fail = True
    w.refresh()
    qtbot.waitUntil(lambda: w.view.state is ViewState.ERROR)
    assert w.matrix.rowCount() == 0 and w.rules() == [] and w.accounts() == []
    assert w.status.counts_label.text() == ""


def test_colours_follow_theme(demo):
    w, _ = demo
    set_current(LIGHT)
    set_current(DARK)
    assert _cell(w, "ec2-public-ip", A).foreground().color() == QColor(current_theme().bad)
    set_current(LIGHT)
    assert _cell(w, "ec2-public-ip", A).foreground().color() == QColor(LIGHT.bad)
    assert _cell(w, "budget-missing", A).foreground().color() == QColor(LIGHT.ok)
    assert _cell(w, "iam-user-no-mfa", B).foreground().color() == QColor(LIGHT.muted)


def test_no_duplicate_mnemonics(demo):
    w, _ = demo

    def letter(text):
        m = re.search(r"(?<!&)&([A-Za-z])", text.replace("&&", ""))
        return m.group(1).lower() if m else None

    bar = [letter(a.text()) for a in w.menu_bar.actions()]
    assert len(bar) == len(set(bar)), bar
    own = [letter(a.text()) for a in w.program_menu.actions()]
    assert not (set(own) - {None}) & set(bar), own


def test_headers_follow_scope_bar(demo, qtbot):
    w, ctx = demo
    qtbot.waitUntil(lambda: w.matrix.horizontalHeaderItem(0).text() == "lab-sandbox")
    assert w.matrix.horizontalHeaderItem(0).toolTip() == A
    ctx.settings.set_account_label(A, "Lab", "#00ff00")
    qtbot.waitUntil(lambda: w.matrix.horizontalHeaderItem(0).text() == "Lab")


def test_accounts_load_after_matrix_renders(program_env, qtbot):
    w, _ = program_env(make_fleet, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: len(w.rules()) == 10)
    qtbot.waitUntil(lambda: len(w.scope_bar.accounts) == 2)
    refs = list(w.scope_bar.accounts)
    assert refs
    w.scope_bar.set_accounts([dataclasses.replace(refs[0], nickname="late-name")] + refs[1:])
    assert w.matrix.horizontalHeaderItem(0).text() == "late-name"


def test_return_key_navigates(demo, qtbot):
    from PySide6.QtCore import Qt
    w, ctx = demo
    w.matrix.setCurrentCell(w.rules().index("sg-open-ssh"), w.accounts().index(B))
    n = len(ctx.shell.calls)
    qtbot.keyClick(w.matrix, Qt.Key.Key_Return)
    assert len(ctx.shell.calls) == n + 1
    assert ctx.shell.calls[-1] == ("navigate", ("findings", {"rule": "sg-open-ssh", "account": B}))


def test_long_cost_is_not_elided(program_env, qtbot):
    from decimal import Decimal
    ps = demo_provider_set()

    class Big:
        def __init__(self, inner):
            self.inner = inner

        def breakdown(self, scope, by, *a, **k):
            r = self.inner.breakdown(scope, by, *a, **k)
            pts = tuple(dataclasses.replace(p, amount=Decimal("12345678901234567.89")) for p in r.data)
            return dataclasses.replace(r, data=pts)

    w, _ = program_env(make_fleet, dataclasses.replace(ps, billing=Big(ps.billing)))
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: len(w.rules()) == 10 and w.matrix.item(10, 0) is not None
                    and w.matrix.item(10, 0).text() != "—")
    it = w.matrix.item(10, 0)
    assert it.toolTip() == it.text() and it.text().startswith("12,345,678,901,234,567.89")
    assert w.matrix.columnWidth(0) >= w.matrix.fontMetrics().horizontalAdvance(it.text())


def test_billing_error_keeps_matrix_status(program_env, qtbot):
    class Bad:
        def breakdown(self, scope, by, *a, **k):
            return Result.error("boom")

    ps = demo_provider_set()
    w, _ = program_env(make_fleet, dataclasses.replace(ps, billing=Bad()))
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.cost_view.state is ViewState.ERROR and len(w.rules()) == 10)
    # an ERROR result has no freshness, so a non-empty label can only come from the matrix result
    assert w.status.freshness_label.text() not in ("", "Not connected")


def test_unavailable_cost_row_is_one_line_not_a_full_panel(program_env, qtbot):
    """Live Billing answers the per-account breakdown with "Not in this release": the window says so in one plain
    line under the matrix instead of a full-height "Not connected" panel that steals the matrix's room."""
    from aws95.contracts.billing import NOT_IN_RELEASE

    class NoBreakdown:
        def __getattr__(self, name):
            return getattr(demo_provider_set().billing, name)

        def breakdown(self, *a, **k):
            return Result.not_connected(NOT_IN_RELEASE)

    ps = dataclasses.replace(demo_provider_set(), billing=NoBreakdown())
    w, _ = program_env(make_fleet, ps)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.matrix.rowCount() > 0, timeout=3000)
    qtbot.waitUntil(lambda: w.cost_note.isVisible(), timeout=3000)
    assert w.cost_note.text() == f"Cost row: {NOT_IN_RELEASE}" and w.cost_view.isHidden()
    assert w.cost_note.height() <= 3 * w.cost_note.fontMetrics().height()
