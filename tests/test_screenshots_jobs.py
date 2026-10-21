"""The running-job words in the tray and the job-finished balloon, Light, Dark and Dark-150 % (owner, 2026-10-15)."""
import pytest
from PySide6.QtCore import QPoint, QSettings, Qt, QTime
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget

from aws95.core.settings import Settings
from aws95.core.tasks import TaskRunner
from aws95.kit.style import apply_theme
from aws95.kit.widgets.balloon import Balloon
from aws95.programs import DEFAULT_MANIFESTS
from aws95.providers.empty import empty_provider_set
from aws95.shell.context import NullSound, ProgramContext
from aws95.shell.desktop import Desktop
from aws95.shell.registry import Registry
from aws95.shell.taskbar import Taskbar
from tests.test_screenshots import THEMES3, _compare, _wait_stable
from tests.test_taskbar import FakeShell


@pytest.mark.parametrize("theme_name", list(THEMES3))
def test_tray_job_text_screenshot(request, tmp_path, qapp, qtbot, theme_name):
    theme, scale = THEMES3[theme_name]
    apply_theme(qapp, theme, font_scale=scale)
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    runner = TaskRunner()
    shell = FakeShell()
    ctx = ProgramContext(settings, runner, empty_provider_set(), NullSound(), shell)
    desk = Desktop(Registry(DEFAULT_MANIFESTS), lambda: ctx, settings)
    bar = Taskbar(shell, desk, settings, runner)
    for w in (desk, bar):
        qtbot.addWidget(w)
    bar.resize(1000, bar.sizeHint().height())
    bar.show()
    bar.tray.clock.refresh(QTime(9, 41))
    bar.tray.set_job_text("Snapshot dev: 7/19 collectors · me-south-1")
    _wait_stable(qtbot, bar.tray)
    try:
        _compare(request, bar.tray.grab().toImage(), f"tray-job-{theme_name}")
    finally:
        runner.shutdown()


@pytest.mark.parametrize("theme_name", list(THEMES3))
def test_job_balloon_screenshot(request, qapp, qtbot, theme_name):
    theme, scale = THEMES3[theme_name]
    apply_theme(qapp, theme, font_scale=scale)
    host = QWidget()
    qtbot.addWidget(host)
    balloon = Balloon("Snapshot finished with gaps", "dev: complete · prod: partial (2 denied)", parent=host)
    balloon.show_at(QPoint(600, 400))
    _wait_stable(qtbot, balloon)
    img = QImage(balloon.size(), QImage.Format.Format_ARGB32)    # render, not grab: the corners are see-through
    img.fill(Qt.GlobalColor.transparent)
    balloon.render(img)
    _compare(request, img, f"job-balloon-{theme_name}")
    balloon.hide()


def test_balloon_and_tray_text_grow_with_the_font(qapp, qtbot, tmp_path):
    """Dark and 150 % font are Important: the balloon's title is never cut, and the tray shows as many characters
    of the job's words at 150 % as at 100 %."""
    from PySide6.QtGui import QFont, QFontMetrics
    from aws95.kit import theme as th
    apply_theme(qapp, th.DARK, font_scale=150)
    try:
        b = Balloon("Snapshot finished with gaps", "dev: complete · prod: partial (2 denied)")
        qtbot.addWidget(b)
        bold = QFont(b.font())
        bold.setBold(True)
        assert b.sizeHint().width() >= QFontMetrics(bold).horizontalAdvance(b.title()) + 18
        from aws95.shell import taskbar
        tray_max = taskbar.job_width(QFontMetrics(qapp.font()))
        assert tray_max >= QFontMetrics(qapp.font()).averageCharWidth() * 30
    finally:
        apply_theme(qapp, th.LIGHT, font_scale=100)
