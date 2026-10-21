"""Accounts remembers the last successful verification (spec §6.3, §7: accounts/verified)."""
from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog

from aws95.contracts.accounts import RosterEntry
from aws95.contracts.common import Status
from aws95.programs.accounts import AccountsProgram
from tests.account_stubs import P, StubAccounts
from tests.test_accounts_rescan import open_accounts

NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
A = "123456789012"


@pytest.fixture(autouse=True)
def pinned_clock(monkeypatch):
    monkeypatch.setattr(AccountsProgram, "clock", staticmethod(lambda: NOW), raising=False)


def _stub(**accounts):
    return StubAccounts((P("dev", selected=True, account=A),), accounts or {"dev": A})


def _open(program_env, qtbot, acc, stored=None, **kw):
    w, ctx = open_accounts(program_env, acc, qtbot, roster=(RosterEntry("dev", A),), **kw)
    return w, ctx


def test_stored_verification_shows_relative_time_without_checking(program_env, qtbot):
    acc = _stub()
    w, ctx = open_accounts(program_env, acc, qtbot, roster=(RosterEntry("dev", A),))
    ctx.settings.record_verified("dev", A, NOW - timedelta(minutes=3))
    qtbot.waitUntil(lambda: w.status_of("dev") == "✔ Verified 3 min ago", timeout=3000)
    assert acc.verified == []


@pytest.mark.parametrize("age,text", [(timedelta(seconds=10), "just now"), (timedelta(seconds=59), "just now"),
                                      (timedelta(seconds=60), "1 min ago"), (timedelta(minutes=59), "59 min ago"),
                                      (timedelta(hours=5, minutes=30), "5 h ago"), (timedelta(hours=24), "1 day ago"),
                                      (timedelta(days=12), "12 days ago")])
def test_relative_formats(program_env, qtbot, age, text):
    w, ctx = open_accounts(program_env, _stub(), qtbot, roster=(RosterEntry("dev", A),))
    ctx.settings.record_verified("dev", A, NOW - age)
    qtbot.waitUntil(lambda: w.status_of("dev") == f"✔ Verified {text}", timeout=3000)


def test_account_mismatch_is_ignored_and_forgotten(program_env, qtbot):
    w, ctx = open_accounts(program_env, _stub(), qtbot, roster=(RosterEntry("dev", A),))
    ctx.settings.record_verified("dev", "999999999999", NOW - timedelta(minutes=3))
    qtbot.waitUntil(lambda: w.status_of("dev") == "● In use (not checked yet)", timeout=3000)
    assert ctx.settings.last_verified("dev") is None


def test_session_check_wins_over_stored(program_env, qtbot):
    acc = StubAccounts((P("dev", selected=True, account=A),), {"dev": Status.DENIED})
    w, ctx = open_accounts(program_env, acc, qtbot, roster=(RosterEntry("dev", A),))
    ctx.settings.record_verified("dev", A, NOW)
    qtbot.waitUntil(lambda: w.select_profile("dev"), timeout=3000)
    w.verify()
    qtbot.waitUntil(lambda: w.status_of("dev").startswith("✖"), timeout=3000)


def test_successful_verify_records(program_env, qtbot):
    acc = _stub()
    w, ctx = open_accounts(program_env, acc, qtbot, roster=(RosterEntry("dev", A),))
    qtbot.waitUntil(lambda: w.select_profile("dev"), timeout=3000)
    w.verify()
    qtbot.waitUntil(lambda: ctx.settings.last_verified("dev") == (A, NOW), timeout=3000)
    assert w.status_of("dev") == "✔ Verified"                    # this session's check wins


def test_failed_verify_forgets(program_env, qtbot):
    acc = StubAccounts((P("dev", selected=True, account=A),), {"dev": Status.DENIED})
    w, ctx = open_accounts(program_env, acc, qtbot, roster=(RosterEntry("dev", A),))
    ctx.settings.record_verified("dev", A, NOW - timedelta(hours=2))
    qtbot.waitUntil(lambda: w.select_profile("dev"), timeout=3000)
    w.verify()
    qtbot.waitUntil(lambda: ctx.settings.last_verified("dev") is None, timeout=3000)


