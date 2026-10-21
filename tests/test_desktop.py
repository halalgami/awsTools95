import pytest
from PySide6.QtCore import QRect, QSettings
from PySide6.QtWidgets import QWidget

from aws95.core.settings import Settings
from aws95.core.tasks import TaskRunner
from aws95.kit import theme as th
from aws95.kit.style import apply_theme
from aws95.programs import DEFAULT_MANIFESTS
from aws95.providers.empty import empty_provider_set
from aws95.shell.context import NullSound, ProgramContext
from aws95.shell.desktop import Desktop
from aws95.shell.registry import Registry
from aws95.shell.window import clamp_rect, decode_rect, encode_rect


class _FakeShell:
    def __getattr__(self, name):
        return lambda *a, **k: None


@pytest.fixture
def env(tmp_path, qapp, qtbot):
    apply_theme(qapp, th.LIGHT)
    settings = Settings(QSettings(str(tmp_path / "d.ini"), QSettings.Format.IniFormat))
    runner = TaskRunner()
    ctx = ProgramContext(settings, runner, empty_provider_set(), NullSound(), _FakeShell())
    desk = Desktop(Registry(DEFAULT_MANIFESTS), lambda: ctx, settings)
    qtbot.addWidget(desk)
    desk.resize(1000, 700)
    desk.show()
    qtbot.waitExposed(desk)
    yield desk, settings
    runner.shutdown()


def test_rect_codec_and_clamp():
    r = QRect(10, 20, 300, 200)
    assert decode_rect(encode_rect(r)) == r
    assert decode_rect(None) is None
    from PySide6.QtCore import QByteArray
    assert decode_rect(QByteArray(b"garbage")) is None
    area = QRect(0, 0, 800, 600)
    assert clamp_rect(QRect(5000, 5000, 300, 200), area) is None
    clamped = clamp_rect(QRect(700, -50, 300, 200), area)
    assert clamped.top() >= 0 and clamped.left() <= 800 - 40


def test_singleton_opens_once(env):
    desk, _ = env
    a = desk.open_program("billing")
    b = desk.open_program("billing")
    c = desk.open_program("billing")
    assert a is b is c
    assert len([w for w in desk.program_windows() if w.manifest.id == "billing"]) == 1


def test_multi_opens_many_with_instance_slots(env):
    desk, _ = env
    w1 = desk.open_program("resources")
    w2 = desk.open_program("resources")
    assert w1 is not w2 and {w1.instance, w2.instance} == {0, 1}
    assert w1.geometry_key == "resources#0"


def test_geometry_persists_and_clamps(env, qtbot):
    desk, settings = env
    w = desk.open_program("billing")
    w.setGeometry(QRect(100, 120, 400, 300))
    w.close()
    qtbot.waitUntil(lambda: not desk.program_windows())
    w2 = desk.open_program("billing")
    assert w2.geometry() == QRect(100, 120, 400, 300)
    w2.close()
    settings.set_geometry("billing", encode_rect(QRect(9000, 9000, 400, 300)))
    w3 = desk.open_program("billing")
    assert desk.viewport().rect().intersects(w3.geometry())


def test_close_goes_to_recycle_bin_and_restores(env, qtbot):
    desk, _ = env
    desk.open_program("snapshots").close()
    qtbot.waitUntil(lambda: len(desk.recycle.items()) == 1)
    assert desk.recycle.items()[0].program_id == "snapshots"
    desk.recycle.restore(0)
    assert any(w.manifest.id == "snapshots" for w in desk.program_windows())
    assert desk.recycle.items() == ()


def test_minimize_to_taskbar_and_restore(env):
    desk, _ = env
    w = desk.open_program("billing")
    w.minimize_to_taskbar()
    assert w.is_minimized_to_taskbar and not w.isVisible()
    w.restore_from_taskbar()
    assert w.isVisible() and desk.active_program_window() is w


def test_mru_order(env):
    desk, _ = env
    a = desk.open_program("billing")
    b = desk.open_program("findings")
    desk.activate(a)
    assert desk.mru_windows()[:2] == [a, b]


def test_tile_and_cascade(env):
    desk, _ = env
    a = desk.open_program("billing")
    b = desk.open_program("findings")
    desk.tile("vertical")
    area = desk.viewport().rect()
    assert a.geometry().width() == area.width() // 2 or b.geometry().width() == area.width() // 2
    assert a.geometry().height() == area.height()
    desk.tile("horizontal")
    assert a.geometry().width() == area.width()
    desk.cascade()
    assert a.geometry().topLeft() != b.geometry().topLeft()


