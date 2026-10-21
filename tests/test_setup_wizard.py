import threading
from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSettings
from PySide6.QtWidgets import QWizard
from shiboken6 import isValid

from aws95.contracts.accounts import ConfigSource, RosterEntry
from aws95.contracts.common import Result, Status
from aws95.core.cancel import CancelToken
from aws95.core.settings import Settings
from aws95.core.tasks import TaskHandle, TaskRunner
from aws95.shell.context import NullSound
from aws95.shell.setup_wizard import MAX_PARALLEL, SetupWizard, _Loader, should_autostart_wizard
from tests.account_stubs import P, StubAccounts

NEXT = QWizard.WizardButton.NextButton


_OPENED: list = []


@pytest.fixture
def env(tmp_path, qapp):
    settings = Settings(QSettings(str(tmp_path / "w.ini"), QSettings.Format.IniFormat))
    runner = TaskRunner()
    yield settings, runner
    for w in _OPENED:                       # the wizard deletes itself on close (WA_DeleteOnClose)
        if isValid(w):
            w.close()
    _OPENED.clear()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    runner.shutdown()


def make(env, acc, qtbot, **kw):
    settings, runner = env
    w = SetupWizard(settings, runner, acc, NullSound(), **kw)
    _OPENED.append(w)
    w.show()
    return w


def to_choose(w, qtbot):
    w.next()                                                     # welcome → sources
    qtbot.waitUntil(lambda: w.button(NEXT).isEnabled(), timeout=3000)
    w.next()                                                     # sources → choose
    qtbot.waitUntil(lambda: w.choose_page.all_settled(), timeout=5000)


def test_non_modal_classic(env, qtbot):
    w = make(env, StubAccounts((P("dev"),), {"dev": "123456789012"}), qtbot)
    assert not w.isModal() and w.wizardStyle() == QWizard.WizardStyle.ClassicStyle


def test_zero_profiles_blocks_next_offers_finish_empty(env, qtbot):
    acc = StubAccounts((), {}, sources=(ConfigSource("/x/config", "config", False, 0),
                                       ConfigSource("/x/credentials", "credentials", False, 0)))
    w = make(env, acc, qtbot)
    w.next()
    qtbot.waitUntil(lambda: "/x/config" in w.sources_page.text_dump(), timeout=3000)
    assert not w.button(NEXT).isEnabled()
    assert w.sources_page.look_again_button.isVisible() and w.sources_page.finish_empty_button.isVisible()


def test_finish_empty_always_offered(env, qtbot):
    w = make(env, StubAccounts((P("dev"),), {"dev": "123456789012"}), qtbot)
    w.next()
    assert w.sources_page.finish_empty_button.isVisible()           # before the load completes
    qtbot.waitUntil(lambda: w.button(NEXT).isEnabled(), timeout=3000)
    assert w.sources_page.finish_empty_button.isVisible()           # and with profiles present
    w.next()
    assert w.choose_page.finish_empty_button.isVisible()


def test_unreadable_config_shown_as_error(env, qtbot):
    acc = StubAccounts((), {}, sources=Result.error("Could not read AWS config file: /x/config"))
    w = make(env, acc, qtbot)
    w.next()
    qtbot.waitUntil(lambda: "Could not read" in w.sources_page.text_dump(), timeout=3000)
    assert "0 profiles" not in w.sources_page.text_dump()


def test_ticks_verified_and_one_per_account(env, qtbot):
    acc = StubAccounts((P("a"), P("b"), P("c")), {"a": "111111111111", "b": "111111111111", "c": Status.DENIED})
    w = make(env, acc, qtbot)
    to_choose(w, qtbot)
    page = w.choose_page
    assert page.ticked() == ("a",)
    assert "Same account as a" in page.row_status("b")
    assert page.row_status("c").startswith("✖")
    page.set_ticked("b", True)                                   # explicit choice moves the account to b
    assert page.ticked() == ("b",)


def test_duplicate_rule_only_among_verified(env, qtbot):
    acc = StubAccounts((P("a"), P("b")), {"a": Status.DENIED, "b": "111111111111"})
    w = make(env, acc, qtbot)
    to_choose(w, qtbot)
    assert w.choose_page.ticked() == ("b",)


def test_status_strings(env, qtbot):
    acc = StubAccounts((P("proc", verify="manual"), P("dev")), {"dev": "123456789012"})
    w = make(env, acc, qtbot)
    to_choose(w, qtbot)
    assert w.choose_page.row_status("dev") == "✔ 123456789012"
    assert w.choose_page.row_status("proc") == "Click Verify (uses an external credential helper)"


def test_never_and_manual_profiles_not_auto_verified(env, qtbot):
    acc = StubAccounts((P("mfa", verify="never", issue="Needs MFA, which isn't supported yet"),
                        P("proc", verify="manual"), P("dev")),
                       {"mfa": "1", "proc": "222222222222", "dev": "123456789012"})
    w = make(env, acc, qtbot)
    to_choose(w, qtbot)
    assert acc.verified == ["dev"]
    assert "MFA" in w.choose_page.row_status("mfa") and not w.choose_page.can_tick("mfa")
    w.choose_page.verify("proc")
    qtbot.waitUntil(lambda: w.choose_page.row_status("proc").startswith("✔"), timeout=3000)


