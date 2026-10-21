import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QWidget

from aws95.core.settings import Settings
from aws95.shell.shutdown import RESTART_CODE, CrtOff, ShutdownDialog, perform_shutdown
from aws95.shell.splash import Splash, make_splash_pixmap


@pytest.fixture
def settings(tmp_path, qapp):
    return Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))


def test_splash_progress(qtbot):
    pm = make_splash_pixmap()
    assert (pm.width(), pm.height()) == (480, 280)
    s = Splash()
    qtbot.addWidget(s)
    s.set_progress(150, "Loading programs…")
    assert s.progress() == 100
    s.set_progress(-3, "x")
    assert s.progress() == 0


def test_dialog_defaults_and_choices(qtbot):
    d = ShutdownDialog()
    qtbot.addWidget(d)
    assert d.shutdown_radio.isChecked() and d.yes_button.isDefault()
    d.restart_radio.setChecked(True)
    d.yes_button.click()
    assert d.choice == "restart"
    d2 = ShutdownDialog()
    qtbot.addWidget(d2)
    d2.no_button.click()
    assert d2.choice is None


def test_dialog_keyboard(qtbot):
    d = ShutdownDialog()
    qtbot.addWidget(d)
    d.show()
    qtbot.keyClick(d, Qt.Key.Key_Down)
    qtbot.keyClick(d.yes_button, Qt.Key.Key_Return)
    assert d.choice in ("shutdown", "restart")


def test_crt_finishes(qtbot):
    target = QWidget()
    qtbot.addWidget(target)
    target.resize(300, 200)
    target.show()
    crt = CrtOff(target)
    with qtbot.waitSignal(crt.finished, timeout=2000):
        crt.play(40)


class FakeApp:
    def __init__(self):
        self.codes = []

    def exit(self, code):
        self.codes.append(code)


class Sound:
    def __init__(self):
        self.played = []

    def play(self, e):
        self.played.append(e)
        return True


def test_perform_shutdown_reduce_motion_is_immediate(settings):
    settings.reduce_motion = True
    app, sound = FakeApp(), Sound()
    perform_shutdown("restart", settings=settings, sound=sound, app=app, overlay_target=None)
    assert app.codes == [RESTART_CODE] and sound.played == ["shutdown"]


def test_perform_shutdown_with_crt(settings, qtbot):
    target = QWidget()
    qtbot.addWidget(target)
    target.resize(200, 100)
    target.show()
    app = FakeApp()
    perform_shutdown("shutdown", settings=settings, sound=Sound(), app=app, overlay_target=target, duration_ms=30)
    qtbot.waitUntil(lambda: app.codes == [0], timeout=2000)


def test_perform_shutdown_exits_once_when_called_twice(settings, qtbot):
    target = QWidget()
    qtbot.addWidget(target)
    target.resize(200, 100)
    target.show()
    app = FakeApp()
    for _ in range(2):
        perform_shutdown("shutdown", settings=settings, sound=Sound(), app=app, overlay_target=target, duration_ms=30)
    qtbot.wait(300)
    assert app.codes == [0]


def test_crt_overlay_covers_target_and_blocks_input(qtbot):
    target = QWidget()
    qtbot.addWidget(target)
    target.resize(300, 200)
    target.show()
    crt = CrtOff(target)
    crt.play(500)
    assert crt.geometry() == target.rect()
    assert not crt.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert target.childAt(150, 100) is crt
    target.resize(400, 250)
    assert crt.geometry() == target.rect()


def test_dialog_keyboard_from_any_child(qtbot):
    d = ShutdownDialog()
    qtbot.addWidget(d)
    d.show()
    qtbot.keyClick(d.restart_radio, Qt.Key.Key_Up)
    assert d.restart_radio.isChecked()
    qtbot.keyClick(d.yes_button, Qt.Key.Key_Down)
    assert d.shutdown_radio.isChecked()
    qtbot.keyClick(d.no_button, Qt.Key.Key_Up)
    assert d.restart_radio.isChecked()
    qtbot.keyClick(d.shutdown_radio, Qt.Key.Key_Return)
    assert d.choice == "restart"
    d2 = ShutdownDialog()
    qtbot.addWidget(d2)
    d2.show()
    qtbot.keyClick(d2.restart_radio, Qt.Key.Key_Escape)
    assert d2.choice is None and not d2.isVisible()


