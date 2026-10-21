import threading

from PySide6.QtCore import Qt
from dataclasses import replace

from aws95.contracts.accounts import ConfigSource, RosterEntry
from aws95.contracts.common import Result, Status
from aws95.programs.accounts import AccountsProgram
from aws95.providers.empty import empty_provider_set
from tests.account_stubs import P, StubAccounts


def open_accounts(program_env, acc, qtbot, roster=(), demo=False):
    providers = replace(empty_provider_set(), accounts=acc, is_live=not demo, is_demo=demo)
    w, ctx = program_env(AccountsProgram, providers)
    if roster:
        ctx.settings.set_roster(roster)
    qtbot.addWidget(w)
    w.show()
    return w, ctx


def test_header_shows_paths(program_env, qtbot, monkeypatch):
    monkeypatch.setenv("HOME", "/Users/u")
    acc = StubAccounts((P("dev"),), {"dev": "123456789012"},
                       sources=(ConfigSource("/Users/u/.aws/config", "config", True, 1),
                                ConfigSource("/Users/u/.aws/credentials", "credentials", False, 0)))
    w, _ = open_accounts(program_env, acc, qtbot)
    qtbot.waitUntil(lambda: "~/.aws/config ✔" in w.header_label.text(), timeout=3000)
    assert "~/.aws/credentials ✖" in w.header_label.text()
    assert w.status.counts_label.text().endswith("Looking in ~/.aws")


def test_header_error(program_env, qtbot):
    acc = StubAccounts((P("dev"),), {}, sources=Result.error("config unreadable"))
    w, _ = open_accounts(program_env, acc, qtbot)
    qtbot.waitUntil(lambda: w.header_label.text() == "Looking in: ✖ config unreadable", timeout=3000)


def test_opening_makes_no_checks(program_env, qtbot):
    acc = StubAccounts((P("dev", selected=True, account="123456789012"),), {"dev": "123456789012"})
    w, _ = open_accounts(program_env, acc, qtbot, roster=(RosterEntry("dev", "123456789012"),))
    qtbot.waitUntil(lambda: w.status_of("dev").startswith("●"), timeout=3000)
    assert acc.verified == []


def test_label_apply_makes_no_checks(program_env, qtbot):
    acc = StubAccounts((P("dev", selected=True, account="123456789012"),), {"dev": "123456789012"})
    w, ctx = open_accounts(program_env, acc, qtbot, roster=(RosterEntry("dev", "123456789012"),))
    qtbot.waitUntil(lambda: w.select_profile("dev"), timeout=3000)
    qtbot.waitUntil(lambda: w.nickname_edit.isEnabled(), timeout=3000)
    w.nickname_edit.setText("Dev box")
    w.apply_label_button.click()
    qtbot.waitUntil(lambda: w.selected_profile() is not None and w.nickname_edit.text() == "Dev box", timeout=3000)
    qtbot.wait(100)
    assert ctx.settings.account_label("123456789012")[0] == "Dev box"
    assert acc.verified == []


def test_rescan_new_profile_requires_confirm(program_env, qtbot):
    acc = StubAccounts((P("dev", selected=True, account="123456789012"), P("new")),
                       {"dev": "123456789012", "new": "555555555555"})
    w, ctx = open_accounts(program_env, acc, qtbot, roster=(RosterEntry("dev", "123456789012"),))
    w.rescan()
    qtbot.waitUntil(lambda: w.new_profiles_dialog is not None and w.new_profiles_dialog.isVisible(), timeout=5000)
    assert ctx.settings.roster() == (RosterEntry("dev", "123456789012"),)
    w.new_profiles_dialog.add_selected()
    assert RosterEntry("new", "555555555555") in ctx.settings.roster()
    assert set(ctx.settings.known_profiles()) == {"dev", "new"}


def test_known_profiles_not_rechecked(program_env, qtbot):
    acc = StubAccounts((P("dev", selected=True, account="123456789012"), P("old")),
                       {"dev": "123456789012", "old": "555555555555"})
    w, ctx = open_accounts(program_env, acc, qtbot, roster=(RosterEntry("dev", "123456789012"),))
    ctx.settings.set_known_profiles(["dev", "old"])
    w.rescan()
    qtbot.waitUntil(lambda: w.status_of("dev").startswith("✔"), timeout=5000)
    assert acc.verified == ["dev"]


