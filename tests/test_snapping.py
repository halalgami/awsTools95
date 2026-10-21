import pytest
from PySide6.QtCore import QPoint, QRect, QSettings

from aws95.core.settings import Settings
from aws95.core.tasks import TaskRunner
from aws95.programs import DEFAULT_MANIFESTS
from aws95.providers.empty import empty_provider_set
from aws95.shell.context import NullSound, ProgramContext
from aws95.shell.desktop import Desktop
from aws95.shell.registry import Registry
from aws95.shell.snapping import SnapController, Zone, zone_at, zone_rect

AREA = QRect(0, 0, 1000, 700)


@pytest.mark.parametrize("pos,zone", [
    (QPoint(3, 300), Zone.LEFT), (QPoint(997, 300), Zone.RIGHT), (QPoint(500, 2), Zone.TOP),
    (QPoint(2, 2), Zone.TOP_LEFT), (QPoint(998, 1), Zone.TOP_RIGHT),
    (QPoint(1, 698), Zone.BOTTOM_LEFT), (QPoint(999, 699), Zone.BOTTOM_RIGHT),
    (QPoint(500, 698), None), (QPoint(500, 300), None),
])
def test_zone_at(pos, zone):
    assert zone_at(pos, AREA) is zone


def test_zone_rects():
    assert zone_rect(Zone.LEFT, AREA) == QRect(0, 0, 500, 700)
    assert zone_rect(Zone.RIGHT, AREA) == QRect(500, 0, 500, 700)
    assert zone_rect(Zone.TOP, AREA) == AREA
    assert zone_rect(Zone.BOTTOM_RIGHT, AREA) == QRect(500, 350, 500, 350)


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
    d.resize(1000, 700)
    d.show()
    qtbot.waitExposed(d)
    yield d
    runner.shutdown()


def test_drag_preview_and_finish(desk):
    snap = SnapController(desk)
    w = desk.open_program("billing")
    snap.update(w, QPoint(2, 300))
    assert snap.preview.isVisible()
    assert snap.finish(w) is Zone.LEFT
    assert w.geometry() == zone_rect(Zone.LEFT, desk.viewport().rect())
    assert not snap.preview.isVisible()


def test_drag_away_from_edge_cancels(desk):
    snap = SnapController(desk)
    w = desk.open_program("billing")
    before = w.geometry()
    snap.update(w, QPoint(2, 300))
    snap.update(w, QPoint(400, 300))
    assert snap.finish(w) is None and w.geometry() == before


def test_keyboard_snap(desk):
    snap = SnapController(desk)
    w = desk.open_program("billing")
    snap.snap_active("right")
    assert w.geometry() == zone_rect(Zone.RIGHT, desk.viewport().rect())
    snap.snap_active("up")
    assert w.isMaximized()
    snap.snap_active("down")
    assert not w.isMaximized()
    snap.snap_active("down")
    assert w.is_minimized_to_taskbar


def test_watch_installs_on_open(desk):
    snap = SnapController(desk)
    w = desk.open_program("findings")
    assert w in snap._watched


def test_keyboard_snap_ignores_minimized_window(desk):
    snap = SnapController(desk)
    w = desk.open_program("billing")
    w.minimize_to_taskbar()
    before = w.geometry()
    snap.apply(w, Zone.LEFT)
    assert w.geometry() == before and w.is_minimized_to_taskbar
    snap.snap_active("left")  # no active window: must not raise


def test_apply_ignores_closing_window(desk):
    snap = SnapController(desk)
    w = desk.open_program("billing")
    before = w.geometry()
    w.is_closing = True
    snap.apply(w, Zone.LEFT)
    assert w.geometry() == before
    w.is_closing = False


def test_closed_window_is_forgotten(desk, qtbot):
    snap = SnapController(desk)
    w = desk.open_program("billing")
    snap.update(w, QPoint(2, 300))
    assert w in snap._watched and w in snap._pending
    w.close()
    qtbot.waitUntil(lambda: not snap._watched and not snap._pending)


