import dataclasses
import re
import threading

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QGroupBox, QLabel, QPushButton

from aws95.kit.widgets.stateview import ViewState
from aws95.programs.accounts import AccountsProgram, make_accounts
from aws95.providers.demo import demo_provider_set

A = "111111111111"


@pytest.fixture
def demo(program_env, qtbot):
    w, ctx = program_env(make_accounts, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: len(w.profiles.visible_rows()) == 3)   # StateView starts in CONTENT: wait on the render
    return w, ctx


@pytest.fixture
def writable(program_env, qtbot):
    """The demo fixtures with the demo flag off, so the detail pane's settings writes are exercised (demo itself
    never writes: see test_demo_detail_pane_writes_nothing)."""
    w, ctx = program_env(make_accounts, dataclasses.replace(demo_provider_set(), is_demo=False))
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: len(w.profiles.visible_rows()) == 3)
    return w, ctx


def test_empty_mode_is_not_connected(program_env, qtbot):
    w, _ = program_env(make_accounts)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.profiles_view.state is ViewState.NOT_CONNECTED)
    assert w.status.freshness_label.text() == "Not connected"
    assert w.scope_bar is None


def test_demo_lists_profiles_and_watermarks(demo):
    w, _ = demo
    assert w.is_demo
    assert [r["name"] for r in w.profiles.visible_rows()] == ["lab", "dev", "personal"]


def test_selecting_verified_profile_loads_health(writable, qtbot):
    w, _ = writable
    assert w.select_profile("dev")
    qtbot.waitUntil(lambda: len(w.health.visible_rows()) > 0)
    assert w.health_view.state is ViewState.CONTENT
    statuses = {r["collector"]: r["status"] for r in w.health.visible_rows()}
    assert statuses["iam"] == "denied" and statuses["rds"] == "partial"
    assert w.nickname_edit.text() == "dev-shared" and w.nickname_edit.isEnabled()


def test_unverified_profile_disables_detail(demo, qtbot):
    w, _ = demo
    assert w.select_profile("personal")
    assert not w.nickname_edit.isEnabled() and not w.active_button.isEnabled()
    assert w.health_view.state is ViewState.EMPTY


def test_apply_label_and_reject_bad_nickname(writable, qtbot, monkeypatch):
    w, ctx = writable
    w.select_profile("lab")
    monkeypatch.setattr(AccountsProgram, "ask_color", staticmethod(lambda parent, initial: QColor("#00aa00")))
    w.color_button.click()
    w.nickname_edit.setText("Lab box")
    w.apply_label_button.click()
    assert ctx.settings.account_label(A) == ("Lab box", "#00aa00")
    # saving reloads the profiles (cleared at load start, M3-R5); the selection comes back with the rows
    qtbot.waitUntil(lambda: w.selected_profile() is not None and w.nickname_edit.isEnabled())
    w.nickname_edit.setText("   ")
    w.apply_label_button.click()
    assert ctx.settings.account_label(A) == ("Lab box", "#00aa00")
    assert "nickname" in w.status.counts_label.text().lower()
    w.reset_label_button.click()
    assert ctx.settings.account_label(A) is None


def test_cancelled_colour_keeps_previous(demo, monkeypatch):
    w, _ = demo
    w.select_profile("lab")
    before = w.color_button.property("color")
    monkeypatch.setattr(AccountsProgram, "ask_color", staticmethod(lambda parent, initial: QColor()))
    w.color_button.click()
    assert w.color_button.property("color") == before


def test_regions_and_active_account(writable):
    w, ctx = writable
    w.select_profile("lab")
    for i in range(w.region_list.count()):
        item = w.region_list.item(i)
        if item.text() in ("eu-west-1", "me-south-1"):
            item.setCheckState(Qt.CheckState.Checked)
    w.apply_regions_button.click()
    assert set(ctx.settings.account_regions(A)) == {"eu-west-1", "me-south-1"}
    w.active_button.click()
    assert ctx.settings.active_account == A


def test_verify_shows_account(demo, qtbot):
    w, _ = demo
    w.select_profile("lab")
    w.verify_button.click()
    qtbot.waitUntil(lambda: A in w.verify_label.text())
    assert w.verify_view.state is ViewState.CONTENT
    assert "lab-sandbox" in w.verify_label.text()


class _Gated:
    """Wraps the demo accounts provider; the first call of each gated method blocks until released."""

    def __init__(self, inner):
        self._inner = inner
        self.gates = {"collector_health": threading.Event(), "verify_profile": threading.Event()}
        self.started = {k: threading.Event() for k in self.gates}
        self._calls = {k: 0 for k in self.gates}

    def __getattr__(self, name):
        fn = getattr(self._inner, name)
        if name not in self.gates:
            return fn

        def call(*a, **k):
            self._calls[name] += 1
            if self._calls[name] == 1:
                self.started[name].set()
                self.gates[name].wait(5)
            return fn(*a, **k)
        return call


@pytest.fixture
def gated(program_env, qtbot):
    ps = demo_provider_set()
    g = _Gated(ps.accounts)
    w, ctx = program_env(make_accounts, dataclasses.replace(ps, accounts=g))
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: len(w.profiles.visible_rows()) == 3)
    try:
        yield w, g
    finally:
        for gate in g.gates.values():
            gate.set()