def test_restart_flow_reuses_app_across_sessions(settings, qtbot):
    app = FakeApp()
    a, b = QWidget(), QWidget()
    for w in (a, b):
        qtbot.addWidget(w)
        w.resize(200, 100)
        w.show()
    perform_shutdown("restart", settings=settings, sound=Sound(), app=app, overlay_target=a, duration_ms=30)
    qtbot.waitUntil(lambda: app.codes == [RESTART_CODE], timeout=2000)
    perform_shutdown("restart", settings=settings, sound=Sound(), app=app, overlay_target=b, duration_ms=30)
    qtbot.waitUntil(lambda: app.codes == [RESTART_CODE, RESTART_CODE], timeout=2000)


def test_same_target_reusable_after_exit_issued(settings, qtbot):
    app = FakeApp()
    a = QWidget()
    qtbot.addWidget(a)
    a.resize(200, 100)
    a.show()
    for n in (1, 2):
        perform_shutdown("shutdown", settings=settings, sound=Sound(), app=app, overlay_target=a, duration_ms=30)
        qtbot.waitUntil(lambda n=n: len(app.codes) == n, timeout=2000)


def test_reduce_motion_sequential_calls_each_exit(settings):
    settings.reduce_motion = True
    app = FakeApp()
    for _ in range(2):
        perform_shutdown("restart", settings=settings, sound=Sound(), app=app, overlay_target=None)
    assert app.codes == [RESTART_CODE, RESTART_CODE]


def test_crt_releases_keyboard_and_is_deleted(settings, qtbot):
    target = QWidget()
    qtbot.addWidget(target)
    target.resize(200, 100)
    target.show()
    crt = CrtOff(target)
    with qtbot.waitSignal(crt.finished, timeout=2000):
        crt.play(30)
    assert QWidget.keyboardGrabber() is not crt
    assert not crt.isVisible()
    app = FakeApp()
    perform_shutdown("shutdown", settings=settings, sound=Sound(), app=app, overlay_target=target, duration_ms=30)
    qtbot.waitUntil(lambda: app.codes == [0], timeout=2000)
    qtbot.waitUntil(lambda: len(target.findChildren(CrtOff)) == 1, timeout=2000)


def test_crt_skipped_by_key_or_click_finishes_once(qtbot):
    from PySide6.QtCore import QPoint
    for skip in ("key", "click"):
        target = QWidget()
        qtbot.addWidget(target)
        target.resize(300, 200)
        target.show()
        crt = CrtOff(target)
        fired = []
        crt.finished.connect(lambda: fired.append(1))
        crt.play(60_000)                                  # would run for a minute
        if skip == "key":
            qtbot.keyClick(crt, Qt.Key.Key_Escape)
        else:
            qtbot.mouseClick(crt, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
        assert fired == [1] and not crt.isVisible()
        crt.skip()                                       # a second press can never finish it again
        qtbot.wait(20)
        assert fired == [1]


def test_skipped_crt_exits_exactly_once(settings, qtbot):
    target = QWidget()
    qtbot.addWidget(target)
    target.resize(200, 100)
    target.show()
    app = FakeApp()
    perform_shutdown("restart", settings=settings, sound=Sound(), app=app, overlay_target=target, duration_ms=60_000)
    crt = target.findChildren(CrtOff)[0]
    qtbot.keyClick(crt, Qt.Key.Key_A)               # skips at once
    crt.skip()                                      # a second skip (key repeat, click) is a no-op
    qtbot.wait(20)
    assert app.codes == [RESTART_CODE]


def test_splash_show_is_fast(qtbot):
    """Boot budget (spec 4.5): the splash must be visible within 100 ms of main(); QSplashScreen.show()
    blocked ~1 s waiting for an expose event, so Splash is a plain QWidget."""
    import time
    s = Splash()
    qtbot.addWidget(s)
    t0 = time.perf_counter()
    s.show()
    elapsed = time.perf_counter() - t0
    assert s.isVisible()
    assert elapsed <= 0.1, f"Splash.show() took {elapsed * 1000:.0f} ms"
    s.set_progress(40, "Loading…")
    s.finish(None)
    assert not s.isVisible()


def test_splash_finish_frees_the_widget(qtbot):
    from shiboken6 import isValid

    from PySide6.QtCore import QCoreApplication, QEvent
    s = Splash()
    s.show()
    s.finish(None)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(s)
