import random

import pytest
from PySide6.QtCore import QEvent, QPointF, QSettings, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QDialog, QWidget

from aws95.core.settings import Settings
from aws95.shell.screensaver import SAVERS
from aws95.shell.screensaver.idle import IdleMonitor
from aws95.shell.screensaver.manager import ScreensaverManager


@pytest.fixture
def settings(tmp_path, qapp):
    return Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))


@pytest.fixture(autouse=True)
def _end_saver_sessions(monkeypatch):
    """A session survives external window closes (only Resume ends it), so end every session a test
    started; otherwise its app-wide input filter would leak into later tests."""
    started = []
    real_start = ScreensaverManager.start

    def start(self, name=None):
        started.append(self)
        real_start(self, name)

    monkeypatch.setattr(ScreensaverManager, "start", start)
    yield
    for mgr in started:
        mgr.dismiss()


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_idle_fires_after_timeout(settings, qtbot):
    clock = Clock()
    mon = IdleMonitor(settings, clock=clock)
    settings.saver_timeout_min = 1
    fired = []
    mon.idle.connect(lambda: fired.append(1))
    clock.t = 59
    mon.check()
    assert fired == []
    clock.t = 61
    mon.check()
    mon.check()
    assert fired == [1]                  # fires once until rearmed
    mon.rearm()
    clock.t = 200
    mon.check()
    assert fired == [1, 1]


def test_idle_disabled_and_inhibited(settings):
    clock = Clock()
    mon = IdleMonitor(settings, clock=clock)
    fired = []
    mon.idle.connect(lambda: fired.append(1))
    settings.saver_timeout_min = 0
    clock.t = 10_000
    mon.check()
    settings.saver_timeout_min = 1
    mon.set_inhibited(True)
    mon.check()
    assert fired == []


def test_idle_never_over_modal(settings, qtbot):
    clock = Clock()
    mon = IdleMonitor(settings, clock=clock)
    settings.saver_timeout_min = 1
    fired = []
    mon.idle.connect(lambda: fired.append(1))
    dlg = QDialog()
    qtbot.addWidget(dlg)
    dlg.setModal(True)
    dlg.show()
    clock.t = 999
    mon.check()
    assert fired == []
    dlg.close()


def _mgr(settings, qtbot, **kw):
    target = QWidget()
    qtbot.addWidget(target)
    target.show()
    sounds = []

    class Sound:
        def play(self, e):
            sounds.append(e)
            return True

        def preview(self, e):
            return True

    mgr = ScreensaverManager(settings, Sound(), rng=random.Random(1), hide_targets=lambda: [target], **kw)
    return mgr, target, sounds


def test_start_hides_content_and_input_does_not_dismiss(settings, qtbot):
    mgr, target, sounds = _mgr(settings, qtbot)
    mgr.start("pipes")
    assert mgr.active and mgr.current_name == "pipes" and not target.isVisible()
    assert sounds == ["saver_start"]
    win = mgr.windows[0]
    win.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier))
    assert mgr.active and mgr.panel.isVisible()
    win.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, QPointF(5, 5), QPointF(5, 5),
                                   Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    assert mgr.active


def test_resume_click_dismisses(settings, qtbot):
    mgr, target, _ = _mgr(settings, qtbot)
    mgr.start()
    mgr.windows[0].keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
    with qtbot.waitSignal(mgr.dismissed):
        mgr.panel.resume_button.click()
    assert not mgr.active and target.isVisible() and mgr.windows == []


def test_enter_on_focused_resume_dismisses(settings, qtbot):
    mgr, _t, _ = _mgr(settings, qtbot)
    mgr.start()
    mgr.windows[0].keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_B, Qt.KeyboardModifier.NoModifier))
    mgr.panel.resume_button.setFocus()
    qtbot.waitUntil(mgr.panel.resume_button.hasFocus, timeout=2000)   # window activation is asynchronous
    mgr.panel.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    assert not mgr.active


def test_choice_random_vs_fixed(settings, qtbot):
    mgr, *_ = _mgr(settings, qtbot)
    settings.saver_choice = "maze"
    assert mgr.choose() == "maze"
    settings.saver_choice = "random"
    picks = {mgr.choose() for _ in range(40)}
    assert picks <= set(SAVERS) and len(picks) > 1


def test_panel_auto_hides(settings, qtbot):
    mgr, *_ = _mgr(settings, qtbot, panel_hide_ms=50)
    mgr.start()
    mgr.windows[0].keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.NoModifier))
    qtbot.waitUntil(lambda: not mgr.panel.isVisible(), timeout=2000)
    assert mgr.active


