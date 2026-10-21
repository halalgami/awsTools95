import pytest
from PySide6.QtCore import QPoint, QSettings, Qt

from aws95.core.settings import Settings
from aws95.core.tasks import TaskRunner
from aws95.programs import DEFAULT_MANIFESTS
from aws95.providers.empty import empty_provider_set
from aws95.shell.context import NullSound, ProgramContext
from aws95.shell.desktop import Desktop
from aws95.shell.icon_layer import IconLayer
from aws95.shell.registry import Registry


class _FakeShell:
    def __getattr__(self, name):
        return lambda *a, **k: None


@pytest.fixture
def env(tmp_path, qapp, qtbot):
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    runner = TaskRunner()
    ctx = ProgramContext(settings, runner, empty_provider_set(), NullSound(), _FakeShell())
    reg = Registry(DEFAULT_MANIFESTS)
    desk = Desktop(reg, lambda: ctx, settings)
    qtbot.addWidget(desk)
    desk.resize(1000, 700)
    opened = []
    layer = IconLayer(desk, reg, settings, opened.append)
    desk.show()
    qtbot.waitExposed(desk)
    yield desk, layer, settings, opened, reg
    runner.shutdown()


def test_one_icon_per_desktop_manifest(env):
    _d, layer, _s, _o, reg = env
    assert [i.program_id for i in layer.icons()] == [m.id for m in reg.desktop()]


def test_double_click_opens(env, qtbot):
    _d, layer, _s, opened, _r = env
    first = layer.icons()[0]
    qtbot.mouseDClick(first, Qt.MouseButton.LeftButton)
    assert opened == [first.program_id]


def test_keyboard_navigation_and_enter(env, qtbot):
    _d, layer, _s, opened, _r = env
    layer.select(layer.icons()[0].program_id)
    layer.setFocus()
    qtbot.keyClick(layer, Qt.Key.Key_Down)
    assert layer.selected_id() == layer.icons()[1].program_id
    qtbot.keyClick(layer, Qt.Key.Key_Return)
    assert opened == [layer.icons()[1].program_id]


def test_keyboard_without_selection_selects_first_and_clamps(env, qtbot):
    _d, layer, _s, _o, _r = env
    qtbot.keyClick(layer, Qt.Key.Key_Down)
    assert layer.selected_id() == layer.icons()[0].program_id
    qtbot.keyClick(layer, Qt.Key.Key_Up)
    assert layer.selected_id() == layer.icons()[0].program_id
    qtbot.keyClick(layer, Qt.Key.Key_Right)
    assert layer.selected_id() == layer.icons()[min(len(layer.icons()) - 1, layer._rows())].program_id


def test_move_persists_and_arrange_resets(env):
    _d, layer, settings, _o, _r = env
    pid = layer.icons()[0].program_id
    layer.move_icon(pid, QPoint(300, 200))
    assert settings.icon_position(pid) == (300, 200)
    layer.arrange_icons()
    assert settings.icon_position(pid) is None
    assert layer.icons()[0].pos() == QPoint(8, 8)


def test_layer_stays_below_windows(env):
    desk, layer, _s, _o, _r = env
    w = desk.open_program("billing")
    children = desk.viewport().children()
    assert children.index(layer) < children.index(w)


def test_offscreen_saved_position_is_brought_into_view(tmp_path, qapp, qtbot):
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    reg = Registry(DEFAULT_MANIFESTS)
    pid = reg.desktop()[0].id
    settings.set_icon_position(pid, 5000, -400)
    desk = Desktop(reg, lambda: None, settings)
    qtbot.addWidget(desk)
    desk.resize(1000, 700)
    layer = IconLayer(desk, reg, settings, lambda _p: None)
    desk.show()
    qtbot.waitExposed(desk)
    ic = layer.icons()[0]
    assert layer.rect().contains(ic.geometry())
    desk.resize(300, 200)
    qtbot.waitUntil(lambda: layer.rect().contains(ic.geometry()))


def test_move_icon_clamps_to_layer(env):
    _d, layer, settings, _o, _r = env
    pid = layer.icons()[0].program_id
    layer.move_icon(pid, QPoint(-100, 99999))
    ic = layer.icons()[0]
    assert layer.rect().contains(ic.geometry())
    assert settings.icon_position(pid) == (ic.x(), ic.y())