def test_rescan_records(program_env, qtbot):
    acc = _stub()
    w, ctx = open_accounts(program_env, acc, qtbot, roster=(RosterEntry("dev", A),))
    w.rescan()
    qtbot.waitUntil(lambda: ctx.settings.last_verified("dev") == (A, NOW), timeout=5000)


def test_remove_missing_forgets(program_env, qtbot):
    from aws95.contracts.accounts import NOT_IN_CONFIG
    acc = StubAccounts((P("dev", issue=NOT_IN_CONFIG),), {})
    w, ctx = open_accounts(program_env, acc, qtbot, roster=(RosterEntry("dev", A),))
    ctx.settings.record_verified("dev", A, NOW)
    qtbot.waitUntil(lambda: w.select_profile("dev"), timeout=3000)
    w.remove_missing()
    assert ctx.settings.roster() == () and ctx.settings.last_verified("dev") is None


def test_demo_writes_nothing(program_env, qtbot):
    acc = StubAccounts((P("dev", selected=True, account=A),), {"dev": A}, is_demo=True)
    w, ctx = open_accounts(program_env, acc, qtbot, demo=True)
    qtbot.waitUntil(lambda: w.select_profile("dev"), timeout=3000)
    w.verify()
    qtbot.waitUntil(lambda: w.status_of("dev").startswith("✔ Verified"), timeout=3000)
    w.rescan()
    qtbot.wait(300)
    assert ctx.settings.last_verified("dev") is None


def test_colour_hook_uses_qt_dialog_and_parent_window(program_env, qtbot, monkeypatch):
    seen = {}

    def fake(initial, parent, title, options):
        seen.update(parent=parent, title=title, options=options)
        return QColor("#00aa00")

    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(fake))
    w, _ = open_accounts(program_env, _stub(), qtbot, roster=(RosterEntry("dev", A),))
    assert AccountsProgram.ask_color(w, QColor("#c0c0c0")).name() == "#00aa00"
    assert seen["options"] & QColorDialog.ColorDialogOption.DontUseNativeDialog
    assert seen["parent"] is w.window() and seen["title"] == "Account colour"


@pytest.mark.parametrize("pick", ["#00aa00", None])
def test_pick_color_raises_and_activates(program_env, qtbot, monkeypatch, pick):
    w, _ = open_accounts(program_env, _stub(), qtbot, roster=(RosterEntry("dev", A),))
    calls = []
    monkeypatch.setattr(w.window(), "raise_", lambda: calls.append("raise"), raising=False)
    monkeypatch.setattr(w.window(), "activateWindow", lambda: calls.append("activate"), raising=False)
    monkeypatch.setattr(AccountsProgram, "ask_color", staticmethod(lambda parent, initial: QColor(pick) if pick else QColor()))
    w._pick_color()
    assert calls == ["raise", "activate"]


def test_unstorable_profile_name_does_not_break_rescan(program_env, qtbot):
    long_name = "x" * 129
    acc = StubAccounts((P("dev", selected=True, account=A), P(long_name)), {"dev": A, long_name: "555555555555"})
    w, ctx = open_accounts(program_env, acc, qtbot, roster=(RosterEntry("dev", A),))
    w.rescan()
    qtbot.waitUntil(lambda: w.status_of(long_name) == "✔ Verified", timeout=5000)
    assert w._queue is None
    assert ctx.settings.last_verified(long_name) is None and ctx.settings.last_verified("dev") == (A, NOW)


def test_future_stored_time_is_ignored_and_forgotten(program_env, qtbot):
    w, ctx = open_accounts(program_env, _stub(), qtbot, roster=(RosterEntry("dev", A),))
    ctx.settings.record_verified("dev", A, NOW + timedelta(minutes=10))
    qtbot.waitUntil(lambda: w.status_of("dev") == "● In use (not checked yet)", timeout=3000)
    assert ctx.settings.last_verified("dev") is None


def test_stored_entry_not_shown_on_profile_with_issue(program_env, qtbot):
    acc = StubAccounts((P("dev", selected=True, account=A, issue="Role missing"),), {"dev": A})
    w, ctx = open_accounts(program_env, acc, qtbot, roster=(RosterEntry("dev", A),))
    ctx.settings.record_verified("dev", A, NOW)
    qtbot.waitUntil(lambda: w.status_of("dev") != "", timeout=3000)
    assert "Verified" not in w.status_of("dev")