def test_one_window_per_screen(settings, qtbot):
    from PySide6.QtGui import QGuiApplication
    mgr, *_ = _mgr(settings, qtbot)
    mgr.start()
    assert len(mgr.windows) == len(QGuiApplication.screens())
    mgr.dismiss()


# ----- hardening: privacy, focus, lifecycle (beyond the brief) -----
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout  # noqa: E402
from shiboken6 import isValid  # noqa: E402


def _reveal(mgr, qtbot):
    QTest.keyClick(mgr.windows[0], Qt.Key.Key_A)
    qtbot.waitUntil(lambda: QApplication.focusWidget() is mgr.panel.resume_button, timeout=2000)


def test_focus_lands_on_resume_when_panel_revealed(settings, qtbot):
    mgr, *_ = _mgr(settings, qtbot)
    mgr.start()
    _reveal(mgr, qtbot)
    assert mgr.active and mgr.panel.isVisible()
    assert QApplication.activeWindow() is mgr.windows[0]
    mgr.dismiss()


def test_only_enter_or_space_on_resume_dismiss(settings, qtbot):
    mgr, *_ = _mgr(settings, qtbot)
    mgr.start()
    _reveal(mgr, qtbot)
    for key in (Qt.Key.Key_Escape, Qt.Key.Key_A, Qt.Key.Key_Q, Qt.Key.Key_F4, Qt.Key.Key_Backspace):
        QTest.keyClick(mgr.panel.resume_button, key)
        assert mgr.active
    QTest.mouseClick(mgr.windows[0], Qt.MouseButton.LeftButton)
    QTest.mouseClick(mgr.panel, Qt.MouseButton.LeftButton)
    assert mgr.active
    QTest.keyClick(mgr.panel.resume_button, Qt.Key.Key_Space)
    assert not mgr.active


def test_real_enter_on_resume_dismisses(settings, qtbot):
    mgr, *_ = _mgr(settings, qtbot)
    mgr.start()
    _reveal(mgr, qtbot)
    QTest.keyClick(mgr.panel.resume_button, Qt.Key.Key_Return)
    assert not mgr.active