def test_existing_roster_profiles_pretick_even_if_failing(env, qtbot):
    settings, _ = env
    settings.set_roster((RosterEntry("dev", "123456789012"),))
    acc = StubAccounts((P("dev", selected=True, account="123456789012"),), {"dev": Status.UNREACHABLE})
    w = make(env, acc, qtbot)
    to_choose(w, qtbot)
    assert w.choose_page.ticked() == ("dev",)
    assert w.choose_page.chosen() == (RosterEntry("dev", "123456789012"),)


class _Held(StubAccounts):
    """sign_in relays its prompt, then waits for the test to release it (the dialog closes on success)."""
    release = None

    def sign_in(self, profile="", on_prompt=None, *, cancel=None):
        if on_prompt is not None:
            on_prompt("https://device.sso.us-east-1.amazonaws.com/", "ABCD-EFGH")
        self.release.wait(3)
        return super().sign_in(profile, None, cancel=cancel)


def test_sign_in_relays_prompt_and_reverifies(env, qtbot):
    acc = _Held((P("corp", "sso"),), {"corp": Status.NOT_CONNECTED})
    acc.release = threading.Event()
    w = make(env, acc, qtbot)
    to_choose(w, qtbot)
    assert w.choose_page.sign_in_available("corp")
    w.choose_page.sign_in("corp")
    qtbot.waitUntil(lambda: "ABCD-EFGH" in w.choose_page.sign_in_dialog_text(), timeout=3000)
    assert "https://device.sso.us-east-1.amazonaws.com/" in w.choose_page.sign_in_dialog_text()
    acc.release.set()
    qtbot.waitUntil(lambda: w.choose_page.ticked() == ("corp",), timeout=5000)
    assert acc.signed_in == ["corp"]


def test_suppress_boxes_in_sync(env, qtbot):
    settings, _ = env
    w = make(env, StubAccounts((P("dev"),), {"dev": "123456789012"}), qtbot)
    w.welcome_page.suppress_box.setChecked(True)
    assert w.choose_page.suppress_box.isChecked()
    w.reject()
    assert settings.setup_suppress_autostart is True and settings.roster() == () and not settings.setup_completed


def test_close_wizard_mid_probe(env, qtbot):
    gate = threading.Event()
    acc = StubAccounts((P("dev"),), {"dev": "123456789012"}, gate=gate)
    w = make(env, acc, qtbot)
    w.next()
    qtbot.waitUntil(lambda: w.button(NEXT).isEnabled(), timeout=3000)
    w.next()
    w.close()
    gate.set()
    qtbot.wait(150)                                              # late results must not touch the closed wizard


def test_autostart_rules(env):
    from aws95.providers.empty import empty_provider_set
    settings, _ = env
    live = replace(empty_provider_set(), is_live=True)
    assert should_autostart_wizard(settings, live)
    assert not should_autostart_wizard(settings, empty_provider_set())
    settings.setup_suppress_autostart = True
    assert not should_autostart_wizard(settings, live)
    settings.setup_suppress_autostart = False
    settings.set_roster((RosterEntry("dev", "123456789012"),))
    assert not should_autostart_wizard(settings, live)


def test_only_new_mode_lists_unknown_profiles(env, qtbot):
    settings, _ = env
    settings.set_roster((RosterEntry("dev", "123456789012"),))
    acc = StubAccounts((P("dev", selected=True, account="123456789012"), P("new")),
                       {"dev": "123456789012", "new": "555555555555"})
    w = make(env, acc, qtbot, start_page="choose", only_new=True)
    qtbot.waitUntil(lambda: w.choose_page.all_settled(), timeout=5000)
    assert w.choose_page.names() == ("new",) and w.choose_page.ticked() == ("new",)


def test_next_cancels_probes_and_back_offers_verify(env, qtbot):
    gate = threading.Event()
    settings, _ = env
    settings.set_roster((RosterEntry("r", "999999999999"),))
    acc = StubAccounts((P("r", selected=True, account="999999999999"), P("dev")),
                       {"r": "999999999999", "dev": "123456789012"}, gate=gate)
    w = make(env, acc, qtbot)
    w.next()
    qtbot.waitUntil(lambda: w.button(NEXT).isEnabled(), timeout=3000)
    w.next()                                                     # choose; probes blocked on the gate
    page = w.choose_page
    qtbot.waitUntil(lambda: page.ticked() == ("r",), timeout=3000)
    w.next()                                                     # Next does not wait for the probes
    assert w.currentId() == 3
    gate.set()
    qtbot.wait(200)
    assert page.chosen() == (RosterEntry("r", "999999999999"),)
    assert page.row_status("dev") == "○ Not checked" and page.row_status("r") == "○ Not checked"
    assert page.all_settled()
    w.back()
    assert page.verify_available("dev")
    page.verify("dev")
    qtbot.waitUntil(lambda: page.row_status("dev").startswith("✔"), timeout=3000)
    assert page.ticked() == ("r", "dev") or page.ticked() == ("r",)


class _Scripted(StubAccounts):
    """sign_in relays the scripted prompts, then waits for release (or for cancel); the result is scripted too."""
    prompts: tuple = ()
    outcome = None
    release = None
    cancelled = False

    def sign_in(self, profile="", on_prompt=None, *, cancel=None):
        self.signed_in.append(profile)
        for url, code in self.prompts:
            on_prompt(url, code)
        for _ in range(60):
            if self.release.wait(0.05):
                break
            if cancel is not None and cancel.cancelled:
                self.cancelled = True
                return Result.error("Sign-in cancelled.")
        return self.outcome or Result.ok(None)


