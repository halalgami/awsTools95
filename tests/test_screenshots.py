import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from aws95.kit import theme as th
from aws95.kit.style import apply_theme
from aws95.kit.widgets.stateview import StateView, ViewState
from aws95.programs import DEFAULT_MANIFESTS
from aws95.programs.activity import ActivityProgram
from aws95.programs.reports import ReportsProgram
from aws95.providers.demo import demo_provider_set, store
from aws95.shell.registry import Registry

PINNED_NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
GOLDEN_DIR = Path(__file__).resolve().parent / "screenshots" / sys.platform
TOLERANCE = 1.0
PROGRAM_IDS = [m.id for m in DEFAULT_MANIFESTS if m.category == "programs"]
# (program id, golden name) pairs; a second state per program is reached by a step in STATES (M3-R19).
CASES = [(pid, pid) for pid in PROGRAM_IDS] + [("reports", "reports-rendered"), ("insights", "insights-result")]
THEMES = {"light": th.LIGHT, "dark": th.DARK}
REG = Registry(DEFAULT_MANIFESTS)


def perceptual_diff(a: QImage, b: QImage) -> float:
    size = (225, 150)
    ga = a.scaled(*size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation) \
        .convertToFormat(QImage.Format.Format_Grayscale8)
    gb = b.scaled(*size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation) \
        .convertToFormat(QImage.Format.Format_Grayscale8)
    total = 0
    for y in range(size[1]):
        for x in range(size[0]):
            total += abs(ga.pixelColor(x, y).value() - gb.pixelColor(x, y).value())
    return total / (size[0] * size[1])


@pytest.fixture(autouse=True)
def _restore_light(qapp):
    yield
    apply_theme(qapp, th.LIGHT)


@pytest.fixture
def pinned(monkeypatch, qapp):
    from aws95.kit import mascot
    from aws95.kit.widgets import stateview
    # The empty-state mascot is a process-wide global that app startup (and other tests) set; pin it to the
    # shipped artwork so a golden does not depend on test order.
    monkeypatch.setattr(stateview, "_mascot_pixmap", mascot.cloudy_pixmap(48))
    monkeypatch.setattr(store, "now", lambda: PINNED_NOW)
    monkeypatch.setattr(ActivityProgram, "clock", staticmethod(lambda: PINNED_NOW))
    monkeypatch.setattr(ReportsProgram, "clock", staticmethod(lambda: PINNED_NOW))


def _reports_rendered(w, qtbot):
    w.types.setCurrentRow(0)
    qtbot.waitUntil(lambda: bool(w.preview.toPlainText()) and "Generated" in w.preview.toPlainText(), timeout=5000)


def _insights_result(w, qtbot):
    qtbot.waitUntil(lambda: w.consent_box.isEnabled(), timeout=5000)
    w.consent_box.setChecked(True)
    qtbot.waitUntil(lambda: w.analyze_button.isEnabled(), timeout=5000)
    qtbot.mouseClick(w.analyze_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: bool(w.results.toPlainText()), timeout=5000)


STATES = {"reports-rendered": _reports_rendered, "insights-result": _insights_result}