def test_minimize_all(env):
    desk, _ = env
    a = desk.open_program("billing")
    b = desk.open_program("findings")
    desk.minimize_all()
    assert a.is_minimized_to_taskbar and b.is_minimized_to_taskbar


def test_chip_and_demo_title(env):
    desk, _ = env
    w = desk.open_program("billing")
    w.set_scope_chip("lab-sandbox", "#ffd34d")
    assert w.property("aws95ChipText") == "lab-sandbox"
    w.set_demo(True)
    assert w.windowTitle() == "Billing [DEMO]"


def test_wallpaper_follows_setting(env):
    desk, settings = env
    settings.wallpaper = "dots"
    assert not desk.background().texture().isNull()
    settings.wallpaper = "none"
    assert desk.background().color().name() == th.LIGHT.desktop


def test_clamp_oversized_window_fits_area():
    area = QRect(0, 0, 800, 600)
    r = clamp_rect(QRect(10, 10, 2000, 1500), area)
    assert r.size() == area.size() and r.topLeft() == area.topLeft()


def test_singleton_reopen_right_after_close_and_mru_survives(env, qtbot):
    desk, _ = env
    a = desk.open_program("billing")
    desk.open_program("findings")
    a.close()
    again = desk.open_program("billing")
    assert again is not a and not again.is_closing
    qtbot.waitUntil(lambda: len(desk.program_windows()) == 2)
    assert all(w.manifest.id in ("billing", "findings") for w in desk.mru_windows())


def test_native_minimize_routes_to_taskbar(env, qtbot):
    desk, _ = env
    w = desk.open_program("billing")
    w.setGeometry(QRect(50, 60, 400, 300))
    w.showMinimized()
    qtbot.waitUntil(lambda: w.is_minimized_to_taskbar)
    assert not w.isVisible() and not w.isMinimized()
    w.restore_from_taskbar()
    assert w.isVisible() and w.geometry() == QRect(50, 60, 400, 300)


def test_native_minimize_from_maximized_restores_maximized(env, qtbot):
    desk, _ = env
    w = desk.open_program("billing")
    w.setGeometry(QRect(50, 60, 400, 300))
    w.showMaximized()
    w.showMinimized()
    qtbot.waitUntil(lambda: w.is_minimized_to_taskbar)
    assert not w.isVisible()
    w.restore_from_taskbar()
    assert w.isVisible() and w.isMaximized()


def test_minimizing_active_window_activates_next(env):
    desk, _ = env
    a = desk.open_program("billing")
    b = desk.open_program("findings")
    b.minimize_to_taskbar()
    assert desk.active_program_window() is a
    a.minimize_to_taskbar()
    assert desk.active_program_window() is None


class _Refuser(QWidget):
    def closeEvent(self, event):  # noqa: N802
        event.ignore()


def test_refused_close_keeps_state(env, qtbot, monkeypatch):
    desk, _ = env
    w = desk.open_program("billing")
    w.setWidget(_Refuser())
    assert w.close() is False
    assert not w.is_closing and w in desk.program_windows()
    assert desk.recycle.items() == ()
    assert desk.open_program("billing") is w


def test_active_program_window_none_when_closing(env):
    desk, _ = env
    w = desk.open_program("billing")
    w.is_closing = True
    assert desk.active_program_window() is None


def test_moving_active_window_then_native_minimize_restores_moved_rect(env, qtbot):
    desk, _ = env
    w = desk.open_program("billing")
    desk.open_program("findings")          # billing is now inactive
    w.setGeometry(QRect(30, 40, 400, 300))  # records a (soon stale) normal rect
    desk.setActiveSubWindow(w)
    assert desk.active_program_window() is w
    w.setGeometry(QRect(120, 90, 420, 310))
    w.showMinimized()
    qtbot.waitUntil(lambda: w.is_minimized_to_taskbar)
    w.restore_from_taskbar()
    assert w.geometry() == QRect(120, 90, 420, 310)


@pytest.mark.parametrize("demo", [True, False])
def test_demo_providers_mark_window_titles(tmp_path, qapp, qtbot, demo):
    from aws95.providers.demo import demo_provider_set
    settings = Settings(QSettings(str(tmp_path / "demo.ini"), QSettings.Format.IniFormat))
    runner = TaskRunner()
    providers = demo_provider_set() if demo else empty_provider_set()
    ctx = ProgramContext(settings, runner, providers, NullSound(), _FakeShell())
    desk = Desktop(Registry(DEFAULT_MANIFESTS), lambda: ctx, settings)
    qtbot.addWidget(desk)
    desk.resize(800, 600)
    w = desk.open_program("billing")
    assert w.windowTitle() == ("Billing [DEMO]" if demo else "Billing")
    runner.shutdown()