def test_stale_health_does_not_land_under_other_profile(gated, qtbot):
    w, g = gated
    w.select_profile("lab")
    assert g.started["collector_health"].wait(5)
    w.select_profile("personal")
    g.gates["collector_health"].set()
    QApplication.processEvents()
    qtbot.wait(100)
    assert w.health_view.state is ViewState.EMPTY
    assert w.health.visible_rows() == []


def test_stale_verify_does_not_land_under_other_profile(gated, qtbot):
    w, g = gated
    w.select_profile("lab")
    w.verify_button.click()
    assert g.started["verify_profile"].wait(5)
    w.select_profile("dev")
    g.gates["verify_profile"].set()
    qtbot.wait(100)
    QApplication.processEvents()
    assert w.verify_view.state is ViewState.EMPTY
    assert w.verify_label.text() == ""


def test_provider_colour_is_validated(demo):
    w, _ = demo
    w._show_color("red; } QPushButton { background: black")
    assert w.color_button.property("color") == "#c0c0c0"
    assert w.color_button.styleSheet() == ""
    w._show_color("#FFD34D")
    assert w.color_button.property("color") == "#ffd34d"
    img = w.color_button.icon().pixmap(16, 16).toImage()
    assert img.pixelColor(8, 8) == QColor("#ffd34d") and img.pixelColor(0, 0) == QColor("#000000")


def test_no_duplicate_mnemonics(demo):
    w, _ = demo
    texts = [a.text() for a in w.menu_bar.actions()]
    texts += [b.text() for b in w.findChildren(QPushButton)]
    texts += [g.title() for g in w.findChildren(QGroupBox)]
    texts += [lb.text() for lb in w.findChildren(QLabel)]
    letters = []
    for t in texts:
        m = re.search(r"(?<!&)&([A-Za-z])", t.replace("&&", ""))
        if m:
            letters.append(m.group(1).lower())
    assert len(letters) > 8 and len(letters) == len(set(letters)), sorted(letters)


def test_menu_actions_follow_button_state(writable):
    w, _ = writable
    w.select_profile("personal")
    assert not w.active_action.isEnabled() and w.verify_action.isEnabled()
    w.select_profile("lab")
    assert w.active_action.isEnabled() == w.active_button.isEnabled() is True


class _FailAfterFirst:
    """Accounts provider whose list_profiles succeeds once, then returns ERROR."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def list_profiles(self, *a, **k):
        self.calls += 1
        if self.calls == 1:
            return self._inner.list_profiles(*a, **k)
        from aws95.contracts.common import Result
        return Result.error("profiles unreadable")


def test_refresh_clears_profiles_and_counts_are_visible_based(program_env, qtbot, tmp_path, monkeypatch):
    ps = demo_provider_set()
    failing = _FailAfterFirst(ps.accounts)
    w, _ = program_env(make_accounts, dataclasses.replace(ps, accounts=failing))
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: len(w.profiles.visible_rows()) == 3)
    assert w.status.counts_label.text() == "3 of 3 profiles · Looking in ~/.aws"
    w.profiles.set_filter_text("name:lab")
    qtbot.waitUntil(lambda: w.status.counts_label.text() == "1 of 3 profiles · Looking in ~/.aws")
    w.profiles.set_filter_text("")
    w.refresh()
    assert w.profiles.visible_rows() == [] and w.profiles.model.total_count == 0    # cleared at load start
    qtbot.waitUntil(lambda: w.profiles_view.state is ViewState.ERROR)
    assert w.profiles.visible_rows() == []
    assert "3 profiles" not in w.status.counts_label.text()
    out = tmp_path / "a.csv"
    monkeypatch.setattr(AccountsProgram, "ask_save_path", staticmethod(lambda *a: str(out)))
    assert w.export() is False and not out.exists()


def test_billing_choice_is_saved_per_account(writable, qtbot):
    w, ctx = writable
    assert w.select_profile("dev")
    acct = w._account_id()
    assert w.billing_check.isEnabled() and not w.billing_check.isChecked()
    w.billing_check.setChecked(True)
    assert ctx.settings.account_billing(acct) is True
    w.billing_check.setChecked(False)
    assert ctx.settings.account_billing(acct) is False


def test_demo_billing_choice_is_read_only(demo):
    w, ctx = demo
    assert w.select_profile("dev")
    assert not w.billing_check.isEnabled()


def test_health_shows_denied_counts(writable, qtbot):
    w, _ = writable
    assert w.select_profile("dev")
    qtbot.waitUntil(lambda: len(w.health.visible_rows()) > 0)
    assert all("denied" in r for r in w.health.visible_rows())


def test_billing_checkbox_states_its_cost(writable, qtbot):
    """Spec §2.1: the cost sits next to the checkbox in visible text, not only in a tooltip."""
    from PySide6.QtWidgets import QLabel
    from aws95.contracts.billing import BILLING_COST_NOTE
    w, _ = writable
    assert w.select_profile("dev")
    notes = [lab for lab in w.billing_check.parent().findChildren(QLabel) if lab.text() == BILLING_COST_NOTE]
    assert len(notes) == 1 and not notes[0].isHidden()


def test_long_config_path_never_widens_the_window(writable):
    """A long ~/.aws path (deep Windows profile dirs, test temp dirs) wraps; it must not force a 3000 px window."""
    w, _ = writable
    long = "/very/long/directory" * 40 + "/config"
    w.header_label.setText("Looking in: " + long)
    w.status.set_counts("2 of 2 profiles · Looking in " + long)
    w.resize(900, 600)
    w.layout().activate()
    assert w.layout().minimumSize().width() < 1200 and w.width() < 1200