def _scripted(prompts=(), outcome=None):
    acc = _Scripted((P("corp", "sso"),), {"corp": Status.NOT_CONNECTED})
    acc.prompts, acc.outcome, acc.release = prompts, outcome, threading.Event()
    return acc


def test_sign_in_prompt_is_cleaned(env, qtbot):
    acc = _scripted((("https://x\u202e.com/\x07", "AB\u200bCD"),))
    w = make(env, acc, qtbot)
    to_choose(w, qtbot)
    w.choose_page.sign_in("corp")
    qtbot.waitUntil(lambda: "https://x.com/" in w.choose_page.sign_in_dialog_text(), timeout=3000)
    assert "\u202e" not in w.choose_page.sign_in_dialog_text() and "ABCD" in w.choose_page.sign_in_dialog_text()
    acc.release.set()


def test_sign_in_non_https_url_not_shown(env, qtbot):
    acc = _scripted((("javascript:alert(1)", "ABCD"),))
    w = make(env, acc, qtbot)
    to_choose(w, qtbot)
    w.choose_page.sign_in("corp")
    qtbot.waitUntil(lambda: "not recognised" in w.choose_page.sign_in_dialog_text(), timeout=3000)
    assert "javascript" not in w.choose_page.sign_in_dialog_text()
    acc.release.set()


def test_sign_in_url_without_code_is_shown_alone(env, qtbot):
    acc = _scripted((("https://auth.example/x", ""),))
    w = make(env, acc, qtbot)
    to_choose(w, qtbot)
    w.choose_page.sign_in("corp")
    qtbot.waitUntil(lambda: "Open https://auth.example/x" in w.choose_page.sign_in_dialog_text(), timeout=3000)
    assert "enter code" not in w.choose_page.sign_in_dialog_text()
    acc.release.set()


def test_close_hides_sign_in_dialog(env, qtbot):
    acc = _scripted()
    w = make(env, acc, qtbot)
    to_choose(w, qtbot)
    w.choose_page.sign_in("corp")
    dlg = w.choose_page._sign_in_dialog
    w.close()
    assert not isValid(dlg) or not dlg.isVisible()
    acc.release.set()


def _dialog(env, qtbot, acc, profile="corp"):
    from aws95.shell.setup_wizard import SignInDialog
    _, runner = env
    dlg = SignInDialog(acc, runner, profile)
    _OPENED.append(dlg)
    dlg.show()
    return dlg


@pytest.mark.parametrize("button", ["cancel_button", "close_button"])
def test_sign_in_dialog_cancel_and_close_both_cancel_the_cli(env, qtbot, button):
    acc = _scripted((("https://auth.example/x", "ABCD-EFGH"),))
    dlg = _dialog(env, qtbot, acc)
    qtbot.waitUntil(lambda: "ABCD-EFGH" in dlg.text_dump(), timeout=3000)
    if button == "close_button":
        dlg.close_button.show()
    getattr(dlg, button).click()
    qtbot.waitUntil(lambda: acc.cancelled, timeout=3000)
    acc.release.set()


def test_sign_in_dialog_copy_copies_only_the_command(env, qtbot, qapp):
    from aws95.aws import sso
    acc = _scripted((("https://auth.example/x", "ABCD-EFGH"),))
    dlg = _dialog(env, qtbot, acc, "my profile")
    qtbot.waitUntil(lambda: "ABCD-EFGH" in dlg.text_dump(), timeout=3000)
    qapp.clipboard().setText("untouched")
    assert qapp.clipboard().text() == "untouched"            # nothing copied unasked
    dlg.copy_button.click()
    assert qapp.clipboard().text() == sso.manual_command("my profile") == "aws sso login --profile 'my profile'"
    acc.release.set()


def test_login_command_matches_sso_quoting(monkeypatch):
    from aws95.aws import sso
    from aws95.shell import setup_wizard
    for plat in ("linux", "win32"):
        monkeypatch.setattr(sso.sys, "platform", plat)
        monkeypatch.setattr(setup_wizard.sys, "platform", plat)
        for name in ("dev", "quoted name", "a&b", "it's"):
            assert setup_wizard.login_command(name) == sso.manual_command(name)


def test_sign_in_dialog_no_cli_shows_link_and_command_without_touching_clipboard(env, qtbot, qapp):
    acc = _scripted(outcome=Result.not_connected("AWS CLI not found."))
    acc.release.set()
    qapp.clipboard().setText("keep me")
    dlg = _dialog(env, qtbot, acc)
    qtbot.waitUntil(lambda: "not found" in dlg.text_dump(), timeout=3000)
    assert "https://aws.amazon.com/cli/" in dlg.text_dump() and "aws sso login --profile corp" in dlg.text_dump()
    assert qapp.clipboard().text() == "keep me"
    assert dlg.close_button.isVisible()


def test_sign_in_dialog_success_signals_and_closes(env, qtbot):
    acc = _scripted()
    acc.release.set()
    dlg = _dialog(env, qtbot, acc)
    with qtbot.waitSignal(dlg.finished_ok, timeout=3000) as sig:
        pass
    assert sig.args == ["corp"]
    qtbot.waitUntil(lambda: not dlg.isVisible(), timeout=3000)


