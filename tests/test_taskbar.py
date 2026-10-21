import threading

import pytest
from PySide6.QtCore import QSettings, QTime

from aws95.core.settings import Settings
from aws95.core.tasks import TaskRunner
from aws95.programs import DEFAULT_MANIFESTS
from aws95.providers.empty import empty_provider_set
from aws95.shell.context import NullSound, ProgramContext
from aws95.shell.desktop import Desktop
from aws95.shell.registry import Registry
from aws95.shell.taskbar import Clock, Taskbar


class FakeShell:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a))


@pytest.fixture
def env(tmp_path, qapp, qtbot):
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    runner = TaskRunner()
    shell = FakeShell()
    ctx = ProgramContext(settings, runner, empty_provider_set(), NullSound(), shell)
    desk = Desktop(Registry(DEFAULT_MANIFESTS), lambda: ctx, settings)
    bar = Taskbar(shell, desk, settings, runner)
    for w in (desk, bar):
        qtbot.addWidget(w)
    desk.resize(1000, 700)
    bar.resize(1000, 30)
    desk.show()
    bar.show()
    yield desk, bar, settings, runner, shell
    runner.shutdown()


def test_clock_format(qapp):
    c = Clock()
    c.refresh(QTime(21, 41))
    assert c.text() == "9:41 PM"


def test_buttons_follow_windows(env, qtbot):
    desk, bar, *_ = env
    a = desk.open_program("billing")
    b = desk.open_program("findings")
    qtbot.waitUntil(lambda: all(x.width() == 160 for x in bar.buttons()))
    assert [btn.text() for btn in bar.buttons()] == ["Billing", "Findings"]
    assert bar.button_for(b).isChecked() and not bar.button_for(a).isChecked()
    a.close()
    qtbot.waitUntil(lambda: len(bar.buttons()) == 1)


def test_button_click_minimizes_then_restores(env):
    desk, bar, *_ = env
    w = desk.open_program("billing")
    bar.button_for(w).click()
    assert w.is_minimized_to_taskbar
    bar.button_for(w).click()
    assert not w.is_minimized_to_taskbar and desk.active_program_window() is w


def test_start_button_asks_shell(env):
    _d, bar, _s, _r, shell = env
    bar.start_button.click()
    assert ("show_start_menu", ()) in shell.calls


def test_speaker_toggles_mute(env):
    _d, bar, settings, *_ = env
    assert settings.sound_muted is False
    bar.tray.speaker_button.click()
    assert settings.sound_muted is True


def test_job_indicator_tracks_runner(env, qtbot):
    _d, bar, _s, runner, _sh = env
    assert not bar.tray.job_progress.isVisible()
    gate = threading.Event()
    h = runner.submit(gate.wait, 5)
    assert bar.tray.job_progress.isVisible()
    gate.set()
    qtbot.waitSignal(h.settled, timeout=3000)
    qtbot.waitUntil(lambda: not bar.tray.job_progress.isVisible())


def test_account_label(env):
    _d, bar, settings, *_ = env
    assert bar.tray.account_button.text() == "No account"
    settings.active_account = "lab-sandbox"
    assert bar.tray.account_button.text() == "lab-sandbox"


def test_context_menu_actions_call_shell(env):
    _d, bar, _s, _r, shell = env
    menu = bar.build_context_menu()
    {a.text(): a for a in menu.actions()}["&Cascade"].trigger()
    assert ("cascade", ()) in shell.calls


def test_title_change_updates_button_text(env, qtbot):
    desk, bar, *_ = env
    w = desk.open_program("billing")
    w.set_demo(True)
    btn = bar.button_for(w)
    qtbot.waitUntil(lambda: btn.width() == 160)
    assert btn.text() == "Billing [DEMO]"
    assert btn.toolTip() == "Billing [DEMO]"


