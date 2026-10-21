import os
import stat
import sys

import pytest
from PySide6.QtCore import QByteArray, QSettings

from aws95.core.paths import app_dir
from aws95.core.settings import Settings, ThemeMode


@pytest.fixture
def settings(tmp_path, qapp):
    qs = QSettings(str(tmp_path / "test.ini"), QSettings.Format.IniFormat)
    return Settings(qs)


def test_defaults(settings):
    assert settings.theme_mode is ThemeMode.SYSTEM
    assert settings.font_scale == 100
    assert settings.sound_volume == 80
    assert settings.saver_choice == "random"
    assert settings.saver_timeout_min == 10
    assert settings.play_startup_sound is True
    assert settings.skip_splash is False


def test_round_trip_and_changed_signal(settings, qtbot):
    with qtbot.waitSignal(settings.changed) as blocker:
        settings.theme_mode = ThemeMode.DARK
    assert blocker.args == ["appearance/theme_mode"]
    assert settings.theme_mode is ThemeMode.DARK
    settings.reduce_motion = True
    assert settings.reduce_motion is True


def test_corrupt_values_fall_back_to_defaults(settings):
    settings._qs.setValue("appearance/font_scale", "abc")
    settings._qs.setValue("appearance/theme_mode", "purple")
    settings._qs.setValue("boot/skip_splash", "maybe")
    assert settings.font_scale == 100
    assert settings.theme_mode is ThemeMode.SYSTEM
    assert settings.skip_splash is False


def test_out_of_range_values_are_clamped(settings):
    settings.font_scale = 400
    settings.sound_volume = -5
    settings.saver_timeout_min = 999
    assert settings.font_scale == 150
    assert settings.sound_volume == 0
    assert settings.saver_timeout_min == 60


def test_geometry_round_trip(settings):
    assert settings.geometry("billing") is None
    settings.set_geometry("billing", QByteArray(b"\x01\x02"))
    assert bytes(settings.geometry("billing")) == b"\x01\x02"


def test_values_persist_across_instances(tmp_path, qapp):
    path = str(tmp_path / "persist.ini")
    first = Settings(QSettings(path, QSettings.Format.IniFormat))
    first.theme_mode = ThemeMode.LIGHT
    first.sync()
    second = Settings(QSettings(path, QSettings.Format.IniFormat))
    assert second.theme_mode is ThemeMode.LIGHT


def test_app_dir_override_and_permissions(tmp_path, monkeypatch):
    target = tmp_path / "home"
    monkeypatch.setenv("AWS95_HOME", str(target))
    path = app_dir()
    assert path == target and path.is_dir()
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o700


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_app_dir_tightens_preexisting_own_dir_to_0700(tmp_path, monkeypatch):
    target = tmp_path / "pre"
    target.mkdir()
    os.chmod(target, 0o755)
    monkeypatch.setenv("AWS95_HOME", str(target))
    app_dir()
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o700


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX ownership")
def test_app_dir_refuses_dir_not_owned_by_current_user(tmp_path, monkeypatch):
    target = tmp_path / "foreign"
    target.mkdir()
    os.chmod(target, 0o755)
    monkeypatch.setenv("AWS95_HOME", str(target))
    monkeypatch.setattr(os, "getuid", lambda: os.stat(target).st_uid + 1)
    with pytest.raises(PermissionError, match="not owned by current user"):
        app_dir()
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o755


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_app_dir_regular_file_at_path_raises(tmp_path, monkeypatch):
    target = tmp_path / "afile"
    target.write_text("x")
    monkeypatch.setenv("AWS95_HOME", str(target))
    with pytest.raises(FileExistsError):
        app_dir()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_app_dir_new_nested_dir_is_0700(tmp_path, monkeypatch):
    target = tmp_path / "a" / "b"
    monkeypatch.setenv("AWS95_HOME", str(target))
    app_dir()
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o700