def test_only_new_refuses_account_held_by_roster(env, qtbot):
    settings, _ = env
    settings.set_roster((RosterEntry("dev", "123456789012"),))
    acc = StubAccounts((P("dev", selected=True, account="123456789012"), P("new")),
                       {"dev": "123456789012", "new": "123456789012"})
    w = make(env, acc, qtbot, start_page="choose", only_new=True)
    qtbot.waitUntil(lambda: w.choose_page.all_settled(), timeout=5000)
    assert w.choose_page.ticked() == () and "Same account as dev" in w.choose_page.row_status("new")
    w.choose_page.set_ticked("new", True)
    assert w.choose_page.ticked() == ()


class _FakeRunner:
    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args, **kwargs):
        handle = TaskHandle(CancelToken())
        self.jobs.append((fn, args, handle))
        return handle


def test_loader_limit_fifo_and_bump(qapp):
    runner = _FakeRunner()
    loader = _Loader(runner)
    owner = QObject()
    done = []
    for i in range(MAX_PARALLEL + 2):
        loader.submit(owner, lambda: None, i, on_done=done.append, limited=True)
    assert [a[0] for _, a, _ in runner.jobs] == list(range(MAX_PARALLEL))      # at most MAX_PARALLEL started
    runner.jobs[0][2].finished.emit("r0")
    assert [a[0] for _, a, _ in runner.jobs][-1] == MAX_PARALLEL               # FIFO: next queued job starts
    assert len(runner.jobs) == MAX_PARALLEL + 1
    loader.bump()
    runner.jobs[1][2].finished.emit("stale")                                   # stale result dropped
    assert done == ["r0"] and len(runner.jobs) == MAX_PARALLEL + 1             # queued job never started


def walk_to(w, qtbot, page_id):
    from aws95.shell.setup_wizard import PAGE_IDS
    to_choose(w, qtbot)
    while PAGE_IDS[w.currentId()] != page_id:
        qtbot.waitUntil(lambda: w.button(NEXT).isEnabled(), timeout=3000)
        w.next()


def test_permissions_billing_only_rerun(env, qtbot):
    acc = StubAccounts((P("dev"),), {"dev": "123456789012"})
    w = make(env, acc, qtbot)
    walk_to(w, qtbot, "permissions")
    qtbot.waitUntil(lambda: acc.checked == [("dev", False, False)], timeout=3000)
    w.permissions_page.panel.billing_box.setChecked(True)
    qtbot.waitUntil(lambda: ("dev", True, True) in acc.checked, timeout=3000)
    assert len(acc.checked) == 2
    qtbot.waitUntil(lambda: "Billing: Verified." in w.permissions_page.panel.text_dump(), timeout=3000)
    dump = w.permissions_page.panel.text_dump()
    assert "cloudtrail:LookupEvents" in dump and "AccessDenied" in w.permissions_page.panel.audit_note.text()


def test_names_validation_blocks_next(env, qtbot):
    w = make(env, StubAccounts((P("dev"),), {"dev": "123456789012"}), qtbot)
    walk_to(w, qtbot, "names")
    w.names_page.set_nickname("dev", "   ")
    assert not w.button(NEXT).isEnabled()
    w.names_page.set_nickname("dev", "Lab")
    assert w.button(NEXT).isEnabled()


def test_finish_writes_once(env, qtbot):
    settings, _ = env
    w = make(env, StubAccounts((P("dev"),), {"dev": "123456789012"}), qtbot)
    walk_to(w, qtbot, "names")
    w.names_page.set_nickname("dev", "Lab")
    w.next()
    keys = []
    settings.changed.connect(keys.append)
    w.accept()
    assert settings.roster() == (RosterEntry("dev", "123456789012"),)
    assert settings.account_label("123456789012")[0] == "Lab"
    assert settings.setup_completed is True
    assert keys.count("accounts/roster") == 1


def test_failing_roster_profile_kept_on_finish(env, qtbot):
    settings, _ = env
    settings.set_roster((RosterEntry("dev", "123456789012"),))
    acc = StubAccounts((P("dev", selected=True, account="123456789012"), P("new")),
                       {"dev": Status.UNREACHABLE, "new": "555555555555"})
    w = make(env, acc, qtbot)
    walk_to(w, qtbot, "finish")
    w.accept()
    assert settings.roster() == (RosterEntry("dev", "123456789012"), RosterEntry("new", "555555555555"))


def test_finish_validation_failure_keeps_wizard_and_writes_nothing(env, qtbot, monkeypatch):
    settings, _ = env
    w = make(env, StubAccounts((P("dev"),), {"dev": "123456789012"}), qtbot)
    walk_to(w, qtbot, "finish")
    monkeypatch.setattr(settings, "validate_roster", lambda entries: (_ for _ in ()).throw(ValueError("nope")))
    w.accept()
    assert w.isVisible() and "nope" in w.finish_page.error_label.text()
    assert settings.roster() == () and not settings.setup_completed and settings.account_label("123456789012") is None