def test_changed_account_requires_confirm(program_env, qtbot):
    acc = StubAccounts((P("dev", selected=True, account="123456789012"),), {"dev": "999999999999"})
    w, ctx = open_accounts(program_env, acc, qtbot, roster=(RosterEntry("dev", "123456789012"),))
    w.rescan()
    qtbot.waitUntil(lambda: w.new_profiles_dialog is not None and w.new_profiles_dialog.isVisible(), timeout=5000)
    assert "now 999999999999 (was 123456789012)" in w.new_profiles_dialog.text_dump()
    assert ctx.settings.roster() == (RosterEntry("dev", "123456789012"),)
    w.new_profiles_dialog.add_selected()
    assert ctx.settings.roster() == (RosterEntry("dev", "999999999999"),)


def test_dialog_cancel_changes_nothing(program_env, qtbot):
    acc = StubAccounts((P("new"),), {"new": "555555555555"})
    w, ctx = open_accounts(program_env, acc, qtbot)
    w.rescan()
    qtbot.waitUntil(lambda: w.new_profiles_dialog is not None and w.new_profiles_dialog.isVisible(), timeout=5000)
    w.new_profiles_dialog.cancel_button.click()
    assert not w.new_profiles_dialog.isVisible() and ctx.settings.roster() == ()


def test_missing_profile_marked_and_removable(program_env, qtbot):
    acc = StubAccounts((P("gone", selected=True, issue="Not in config any more", verify="never"),), {})
    w, ctx = open_accounts(program_env, acc, qtbot, roster=(RosterEntry("gone", "123456789012"),))
    qtbot.waitUntil(lambda: w.status_of("gone").startswith("⊘"), timeout=3000)
    w.select_profile("gone")
    assert w.remove_action.isEnabled()
    w.remove_action.trigger()
    assert ctx.settings.roster() == ()


def test_actions_route_to_shell(program_env, qtbot):
    acc = StubAccounts((P("dev", selected=True, account="123456789012"),), {"dev": "123456789012"})
    w, ctx = open_accounts(program_env, acc, qtbot, roster=(RosterEntry("dev", "123456789012"),))
    qtbot.waitUntil(lambda: w.select_profile("dev"), timeout=3000)
    w.setup_action.trigger()
    w.permissions_action.trigger()
    assert ("open_setup_wizard", ()) in ctx.shell.calls
    assert ("check_permissions", ("dev", "123456789012")) in ctx.shell.calls


def test_sign_in_enabled_only_for_expired_sso(program_env, qtbot):
    acc = StubAccounts((P("corp", "sso"),), {"corp": Status.NOT_CONNECTED})
    w, _ = open_accounts(program_env, acc, qtbot)
    w.rescan()
    qtbot.waitUntil(lambda: w.status_of("corp").startswith("⚠"), timeout=5000)
    w.select_profile("corp")
    assert w.sign_in_action.isEnabled()


def test_sign_in_routes_through_shell_then_reverifies(program_env, qtbot):
    acc = StubAccounts((P("corp", "sso"),), {"corp": Status.NOT_CONNECTED})
    w, ctx = open_accounts(program_env, acc, qtbot)
    w.rescan()
    qtbot.waitUntil(lambda: w.status_of("corp").startswith("⚠"), timeout=5000)
    w.select_profile("corp")
    w.sign_in_action.trigger()
    assert ("sign_in", ("corp",)) in ctx.shell.calls
    assert acc.signed_in == []                       # the program never runs the CLI itself
    acc.accounts["corp"] = "444444444444"            # the dialog's sign-in made the token valid
    ctx.shell.sign_in_succeeded.emit("corp")
    qtbot.waitUntil(lambda: w.status_of("corp").startswith("✔"), timeout=5000)


def test_sign_in_success_reverifies_rows_that_chain_to_it(program_env, qtbot):
    acc = StubAccounts((P("corp", "sso"), P("hop", "assume_role", sign_in_profile="corp"), P("other", "sso")),
                       {"corp": Status.NOT_CONNECTED, "hop": Status.NOT_CONNECTED, "other": Status.NOT_CONNECTED})
    w, ctx = open_accounts(program_env, acc, qtbot)
    w.rescan()
    qtbot.waitUntil(lambda: all(w.status_of(n).startswith("⚠") for n in ("corp", "hop", "other")), timeout=5000)
    w.select_profile("hop")
    w.sign_in_action.trigger()
    assert ("sign_in", ("corp",)) in ctx.shell.calls          # the CLI profile, not the role
    acc.accounts.update(corp="111111111111", hop="222222222222")
    before = list(acc.verified)
    ctx.shell.sign_in_succeeded.emit("corp")
    qtbot.waitUntil(lambda: w.status_of("hop").startswith("✔") and w.status_of("corp").startswith("✔"), timeout=5000)
    assert w.status_of("other").startswith("⚠") and "other" not in acc.verified[len(before):]