def test_keys_and_shortcuts_never_reach_hidden_windows(settings, qtbot):
    target = QWidget()
    qtbot.addWidget(target)
    edit = QLineEdit(target)
    QVBoxLayout(target).addWidget(edit)
    fired = []
    sc = QShortcut(QKeySequence("Ctrl+K"), target)
    sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
    sc.activated.connect(lambda: fired.append(1))
    target.show()
    bystander = QWidget()           # visible but not a hide target: Qt alone would still fire its shortcut
    qtbot.addWidget(bystander)
    bsc = QShortcut(QKeySequence("Ctrl+J"), bystander)
    bsc.setContext(Qt.ShortcutContext.ApplicationShortcut)
    bsc.activated.connect(lambda: fired.append(2))
    bystander.show()
    mgr = ScreensaverManager(settings, rng=random.Random(1), hide_targets=lambda: [target])
    mgr.start()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is mgr.windows[0], timeout=2000)
    QTest.keyClick(mgr.windows[0], Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(mgr.windows[0], Qt.Key.Key_J, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(edit, Qt.Key.Key_X)
    assert fired == [] and edit.text() == "" and mgr.active and mgr.panel.isVisible()
    mgr.dismiss()
    QTest.keyClick(edit, Qt.Key.Key_X)              # the filter is gone once dismissed
    assert edit.text() == "x"


def test_restores_exactly_the_windows_it_hid(settings, qtbot):
    shown, never, closed = QWidget(), QWidget(), QWidget()
    for w in (shown, never, closed):
        qtbot.addWidget(w)
    shown.show()
    closed.show()
    mgr = ScreensaverManager(settings, rng=random.Random(1), hide_targets=lambda: [shown, never, closed])
    mgr.start()
    assert not shown.isVisible() and not closed.isVisible()
    closed.close()                                  # closed while hidden: must not be resurrected
    mgr.dismiss()
    assert shown.isVisible() and not never.isVisible() and not closed.isVisible()


def test_open_popup_is_closed_and_never_reshown(settings, qtbot):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QMenu
    menu = QMenu()
    qtbot.addWidget(menu)
    menu.addAction("Account 1234")
    menu.popup(QPoint(0, 0))
    assert menu.isVisible()
    mgr = ScreensaverManager(settings, rng=random.Random(1), hide_targets=lambda: [menu])
    mgr.start()
    assert not menu.isVisible()
    mgr.dismiss()
    assert not menu.isVisible()


def test_window_opened_during_session_stays_hidden_until_resume(settings, qtbot):
    mgr, *_ = _mgr(settings, qtbot)
    mgr.start()
    late = QWidget()
    qtbot.addWidget(late)
    late.show()
    assert late.windowOpacity() == 0.0              # transparent before it is ever painted
    qtbot.waitUntil(lambda: not late.isVisible(), timeout=2000)
    assert mgr.active
    mgr.dismiss()
    assert late.isVisible() and late.windowOpacity() == 1.0


def test_modal_opened_during_session_is_covered_and_does_not_block_resume(settings, qtbot):
    mgr, *_ = _mgr(settings, qtbot)
    mgr.start()
    dlg = QDialog()
    qtbot.addWidget(dlg)
    dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
    dlg.show()
    assert dlg.isVisible() and dlg.windowOpacity() == 0.0
    assert mgr.windows[0].windowHandle().transientParent() == dlg.windowHandle()
    _reveal(mgr, qtbot)
    QTest.mouseClick(mgr.panel.resume_button, Qt.MouseButton.LeftButton)
    assert not mgr.active
    assert dlg.isVisible() and dlg.windowOpacity() == 1.0
    dlg.close()


def test_never_starts_over_modal_and_never_twice(settings, qtbot):
    mgr, *_ = _mgr(settings, qtbot)
    dlg = QDialog()
    qtbot.addWidget(dlg)
    dlg.setModal(True)
    dlg.show()
    mgr.start("maze")
    assert not mgr.active and mgr.windows == []
    dlg.close()
    started = []
    mgr.started.connect(started.append)
    mgr.start("maze")
    mgr.start("pipes")
    assert started == ["maze"] and mgr.current_name == "maze"
    assert len(mgr.windows) == len(QGuiApplication.screens())
    mgr.dismiss()


def test_dismiss_is_idempotent_stops_timers_and_frees_windows(settings, qtbot):
    mgr, *_ = _mgr(settings, qtbot)
    mgr.start()
    _reveal(mgr, qtbot)
    wins = list(mgr.windows)
    savers = [w.saver for w in wins]
    panel = mgr.panel
    dismissed = []
    mgr.dismissed.connect(lambda: dismissed.append(1))
    mgr.dismiss()
    mgr.dismiss()
    assert dismissed == [1]
    assert not mgr._panel_timer.isActive()
    assert not any(s.is_running() for s in savers)
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not any(isValid(w) for w in wins) and not isValid(panel)


def test_screens_added_and_removed_while_active(settings, qtbot):
    mgr, *_ = _mgr(settings, qtbot)
    mgr.start()
    n = len(mgr.windows)
    screen = QGuiApplication.primaryScreen()
    mgr._on_screen_added(screen)
    assert len(mgr.windows) == n + 1
    mgr._on_screen_removed(screen)                  # every window on it goes; a fallback keeps Resume reachable
    assert mgr.active and mgr.windows and isValid(mgr.panel)
    _reveal(mgr, qtbot)
    mgr.panel.resume_button.click()
    assert not mgr.active


def test_external_close_never_ends_the_session(settings, qtbot):
    mgr, target, _ = _mgr(settings, qtbot)
    mgr.start()
    dismissed = []
    mgr.dismissed.connect(lambda: dismissed.append(1))
    QApplication.closeAllWindows()                  # app not quitting: must not become an invisible zombie
    qtbot.waitUntil(lambda: len(mgr.windows) == len(QGuiApplication.screens())
                    and all(isValid(w) and w.isVisible() for w in mgr.windows), timeout=2000)
    assert mgr.active and not target.isVisible() and isValid(mgr.panel) and dismissed == []
    _reveal(mgr, qtbot)
    mgr.panel.resume_button.click()
    assert not mgr.active and target.isVisible() and dismissed == [1]


def test_about_to_quit_tears_down_without_uncovering(settings, qtbot):
    mgr, target, _ = _mgr(settings, qtbot)
    mgr.start()
    wins = list(mgr.windows)
    dismissed = []
    mgr.dismissed.connect(lambda: dismissed.append(1))
    mgr._on_about_to_quit()                         # connected to QApplication.aboutToQuit while active
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not mgr.active and mgr.windows == [] and not any(isValid(w) for w in wins)
    assert not target.isVisible() and dismissed == []


class _Boom(SAVERS["pipes"]):
    name = "boom"

    def __init__(self, *a, **kw):
        raise RuntimeError("saver exploded")


def test_failed_start_rolls_back_completely(settings, qtbot, monkeypatch, caplog):
    monkeypatch.setitem(SAVERS, "boom", _Boom)
    target = QWidget()
    qtbot.addWidget(target)
    edit = QLineEdit(target)
    target.show()
    mgr = ScreensaverManager(settings, rng=random.Random(1), hide_targets=lambda: [target])
    mgr.start("boom")
    assert not mgr.active and mgr.windows == [] and mgr.panel is None
    assert target.isVisible() and "failed to start" in caplog.text
    QTest.keyClick(edit, Qt.Key.Key_X)              # no app-wide filter left behind
    assert edit.text() == "x"
    mgr.start("pipes")
    assert mgr.active and mgr.current_name == "pipes" and not target.isVisible()
    mgr.dismiss()
    assert target.isVisible()


def test_failing_hide_targets_does_not_wedge_the_manager(settings, qtbot, caplog):
    target = QWidget()
    qtbot.addWidget(target)
    target.show()
    calls = []

    def targets():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("enumeration failed")
        return [target]

    mgr = ScreensaverManager(settings, rng=random.Random(1), hide_targets=targets)
    mgr.start()
    assert not mgr.active and mgr.windows == [] and target.isVisible()
    mgr.start()
    assert mgr.active and not target.isVisible()
    mgr.dismiss()


def test_held_enter_does_not_dismiss_but_a_fresh_press_does(settings, qtbot):
    mgr, *_ = _mgr(settings, qtbot)
    mgr.start()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is mgr.windows[0], timeout=2000)
    QTest.keyPress(mgr.windows[0], Qt.Key.Key_Return)           # reveals; Resume gets focus
    button = mgr.panel.resume_button
    qtbot.waitUntil(lambda: QApplication.focusWidget() is button, timeout=2000)
    QApplication.sendEvent(button, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                                             Qt.KeyboardModifier.NoModifier, "\r", True))   # auto-repeat
    assert mgr.active
    QTest.keyPress(button, Qt.Key.Key_Enter)                    # other activation key while still held
    assert mgr.active
    QTest.keyRelease(button, Qt.Key.Key_Return)
    QTest.keyPress(button, Qt.Key.Key_Return)                   # a fresh press
    assert not mgr.active