def test_finish_without_accounts(env, qtbot):
    settings, _ = env
    acc = StubAccounts((), {}, sources=(ConfigSource("/x/config", "config", False, 0),
                                       ConfigSource("/x/credentials", "credentials", False, 0)))
    w = make(env, acc, qtbot)
    w.next()
    qtbot.waitUntil(lambda: w.sources_page.finish_empty_button.isVisible(), timeout=3000)
    w.sources_page.finish_empty_button.click()
    assert w.currentId() == 5 and "No accounts added" in w.finish_page.summary.text()
    w.accept()
    assert settings.setup_completed is True and settings.roster() == ()


def test_demo_mode_writes_nothing(env, qtbot):
    settings, _ = env
    w = make(env, StubAccounts((P("dev"),), {"dev": "123456789012"}, is_demo=True), qtbot, is_demo=True)
    assert w.windowTitle().endswith("[DEMO]")
    walk_to(w, qtbot, "finish")
    w.accept()
    assert settings.roster() == () and not settings.setup_completed
    assert "nothing was saved" in w.finish_page.summary.text().lower()


def test_policy_dialog_copy_and_save_error(env, qtbot, qapp, tmp_path):
    w = make(env, StubAccounts((P("dev"),), {"dev": "123456789012"}), qtbot)
    dlg = w.show_policy()
    from tests.account_stubs import POLICY
    qtbot.waitUntil(lambda: POLICY in dlg.text.toPlainText(), timeout=3000)
    dlg.copy_button.click()
    assert qapp.clipboard().text() == POLICY and not dlg.isModal()
    assert "ViewOnlyAccess" in dlg.advice.text() and "ReadOnlyAccess" not in dlg.advice.text().replace("ViewOnlyAccess", "")
    dlg.ask_save_path = staticmethod(lambda parent: str(tmp_path / "missing-dir" / "p.json"))
    dlg.save_button.click()
    assert dlg.error_label.text()


def test_permissions_dialog_never_writes(env, qtbot):
    from aws95.shell.setup_wizard import PermissionsDialog
    settings, runner = env
    acc = StubAccounts((P("dev"),), {"dev": "123456789012"})
    dlg = PermissionsDialog(acc, runner, "dev", "123456789012")
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitUntil(lambda: bool(acc.checked), timeout=3000)
    dlg.close()
    assert settings.roster() == () and not dlg.isModal()


def test_finish_summary_lists_permission_gaps(env, qtbot):
    w = make(env, StubAccounts((P("dev"),), {"dev": "123456789012"}), qtbot)
    walk_to(w, qtbot, "permissions")
    qtbot.waitUntil(lambda: "Activity Log" in w.permissions_page.panel.text_dump(), timeout=3000)
    w.next()
    w.next()
    assert w.finish_page.summary.text() == \
        "1 account(s) ready. Permission gaps: Activity Log on 1 account(s) (feature arrives later)"


def test_colour_hook_and_labels_written(env, qtbot):
    from PySide6.QtGui import QColor
    settings, _ = env
    w = make(env, StubAccounts((P("dev"),), {"dev": "123456789012"}), qtbot)
    walk_to(w, qtbot, "names")
    w.names_page.ask_color = staticmethod(lambda initial, parent: QColor("#336699"))
    w.names_page._rows[0]["button"].click()
    w.next()
    w.accept()
    assert settings.account_label("123456789012") == ("dev", "#336699")


class _GatedFull(StubAccounts):
    """check_permissions: the full run waits on the gate; billing-only calls go straight through."""

    def check_permissions(self, scope, profile="", include_billing=False, *, only_billing=False, cancel=None):
        if not only_billing and self.gate is not None:
            self.gate.wait(3)
        return super().check_permissions(scope, profile, include_billing, only_billing=only_billing)


def _panel(env, qtbot, acc):
    from aws95.shell.setup_wizard import PermissionsPanel
    panel = PermissionsPanel(acc, env[1], [("dev", "123456789012")])
    qtbot.addWidget(panel)
    return panel


def test_unticking_billing_makes_no_call(env, qtbot):
    acc = StubAccounts((P("dev"),), {"dev": "123456789012"})
    panel = _panel(env, qtbot, acc)
    panel.run()
    qtbot.waitUntil(lambda: "Identity" in panel.text_dump(), timeout=3000)
    panel.billing_box.setChecked(True)
    qtbot.waitUntil(lambda: ("dev", True, True) in acc.checked, timeout=3000)
    qtbot.waitUntil(lambda: "Billing: Verified." in panel.text_dump(), timeout=3000)
    n = len(acc.checked)
    panel.billing_box.setChecked(False)
    qtbot.wait(100)
    assert len(acc.checked) == n and "Not checked (AWS charges $0.01 per check)" in panel.text_dump()


def test_billing_result_held_until_run_lands(env, qtbot):
    gate = threading.Event()
    acc = _GatedFull((P("dev"),), {"dev": "123456789012"}, gate=gate)
    panel = _panel(env, qtbot, acc)
    try:
        panel.run()
        panel.billing_box.setChecked(True)
        qtbot.waitUntil(lambda: ("dev", True, True) in acc.checked, timeout=3000)
        qtbot.wait(100)
        assert panel.text_dump() == "Checking…"                  # billing-only result is held, not shown
    finally:
        gate.set()
    qtbot.waitUntil(lambda: "Billing: Verified." in panel.text_dump(), timeout=3000)
    assert "Identity" in panel.text_dump()