def test_never_row_cannot_verify_or_check_permissions(program_env, qtbot):
    acc = StubAccounts((P("mfa", issue="Needs MFA, which isn't supported yet", verify="never", account="123456789012"),
                        P("ok", account="210987654321")), {})
    w, ctx = open_accounts(program_env, acc, qtbot)
    qtbot.waitUntil(lambda: w.select_profile("mfa"), timeout=3000)
    assert not w.verify_button.isEnabled() and not w.verify_action.isEnabled()
    assert not w.permissions_action.isEnabled()
    w.verify()                                        # even called directly (shortcut, menu): nothing runs
    w.check_permissions()
    assert acc.verified == [] and ("check_permissions", ("mfa", "123456789012")) not in ctx.shell.calls
    qtbot.waitUntil(lambda: w.select_profile("ok") and w.verify_button.isEnabled(), timeout=3000)
    assert w.permissions_action.isEnabled()


def test_use_toggle_on_never_row_says_why(program_env, qtbot):
    acc = StubAccounts((P("mfa", issue="Needs MFA, which isn't supported yet", verify="never"),), {})
    w, ctx = open_accounts(program_env, acc, qtbot)
    qtbot.waitUntil(lambda: w.select_profile("mfa"), timeout=3000)
    w.use_action.trigger()
    text = w.status.counts_label.text()
    assert "Needs MFA" in text and "Verify this profile first" not in text and ctx.settings.roster() == ()


def test_replacing_another_profile_asks_first(program_env, qtbot):
    acc = StubAccounts((P("a", account="111111111111"), P("b", account="111111111111")), {})
    w, ctx = open_accounts(program_env, acc, qtbot, roster=(RosterEntry("a", "111111111111"),))
    qtbot.waitUntil(lambda: w.select_profile("b"), timeout=3000)
    w.use_action.trigger()
    box = w._confirm
    assert box is not None and not box.isModal()
    assert ctx.settings.roster() == (RosterEntry("a", "111111111111"),)      # nothing yet
    box.buttons[1].click()                                                   # No
    assert ctx.settings.roster() == (RosterEntry("a", "111111111111"),)
    qtbot.waitUntil(lambda: w.select_profile("b"), timeout=3000)
    w.use_action.trigger()
    w._confirm.buttons[0].click()                                            # Yes
    assert ctx.settings.roster() == (RosterEntry("b", "111111111111"),)
    assert "replaces a" in w.status.counts_label.text()


def test_cancelled_new_profile_offer_is_not_repeated(program_env, qtbot):
    acc = StubAccounts((P("dev", selected=True, account="123456789012"), P("new")),
                       {"dev": "123456789012", "new": "555555555555"})
    w, ctx = open_accounts(program_env, acc, qtbot, roster=(RosterEntry("dev", "123456789012"),))
    w.rescan()
    qtbot.waitUntil(lambda: w.new_profiles_dialog is not None, timeout=5000)
    w.new_profiles_dialog.cancel_button.click()
    w.new_profiles_dialog = None
    acc.verified.clear()
    w.rescan()
    qtbot.waitUntil(lambda: w.status_of("new").startswith("✔"), timeout=5000)
    qtbot.wait(200)
    assert "new" not in acc.verified and w.new_profiles_dialog is None
    assert ctx.settings.roster() == (RosterEntry("dev", "123456789012"),)


def test_unticked_profile_left_by_wizard_is_not_offered(program_env, qtbot):
    acc = StubAccounts((P("dev", selected=True, account="123456789012"), P("skipped")),
                       {"dev": "123456789012", "skipped": "555555555555"})
    w, ctx = open_accounts(program_env, acc, qtbot, roster=(RosterEntry("dev", "123456789012"),))
    ctx.settings.set_known_profiles(["dev", "skipped"])          # what SetupWizard.accept() now writes
    w.rescan()
    qtbot.waitUntil(lambda: w.status_of("dev").startswith("✔"), timeout=5000)
    qtbot.wait(200)
    assert "skipped" not in acc.verified and w.new_profiles_dialog is None


