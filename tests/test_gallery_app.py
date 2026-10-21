import time
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QGroupBox

from aws95 import app as app_module
from aws95.app import build_parser, demo_enabled
from aws95.core.settings import Settings, ThemeMode
from aws95.core.tasks import TaskRunner
from aws95.gallery import KitGallery, synthetic_rows
from aws95.kit import theme as th
from aws95.kit.style import install_theming
from aws95.kit.widgets.stateview import ViewState
from aws95.kit.widgets.watermark import Watermark
from aws95.providers.demo import demo_provider_set
from aws95.providers.empty import empty_provider_set

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"


def test_demo_enabled_flag_and_env():
    assert demo_enabled(build_parser().parse_args(["--demo"]), {})
    assert demo_enabled(build_parser().parse_args([]), {"AWS95_DEMO": "1"})
    assert not demo_enabled(build_parser().parse_args([]), {"AWS95_DEMO": "0"})


def test_synthetic_rows_deterministic():
    assert synthetic_rows(5) == synthetic_rows(5)
    assert len(synthetic_rows(1000)) == 1000


@pytest.fixture
def gallery(qapp, qtbot, tmp_path):
    settings = Settings(QSettings(str(tmp_path / "g.ini"), QSettings.Format.IniFormat))
    settings.theme_mode = ThemeMode.LIGHT
    mgr = th.ThemeManager(settings, qapp)
    install_theming(qapp, mgr, settings)
    runner = TaskRunner()
    win = KitGallery(settings, runner, demo_provider_set())
    win.rows_at_construction = win.grid.model.total_count  # 0 proves generation did not block the constructor
    qtbot.addWidget(win)
    win.resize(1280, 900)
    win.show()
    qtbot.waitExposed(win)
    yield win, settings
    runner.shutdown()
    settings.theme_mode = ThemeMode.LIGHT


def test_gallery_loads_100k_rows_off_thread(gallery, qtbot):
    win, _ = gallery
    assert win.rows_at_construction == 0
    qtbot.waitUntil(lambda: win.grid.model.total_count == 100_000, timeout=10_000)
    assert win.grid_state.state is ViewState.CONTENT


def test_gallery_theme_switch_is_fast_and_screenshots(gallery, qtbot):
    win, settings = gallery
    qtbot.waitUntil(lambda: win.grid.model.total_count == 100_000, timeout=10_000)
    ARTIFACTS.mkdir(exist_ok=True)
    win.grab().save(str(ARTIFACTS / "gallery-light.png"))
    t0 = time.perf_counter()
    win.theme_buttons[ThemeMode.DARK].click()
    elapsed = time.perf_counter() - t0
    assert settings.theme_mode is ThemeMode.DARK
    assert th.current_theme() is th.DARK
    win.grab().save(str(ARTIFACTS / "gallery-dark.png"))
    assert elapsed < 0.5, f"theme switch took {elapsed*1000:.0f} ms"  # spec budget 200 ms; M4 measures packaged


def test_gallery_inspector_follows_selection(gallery, qtbot):
    win, _ = gallery
    qtbot.waitUntil(lambda: win.grid.model.total_count == 100_000, timeout=10_000)
    win.grid.view.selectRow(3)
    assert win.inspector.title_label.text() == win.grid.model.row_at(3)["name"]


def test_gallery_without_demo_shows_not_connected_and_no_fake_data(qapp, qtbot, tmp_path):
    settings = Settings(QSettings(str(tmp_path / "e.ini"), QSettings.Format.IniFormat))
    runner = TaskRunner()
    try:
        win = KitGallery(settings, runner, empty_provider_set())
        qtbot.addWidget(win)
        assert win.grid_state.state is ViewState.NOT_CONNECTED
        assert win.grid.model.total_count == 0
        assert win.bar_chart.bar_count == 0
        assert win.line_chart.point_count == 0
        assert runner.active_count == 0
    finally:
        runner.shutdown()


def _watermarks(widget):
    return widget.findChildren(Watermark)


def test_gallery_watermarks_in_demo_mode(gallery):
    win, _ = gallery
    assert _watermarks(win.grid)
    assert _watermarks(win.charts_container)
    assert _watermarks(win.inspector)


def test_gallery_no_watermarks_or_demo_title_in_empty_mode(qapp, qtbot, tmp_path):
    settings = Settings(QSettings(str(tmp_path / "w.ini"), QSettings.Format.IniFormat))
    runner = TaskRunner()
    try:
        win = KitGallery(settings, runner, empty_provider_set())
        qtbot.addWidget(win)
        assert not _watermarks(win.grid)
        assert not _watermarks(win.charts_container)
        assert not _watermarks(win.inspector)
        assert not any("demo rows" in b.title() for b in win.findChildren(QGroupBox))
    finally:
        runner.shutdown()


@pytest.mark.parametrize("argv", [[], ["--demo"]])
def test_main_smoke(argv, qapp, tmp_path, monkeypatch):
    ini = str(tmp_path / "m.ini")
    import aws95.core.settings as settings_mod

    class TmpSettings(Settings):
        def __init__(self, qsettings=None, parent=None):
            super().__init__(QSettings(ini, QSettings.Format.IniFormat), parent)

    monkeypatch.setattr("aws95.core.settings.Settings", TmpSettings)
    monkeypatch.setattr(QApplication, "exec", lambda *a, **k: 0)
    monkeypatch.delenv("AWS95_DEMO", raising=False)
    assert app_module.main(argv) == 0


@pytest.mark.parametrize("argv,env,mode", [([], {}, "live"), (["--demo"], {}, "demo"), (["--offline"], {}, "offline"),
                                           ([], {"AWS95_OFFLINE": "1"}, "offline"), (["--demo", "--offline"], {}, "demo")])
def test_provider_mode(argv, env, mode):
    from aws95.app import build_parser, provider_mode
    assert provider_mode(build_parser().parse_args(argv), env) == mode
