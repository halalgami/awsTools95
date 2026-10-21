import pytest
from PySide6.QtCore import QSettings

from aws95.core.settings import Settings


@pytest.fixture
def settings(tmp_path, qapp):
    return Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))


def test_new_defaults(settings):
    assert settings.wallpaper == "none"
    assert settings.show_tips is True and settings.tips_seen is False
    assert settings.active_account == ""


def test_wallpaper_rejects_unknown(settings):
    settings._qs.setValue("appearance/wallpaper", "plaid")
    assert settings.wallpaper == "none"
    settings.wallpaper = "bricks"
    assert settings.wallpaper == "bricks"


def test_sound_event_toggles(settings, qtbot):
    assert settings.sound_event_enabled("balloon") is True
    assert settings.sound_event_enabled("click", default=False) is False
    with qtbot.waitSignal(settings.changed) as blocker:
        settings.set_sound_event_enabled("balloon", False)
    assert blocker.args == ["sound/event/balloon"]
    assert settings.sound_event_enabled("balloon") is False
    settings._qs.setValue("sound/event/error", "garbage")
    assert settings.sound_event_enabled("error") is True


def test_icon_positions(settings):
    assert settings.icon_position("billing") is None
    settings.set_icon_position("billing", 8, 80)
    assert settings.icon_position("billing") == (8, 80)
    settings._qs.setValue("desktop/icon/bad", "x,y")
    assert settings.icon_position("bad") is None
    settings.clear_icon_positions()
    assert settings.icon_position("billing") is None
