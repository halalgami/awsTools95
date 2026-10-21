import pytest
from PySide6.QtCore import QSettings

from aws95.contracts.activity import NO_HISTORY
from aws95.core.settings import Settings
from aws95.core.tasks import TaskRunner
from aws95.kit.widgets.stateview import ViewState
from aws95.programs.activity import ActivityProgram, make_activity
from aws95.providers.demo import demo_provider_set
from aws95.shell.context import NullSound, ProgramContext
from aws95.store import Store
from tests.activity_seed import PINNED, ROSTER, seed_activity
from tests.conftest import RecordingShell


@pytest.fixture
def live_env(tmp_path, qapp, monkeypatch):
    from aws95.providers.live import live_provider_set
    monkeypatch.setattr(ActivityProgram, "clock", staticmethod(lambda: PINNED))
    settings = Settings(QSettings(str(tmp_path / "w.ini"), QSettings.Format.IniFormat))
    settings.set_roster(ROSTER)
    runner = TaskRunner()
    stores = []

    def make(store):
        stores.append(store)                       # closed at teardown (-W error: no unclosed database)
        providers = live_provider_set(settings, store=store, clock=lambda: PINNED)
        ctx = ProgramContext(settings, runner, providers, NullSound(), RecordingShell())
        return make_activity(ctx), ctx
    yield make
    runner.shutdown()
    for store in stores:
        store.close()


def shown(w, qtbot):
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.overview.series_view.state is not ViewState.LOADING, timeout=5000)
    return w


def calls(ctx, name):
    return [args for n, args in ctx.shell.calls if n == name]


def test_live_window_shows_history_and_resume(live_env, qtbot, tmp_path):
    w, ctx = live_env(seed_activity(tmp_path / "a.db"))
    shown(w, qtbot)
    panel = w.history_panel
    qtbot.waitUntil(lambda: panel.isVisible() and "Denied change attempts: covered." in panel.summary_label.text(),
                    timeout=5000)
    assert "210987654321: looked up" in panel.summary_label.text() and "(unfinished" in panel.summary_label.text()
    assert panel.action == "resume" and panel.action_button.text() == "Resume Loa&ding"
    panel.action_button.click()
    assert calls(ctx, "resume_activity") == [((),)]
    qtbot.waitUntil(lambda: w.overview.series_view.state is ViewState.CONTENT, timeout=5000)


def test_live_window_without_history_offers_load_and_never_shows_empty(live_env, qtbot, tmp_path):
    w, ctx = live_env(Store(tmp_path / "empty.db"))
    shown(w, qtbot)
    panel = w.history_panel
    qtbot.waitUntil(lambda: panel.action == "load", timeout=5000)
    assert panel.summary_label.text() == NO_HISTORY and panel.action_button.text() == "Loa&d Activity History…"
    assert w.overview.series_view.state is ViewState.NOT_CONNECTED
    panel.action_button.click()
    assert calls(ctx, "load_activity") == [((),)]


def test_f5_updates_in_live_and_only_rereads_in_demo(live_env, program_env, qtbot, tmp_path):
    w, ctx = live_env(seed_activity(tmp_path / "a.db"))
    shown(w, qtbot)
    w.user_refresh()
    assert calls(ctx, "update_activity") == [((),)]
    d, dctx = program_env(make_activity, demo_provider_set())
    shown(d, qtbot)
    d.user_refresh()
    d.refresh_action.trigger()
    assert calls(dctx, "update_activity") == []


def test_demo_strip_has_no_action_and_offline_hides_it(program_env, qtbot):
    d, _ = program_env(make_activity, demo_provider_set())
    shown(d, qtbot)
    qtbot.waitUntil(lambda: d.history_panel.isVisible(), timeout=5000)
    assert d.history_panel.action is None and not d.history_panel.action_button.isVisible()
    o, _ = program_env(make_activity)
    shown(o, qtbot)
    qtbot.waitUntil(lambda: o.overview.series_view.state is ViewState.NOT_CONNECTED, timeout=5000)
    qtbot.waitUntil(lambda: o.history_panel.view.state is not ViewState.LOADING, timeout=5000)
    assert not o.history_panel.isVisible()


def test_progress_shows_in_the_status_bar_and_refreshes_at_most_every_15_s(live_env, qtbot, tmp_path):
    w, ctx = live_env(seed_activity(tmp_path / "a.db"))
    shown(w, qtbot)
    count = []
    w.refresh = lambda: count.append(1)
    ctx.shell.activity_progress.emit("Activity dev: 1/7 days · us-east-1")
    ctx.shell.activity_progress.emit("Activity dev: 2/7 days · us-east-1")
    assert w.status.counts_label.text() == "Activity dev: 2/7 days · us-east-1" and len(count) == 1
    ctx.shell.activity_finished.emit(None)
    assert len(count) == 2
    ctx.shell.activity_progress.emit("")
    assert w.status.counts_label.text() != "Activity dev: 2/7 days · us-east-1"


def test_menu_actions_call_the_shell(live_env, qtbot, tmp_path):
    w, ctx = live_env(seed_activity(tmp_path / "a.db"))
    for action in (w.load_action, w.resume_action, w.cancel_action, w.import_action):
        action.trigger()
    names = [n for n, _ in ctx.shell.calls]
    assert names[-4:] == ["load_activity", "resume_activity", "cancel_activity", "import_activity_history"]
    assert calls(ctx, "import_activity_history") == [("",)]


def test_retention_dialog_saves_and_prunes(live_env, qtbot, tmp_path):
    w, ctx = live_env(seed_activity(tmp_path / "a.db"))
    dlg = w.open_retention()
    assert dlg.days_spin.value() == 90 and dlg.days_spin.minimum() == 1 and dlg.days_spin.maximum() == 3650
    dlg.days_spin.setValue(30)
    dlg.save_button.click()
    assert ctx.settings.activity_retention_days == 30 and calls(ctx, "prune_activity") == [()]
    assert ctx.providers.activity.retention_days == 30


def test_retention_dialog_is_read_only_in_demo(program_env, qtbot):
    d, dctx = program_env(make_activity, demo_provider_set())
    dlg = d.open_retention()
    assert not dlg.save_button.isEnabled() and dlg.note_label.text().endswith("Demo mode never writes settings.")


def test_history_strip_fits_its_text_at_dark_150(live_env, qapp, qtbot, tmp_path):
    """Dark and 150 % font are Important: the wrapped coverage lines must never be clipped."""
    from aws95.kit import theme as th
    from aws95.kit.style import apply_theme
    apply_theme(qapp, th.DARK, font_scale=150)
    try:
        w, _ = live_env(seed_activity(tmp_path / "a.db"))
        w.resize(900, 1034)
        shown(w, qtbot)
        label = w.history_panel.summary_label
        qtbot.waitUntil(lambda: "Denied change attempts" in label.text(), timeout=5000)
        qtbot.wait(50)
        assert label.height() >= label.heightForWidth(label.width())
    finally:
        apply_theme(qapp, th.LIGHT, font_scale=100)
