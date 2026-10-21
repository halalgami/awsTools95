"""Spec §7: every data window in its live-populated state over a seeded store, Light, Dark and Dark-150 %."""
import pytest
from PySide6.QtCore import QSettings

from aws95.kit import theme as th
from aws95.kit.style import apply_theme
from aws95.kit.widgets import statusstrip
from aws95.kit.widgets.stateview import StateView, ViewState
from aws95.programs import DEFAULT_MANIFESTS
from aws95.shell.registry import Registry
from tests.account_stubs import FakeSignal
from tests.store_seed import DEV, PINNED, PROD, ROSTER, seed_store
from tests.test_screenshots import THEMES3, _compare, _wait_stable

REG = Registry(DEFAULT_MANIFESTS)


class _Shell:
    def __init__(self):
        self.sign_in_succeeded = FakeSignal()
        self.snapshot_finished = FakeSignal()
        self.snapshot_progress = FakeSignal()

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a, **k: None


@pytest.fixture
def live_ctx(tmp_path, qapp, monkeypatch):
    from aws95.core.settings import Settings
    from aws95.core.tasks import TaskRunner
    from aws95.kit import mascot
    from aws95.kit.widgets import stateview
    from aws95.providers.live import live_provider_set
    from aws95.shell.context import NullSound, ProgramContext
    # The Accounts header shows these paths; under a temp HOME they read "~/.aws/…" on every run (no random digits).
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".aws").mkdir()
    config = tmp_path / ".aws" / "config"
    config.write_text("[profile dev]\nregion = eu-west-1\n\n[profile prod]\nregion = us-east-1\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / ".aws" / "credentials"))
    monkeypatch.setattr(statusstrip, "_utcnow", lambda: PINNED)
    monkeypatch.setattr(stateview, "_mascot_pixmap", mascot.cloudy_pixmap(48))
    settings = Settings(QSettings(str(tmp_path / "live.ini"), QSettings.Format.IniFormat))
    settings.set_roster(ROSTER)
    settings.set_account_label(DEV, "dev", "#2e8b57")
    settings.set_account_label(PROD, "prod", "#b22222")
    store = seed_store(tmp_path / "aws95.db")
    providers = live_provider_set(settings, store=store, clock=lambda: PINNED)
    runner = TaskRunner()
    yield ProgramContext(settings, runner, providers, NullSound(), _Shell())
    runner.shutdown()
    store.close()


# StateView starts in CONTENT, so "not LOADING" is no proof the data arrived: each step waits on render evidence.
def _resources(w, qtbot):
    qtbot.waitUntil(lambda: w.select_rtype("ec2.instance"), timeout=5000)
    qtbot.waitUntil(lambda: len(w.grid.visible_rows()) == 3, timeout=5000)


def _snapshots(w, qtbot):
    qtbot.waitUntil(lambda: len(w.history.visible_rows()) > 0, timeout=5000)
    w.history.view.selectRow(0)
    qtbot.waitUntil(lambda: len(w.health.visible_rows()) > 0, timeout=5000)


def _accounts(w, qtbot):
    # Every async part of the window has landed before the grab: the config paths, the profile count, the health.
    qtbot.waitUntil(lambda: "~/.aws/config" in w.header_label.text()
                    and w.status.counts_label.text().startswith("2 of 2 profiles"), timeout=5000)
    qtbot.waitUntil(lambda: w.select_profile("prod"), timeout=5000)
    qtbot.waitUntil(lambda: len(w.health.visible_rows()) > 0, timeout=5000)
    from PySide6.QtWidgets import QScrollArea
    for area in w.findChildren(QScrollArea):
        if area.isAncestorOf(w.health_view):
            # Collector health is the point here; scroll to the bottom exactly (ensureWidgetVisible can land 1 px
            # apart from run to run, which made this golden flaky).
            area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())


def _findings(w, qtbot):
    qtbot.waitUntil(lambda: len(w.grid.visible_rows()) > 0, timeout=5000)


def _billing(w, qtbot):
    qtbot.waitUntil(lambda: w.mtd_label.text() != "—", timeout=5000)


def _fleet(w, qtbot):
    qtbot.waitUntil(lambda: w.matrix.rowCount() > 0, timeout=5000)
    last = lambda: w.matrix.rowCount() - 1                              # noqa: E731
    # Billing complete: the cost row shows dev's month to date (prod has no Billing: "—" with the reason).
    qtbot.waitUntil(lambda: any(w.matrix.item(last(), c) is not None and "USD" in w.matrix.item(last(), c).text()
                                for c in range(w.matrix.columnCount())), timeout=5000)


STEPS = {"resources": _resources, "snapshots": _snapshots, "accounts": _accounts, "findings": _findings,
         "billing": _billing, "fleet": _fleet}


@pytest.mark.parametrize("theme_name", list(THEMES3))
@pytest.mark.parametrize("pid", ["resources", "findings", "fleet", "snapshots", "billing", "accounts"])
def test_live_window_screenshot(request, qapp, qtbot, live_ctx, pid, theme_name):
    theme, scale = THEMES3[theme_name]
    apply_theme(qapp, theme, font_scale=scale)
    w = REG.get(pid).load_factory()(live_ctx)
    qtbot.addWidget(w)
    w.resize(900 * scale // 100, 600 * scale // 100)       # a larger font gets a proportionally larger window
    w.show()
    qtbot.waitUntil(lambda: all(v.state is not ViewState.LOADING for v in w.findChildren(StateView)), timeout=5000)
    if pid in STEPS:
        STEPS[pid](w, qtbot)
    _wait_stable(qtbot, w)
    assert not w.is_demo
    _compare(request, w.grab().toImage(), f"live-{pid}-{theme_name}")


@pytest.mark.parametrize("theme_name", list(THEMES3))
def test_live_run_tab_screenshot(request, qapp, qtbot, live_ctx, theme_name):
    """Resumable-snapshots spec §9: the Run tab in Light, Dark and Dark at 150 %."""
    theme, scale = THEMES3[theme_name]
    apply_theme(qapp, theme, font_scale=scale)
    w = REG.get("snapshots").load_factory()(live_ctx)
    qtbot.addWidget(w)
    w.resize(900 * scale // 100, 600 * scale // 100)
    w.show()
    w.tabs.setCurrentWidget(w.run_tab)
    qtbot.waitUntil(lambda: len(w.run_tab.grid.visible_rows()) > 0 and bool(w.run_tab.stage_labels), timeout=5000)
    _wait_stable(qtbot, w)
    assert not w.is_demo
    _compare(request, w.grab().toImage(), f"live-snapshots-run-{theme_name}")