def test_chip_change_repaints_button(env, qtbot):
    desk, bar, *_ = env
    w = desk.open_program("billing")
    btn = bar.button_for(w)
    w.set_scope_chip("PROD", "#ff0000")
    img = btn.grab().toImage()
    assert img.pixelColor(btn.width() // 2, btn.height() - 4).name() == "#ff0000"




def test_many_windows_shrink_buttons(env, qtbot):
    desk, bar, *_ = env
    for _ in range(15):
        desk.open_program("resources")
    bar.resize(1000, 30)
    bar.layout().activate()
    assert len(bar.buttons()) == 15
    assert bar.tray.geometry().right() <= bar.width()
    assert bar.minimumSizeHint().width() < 15 * 160
    bound = 15 * 40 + bar.start_button.sizeHint().width() + bar.tray.sizeHint().width() + 15 * 3 + 60
    assert bar.minimumSizeHint().width() <= bound
    assert all(40 <= b.width() <= 160 for b in bar.buttons())
    narrow = bar.buttons()[0]
    assert narrow.toolTip() == "Resources"


def test_long_account_is_capped(env):
    _d, bar, settings, *_ = env
    settings.active_account = "x" * 200
    assert bar.tray.account_button.maximumWidth() == 140
    assert bar.tray.account_button.sizeHint().width() <= 140
    assert bar.tray.account_button.toolTip() == "x" * 200


def test_job_indicator_seeded_at_construction(env, qtbot):
    desk, _bar, settings, runner, shell = env
    gate = threading.Event()
    h = runner.submit(gate.wait, 5)
    late = Taskbar(shell, desk, settings, runner)
    qtbot.addWidget(late)
    late.show()
    assert late.tray.job_progress.isVisible()
    gate.set()
    qtbot.waitSignal(h.settled, timeout=3000)


def test_minimize_hands_checked_state_to_next_mru(env):
    desk, bar, *_ = env
    a = desk.open_program("billing")
    b = desk.open_program("findings")
    bar.button_for(b).click()
    assert b.is_minimized_to_taskbar
    assert bar.button_for(a).isChecked() and not bar.button_for(b).isChecked()


def test_height_follows_font_scale(env, qapp):
    from aws95.kit import theme as th
    from aws95.kit.style import apply_theme
    _desk, bar, *_ = env
    apply_theme(qapp, th.LIGHT, font_scale=100)
    assert bar.height() == 30                       # the classic height at 100 %
    try:
        apply_theme(qapp, th.LIGHT, font_scale=150)
        assert bar.height() > 30
        assert bar.height() >= bar.start_button.sizeHint().height()
        assert bar.height() >= bar.tray.sizeHint().height()
    finally:
        apply_theme(qapp, th.LIGHT, font_scale=100)
    assert bar.height() == 30


def test_context_menu_is_deleted_and_actions_still_run(env, qapp):
    from PySide6.QtCore import QCoreApplication, QEvent, QPoint
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtWidgets import QMenu
    _desk, bar, _s, _r, shell = env

    def right_click():
        bar.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(300, 10),
                                               bar.mapToGlobal(QPoint(300, 10))))
        return next(m for m in bar.findChildren(QMenu) if m.isVisible())

    for _ in range(20):
        menu = right_click()
        menu.hide()
        qapp.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert len(bar.findChildren(QMenu)) == 0
    menu = right_click()
    action = next(a for a in menu.actions() if a.text() == "&Cascade")
    menu.hide()                                     # QMenu hides before it triggers the clicked action
    action.trigger()
    assert ("cascade", ()) in shell.calls


def test_tray_shows_active_account_nickname(env):
    _desk, bar, settings, *_ = env
    settings.active_account = "111111111111"
    assert bar.tray.account_button.toolTip() == "111111111111"
    settings.set_account_label("111111111111", "Lab", "#ffd34d")
    assert bar.tray.account_button.text().startswith("Lab")
    assert bar.tray.account_button.toolTip() == "Lab (111111111111)"


def test_tray_nickname_ampersand_and_markup_are_literal(env):
    _desk, bar, settings, *_ = env
    settings.active_account = "111111111111"
    settings.set_account_label("111111111111", "R&D", "#ffd34d")
    assert bar.tray.account_button.text() == "R&&D"        # shown as "R&D", not a mnemonic
    settings.set_account_label("111111111111", "<b>x</b>", "#ffd34d")
    tip = bar.tray.account_button.toolTip()
    assert "&lt;b&gt;" in tip and "<b>" not in tip


def test_job_text_is_readable_in_the_tray_and_opens_the_job(env, qtbot):
    """A running job's words sit next to the bar (not only in a tooltip); clicking them opens the job's window."""
    _, bar, _, _, shell = env
    tray = bar.tray
    assert tray.job_button.isHidden()
    long = "Snapshot dev: 7/19 collectors · me-south-1 · Activity prod: 23/90 days · eu-west-1"
    tray.set_job_text(long)
    assert tray.job_button.isVisible() and tray.job_button.text().startswith("Snapshot dev")
    assert tray.job_button.toolTip() == long
    assert tray.job_button.width() <= tray.job_button.maximumWidth()
    tray.job_button.click()
    assert ("show_running_job", ()) in shell.calls
    tray.set_job_text("")
    assert tray.job_button.isHidden()