def test_restore_after_maximize_snap_keeps_pre_maximize_rect(desk):
    snap = SnapController(desk)
    w = desk.open_program("billing")
    snap.apply(w, Zone.LEFT)
    left = QRect(w.geometry())
    snap.apply(w, Zone.TOP)
    assert w.isMaximized()
    w.showNormal()
    assert w.geometry() == left



# ---- review fixes ----
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402

from aws95.shell import snapping  # noqa: E402


def _mouse(kind, pos):
    return QMouseEvent(kind, QPointF(pos), QPointF(pos), Qt.MouseButton.LeftButton,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)


def _press(snap, w, pos):
    snap.eventFilter(w, _mouse(QEvent.Type.MouseButtonPress, pos))


def _release(snap, w):
    snap.eventFilter(w, _mouse(QEvent.Type.MouseButtonRelease, QPoint(20, 10)))


def _move(snap, w, monkeypatch, global_pos, down=True):
    monkeypatch.setattr(snapping, "_left_button_down", lambda: down)
    monkeypatch.setattr(snapping, "_cursor_pos", lambda: desk_global(snap, global_pos))
    snap.eventFilter(w, QEvent(QEvent.Type.Move))


def desk_global(snap, vp_pos):
    return snap._desktop.viewport().mapToGlobal(vp_pos)


TITLE = QPoint(60, 10)
BORDER = QPoint(1, 100)


def test_title_press_move_near_edge_shows_preview_and_snaps(desk, monkeypatch):
    snap = SnapController(desk)
    w = desk.open_program("billing")
    _press(snap, w, TITLE)
    _move(snap, w, monkeypatch, QPoint(2, 300))
    assert snap.preview.isVisible()
    _release(snap, w)
    assert w.geometry() == zone_rect(Zone.LEFT, desk.viewport().rect())


def test_border_press_move_near_edge_does_not_snap(desk, monkeypatch):
    snap = SnapController(desk)
    w = desk.open_program("billing")
    before = w.geometry()
    _press(snap, w, BORDER)
    _move(snap, w, monkeypatch, QPoint(2, 300))
    assert not snap.preview.isVisible()
    _release(snap, w)
    assert w.geometry() == before


def test_hide_mid_drag_clears_preview_and_pending(desk):
    snap = SnapController(desk)
    w = desk.open_program("billing")
    snap.update(w, QPoint(2, 300))
    w.hide()
    assert not snap.preview.isVisible() and not snap._pending


def test_release_without_drag_does_not_apply_stale_zone(desk):
    snap = SnapController(desk)
    w = desk.open_program("billing")
    before = w.geometry()
    snap.update(w, QPoint(2, 300))
    _release(snap, w)
    assert w.geometry() == before and not snap._pending and not snap.preview.isVisible()


def test_move_without_button_cancels(desk, monkeypatch):
    snap = SnapController(desk)
    w = desk.open_program("billing")
    _press(snap, w, TITLE)
    _move(snap, w, monkeypatch, QPoint(2, 300))
    _move(snap, w, monkeypatch, QPoint(2, 300), down=False)
    assert not snap.preview.isVisible() and not snap._pending


def test_snap_active_then_native_minimize_restores_snapped_rect(desk, qtbot):
    snap = SnapController(desk)
    w = desk.open_program("billing")
    desk.open_program("findings")           # billing is now inactive
    w.setGeometry(QRect(10, 10, 300, 200))  # records a (soon stale) normal rect
    desk.setActiveSubWindow(w)
    snap.snap_active("right")
    snapped = zone_rect(Zone.RIGHT, desk.viewport().rect())
    w.showMinimized()
    qtbot.waitUntil(lambda: w.is_minimized_to_taskbar)
    w.restore_from_taskbar()
    assert w.geometry() == snapped