def test_drag_is_clamped_and_persisted(env, qtbot):
    _d, layer, settings, _o, _r = env
    ic = layer.icons()[0]
    qtbot.mousePress(ic, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    qtbot.mouseMove(ic, QPoint(-5000, 10))
    qtbot.mouseRelease(ic, Qt.MouseButton.LeftButton, pos=QPoint(-5000, 10))
    assert layer.rect().contains(ic.geometry())
    assert settings.icon_position(ic.program_id) == (ic.x(), ic.y())


def test_click_on_empty_desktop_deselects(env, qtbot):
    _d, layer, _s, _o, _r = env
    layer.select(layer.icons()[0].program_id)
    qtbot.mouseClick(layer, Qt.MouseButton.LeftButton, pos=QPoint(600, 500))
    assert layer.selected_id() is None


def test_layer_built_after_show_lays_out_in_column_and_clamps(tmp_path, qapp, qtbot):
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    reg = Registry(DEFAULT_MANIFESTS)
    settings.set_icon_position(reg.desktop()[1].id, 9000, 9000)
    desk = Desktop(reg, lambda: None, settings)
    qtbot.addWidget(desk)
    desk.resize(1000, 700)
    desk.show()
    qtbot.waitExposed(desk)
    layer = IconLayer(desk, reg, settings, lambda _p: None)
    icons = layer.icons()
    assert icons[0].x() == icons[2].x() and icons[2].y() > icons[0].y()
    assert layer.rect().contains(icons[1].geometry())


def test_context_menu_collects_contributions(env):
    _d, layer, _s, _o, _r = env
    layer.context_menu_requested.connect(lambda m: m.addAction("Properties"))
    texts = [a.text() for a in layer.build_context_menu().actions()]
    assert "&Arrange Icons" in texts and "Properties" in texts


def test_right_double_click_does_not_open(env, qtbot):
    _d, layer, _s, opened, _r = env
    qtbot.mouseDClick(layer.icons()[0], Qt.MouseButton.RightButton)
    assert opened == []


def test_command_icon_comes_first_and_runs_its_command(tmp_path, qapp, qtbot):
    """Owner, 2026-10-17: Take Snapshot sits first on the desktop so it is easy to find."""
    from aws95.shell.icon_layer import DesktopCommand
    settings = Settings(QSettings(str(tmp_path / "c.ini"), QSettings.Format.IniFormat))
    reg = Registry(DEFAULT_MANIFESTS)
    desk = Desktop(reg, lambda: None, settings)
    qtbot.addWidget(desk)
    desk.resize(1000, 700)
    opened, ran = [], []
    layer = IconLayer(desk, reg, settings, opened.append,
                      commands=(DesktopCommand("cmd:take_snapshot", "Take Snapshot", "camera",
                                               lambda: ran.append(1)),))
    desk.show()
    qtbot.waitExposed(desk)
    first = layer.icons()[0]
    assert first.program_id == "cmd:take_snapshot" and first.toolTip() == "Take Snapshot"
    assert first.pos() == QPoint(8, 8)
    qtbot.mouseDClick(first, Qt.MouseButton.LeftButton)
    layer.select("cmd:take_snapshot")
    qtbot.keyClick(layer, Qt.Key.Key_Return)
    assert ran == [1, 1] and opened == []
    assert [ic.program_id for ic in layer.icons()[1:]] == [m.id for m in reg.desktop()]


def test_two_line_labels_fit(env):
    """Review 2026-10-21: README shots showed "Take Snapshot", "My Accounts" and "Recycle Bin" cut at line two."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QFont, QFontMetrics
    from aws95.shell.icon_layer import ICON_H, ICON_W, TEXT_TOP
    _desk, layer, *_ = env
    for ic in layer.icons():
        font = QFont(ic.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() * 0.9))
        need = QFontMetrics(font).boundingRect(QRect(0, 0, ICON_W, 1000),
                                               int(Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap),
                                               ic.toolTip()).height()
        assert TEXT_TOP + need <= ICON_H, (ic.toolTip(), need)
