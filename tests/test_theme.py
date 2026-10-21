import pytest
from PySide6.QtCore import QSettings

from aws95.core.settings import Settings, ThemeMode
from aws95.kit import theme as th


@pytest.fixture
def settings(tmp_path, qapp):
    return Settings(QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat))


@pytest.mark.parametrize("t", [th.LIGHT, th.DARK], ids=["light", "dark"])
def test_text_pairs_meet_aa(t):
    for fg, bg in th.TEXT_PAIRS:
        ratio = th.contrast_ratio(getattr(t, fg), getattr(t, bg))
        assert ratio >= 4.5, f"{t.name}: {fg} on {bg} = {ratio:.2f}"


@pytest.mark.parametrize("t", [th.LIGHT, th.DARK], ids=["light", "dark"])
def test_ui_pairs_meet_3_to_1(t):
    for fg, bg in th.UI_PAIRS:
        ratio = th.contrast_ratio(getattr(t, fg), getattr(t, bg))
        assert ratio >= 3.0, f"{t.name}: {fg} on {bg} = {ratio:.2f}"


def test_tokens_match_spec():
    assert th.LIGHT.face == "#c0c0c0" and th.LIGHT.sel == "#000080" and th.LIGHT.muted == "#444444"
    assert th.DARK.face == "#2d2f36" and th.DARK.money == "#ffcc73" and th.DARK.desktop == "#123c40"


def test_contrast_math():
    assert th.contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, rel=1e-3)
    assert th.contrast_ratio("#777777", "#777777") == pytest.approx(1.0)


def test_manager_follows_explicit_mode(settings, qapp, qtbot):
    settings.theme_mode = ThemeMode.LIGHT
    mgr = th.ThemeManager(settings, qapp)
    assert mgr.theme is th.LIGHT and th.current_theme() is th.LIGHT
    with qtbot.waitSignal(mgr.changed) as blocker:
        settings.theme_mode = ThemeMode.DARK
    assert blocker.args == [th.DARK]
    assert th.current_theme() is th.DARK


def test_manager_system_mode_uses_os(settings, qapp, monkeypatch):
    settings.theme_mode = ThemeMode.SYSTEM
    mgr = th.ThemeManager(settings, qapp)
    monkeypatch.setattr(mgr, "_os_is_dark", lambda: True)
    mgr.refresh()
    assert mgr.theme is th.DARK
    monkeypatch.setattr(mgr, "_os_is_dark", lambda: False)
    mgr.refresh()
    assert mgr.theme is th.LIGHT


def test_theme_bus_emits_on_change(qapp, qtbot):
    th.set_current(th.LIGHT)
    with qtbot.waitSignal(th.theme_bus().changed):
        th.set_current(th.DARK)
    th.set_current(th.LIGHT)


def test_dark_disabled_text_is_readable():
    for bg in ("face", "well"):
        assert th.contrast_ratio(th.DARK.disabled_text, getattr(th.DARK, bg)) >= 4.5
    assert th.DARK.disabled_text != th.DARK.text