def test_unticking_mid_run_restores_not_checked(env, qtbot):
    gate = threading.Event()
    acc = _GatedFull((P("dev"),), {"dev": "123456789012"}, gate=gate)
    panel = _panel(env, qtbot, acc)
    try:
        panel.billing_box.blockSignals(True)
        panel.billing_box.setChecked(True)
        panel.billing_box.blockSignals(False)
        panel.run()                                              # this run asks for billing
        panel.billing_box.setChecked(False)                      # unticked while it is in flight
    finally:
        gate.set()
    qtbot.waitUntil(lambda: "Identity" in panel.text_dump(), timeout=3000)
    assert "Billing: Verified." not in panel.text_dump()
    assert "○ Billing: Not checked (AWS charges $0.01 per check)." in panel.text_dump()


def test_reject_writes_only_suppress_flag(env, qtbot):
    settings, _ = env
    w = make(env, StubAccounts((P("dev"),), {"dev": "123456789012"}), qtbot)
    walk_to(w, qtbot, "names")
    w.names_page.set_nickname("dev", "Lab")
    w.welcome_page.suppress_box.setChecked(True)
    w.reject()
    assert settings.setup_suppress_autostart is True
    assert settings.roster() == () and not settings.setup_completed and settings.account_label("123456789012") is None


def test_finish_empty_then_back_and_choose_accounts(env, qtbot):
    settings, _ = env
    w = make(env, StubAccounts((P("dev"),), {"dev": "123456789012"}), qtbot)
    to_choose(w, qtbot)
    w.choose_page.finish_empty_button.click()
    assert "No accounts added" in w.finish_page.summary.text()
    w.back()
    assert w.currentId() == 2
    qtbot.waitUntil(lambda: w.button(NEXT).isEnabled(), timeout=3000)
    w.next()
    assert w.currentId() == 3 and not w._finish_empty
    w.next()
    assert w.currentId() == 4                                    # names page is not skipped
    w.next()
    assert "1 account(s) ready." in w.finish_page.summary.text()
    w.accept()
    assert settings.roster() == (RosterEntry("dev", "123456789012"),)


def test_stale_labels_filtered_and_replacement_noted(env, qtbot, monkeypatch):
    settings, _ = env
    settings.set_roster((RosterEntry("gone", "123456789012"),))
    w = make(env, StubAccounts((P("new"),), {"new": "123456789012"}), qtbot)
    walk_to(w, qtbot, "finish")
    assert "gone replaced by new for account 123456789012" in w.finish_page.summary.text()
    monkeypatch.setattr(w.names_page, "labels", lambda: {"123456789012": ("N", "#112233"),
                                                         "999999999999": ("Stale", "#445566")})
    entries, labels = w._payload()
    assert entries == (RosterEntry("new", "123456789012"),) and list(labels) == ["123456789012"]
    w.accept()
    assert settings.account_label("999999999999") is None and settings.roster() == entries


def _boot_env(monkeypatch, tmp_path, live):
    import aws95.core.settings as settings_mod

    class TmpSettings(settings_mod.Settings):
        def __init__(self, qsettings=None, parent=None):
            super().__init__(QSettings(str(tmp_path / "boot.ini"), QSettings.Format.IniFormat), parent)

    monkeypatch.setattr(settings_mod, "Settings", TmpSettings)
    monkeypatch.delenv("AWS95_OFFLINE", raising=False)
    monkeypatch.delenv("AWS95_DEMO", raising=False)
    monkeypatch.setenv("AWS95_EXIT_AFTER_BOOT_MS", "60000")        # the test quits the loop itself
    monkeypatch.setattr("aws95.providers.live.live_provider_set", lambda settings: live)
    TmpSettings().skip_splash = True


def _boot_probe(monkeypatch, qtbot, argv, condition):
    """Run main() with a patched exec that waits for `condition`, records widgets, then exits."""
    from PySide6.QtWidgets import QApplication

    import aws95.app as app_mod
    from aws95.shell.about import TipDialog
    from aws95.shell.setup_wizard import SetupWizard
    seen = {}

    def spy_exec(*a):
        qtbot.waitUntil(lambda: condition(seen), timeout=5000) if condition else None
        QCoreApplication.processEvents()
        seen["wizards"] = [w for w in QApplication.topLevelWidgets() if isinstance(w, SetupWizard) and w.isVisible()]
        seen["tips"] = [w for w in QApplication.allWidgets() if isinstance(w, TipDialog) and w.isVisible()]
        return 0

    monkeypatch.setattr(QApplication, "exec", spy_exec)
    assert app_mod.main(argv) == 0
    return seen


def test_first_launch_boot_opens_wizard_not_tip(monkeypatch, tmp_path, qapp, qtbot):
    from PySide6.QtWidgets import QApplication

    from aws95.providers.empty import empty_provider_set
    from aws95.shell.setup_wizard import SetupWizard
    live = replace(empty_provider_set(), accounts=StubAccounts((P("dev"),), {"dev": "123456789012"}), is_live=True)
    _boot_env(monkeypatch, tmp_path, live)

    def wizard_up(_seen):
        return any(isinstance(w, SetupWizard) and w.isVisible() for w in QApplication.topLevelWidgets())

    seen = _boot_probe(monkeypatch, qtbot, [], wizard_up)
    assert len(seen["wizards"]) == 1 and not seen["tips"]


