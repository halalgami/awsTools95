import pytest
from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from aws95.core.settings import Settings
from aws95.core.tasks import TaskRunner
from aws95.programs import DEFAULT_MANIFESTS
from aws95.providers.empty import empty_provider_set
from aws95.shell.context import NullSound, ProgramContext
from aws95.shell.desktop import Desktop
from aws95.shell.registry import Registry
from aws95.shell.switcher import WindowSwitcher


class _FakeShell:
    def __getattr__(self, name):
        return lambda *a, **k: None


@pytest.fixture
def desk(tmp_path, qapp, qtbot):
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    runner = TaskRunner()
    ctx = ProgramContext(settings, runner, empty_provider_set(), NullSound(), _FakeShell())
    d = Desktop(Registry(DEFAULT_MANIFESTS), lambda: ctx, settings)
    qtbot.addWidget(d)
    d.resize(900, 600)
    d.show()
    yield d
    runner.shutdown()


def _key(kind, key, mods=Qt.KeyboardModifier.NoModifier):
    return QKeyEvent(kind, key, mods)


def test_needs_two_windows(desk, qtbot):
    desk.open_program("billing")
    sw = WindowSwitcher(desk)
    qtbot.addWidget(sw)
    assert sw.open() is False


def test_cycle_and_commit(desk, qtbot):
    a = desk.open_program("billing")
    b = desk.open_program("findings")
    c = desk.open_program("snapshots")            # MRU: c, b, a
    sw = WindowSwitcher(desk)
    qtbot.addWidget(sw)
    assert sw.open() and sw.selected() is b
    sw.cycle()
    assert sw.selected() is a
    sw.cycle()
    assert sw.selected() is c
    sw.cycle(-1)
    sw.commit()
    assert desk.active_program_window() is a and not sw.isVisible()


def test_cancel_keeps_active(desk, qtbot):
    desk.open_program("billing")
    b = desk.open_program("findings")
    sw = WindowSwitcher(desk)
    qtbot.addWidget(sw)
    sw.open()
    sw.cancel()
    assert desk.active_program_window() is b


def test_alt_release_commits(desk, qtbot):
    a = desk.open_program("billing")
    desk.open_program("findings")
    sw = WindowSwitcher(desk)
    qtbot.addWidget(sw)
    sw.open()
    sw.keyReleaseEvent(_key(QEvent.Type.KeyRelease, Qt.Key.Key_Alt))
    assert desk.active_program_window() is a


def test_backtick_cycles(desk, qtbot):
    desk.open_program("billing")
    desk.open_program("findings")
    desk.open_program("snapshots")
    sw = WindowSwitcher(desk)
    qtbot.addWidget(sw)
    sw.open()
    first = sw.selected()
    sw.keyPressEvent(_key(QEvent.Type.KeyPress, Qt.Key.Key_QuoteLeft, Qt.KeyboardModifier.AltModifier))
    assert sw.selected() is not first


def test_gets_keyboard_focus_on_open(desk, qtbot):
    desk.open_program("billing")
    desk.open_program("findings")
    sw = WindowSwitcher(desk)
    qtbot.addWidget(sw)
    sw.open()
    qtbot.waitUntil(lambda: sw.focusWidget() is sw or sw.hasFocus(), timeout=2000)


def test_alt_release_before_focus_still_commits(desk, qtbot):
    # Alt released while focus is elsewhere: the app-level filter must still commit.
    a = desk.open_program("billing")
    desk.open_program("findings")
    sw = WindowSwitcher(desk)
    qtbot.addWidget(sw)
    sw.open()
    QApplication.sendEvent(desk, _key(QEvent.Type.KeyRelease, Qt.Key.Key_Alt))
    assert not sw.isVisible()
    assert desk.active_program_window() is a


def test_closed_window_is_skipped(desk, qtbot):
    a = desk.open_program("billing")
    b = desk.open_program("findings")
    c = desk.open_program("snapshots")
    sw = WindowSwitcher(desk)
    qtbot.addWidget(sw)
    sw.open()                                     # selected: b
    b.close()
    sw.grab()                                     # paint with a closed window: no crash
    sw.commit()
    assert not sw.isVisible()
    assert desk.active_program_window() in (a, c)


def test_all_closed_commit_is_safe(desk, qtbot):
    a = desk.open_program("billing")
    b = desk.open_program("findings")
    sw = WindowSwitcher(desk)
    qtbot.addWidget(sw)
    sw.open()
    a.close()
    b.close()
    sw.grab()
    sw.commit()
    assert not sw.isVisible()


def test_minimized_window_included_and_restored(desk, qtbot):
    a = desk.open_program("billing")
    desk.open_program("findings")
    a.minimize_to_taskbar()
    sw = WindowSwitcher(desk)
    qtbot.addWidget(sw)
    assert sw.open() and a in sw._windows
    sw.commit()
    assert not a.is_minimized_to_taskbar and desk.active_program_window() is a


def test_hide_removes_app_filter(desk, qtbot):
    desk.open_program("billing")
    desk.open_program("findings")
    sw = WindowSwitcher(desk)
    qtbot.addWidget(sw)
    sw.open()
    sw.cancel()
    # a stray Alt release after close must be harmless
    QApplication.sendEvent(desk, _key(QEvent.Type.KeyRelease, Qt.Key.Key_Alt))
    assert not sw.isVisible()
