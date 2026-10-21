import pytest
from PySide6.QtGui import QColor

from aws95.kit import theme as th
from aws95.kit.style import apply_theme
from aws95.kit.widgets.bevel import BevelPanel
from aws95.kit.widgets.buttons import Chip, RAINBOW


@pytest.fixture(autouse=True)
def light(qapp):
    apply_theme(qapp, th.LIGHT)


def _px(w, x, y):
    return w.grab().toImage().pixelColor(x, y).name()


def _px_close(w, x, y, expected_hex, tolerance=8):
    """Compare pixel colour with per-channel tolerance."""
    actual = w.grab().toImage().pixelColor(x, y)
    expected = QColor(expected_hex)
    for component in ('red', 'green', 'blue'):
        actual_val = getattr(actual, component)()
        expected_val = getattr(expected, component)()
        if abs(actual_val - expected_val) > tolerance:
            return False
    return True


def test_bevel_panel_looks(qtbot):
    raised, well = BevelPanel("raised"), BevelPanel("well")
    for w in (raised, well):
        qtbot.addWidget(w)
        w.resize(60, 40)
    assert _px(raised, 30, 20) == th.LIGHT.face
    assert _px(well, 0, 0) == th.LIGHT.lo
    assert _px(well, 30, 20) == th.LIGHT.well


def test_bevel_panel_rejects_unknown_look():
    with pytest.raises(ValueError):
        BevelPanel("bubbly")


def test_chip_text_colour_contrast():
    assert Chip.text_color_for("#ffd34d") == "#000000"
    assert Chip.text_color_for("#000080") == "#ffffff"


def test_chip_paints_border_and_fill(qtbot):
    chip = Chip("lab-sandbox", "#ffd34d")
    qtbot.addWidget(chip)
    chip.resize(chip.sizeHint())
    assert _px(chip, 0, 0) == "#000000"
    assert _px(chip, 2, chip.height() // 2) == "#ffd34d"
    chip.set_color("#7ec8ff")
    assert chip.color() == "#7ec8ff"


def test_rainbow_chip_renders(qtbot):
    chip = Chip("All accounts", "rainbow")
    qtbot.addWidget(chip)
    chip.resize(120, 20)
    chip.show()
    # Verify gradient paints start (RAINBOW[0]) and end (RAINBOW[-1]) colours
    # Sample at corners near edges (avoiding 1px border at x=0, x=width-1)
    # and y near top (avoiding centered text), allow tolerance due to interpolation
    assert _px_close(chip, 3, 1, RAINBOW[0], tolerance=16)
    assert _px_close(chip, chip.width() - 4, 1, RAINBOW[-1], tolerance=16)


def test_chip_rejects_invalid_colour():
    with pytest.raises(ValueError, match="invalid colour"):
        Chip("x", "zzz")


def test_chip_set_color_rejects_invalid():
    chip = Chip("test", "#ffd34d")
    with pytest.raises(ValueError, match="invalid colour"):
        chip.set_color("not-a-colour")
    # Verify colour unchanged after failed set
    assert chip.color() == "#ffd34d"


def test_chip_keeps_size_hint_in_squeezed_layout(qtbot):
    from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget
    box = QWidget()
    qtbot.addWidget(box)
    lay = QHBoxLayout(box)
    chip = Chip("lab-sandbox", "#ffd34d")
    tall = QPushButton("tall")
    tall.setMinimumHeight(120)
    lay.addWidget(chip)
    lay.addWidget(tall)
    box.resize(40, 150)  # narrower than the chip's own hint
    box.show()
    lay.activate()
    assert chip.size() == chip.sizeHint()
    assert chip.minimumSizeHint() == chip.sizeHint()
