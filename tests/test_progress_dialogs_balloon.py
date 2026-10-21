import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QDialog

from aws95.kit import theme as th
from aws95.kit.style import apply_theme
from aws95.kit.widgets.balloon import Balloon
from aws95.kit.widgets.dialogs import Icon, MessageBox95, icon_pixmap
from aws95.kit.widgets.progress import BlockProgress


@pytest.fixture(autouse=True)
def light(qapp):
    apply_theme(qapp, th.LIGHT)


def test_block_progress_clamps(qtbot):
    bar = BlockProgress()
    qtbot.addWidget(bar)
    bar.set_value(150)
    assert bar.value() == 100
    bar.set_value(-3)
    assert bar.value() == 0


def test_block_progress_paints_sel_blocks(qtbot):
    bar = BlockProgress()
    qtbot.addWidget(bar)
    bar.resize(200, 18)
    bar.set_value(100)
    img = bar.grab().toImage()
    row = [img.pixelColor(x, 9).name() for x in range(200)]
    assert row.count(th.LIGHT.sel) > 100


def test_indeterminate_animates_only_when_visible(qtbot):
    bar = BlockProgress()
    qtbot.addWidget(bar)
    bar.set_indeterminate(True)
    assert not bar.is_animating()
    bar.show()
    qtbot.waitExposed(bar)
    assert bar.is_animating()
    bar.hide()
    assert not bar.is_animating()


@pytest.mark.parametrize("icon", list(Icon))
def test_icon_pixmaps(icon):
    pm = icon_pixmap(icon, 32)
    assert not pm.isNull() and pm.width() == 32


def test_message_box_choice_and_default(qtbot):
    box = MessageBox95(None, "Delete?", "Really?", Icon.QUESTION, ("Yes", "No"), default=1)
    qtbot.addWidget(box)
    assert box.buttons[1].isDefault()
    box.buttons[0].click()
    assert box.choice == 0 and box.result() == QDialog.DialogCode.Accepted


def test_message_box_escape_means_no_choice(qtbot):
    box = MessageBox95(None, "Info", "Hello")
    qtbot.addWidget(box)
    box.reject()
    assert box.choice is None


def test_message_box_details_toggle(qtbot):
    box = MessageBox95(None, "Error", "Failed", Icon.ERROR, details="Traceback ...")
    qtbot.addWidget(box)
    box.show()
    assert not box.details_view.isVisible()
    box.details_button.click()
    assert box.details_view.isVisible()


def test_balloon_times_out_and_clicks(qtbot):
    b = Balloon("Refresh complete", "lab-sandbox: done", timeout_ms=50)
    qtbot.addWidget(b)
    with qtbot.waitSignal(b.dismissed, timeout=2000):
        b.show_at(QPoint(400, 400))
    assert not b.isVisible()
    b2 = Balloon("Hi", "click me", timeout_ms=10_000)
    qtbot.addWidget(b2)
    b2.show_at(QPoint(400, 400))
    with qtbot.waitSignal(b2.clicked):
        qtbot.mouseClick(b2, Qt.MouseButton.LeftButton)


def test_message_box_text_is_plain(qtbot):
    raw = "<b>bold</b> <Response [403]>"
    box = MessageBox95(None, "E", raw)
    qtbot.addWidget(box)
    assert box.text_label.textFormat() == Qt.TextFormat.PlainText
    assert box.text_label.text() == raw
