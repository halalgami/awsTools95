import pytest
from PySide6.QtCore import QSettings, Qt

from aws95.core.settings import Settings, ThemeMode
from aws95.core.tasks import TaskRunner
from aws95.programs.display import make_display
from aws95.providers.empty import empty_provider_set
from aws95.shell.context import ProgramContext


class FakeShell:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a))


class FakeSound:
    def __init__(self):
        self.previews = []

    def play(self, e):
        return False

    def preview(self, e):
        self.previews.append(e)
        return True


@pytest.fixture
def env(tmp_path, qapp, qtbot):
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    runner = TaskRunner()
    shell, sound = FakeShell(), FakeSound()
    dp = make_display(ProgramContext(settings, runner, empty_provider_set(), sound, shell))
    qtbot.addWidget(dp)
    yield dp, settings, shell, sound
    runner.shutdown()


def test_background_tab(env):
    dp, settings, *_ = env
    dp.wallpaper_list.setCurrentRow(2)
    assert settings.wallpaper == "bricks"


def test_screensaver_tab(env):
    dp, settings, shell, _ = env
    idx = dp.saver_combo.findData("mystify")
    dp.saver_combo.setCurrentIndex(idx)
    assert settings.saver_choice == "mystify" and dp.monitor.saver.name == "mystify"
    dp.timeout_spin.setValue(0)
    assert settings.saver_timeout_min == 0 and dp.timeout_spin.text() == "None"
    dp.preview_button.click()
    assert ("preview_screensaver", ()) in shell.calls


def test_appearance_tab(env):
    dp, settings, *_ = env
    dp.theme_buttons[ThemeMode.DARK].click()
    assert settings.theme_mode is ThemeMode.DARK
    dp.font_spin.setValue(130)
    assert settings.font_scale == 130
    dp.reduce_motion_box.setChecked(True)
    assert settings.reduce_motion is True


def test_sounds_tab(env):
    dp, settings, _shell, sound = env
    dp.mute_box.setChecked(True)
    assert settings.sound_muted is True
    dp.volume_slider.setValue(35)
    assert settings.sound_volume == 35
    item = next(dp.event_list.item(i) for i in range(dp.event_list.count())
                if dp.event_list.item(i).data(Qt.ItemDataRole.UserRole) == "balloon")
    item.setCheckState(Qt.CheckState.Unchecked)
    assert settings.sound_event_enabled("balloon") is False
    dp.event_list.setCurrentItem(item)
    dp.sound_preview_button.click()
    assert sound.previews == ["balloon"]


def test_click_event_default_unchecked(env):
    dp, *_ = env
    item = next(dp.event_list.item(i) for i in range(dp.event_list.count())
                if dp.event_list.item(i).data(Qt.ItemDataRole.UserRole) == "click")
    assert item.checkState() == Qt.CheckState.Unchecked


def test_select_tab(env):
    dp, *_ = env
    dp.select_tab("sounds")
    assert dp.tabs.tabText(dp.tabs.currentIndex()) == "Sounds"


def test_manifest_registered():
    from aws95.programs import DEFAULT_MANIFESTS
    from aws95.shell.registry import Registry
    m = Registry(DEFAULT_MANIFESTS).get("display")
    assert m.category == "settings" and m.factory == "aws95.programs.display:make_display"


def test_external_changes_sync_without_feedback(env):
    from aws95.core.settings import ThemeMode
    dp, settings, *_ = env
    seen = []
    settings.changed.connect(seen.append)
    settings.theme_mode = ThemeMode.DARK
    settings.font_scale = 140
    settings.saver_choice = "pipes"
    settings.set_sound_event_enabled("click", True)
    assert dp.theme_buttons[ThemeMode.DARK].isChecked()
    assert dp.font_spin.value() == 140
    assert dp.saver_combo.currentData() == "pipes" and dp.monitor.saver.name == "pipes"
    assert dp.event_list.item(next(i for i in range(dp.event_list.count())
                                   if dp.event_list.item(i).data(Qt.ItemDataRole.UserRole) == "click")
                              ).checkState() == Qt.CheckState.Checked
    assert seen == ["appearance/theme_mode", "appearance/font_scale", "saver/choice", "sound/event/click"]


def test_monitor_saver_stops_when_hidden_and_switch_does_not_leak(env):
    dp, *_ = env
    dp.show()
    dp.select_tab("screensaver")
    first = dp.monitor.saver
    assert first.is_running()
    dp.saver_combo.setCurrentIndex(dp.saver_combo.findData("mystify"))
    assert not first.is_running() and dp.monitor.saver is not first
    assert dp.monitor.saver.is_running()
    dp.hide()
    assert not dp.monitor.saver.is_running()


def test_font_spin_step_and_range(env):
    dp, *_ = env
    assert (dp.font_spin.minimum(), dp.font_spin.maximum(), dp.font_spin.singleStep()) == (100, 150, 10)


def test_startup_sound_and_splash_controls(env):
    dp, settings, *_ = env
    assert dp.startup_sound_box.isChecked() is settings.play_startup_sound is True
    assert dp.show_splash_box.isChecked() is True and settings.skip_splash is False
    dp.startup_sound_box.setChecked(False)
    assert settings.play_startup_sound is False
    dp.show_splash_box.setChecked(False)              # inverted: unchecked = skip the splash
    assert settings.skip_splash is True


def test_startup_controls_reflect_external_changes(env):
    dp, settings, *_ = env
    seen = []
    settings.changed.connect(seen.append)
    settings.play_startup_sound = False
    settings.skip_splash = True
    assert not dp.startup_sound_box.isChecked() and not dp.show_splash_box.isChecked()
    settings.skip_splash = False
    assert dp.show_splash_box.isChecked()
    assert seen == ["boot/play_startup_sound", "boot/skip_splash", "boot/skip_splash"]   # no echo