@pytest.mark.parametrize("argv", [["--demo"], ["--offline"]])
def test_demo_and_offline_boot_skip_wizard(monkeypatch, tmp_path, qapp, qtbot, argv):
    from aws95.providers.empty import empty_provider_set
    live = replace(empty_provider_set(), accounts=StubAccounts((P("dev"),), {"dev": "123456789012"}), is_live=True)
    _boot_env(monkeypatch, tmp_path, live)
    seen = _boot_probe(monkeypatch, qtbot, argv, None)
    assert not seen["wizards"]


def test_permissions_page_does_not_probe_never_or_unverified_manual_rows(env, qtbot):
    settings, _ = env
    settings.set_roster((RosterEntry("mfa", "111111111111"), RosterEntry("proc", "222222222222")))
    acc = StubAccounts((P("mfa", verify="never", issue="Needs MFA, which isn't supported yet"),
                        P("proc", verify="manual"), P("dev")),
                       {"mfa": "111111111111", "proc": "222222222222", "dev": "123456789012"})
    w = make(env, acc, qtbot)
    walk_to(w, qtbot, "permissions")
    qtbot.waitUntil(lambda: ("dev", False, False) in acc.checked, timeout=3000)
    qtbot.wait(100)
    assert [c[0] for c in acc.checked] == ["dev"]
    dump = w.permissions_page.panel.text_dump()
    assert "○ Not checked: Needs MFA, which isn't supported yet" in dump
    assert "○ Not checked: Verify this profile first" in dump
    w.next()
    w.next()
    assert "Not checked: 2 account(s)" in w.finish_page.summary.text()


def test_permissions_page_probes_a_manual_row_verified_this_session(env, qtbot):
    acc = StubAccounts((P("proc", verify="manual"),), {"proc": "222222222222"})
    w = make(env, acc, qtbot)
    to_choose(w, qtbot)
    w.choose_page.verify("proc")
    qtbot.waitUntil(lambda: w.choose_page.row_status("proc").startswith("✔"), timeout=3000)
    w.choose_page.set_ticked("proc", True)
    while w.currentId() != 3:
        qtbot.waitUntil(lambda: w.button(NEXT).isEnabled(), timeout=3000)
        w.next()
    qtbot.waitUntil(lambda: [c[0] for c in acc.checked] == ["proc"], timeout=3000)


def test_accept_records_every_listed_profile_as_known(env, qtbot):
    settings, _ = env
    settings.set_known_profiles(["old"])
    acc = StubAccounts((P("dev"), P("left-unticked")), {"dev": "123456789012", "left-unticked": "555555555555"})
    w = make(env, acc, qtbot)
    to_choose(w, qtbot)
    w.choose_page.set_ticked("left-unticked", False)
    walk_to(w, qtbot, "finish")
    w.accept()
    assert set(settings.known_profiles()) == {"old", "dev", "left-unticked"}
    assert [e.profile for e in settings.roster()] == ["dev"]


def test_finish_empty_records_known_profiles_and_demo_does_not(env, qtbot):
    settings, _ = env
    w = make(env, StubAccounts((P("dev"),), {"dev": "123456789012"}), qtbot)
    to_choose(w, qtbot)
    w.finish_without_accounts()
    w.accept()
    assert settings.known_profiles() == ("dev",)


def test_demo_accept_writes_no_known_profiles(env, qtbot):
    settings, _ = env
    w = make(env, StubAccounts((P("dev"),), {"dev": "123456789012"}, is_demo=True), qtbot, is_demo=True)
    walk_to(w, qtbot, "finish")
    w.accept()
    assert settings.known_profiles() == ()


# ----- remembered verification (spec §7: accounts/verified) -----
from datetime import datetime, timezone  # noqa: E402

PINNED = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)


def test_finish_records_verified_chosen_rows(env, qtbot, monkeypatch):
    settings, _ = env
    monkeypatch.setattr(SetupWizard, "clock", staticmethod(lambda: PINNED), raising=False)
    acc = StubAccounts((P("dev"), P("prod"), P("bad")),
                       {"dev": "111111111111", "prod": "222222222222", "bad": Status.DENIED})
    w = make(env, acc, qtbot)
    walk_to(w, qtbot, "finish")
    w.accept()
    assert settings.last_verified("dev") == ("111111111111", PINNED)
    assert settings.last_verified("prod") == ("222222222222", PINNED)
    assert settings.last_verified("bad") is None


def test_finish_does_not_record_a_roster_row_that_failed_its_check(env, qtbot):
    settings, _ = env
    settings.set_roster((RosterEntry("dev", "123456789012"),))
    acc = StubAccounts((P("dev", selected=True, account="123456789012"), P("new")),
                       {"dev": Status.UNREACHABLE, "new": "555555555555"})
    w = make(env, acc, qtbot)
    walk_to(w, qtbot, "finish")
    w.accept()
    assert settings.last_verified("dev") is None
    assert settings.last_verified("new") is not None and settings.last_verified("new")[0] == "555555555555"


def test_demo_finish_records_no_verification(env, qtbot):
    settings, _ = env
    w = make(env, StubAccounts((P("dev"),), {"dev": "123456789012"}, is_demo=True), qtbot, is_demo=True)
    walk_to(w, qtbot, "finish")
    w.accept()
    assert settings.last_verified("dev") is None


