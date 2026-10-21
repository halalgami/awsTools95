import pytest
from PySide6.QtCore import QRect, QSettings
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLineEdit, QProgressBar, QPushButton, QToolButton

from aws95.core.settings import Settings, ThemeMode
from aws95.kit import theme as th
from aws95.kit.paint import bevel_colors, draw_bevel
from aws95.kit.style import apply_theme, install_theming


def _pixel(widget, x, y):
    return widget.grab().toImage().pixelColor(x, y).name()


def test_bevel_colors_raised_and_sunken():
    assert bevel_colors(th.LIGHT, sunken=False, kind="button") == ("#ffffff", "#404040", "#d4d4d4", "#808080")
    assert bevel_colors(th.LIGHT, sunken=False, kind="field")[0] == "#808080"


@pytest.mark.parametrize("kind,sunken,names", [
    ("button", False, ("hi", "dark", "face2", "lo")),
    ("button", True, ("dark", "hi", "lo", "face2")),
    ("panel", False, ("face2", "dark", "hi", "lo")),
    ("panel", True, ("lo", "hi", "dark", "face2")),
    ("field", False, ("lo", "hi", "dark", "face2")),
    ("field", True, ("lo", "hi", "dark", "face2")),
])
def test_bevel_colors_all_combinations(kind, sunken, names):
    t = th.LIGHT
    assert bevel_colors(t, sunken=sunken, kind=kind) == tuple(getattr(t, n) for n in names)


def test_draw_bevel_corners():
    img = QImage(20, 10, QImage.Format.Format_RGB32)
    img.fill(QColor("#123456"))
    p = QPainter(img)
    draw_bevel(p, img.rect(), th.LIGHT, kind="button", fill=th.LIGHT.face)
    p.end()
    assert img.pixelColor(0, 0).name() == th.LIGHT.hi
    assert img.pixelColor(19, 9).name() == th.LIGHT.dark
    assert img.pixelColor(10, 5).name() == th.LIGHT.face


@pytest.fixture
def light_app(qapp):
    apply_theme(qapp, th.LIGHT)
    yield qapp
    apply_theme(qapp, th.LIGHT)


def test_push_button_has_raised_bevel(light_app, qtbot):
    btn = QPushButton("OK")
    qtbot.addWidget(btn)
    btn.resize(80, 26)
    assert _pixel(btn, 0, 0) == th.LIGHT.hi
    assert _pixel(btn, 79, 25) == th.LIGHT.dark


def test_line_edit_is_sunken_well(light_app, qtbot):
    edit = QLineEdit()
    qtbot.addWidget(edit)
    edit.resize(120, 24)
    assert _pixel(edit, 0, 0) == th.LIGHT.lo
    assert _pixel(edit, 60, 12) == th.LIGHT.well


def test_progress_bar_draws_blocks(light_app, qtbot):
    bar = QProgressBar()
    qtbot.addWidget(bar)
    bar.setRange(0, 100)
    bar.setValue(100)
    bar.setTextVisible(False)
    bar.resize(200, 20)
    img = bar.grab().toImage()
    row = [img.pixelColor(x, 10).name() for x in range(img.width())]
    assert row.count(th.LIGHT.sel) > img.width() * 0.5


def test_live_switch_repaints_existing_widgets(qapp, qtbot, tmp_path):
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    settings.theme_mode = ThemeMode.LIGHT
    mgr = th.ThemeManager(settings, qapp)
    install_theming(qapp, mgr, settings)
    btn = QPushButton("OK")
    qtbot.addWidget(btn)
    btn.resize(80, 26)
    assert _pixel(btn, 0, 0) == th.LIGHT.hi
    settings.theme_mode = ThemeMode.DARK
    assert _pixel(btn, 0, 0) == th.DARK.hi
    settings.theme_mode = ThemeMode.LIGHT


def test_font_scale_applies(qapp, tmp_path):
    from aws95.kit.style import BASE_POINT_SIZE
    apply_theme(qapp, th.LIGHT, font_scale=150)
    assert qapp.font().pointSizeF() == pytest.approx(BASE_POINT_SIZE * 1.5)
    apply_theme(qapp, th.LIGHT, font_scale=100)


def test_plain_tool_button_is_raised(light_app, qtbot):
    btn = QToolButton()
    qtbot.addWidget(btn)
    btn.resize(30, 26)
    assert _pixel(btn, 0, 0) == th.LIGHT.hi
    assert _pixel(btn, 29, 25) == th.LIGHT.dark


def test_autoraise_tool_button_flat_when_idle(light_app, qtbot):
    btn = QToolButton()
    qtbot.addWidget(btn)
    btn.setAutoRaise(True)
    btn.resize(30, 26)
    assert _pixel(btn, 0, 0) != th.LIGHT.hi


def test_vertical_progress_bar_fills(light_app, qtbot):
    bar = QProgressBar()
    qtbot.addWidget(bar)
    bar.setOrientation(Qt.Orientation.Vertical)
    bar.setRange(0, 100)
    bar.setValue(100)
    bar.setTextVisible(False)
    bar.resize(20, 200)
    img = bar.grab().toImage()
    col = [img.pixelColor(10, y).name() for y in range(img.height())]
    assert col.count(th.LIGHT.sel) > img.height() * 0.5


def test_inverted_progress_bar_fills_from_right(light_app, qtbot):
    bar = QProgressBar()
    qtbot.addWidget(bar)
    bar.setInvertedAppearance(True)
    bar.setRange(0, 100)
    bar.setValue(30)
    bar.setTextVisible(False)
    bar.resize(200, 20)
    img = bar.grab().toImage()
    row = [img.pixelColor(x, 10).name() for x in range(img.width())]
    assert th.LIGHT.sel in row[-30:]
    assert th.LIGHT.sel not in row[:30]


def test_indeterminate_progress_bar_draws_blocks(light_app, qtbot):
    bar = QProgressBar()
    qtbot.addWidget(bar)
    bar.setRange(0, 0)
    bar.setTextVisible(False)
    bar.resize(200, 20)
    img = bar.grab().toImage()
    row = [img.pixelColor(x, 10).name() for x in range(img.width())]
    assert th.LIGHT.sel in row


def test_raised_frame_uses_panel_bevel(light_app, qtbot):
    frame = QFrame()
    qtbot.addWidget(frame)
    frame.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
    frame.resize(60, 40)
    assert _pixel(frame, 0, 0) == bevel_colors(th.LIGHT, sunken=False, kind="panel")[0]
