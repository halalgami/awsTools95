import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLabel, QMdiArea, QMdiSubWindow

from aws95.contracts.common import Result, Status
from aws95.kit import theme as th
from aws95.kit.paint import draw_title_glyph
from aws95.kit.style import apply_theme
from aws95.kit.widgets.datagrid import Column, DataGrid
from aws95.kit.widgets.stateview import StateView


@pytest.fixture(autouse=True)
def light(qapp):
    apply_theme(qapp, th.LIGHT)
    yield
    apply_theme(qapp, th.LIGHT)


def _area_with(qtbot, n=2):
    area = QMdiArea()
    qtbot.addWidget(area)
    area.resize(800, 600)
    subs = []
    for i in range(n):
        sub = QMdiSubWindow()
        sub.setWidget(QLabel(f"window {i}"))
        sub.setWindowTitle(f"Window {i}")
        area.addSubWindow(sub)
        sub.setGeometry(20 + 40 * i, 20 + 40 * i, 300, 200)
        sub.show()
        subs.append(sub)
    area.show()
    qtbot.waitExposed(area)
    return area, subs


def _title_pixel(sub, x=0):
    # Qt paints the title bar over the top frame edge (rows 0..19); x=0 is the gradient start colour.
    img = sub.grab().toImage()
    return img.pixelColor(x, 10).name()


def test_active_and_inactive_title_colours(qtbot):
    area, (a, b) = _area_with(qtbot)
    area.setActiveSubWindow(a)
    assert _title_pixel(a) == th.LIGHT.title_active_start
    area.setActiveSubWindow(b)
    assert _title_pixel(a) == th.LIGHT.title_inactive_start


def test_frame_is_raised_panel(qtbot):
    area, (a, _b) = _area_with(qtbot)
    img = a.grab().toImage()
    # The title bar covers the frame's top edge, so sample the left edge below it:
    # raised panel = face2 outer, hi inner, dark outer bottom.
    assert img.pixelColor(0, 100).name() == th.LIGHT.face2
    assert img.pixelColor(1, 100).name() == th.LIGHT.hi
    assert img.pixelColor(img.width() // 2, img.height() - 1).name() == th.LIGHT.dark


def test_chip_painted_in_title(qtbot):
    area, (a, _b) = _area_with(qtbot)
    a.setProperty("aws95ChipText", "lab-sandbox")
    a.setProperty("aws95ChipColor", "#ffd34d")
    a.update()
    area.setActiveSubWindow(a)
    img = a.grab().toImage()
    row = [img.pixelColor(x, 10).name() for x in range(img.width())]
    assert row.count("#ffd34d") > 20


@pytest.mark.parametrize("glyph", ["min", "max", "restore", "close"])
def test_title_glyphs_draw(glyph):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QPainter
    img = QImage(16, 14, QImage.Format.Format_RGB32)
    img.fill(QColor("#c0c0c0"))
    p = QPainter(img)
    draw_title_glyph(p, QRect(0, 0, 16, 14), glyph, "#000000")
    p.end()
    blacks = sum(1 for x in range(16) for y in range(14) if img.pixelColor(x, y).name() == "#000000")
    assert blacks >= 6


def test_restore_glyph_back_window_hidden_behind_front():
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QPainter
    img = QImage(16, 14, QImage.Format.Format_RGB32)
    img.fill(QColor("#c0c0c0"))
    p = QPainter(img)
    draw_title_glyph(p, QRect(0, 0, 16, 14), "restore", "#000000")
    p.end()
    x0, y0 = 4, 3
    assert img.pixelColor(x0 + 5, y0).name() == "#000000"       # back window title bar
    assert img.pixelColor(x0 + 8, y0 + 3).name() == "#000000"   # back window right edge
    assert img.pixelColor(x0 + 3, y0 + 5).name() == "#c0c0c0"   # front interior: back's bottom edge clipped
    assert img.pixelColor(x0 + 5, y0 + 4).name() == "#000000"   # front window title bar
    assert img.pixelColor(x0, y0 + 6).name() == "#000000"       # front left edge


def test_long_title_keeps_chip(qtbot):
    area = QMdiArea()
    qtbot.addWidget(area)
    area.resize(800, 600)
    sub = QMdiSubWindow()
    sub.setWidget(QLabel("x"))
    sub.setWindowTitle("A very long window title that cannot possibly fit in the caption " * 3)
    sub.setProperty("aws95ChipText", "lab-sandbox")
    sub.setProperty("aws95ChipColor", "#ffd34d")
    area.addSubWindow(sub)
    sub.setGeometry(10, 10, 220, 160)
    sub.show()
    area.show()
    qtbot.waitExposed(area)
    area.setActiveSubWindow(sub)
    img = sub.grab().toImage()
    row = [img.pixelColor(x, 10).name() for x in range(img.width())]
    assert row.count("#ffd34d") > 20


def test_subwindow_corners_are_square(qtbot):
    area, (a, _b) = _area_with(qtbot)
    img = a.grab().toImage()
    bg = area.palette().window().color().name()
    assert img.pixelColor(0, 0).name() != bg
    assert img.pixelColor(img.width() - 1, img.height() - 1).name() != bg


def test_stateview_banner_follows_theme(qtbot, qapp):
    sv = StateView(QLabel("x"))
    qtbot.addWidget(sv)
    sv.apply_result(Result(Status.PARTIAL, [1], message="eu-west-1 timed out"), lambda d: None)
    assert sv._banner.palette().window().color().name() == th.LIGHT.banner_bg
    apply_theme(qapp, th.DARK)
    assert sv._banner.palette().window().color().name() == th.DARK.banner_bg


def test_datagrid_invalid_colour_follows_theme(qtbot, qapp):
    grid = DataGrid([Column("cpu", "CPU")])
    qtbot.addWidget(grid)
    grid.set_rows([{"cpu": 1}])
    grid.set_filter_text("cpu>")
    assert grid.filter_edit.palette().base().color().name() == th.LIGHT.banner_bg
    apply_theme(qapp, th.DARK)
    assert grid.filter_edit.palette().base().color().name() == th.DARK.banner_bg