def test_use_toggle_needs_known_account_and_replaces_same_account(program_env, qtbot):
    acc = StubAccounts((P("a", account="111111111111"), P("b", account="111111111111"), P("c")), {"c": "333333333333"})
    w, ctx = open_accounts(program_env, acc, qtbot)
    qtbot.waitUntil(lambda: w.select_profile("c"), timeout=3000)
    w.use_action.trigger()
    assert ctx.settings.roster() == () and "Verify this profile first." in w.status.counts_label.text()
    w.select_profile("a")
    w.use_action.trigger()
    assert ctx.settings.roster() == (RosterEntry("a", "111111111111"),)
    qtbot.waitUntil(lambda: w.select_profile("b"), timeout=3000)
    w.use_action.trigger()
    w._confirm.buttons[0].click()                    # Yes: replacing another profile asks first
    assert ctx.settings.roster() == (RosterEntry("b", "111111111111"),)
    assert "replaces a" in w.status.counts_label.text()
    qtbot.waitUntil(lambda: w.select_profile("b"), timeout=3000)
    w.use_action.trigger()
    assert ctx.settings.roster() == ()


def test_demo_disables_roster_writes(program_env, qtbot):
    acc = StubAccounts((P("dev", account="123456789012"),), {"dev": "123456789012"}, is_demo=True)
    w, ctx = open_accounts(program_env, acc, qtbot, demo=True)
    qtbot.waitUntil(lambda: w.select_profile("dev"), timeout=3000)
    assert not w.use_action.isEnabled() and not w.remove_action.isEnabled()
    assert w.use_action.toolTip() == "Not available in demo mode"


def test_demo_detail_pane_writes_nothing(program_env, qtbot):
    acc = StubAccounts((P("dev", account="123456789012"),), {"dev": "123456789012"}, is_demo=True)
    w, ctx = open_accounts(program_env, acc, qtbot, demo=True)
    qtbot.waitUntil(lambda: w.select_profile("dev"), timeout=3000)
    for widget in (w.nickname_edit, w.color_button, w.apply_label_button, w.reset_label_button, w.region_list,
                   w.apply_regions_button, w.active_button):
        assert not widget.isEnabled() and widget.toolTip() == "Not available in demo mode"
    assert not w.active_action.isEnabled()
    keys_before = set(ctx.settings._qs.allKeys())
    w.nickname_edit.setText("Hacked")
    w.region_list.item(0).setCheckState(Qt.CheckState.Checked)
    w._apply_label()
    w._reset_label()
    w._apply_regions()
    w._set_active()
    assert set(ctx.settings._qs.allKeys()) == keys_before
    assert ctx.settings.account_label("123456789012") is None and ctx.settings.active_account != "123456789012"
    assert ctx.settings.account_regions("123456789012") == ()


def test_close_accounts_mid_rescan(program_env, qtbot):
    gate = threading.Event()
    acc = StubAccounts((P("dev"),), {"dev": "123456789012"}, gate=gate)
    w, _ = open_accounts(program_env, acc, qtbot)
    w.rescan()
    w.close()
    gate.set()
    qtbot.wait(150)


def test_rescan_runs_at_most_four_checks_at_once(program_env, qtbot):
    class Counting(StubAccounts):
        live = peak = 0
        lock = threading.Lock()

        def verify_profile(self, scope, profile="", *, cancel=None):
            with self.lock:
                Counting.live += 1
                Counting.peak = max(Counting.peak, Counting.live)
            try:
                return super().verify_profile(scope, profile, cancel=cancel)
            finally:
                with self.lock:
                    Counting.live -= 1

    names = [f"p{i}" for i in range(9)]
    gate = threading.Event()
    acc = Counting(tuple(P(n) for n in names), {n: f"{i + 1:012d}" for i, n in enumerate(names)}, gate=gate)
    w, _ = open_accounts(program_env, acc, qtbot)
    qtbot.waitUntil(lambda: w.profiles.model.total_count == 9, timeout=3000)
    w.rescan()
    qtbot.waitUntil(lambda: Counting.peak >= 4, timeout=3000)
    gate.set()
    qtbot.waitUntil(lambda: w.new_profiles_dialog is not None and w.new_profiles_dialog.isVisible(), timeout=5000)
    assert Counting.peak == 4 and sorted(acc.verified) == sorted(names)