def test_held_space_auto_repeat_never_clicks_resume(settings, qtbot):
    mgr, *_ = _mgr(settings, qtbot)
    mgr.start()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is mgr.windows[0], timeout=2000)
    QTest.keyPress(mgr.windows[0], Qt.Key.Key_Space)
    button = mgr.panel.resume_button
    qtbot.waitUntil(lambda: QApplication.focusWidget() is button, timeout=2000)
    for t in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.KeyPress):
        QApplication.sendEvent(button, QKeyEvent(t, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " ", True))
    QTest.keyRelease(button, Qt.Key.Key_Space)
    assert mgr.active and not button.isDown()
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert not mgr.active


def test_balloon_shown_mid_session_is_not_resurrected(settings, qtbot):
    from PySide6.QtCore import QPoint

    from aws95.kit.widgets.balloon import Balloon
    mgr, target, _ = _mgr(settings, qtbot)
    mgr.start()
    balloon = Balloon("Billing", "Account 1234 spent more")
    qtbot.addWidget(balloon)
    balloon.show_at(QPoint(10, 10))
    qtbot.waitUntil(lambda: not balloon.isVisible(), timeout=2000)
    mgr.dismiss()
    assert target.isVisible() and not balloon.isVisible()


def test_restore_failure_still_closes_the_saver(settings, qtbot):
    class Fragile(QWidget):
        def show(self):
            raise RuntimeError("cannot show")

    fragile, fine = Fragile(), QWidget()
    for w in (fragile, fine):
        qtbot.addWidget(w)
        QWidget.show(w)
    mgr = ScreensaverManager(settings, rng=random.Random(1), hide_targets=lambda: [fragile, fine])
    mgr.start()
    wins = list(mgr.windows)
    mgr.dismiss()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not mgr.active and not any(isValid(w) for w in wins) and fine.isVisible()


def test_auto_repeat_never_clicks_even_after_mouse_reveal(settings, qtbot):
    mgr, *_ = _mgr(settings, qtbot)
    mgr.start()
    QTest.mouseClick(mgr.windows[0], Qt.MouseButton.LeftButton)   # mouse reveal: nothing held
    button = mgr.panel.resume_button
    qtbot.waitUntil(lambda: QApplication.focusWidget() is button, timeout=2000)
    for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
        QApplication.sendEvent(button, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, "\r", True))
    assert mgr.active
    QTest.keyClick(button, Qt.Key.Key_Return)
    assert not mgr.active