def _wait_stable(qtbot, widget, timeout_ms=2000):
    """Poll every 20 ms until two consecutive grabs are identical; fail after the timeout."""
    last = widget.grab().toImage()
    for _ in range(timeout_ms // 20):
        qtbot.wait(20)
        img = widget.grab().toImage()
        if img == last:
            return
        last = img
    raise AssertionError("widget never reached a stable paint")


@pytest.mark.parametrize("theme_name", list(THEMES))
@pytest.mark.parametrize("pid,name", CASES)
def test_program_screenshot(request, qapp, qtbot, program_env, pinned, pid, name, theme_name):
    apply_theme(qapp, THEMES[theme_name])
    w, _ = program_env(REG.get(pid).load_factory(), demo_provider_set())
    qtbot.addWidget(w)
    w.resize(900, 600)
    w.show()
    qtbot.waitUntil(lambda: w.is_demo and all(v.state is not ViewState.LOADING for v in w.findChildren(StateView)),
                    timeout=5000)
    if name in STATES:
        STATES[name](w, qtbot)
    _wait_stable(qtbot, w)                        # let charts and layouts finish their first paint
    assert w._watermark is not None and w._watermark.isVisible(), "demo watermark must be showing"
    assert w.status.isVisible(), "status strip must be showing"
    image = w.grab().toImage()
    golden = GOLDEN_DIR / f"{name}-{theme_name}.png"
    if request.config.getoption("--update-screenshots"):
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        assert image.save(str(golden))
        return
    if not golden.exists():
        pytest.skip(f"no golden for {sys.platform}: run `uv run pytest tests/test_screenshots.py --update-screenshots`")
    diff = perceptual_diff(image, QImage(str(golden)))
    assert diff <= TOLERANCE, f"{name}/{theme_name} differs from its golden by {diff:.2f} (tolerance {TOLERANCE})"


@pytest.mark.parametrize("theme_name", list(THEMES))
def test_screensavers_screenshot(request, qapp, qtbot, program_env, pinned, theme_name):
    """The Screen Savers accessory has no data (no demo watermark); its preview is frozen at a fixed frame."""
    apply_theme(qapp, THEMES[theme_name])
    w, _ = program_env(REG.get("screensavers").load_factory())
    qtbot.addWidget(w)
    w.resize(640, 420)
    w.show()
    assert w.select_saver("starfield")
    saver = w.monitor.saver
    saver._timer.stop()                           # freeze the animation: a seeded, fixed number of steps
    saver.reset()
    for _ in range(40):
        saver.step()
    saver.update()
    qtbot.wait(50)
    image = w.grab().toImage()
    golden = GOLDEN_DIR / f"screensavers-{theme_name}.png"
    if request.config.getoption("--update-screenshots"):
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        assert image.save(str(golden))
        return
    if not golden.exists():
        pytest.skip(f"no golden for {sys.platform}: run `uv run pytest tests/test_screenshots.py --update-screenshots`")
    diff = perceptual_diff(image, QImage(str(golden)))
    assert diff <= TOLERANCE, f"screensavers/{theme_name} differs from its golden by {diff:.2f} (tolerance {TOLERANCE})"


# -- Setup wizard, PolicyDialog, PermissionsDialog and Accounts at Dark 150 % (phase 2a) ----------------

THEMES3 = {"light": (th.LIGHT, 100), "dark": (th.DARK, 100), "dark-150": (th.DARK, 150)}
_OPENED: list = []


def _compare(request, image: QImage, name: str) -> None:
    golden = GOLDEN_DIR / f"{name}.png"
    if request.config.getoption("--update-screenshots"):
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        assert image.save(str(golden))
        return
    if not golden.exists():
        pytest.skip(f"no golden for {sys.platform}: run `uv run pytest tests/test_screenshots.py --update-screenshots`")
    diff = perceptual_diff(image, QImage(str(golden)))
    assert diff <= TOLERANCE, f"{name} differs from its golden by {diff:.2f} (tolerance {TOLERANCE})"


@pytest.fixture
def dlg_env(tmp_path, qapp):
    """Settings + runner; widgets that delete themselves on close are tracked in _OPENED, not qtbot.addWidget."""
    from PySide6.QtCore import QCoreApplication, QEvent, QSettings
    from shiboken6 import isValid

    from aws95.core.settings import Settings
    from aws95.core.tasks import TaskRunner
    settings = Settings(QSettings(str(tmp_path / "w.ini"), QSettings.Format.IniFormat))
    runner = TaskRunner()
    yield settings, runner
    for w in _OPENED:
        if isValid(w):
            w.close()
    _OPENED.clear()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    runner.shutdown()


def _stub():
    from tests.account_stubs import P, StubAccounts
    return StubAccounts((P("dev"), P("prod")), {"dev": "123456789012", "prod": "210987654321"})


@pytest.mark.parametrize("theme_name", list(THEMES3))
@pytest.mark.parametrize("page", ["welcome", "sources", "choose", "permissions", "names", "finish"])
def test_wizard_screenshot(request, qapp, qtbot, dlg_env, page, theme_name):
    from PySide6.QtWidgets import QWizard

    from aws95.shell.context import NullSound
    from aws95.shell.setup_wizard import PAGE_IDS, SetupWizard
    theme, scale = THEMES3[theme_name]
    apply_theme(qapp, theme, font_scale=scale)
    settings, runner = dlg_env
    w = SetupWizard(settings, runner, _stub(), NullSound())
    _OPENED.append(w)
    w.show()
    nxt = QWizard.WizardButton.NextButton
    while PAGE_IDS[w.currentId()] != page:
        if PAGE_IDS[w.currentId()] == "sources":
            qtbot.waitUntil(lambda: w.button(nxt).isEnabled(), timeout=3000)
        if PAGE_IDS[w.currentId()] == "choose":
            qtbot.waitUntil(lambda: w.choose_page.all_settled(), timeout=5000)
        if PAGE_IDS[w.currentId()] == "permissions":
            qtbot.waitUntil(lambda: w.permissions_page.panel.text_dump().count("Activity Log: Missing") == 2, timeout=3000)
        qtbot.waitUntil(lambda: w.button(nxt).isEnabled(), timeout=3000)
        w.next()
    if page == "sources":
        qtbot.waitUntil(lambda: w.button(nxt).isEnabled(), timeout=3000)
    elif page == "choose":
        qtbot.waitUntil(lambda: w.choose_page.all_settled(), timeout=5000)
    elif page == "permissions":
        qtbot.waitUntil(lambda: w.permissions_page.panel.text_dump().count("Activity Log: Missing") == 2, timeout=3000)
    _wait_stable(qtbot, w)
    _compare(request, w.grab().toImage(), f"wizard-{page}-{theme_name}")


@pytest.mark.parametrize("theme_name", list(THEMES3))
def test_policy_dialog_screenshot(request, qapp, qtbot, dlg_env, theme_name):
    from aws95.shell.setup_wizard import PolicyDialog
    from tests.account_stubs import POLICY
    theme, scale = THEMES3[theme_name]
    apply_theme(qapp, theme, font_scale=scale)
    _, runner = dlg_env
    dlg = PolicyDialog(_stub(), runner)
    _OPENED.append(dlg)
    dlg.show()
    qtbot.waitUntil(lambda: POLICY in dlg.text.toPlainText(), timeout=3000)
    _wait_stable(qtbot, dlg)
    _compare(request, dlg.grab().toImage(), f"policy-{theme_name}")


@pytest.mark.parametrize("theme_name", list(THEMES3))
def test_permissions_dialog_screenshot(request, qapp, qtbot, dlg_env, theme_name):
    from aws95.shell.setup_wizard import PermissionsDialog
    theme, scale = THEMES3[theme_name]
    apply_theme(qapp, theme, font_scale=scale)
    _, runner = dlg_env
    dlg = PermissionsDialog(_stub(), runner, "dev", "123456789012")
    _OPENED.append(dlg)
    dlg.show()
    qtbot.waitUntil(lambda: "Activity Log" in dlg.panel.text_dump(), timeout=3000)
    _wait_stable(qtbot, dlg)
    _compare(request, dlg.grab().toImage(), f"permissions-{theme_name}")


@pytest.mark.parametrize("theme_name", list(THEMES3))
def test_sign_in_dialog_screenshot(request, qapp, qtbot, dlg_env, theme_name):
    """The device prompt (URL and code) with Cancel and Copy Command, held open so the grab is deterministic."""
    import threading

    from aws95.contracts.common import Result
    from aws95.shell.setup_wizard import SignInDialog
    from tests.account_stubs import StubAccounts

    class Held(StubAccounts):
        def sign_in(self, profile="", on_prompt=None, *, cancel=None):
            on_prompt("https://device.sso.us-east-1.amazonaws.com/", "ABCD-EFGH")
            gate.wait(5)
            return Result.error("Sign-in cancelled.")

    gate = threading.Event()
    theme, scale = THEMES3[theme_name]
    apply_theme(qapp, theme, font_scale=scale)
    _, runner = dlg_env
    dlg = SignInDialog(Held((), {}), runner, "corp")
    _OPENED.append(dlg)
    dlg.show()
    qtbot.waitUntil(lambda: "ABCD-EFGH" in dlg.text_dump(), timeout=3000)
    _wait_stable(qtbot, dlg)
    _compare(request, dlg.grab().toImage(), f"signin-{theme_name}")
    gate.set()


def test_accounts_dark_150_screenshot(request, qapp, qtbot, program_env, pinned):
    """Task 7 review: the Accounts window at Dark 150 %."""
    apply_theme(qapp, th.DARK, font_scale=150)
    w, _ = program_env(REG.get("accounts").load_factory(), demo_provider_set())
    qtbot.addWidget(w)
    w.resize(900, 600)
    w.show()
    qtbot.waitUntil(lambda: w.is_demo and all(v.state is not ViewState.LOADING for v in w.findChildren(StateView)),
                    timeout=5000)
    _wait_stable(qtbot, w)
    assert w._watermark is not None and w._watermark.isVisible()
    _compare(request, w.grab().toImage(), "accounts-dark-150")


def test_policy_dialog_long_policy_dark_150(request, qapp, qtbot, dlg_env):
    """A realistic multi-statement policy (about 60 actions) at Dark 150 %: legible, and it scrolls."""
    import json

    from aws95.shell.setup_wizard import PolicyDialog
    services = {"ec2": ["DescribeInstances", "DescribeVolumes", "DescribeSnapshots", "DescribeSecurityGroups",
                        "DescribeVpcs", "DescribeSubnets", "DescribeRegions", "DescribeAddresses"],
                "s3": ["ListAllMyBuckets", "GetBucketLocation", "GetBucketPolicyStatus", "GetBucketVersioning",
                       "GetEncryptionConfiguration", "GetBucketPublicAccessBlock", "GetBucketTagging"],
                "iam": ["ListUsers", "ListRoles", "ListAccessKeys", "GetAccountSummary", "ListMFADevices",
                        "GetAccountPasswordPolicy", "ListAttachedRolePolicies", "ListPolicies"],
                "lambda": ["ListFunctions", "ListTags", "GetAccountSettings"],
                "rds": ["DescribeDBInstances", "DescribeDBClusters", "DescribeDBSnapshots"],
                "cloudtrail": ["LookupEvents", "DescribeTrails", "GetTrailStatus"],
                "ce": ["GetCostAndUsage", "GetCostForecast"],
                "sts": ["GetCallerIdentity"],
                "ecs": ["ListClusters", "ListServices", "DescribeServices"],
                "dynamodb": ["ListTables", "DescribeTable"]}
    policy = json.dumps({"Version": "2012-10-17", "Statement": [
        {"Sid": f"Read{svc.upper()}", "Effect": "Allow", "Action": [f"{svc}:{a}" for a in acts], "Resource": "*"}
        for svc, acts in services.items()]}, indent=2)
    assert sum(len(a) for a in services.values()) >= 40
    apply_theme(qapp, th.DARK, font_scale=150)
    _, runner = dlg_env
    acc = _stub()
    acc.required_policy = lambda *, cancel=None: acc._r(policy)
    dlg = PolicyDialog(acc, runner)
    _OPENED.append(dlg)
    dlg.show()
    qtbot.waitUntil(lambda: "ReadEC2" in dlg.text.toPlainText(), timeout=3000)
    _wait_stable(qtbot, dlg)
    assert dlg.text.verticalScrollBar().maximum() > 0, "a long policy must scroll"
    _compare(request, dlg.grab().toImage(), "policy-long-dark-150")


def _prompt_accounts():
    from aws95.contracts.accounts import RosterEntry
    from aws95.shell.snapshot_prompt import PromptAccount
    return (PromptAccount(RosterEntry("dev", "123456789012"), "dev", ("eu-west-1", "us-east-1"), False),
            PromptAccount(RosterEntry("prod", "210987654321"), "prod", ("me-south-1", "us-east-1"), True))


@pytest.mark.parametrize("theme_name", list(THEMES3))
def test_snapshot_prompt_screenshot(request, qapp, qtbot, dlg_env, theme_name):
    from aws95.shell.snapshot_prompt import SnapshotPrompt
    theme, scale = THEMES3[theme_name]
    apply_theme(qapp, theme, font_scale=scale)
    dlg = SnapshotPrompt("It's been 3 days since your last snapshot (dev, prod). Take one now?", _prompt_accounts(), ())
    _OPENED.append(dlg)
    dlg.show()
    _wait_stable(qtbot, dlg)
    _compare(request, dlg.grab().toImage(), f"snapshot-prompt-{theme_name}")


@pytest.mark.parametrize("theme_name", list(THEMES3))
def test_regions_dialog_screenshot(request, qapp, qtbot, dlg_env, theme_name):
    from aws95.shell.snapshot_prompt import RegionsDialog
    theme, scale = THEMES3[theme_name]
    apply_theme(qapp, theme, font_scale=scale)
    settings, _ = dlg_env
    dlg = RegionsDialog(settings, _prompt_accounts())
    _OPENED.append(dlg)
    dlg.show()
    _wait_stable(qtbot, dlg)
    _compare(request, dlg.grab().toImage(), f"regions-dialog-{theme_name}")


@pytest.mark.parametrize("theme_name", list(THEMES3))
def test_palette_hits_screenshot(request, qapp, qtbot, theme_name):
    """2d spec §6: Ctrl+K with store hits in Light, Dark and Dark at 150 %."""
    from aws95.contracts.common import Result
    from aws95.contracts.search import SearchHit
    from aws95.shell.palette import CommandPalette, PaletteItem
    from tests.test_palette_search import Searcher
    theme, scale = THEMES3[theme_name]
    apply_theme(qapp, theme, font_scale=scale)
    s = Searcher()
    pal = CommandPalette(search=s, open_hit=[].append, icon_for=lambda p: {"resources": "server", "findings": "shield",
                                                                          "activity": "magnifier",
                                                                          "billing": "money"}.get(p, ""))
    qtbot.addWidget(pal)
    pal.set_items([PaletteItem("Programs", "Snapshots", lambda: None, icon="camera"),
                   PaletteItem("Commands", "Take Snapshot", lambda: None, icon="camera")])
    pal.resize(560 * scale // 100, 420 * scale // 100)
    pal.show()
    pal.query_edit.setText("pro")
    qtbot.waitUntil(lambda: bool(s.handles), timeout=2000)
    s.handles[-1].finished.emit(Result.ok((
        SearchHit("resource", "prod-data", "s3.bucket · global · 210987654321", "resources", {}),
        SearchHit("finding", "s3.no_lifecycle", "prod-data · 210987654321", "findings", {}),
        SearchHit("principal", "prod-deployer", "AssumedRole · 42 events", "activity", {}),
        SearchHit("service", "Amazon EC2 (prod)", "Billing · 2026-10", "billing", {}))))
    _wait_stable(qtbot, pal)
    _compare(request, pal.grab().toImage(), f"palette-hits-{theme_name}")


@pytest.mark.parametrize("theme_name", list(THEMES3))
@pytest.mark.parametrize("key", ["account", "region", "usage_type", "uncovered"])
def test_billing_tab_screenshot(request, qapp, qtbot, program_env, pinned, key, theme_name):
    """Billing-complete spec §8: the four new tabs in Light, Dark and Dark at 150 %."""
    from aws95.programs.billing import make_billing
    theme, scale = THEMES3[theme_name]
    apply_theme(qapp, theme, font_scale=scale)
    w, _ = program_env(make_billing, demo_provider_set())
    qtbot.addWidget(w)
    w.resize(900 * scale // 100, 600 * scale // 100)
    w.show()
    w.select_tab(key)
    qtbot.waitUntil(lambda: bool(w.pages[key].grid.visible_rows()), timeout=5000)
    _wait_stable(qtbot, w)
    _compare(request, w.grab().toImage(), f"billing-{key}-{theme_name}")


@pytest.mark.parametrize("theme_name", list(THEMES3))
def test_resources_related_screenshot(request, qapp, qtbot, program_env, pinned, theme_name):
    """Activity-polish spec §6: the inspector's Related tab with matching events."""
    from PySide6.QtWidgets import QTreeWidgetItemIterator
    from aws95.programs.resources import RTYPE_ROLE, make_resources
    theme, scale = THEMES3[theme_name]
    apply_theme(qapp, theme, font_scale=scale)
    w, _ = program_env(make_resources, demo_provider_set())
    qtbot.addWidget(w)
    w.resize(1100 * scale // 100, 600 * scale // 100)
    w.show()
    qtbot.waitUntil(lambda: bool(w.grid.visible_rows()), timeout=5000)
    it = QTreeWidgetItemIterator(w.tree)
    while it.value() is not None and it.value().data(0, RTYPE_ROLE) != "ec2.instance":
        it += 1
    w.tree.setCurrentItem(it.value())
    qtbot.waitUntil(lambda: any(r["rid"] == "i-0a1b2c3d4e5f60001" for r in w.grid.visible_rows()), timeout=5000)
    index = next(i for i, r in enumerate(w.grid.visible_rows()) if r["rid"] == "i-0a1b2c3d4e5f60001")
    w.grid.view.selectRow(index)
    w.inspector.tabs.setCurrentWidget(w.related)
    qtbot.waitUntil(lambda: bool(w.related.grid.visible_rows()), timeout=5000)
    _wait_stable(qtbot, w)
    _compare(request, w.grab().toImage(), f"resources-related-{theme_name}")


@pytest.mark.parametrize("theme_name", list(THEMES3))
def test_run_tab_running_screenshot(request, qapp, qtbot, program_env, pinned, theme_name):
    """Activity-polish spec §4.3: the Run tab while a snapshot runs."""
    import dataclasses
    from datetime import datetime, timedelta, timezone
    from aws95.contracts.snapshots import AccountStage, SnapshotSection
    from aws95.programs.snapshots import make_snapshots
    from tests.test_program_snapshots_run import Fake
    now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
    b = "210987654321"
    stages = (AccountStage(b, 2, now, 12, 42, 30, 0, ("lambda eu-west-1",), False, now + timedelta(hours=1)),)
    sections = (SnapshotSection(b, 2, "ec2", "eu-west-1", "done", now, now + timedelta(seconds=4), 7),
                SnapshotSection(b, 2, "lambda", "eu-west-1", "running", now),
                SnapshotSection(b, 2, "s3", "global", "waiting"))
    theme, scale = THEMES3[theme_name]
    apply_theme(qapp, theme, font_scale=scale)
    ps = dataclasses.replace(demo_provider_set(), snapshots=Fake(stages, sections), is_demo=False, is_live=True)
    w, ctx = program_env(make_snapshots, ps)
    qtbot.addWidget(w)
    w.resize(900 * scale // 100, 600 * scale // 100)
    w.show()
    w.tabs.setCurrentWidget(w.run_tab)
    ctx.shell.snapshot_progress.emit("prod: 12/42 sections · lambda eu-west-1")
    qtbot.waitUntil(lambda: len(w.run_tab.grid.visible_rows()) == 3 and bool(w.run_tab.stage_labels), timeout=5000)
    assert w.run_tab.cancel_button.isEnabled() and not w.run_tab.resume_button.isEnabled()
    _wait_stable(qtbot, w)
    _compare(request, w.grab().toImage(), f"snapshots-run-running-{theme_name}")


@pytest.mark.parametrize("theme_name", list(THEMES3))
@pytest.mark.parametrize("which", ["rules", "seed", "backfill"])
def test_findings_tools_screenshot(request, qapp, qtbot, program_env, pinned, which, theme_name):
    """Findings-tools spec §8: the Rules editor, Seed and Backfill dialogs."""
    from aws95.programs.findings import make_findings
    theme, scale = THEMES3[theme_name]
    apply_theme(qapp, theme, font_scale=scale)
    w, _ = program_env(make_findings, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: bool(w.grid.visible_rows()), timeout=5000)
    if which == "rules":
        dlg = w.open_rules_dialog()
        dlg.resize(820 * scale // 100, 620 * scale // 100)
        qtbot.waitUntil(lambda: bool(dlg.table.visible_rows()), timeout=5000)
        dlg.select_rule("iam-access-key-old")                # a demo rule with a parameter
    elif which == "seed":
        w.grid.view.selectRow(0)
        dlg = w.open_seed_dialog()
    else:
        from PySide6.QtCore import QDate
        dlg = w.open_backfill_dialog()
        dlg.since_edit.setDate(QDate(2026, 9, 20))          # the default follows today's date; pin it
    qtbot.addWidget(dlg)
    _wait_stable(qtbot, dlg)
    _compare(request, dlg.grab().toImage(), f"findings-{which}-{theme_name}")


@pytest.mark.parametrize("theme_name", list(THEMES3))
def test_insights_live_screenshot(request, qapp, qtbot, program_env, pinned, theme_name):
    """AI-insights spec §10: per-account models, the estimate line and an answer."""
    import dataclasses
    from aws95.contracts.accounts import RosterEntry
    from aws95.contracts.insights import AccountInsight, InsightReport, InsightSection
    from aws95.programs.insights import make_insights
    from tests.test_program_insights_live import A, B, Stub
    report = InsightReport("insights", "anthropic.m-2", (), (
        AccountInsight("Account 1", "anthropic.m-2", (InsightSection("Cost", ("EC2 is the largest service this month.",
                                                                              "Lambda spend is small.")),
                                                      InsightSection("Findings", ("3 high findings are open.",))),
                       1234, 210),
        AccountInsight("Account 2", "anthropic.m-2", error="Model access isn't enabled for anthropic.m-2 in this "
                                                           "account. Request it in the Bedrock console.")))
    theme, scale = THEMES3[theme_name]
    apply_theme(qapp, theme, font_scale=scale)
    ps = dataclasses.replace(demo_provider_set(), insights=Stub(report), is_demo=False, is_live=True)
    w, ctx = program_env(make_insights, ps)
    ctx.settings.set_roster((RosterEntry("demo-a", A), RosterEntry("demo-b", B)))
    ctx.settings.set_insights_model(A, "anthropic.m-2")
    ctx.settings.set_insights_model(B, "anthropic.m-2")
    qtbot.addWidget(w)
    w.resize(1000 * scale // 100, 640 * scale // 100)
    w.show()
    w.refresh()
    qtbot.waitUntil(lambda: w.consent_box.isEnabled() and len(w.model_pickers) == 2, timeout=5000)
    w.refresh_models_button.click()
    qtbot.waitUntil(lambda: all(p.count() == 2 for p in w.model_pickers.values()), timeout=5000)
    w.consent_box.setChecked(True)
    w.analyze()
    qtbot.waitUntil(lambda: "Cost" in w.results.toPlainText(), timeout=5000)
    _wait_stable(qtbot, w)
    _compare(request, w.grab().toImage(), f"insights-live-{theme_name}")
