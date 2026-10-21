"""Spec §7: the Activity job dialogs and (Task 9) the live-populated Activity window, Light, Dark and Dark-150 %."""
import pytest

from aws95.contracts.common import Result
from aws95.kit.style import apply_theme
from aws95.contracts.activity import AccessCheck, TrailLookup
from tests.test_activity_dialogs import ACCOUNTS, DEV, ESTIMATES, KMS, TODAY, TRAIL
from tests.test_screenshots import THEMES3, _compare, _wait_stable

POLICY = ('[\n  {\n    "Sid": "AWS95DecryptTrailLogs",\n    "Effect": "Allow",\n    "Action": "kms:Decrypt",\n'
          f'    "Resource": "{KMS}"\n  }}\n]')
KMS_TEXT = ("The trail's log files are encrypted with a KMS key this profile can't use. Add kms:Decrypt on that key "
            "(the key's own policy must allow it too):")
ORG_NOTE = ("This is an organisation trail. Its log files usually live in a bucket in your log-archive account, "
            "which this profile may not be able to read. Choose that account's profile under “Read the files with”.")


@pytest.fixture
def opened(qapp):
    made = []
    yield made
    for w in made:
        w.close()


@pytest.mark.parametrize("theme_name", list(THEMES3))
def test_first_use_dialog_screenshot(request, qapp, qtbot, opened, theme_name):
    from aws95.shell.activity_dialogs import LoadHistoryDialog
    theme, scale = THEMES3[theme_name]
    apply_theme(qapp, theme, font_scale=scale)
    dlg = LoadHistoryDialog(ACCOUNTS)
    opened.append(dlg)
    dlg.set_estimates(Result.ok(ESTIMATES))
    dlg.show()
    _wait_stable(qtbot, dlg)
    _compare(request, dlg.grab().toImage(), f"activity-first-use-{theme_name}")


@pytest.mark.parametrize("theme_name", list(THEMES3))
def test_import_dialog_screenshot(request, qapp, qtbot, opened, theme_name):
    from aws95.shell.activity_dialogs import ImportHistoryDialog
    theme, scale = THEMES3[theme_name]
    apply_theme(qapp, theme, font_scale=scale)
    dlg = ImportHistoryDialog(ACCOUNTS, today=TODAY)
    opened.append(dlg)
    dlg.set_trails(Result.ok(TrailLookup(DEV, (TRAIL,), ORG_NOTE)))
    dlg.show()
    dlg.check_button.click()
    dlg.set_check(Result.ok(AccessCheck(False, KMS_TEXT, POLICY)))
    _wait_stable(qtbot, dlg)
    _compare(request, dlg.grab().toImage(), f"activity-import-{theme_name}")


@pytest.mark.parametrize("theme_name", list(THEMES3))
def test_live_activity_window_screenshot(request, qapp, qtbot, tmp_path, monkeypatch, theme_name):
    from PySide6.QtCore import QSettings

    from aws95.core.settings import Settings
    from aws95.core.tasks import TaskRunner
    from aws95.kit import mascot
    from aws95.kit.widgets import stateview, statusstrip
    from aws95.kit.widgets.stateview import StateView, ViewState
    from aws95.programs.activity import ActivityProgram, make_activity
    from aws95.providers.live import live_provider_set
    from aws95.shell.context import NullSound, ProgramContext
    from tests.activity_seed import DEV, PINNED, PROD, ROSTER, seed_activity
    from tests.conftest import RecordingShell
    theme, scale = THEMES3[theme_name]
    apply_theme(qapp, theme, font_scale=scale)
    monkeypatch.setattr(ActivityProgram, "clock", staticmethod(lambda: PINNED))
    monkeypatch.setattr(statusstrip, "_utcnow", lambda: PINNED)
    monkeypatch.setattr(stateview, "_mascot_pixmap", mascot.cloudy_pixmap(48))
    settings = Settings(QSettings(str(tmp_path / "live.ini"), QSettings.Format.IniFormat))
    settings.set_roster(ROSTER)
    settings.set_account_label(DEV, "dev", "#2e8b57")
    settings.set_account_label(PROD, "prod", "#b22222")
    runner = TaskRunner()
    store = seed_activity(tmp_path / "aws95.db")
    try:
        providers = live_provider_set(settings, store=store, clock=lambda: PINNED)
        w = make_activity(ProgramContext(settings, runner, providers, NullSound(), RecordingShell()))
        qtbot.addWidget(w)
        w.resize(900, 600)
        w.show()
        qtbot.waitUntil(lambda: w.history_panel.isVisible() and all(
            v.state is not ViewState.LOADING for v in w.findChildren(StateView)), timeout=5000)
        _wait_stable(qtbot, w)
        assert not w.is_demo and w.history_panel.action == "resume"
        _compare(request, w.grab().toImage(), f"activity-live-{theme_name}")
    finally:
        runner.shutdown()
        store.close()