def test_wizard_then_accounts_shows_verified_without_any_check(env, qtbot, tmp_path):
    from dataclasses import replace as dc_replace

    from aws95.programs.accounts import AccountsProgram
    from aws95.providers.empty import empty_provider_set
    from aws95.shell.context import NullSound, ProgramContext
    from tests.conftest import RecordingShell
    settings, runner = env
    acc = StubAccounts((P("dev"), P("prod")), {"dev": "111111111111", "prod": "222222222222"})
    w = make(env, acc, qtbot)
    walk_to(w, qtbot, "finish")
    w.accept()
    wizard_checks = list(acc.verified)
    assert wizard_checks
    providers = dc_replace(empty_provider_set(), accounts=acc, is_live=True, is_demo=False)
    program = AccountsProgram(ProgramContext(settings, runner, providers, NullSound(), RecordingShell()))
    qtbot.addWidget(program)
    program.show()
    qtbot.waitUntil(lambda: program.status_of("dev").startswith("✔ Verified"), timeout=3000)
    assert program.status_of("prod").startswith("✔ Verified")
    assert acc.verified == wizard_checks                         # Accounts made no verify_profile call


# ----- colour dialog: Qt's own dialog, wizard raised afterwards -----
def test_names_colour_hook_uses_qt_dialog_and_parent_window(env, qtbot, monkeypatch):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog
    from aws95.shell.setup_wizard import NamesPage
    seen = {}

    def fake(initial, parent, title, options):
        seen.update(initial=initial, parent=parent, title=title, options=options)
        return QColor("#336699")

    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(fake))
    w = make(env, StubAccounts((P("dev"),), {"dev": "123456789012"}), qtbot)
    got = NamesPage.ask_color("#112233", w.names_page)
    assert got.name() == "#336699"
    assert seen["options"] & QColorDialog.ColorDialogOption.DontUseNativeDialog
    assert seen["parent"] is w.names_page.window() and seen["title"] == "Account colour"


@pytest.mark.parametrize("pick", ["#336699", None])
def test_pick_raises_and_activates_wizard(env, qtbot, pick):
    from PySide6.QtGui import QColor
    w = make(env, StubAccounts((P("dev"),), {"dev": "123456789012"}), qtbot)
    walk_to(w, qtbot, "names")
    calls = []
    w.raise_ = lambda: calls.append("raise")
    w.activateWindow = lambda: calls.append("activate")
    w.names_page.ask_color = staticmethod(lambda initial, parent: QColor(pick) if pick else QColor())
    w.names_page._pick("123456789012")
    assert calls == ["raise", "activate"]


def test_finish_forgets_entry_of_chosen_row_that_failed(env, qtbot):
    settings, _ = env
    settings.set_roster((RosterEntry("dev", "123456789012"),))
    settings.record_verified("dev", "123456789012", PINNED)
    acc = StubAccounts((P("dev", selected=True, account="123456789012"),), {"dev": Status.UNREACHABLE})
    w = make(env, acc, qtbot)
    walk_to(w, qtbot, "finish")
    w.accept()
    assert settings.last_verified("dev") is None


def test_wizard_billing_probe_states_its_cost(env, qtbot):
    from aws95.contracts.billing import BILLING_CHECK_NOTE
    panel = _panel(env, qtbot, StubAccounts((P("dev"),), {"dev": "123456789012"}))
    assert BILLING_CHECK_NOTE in panel.billing_box.text()
    from aws95.shell.setup_wizard import BILLING_NOT_CHECKED
    assert BILLING_CHECK_NOTE in BILLING_NOT_CHECKED


def test_wizard_sign_in_is_shared_with_the_shell(env, qtbot):
    """2a leftover: the wizard's Sign In registers its dialog with the shell (so Accounts hears about a success and
    a second Sign In for the same profile reuses it), and reuses a dialog the shell already has open."""
    from tests.conftest import RecordingShell
    shell = RecordingShell()
    acc = _Held((P("corp", "sso"),), {"corp": Status.NOT_CONNECTED})
    acc.release = threading.Event()
    w = make(env, acc, qtbot, shell=shell)
    to_choose(w, qtbot)
    w.choose_page.sign_in("corp")
    tracked = [args for name, args in shell.calls if name == "track_sign_in"]
    assert tracked and tracked[0][0] == "corp" and tracked[0][1] is w.choose_page._sign_in_dialog
    acc.release.set()
    qtbot.waitUntil(lambda: w.choose_page.ticked() == ("corp",), timeout=5000)


def test_wizard_reuses_the_shells_open_sign_in_dialog(env, qtbot):
    from aws95.shell.setup_wizard import SignInDialog
    from tests.conftest import RecordingShell
    acc = _Held((P("corp", "sso"),), {"corp": Status.NOT_CONNECTED})
    acc.release = threading.Event()
    existing = SignInDialog(acc, env[1], "corp")
    existing.show()

    class Shell(RecordingShell):
        def sign_in_dialog_for(self, profile):
            return existing if profile == "corp" else None
    shell = Shell()
    w = make(env, acc, qtbot, shell=shell)
    to_choose(w, qtbot)
    w.choose_page.sign_in("corp")
    assert w.choose_page._sign_in_dialog is existing                 # no second dialog for the same profile
    assert not any(name == "track_sign_in" for name, _ in shell.calls)
    acc.release.set()
    existing.close()
