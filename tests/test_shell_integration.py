import pytest
from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from aws95.core.settings import Settings
from aws95.core.tasks import TaskRunner
from aws95.programs import DEFAULT_MANIFESTS
from aws95.providers.empty import empty_provider_set
from aws95.shell.registry import Registry
from aws95.shell.shell import ShellWindow
from aws95.shell.snapping import Zone, zone_rect


class RecSound:
    def __init__(self):
        self.played = []

    def play(self, e):
        self.played.append(e)
        return True

    def preview(self, e):
        return True


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def shell(tmp_path, qapp, qtbot):
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    runner = TaskRunner()
    clock = Clock()
    sound = RecSound()
    win = ShellWindow(settings, runner, empty_provider_set(), sound, Registry(DEFAULT_MANIFESTS), idle_clock=clock)
    qtbot.addWidget(win)
    win.resize(1200, 800)
    win.show()
    qtbot.waitExposed(win)
    yield win, settings, clock, sound
    win.saver.dismiss()
    runner.shutdown()


def test_boot_open_and_taskbar(shell):
    win, _s, _c, sound = shell
    a = win.open_program("resources")
    b = win.open_program("resources")
    win.open_program("billing")
    win.open_program("billing")
    assert len(win.desktop.program_windows()) == 3
    assert len(win.taskbar.buttons()) == 3
    assert sound.played.count("window_open") == 3
    assert a is not b


def test_keyboard_snap_shortcut(shell, qtbot):
    win, *_ = shell
    w = win.open_program("billing")
    win.shortcuts["Ctrl+Alt+Left"].activated.emit()
    assert w.geometry() == zone_rect(Zone.LEFT, win.desktop.viewport().rect())


def test_switcher_flow(shell):
    win, *_ = shell
    a = win.open_program("billing")
    win.open_program("findings")
    win.show_switcher()
    win.switcher.commit()
    assert win.desktop.active_program_window() is a


def test_palette_opens_program(shell):
    win, *_ = shell
    win.show_palette()
    win.command_palette.query_edit.setText("snapshots")       # "snapsh" also finds the Take Snapshot command
    win.command_palette.run_current()
    assert any(w.manifest.id == "snapshots" for w in win.desktop.program_windows())


def test_idle_into_saver_and_resume(shell, qtbot):
    win, settings, clock, _ = shell
    settings.saver_timeout_min = 1
    clock.t = 120
    win.idle.check()
    assert win.saver.active and not win.isVisible()
    saver_window = win.saver.windows[0]
    saver_window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_X, Qt.KeyboardModifier.NoModifier))
    assert win.saver.active
    win.saver.panel.resume_button.click()
    assert not win.saver.active and win.isVisible()
    clock.t = 130
    win.idle.check()                       # re-armed, not yet idle again
    assert not win.saver.active


def test_close_shortcut_and_recycle(shell, qtbot):
    win, _s, _c, sound = shell
    win.open_program("billing")
    win.shortcuts["Ctrl+W"].activated.emit()
    qtbot.waitUntil(lambda: not win.desktop.program_windows())
    assert "window_close" in sound.played
    assert win.recycle_bin().items()[0].program_id == "billing"


def test_manifest_shortcuts_registered(shell):
    win, *_ = shell
    assert "Ctrl+Shift+B" in win.shortcuts
    win.shortcuts["Ctrl+Shift+B"].activated.emit()
    assert any(w.manifest.id == "billing" for w in win.desktop.program_windows())


def test_main_shell_and_gallery_smoke(monkeypatch, tmp_path):
    import aws95.app as app_mod
    import aws95.core.settings as settings_mod

    class TmpSettings(settings_mod.Settings):
        def __init__(self, qsettings=None, parent=None):
            super().__init__(QSettings(str(tmp_path / "m.ini"), QSettings.Format.IniFormat), parent)

    monkeypatch.setattr(settings_mod, "Settings", TmpSettings)
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    TmpSettings().skip_splash = True
    assert app_mod.main([]) == 0
    assert app_mod.main(["--gallery"]) == 0


def test_restart_code_loops(monkeypatch, tmp_path):
    import aws95.app as app_mod
    import aws95.core.settings as settings_mod
    from aws95.shell.shutdown import RESTART_CODE

    class TmpSettings(settings_mod.Settings):
        def __init__(self, qsettings=None, parent=None):
            super().__init__(QSettings(str(tmp_path / "r.ini"), QSettings.Format.IniFormat), parent)

    monkeypatch.setattr(settings_mod, "Settings", TmpSettings)
    codes = iter([RESTART_CODE, 0])
    runs = []
    monkeypatch.setattr(QApplication, "exec", lambda self: (runs.append(1), next(codes))[1])
    TmpSettings().skip_splash = True
    assert app_mod.main([]) == 0 and len(runs) == 2


# ----- additional binding requirements (post-review rulings) -----

def test_saver_sessions_tracked_for_teardown(shell):
    from aws95.shell.screensaver.manager import live_sessions
    win, settings, clock, _ = shell
    settings.saver_timeout_min = 1
    clock.t = 120
    win.idle.check()
    assert win.saver.active and win.saver in live_sessions
    win.saver.dismiss()
    assert not win.saver.active and win.isVisible() and win.saver not in live_sessions


def test_close_saves_every_window_geometry(shell):
    from PySide6.QtCore import QRect

    from aws95.shell.window import decode_rect
    win, settings, *_ = shell
    a = win.open_program("billing")
    b = win.open_program("resources")
    a.setGeometry(QRect(10, 20, 300, 200))
    b.setGeometry(QRect(40, 50, 320, 210))
    win.close()
    assert decode_rect(settings.geometry("billing")) == QRect(10, 20, 300, 200)
    assert decode_rect(settings.geometry("resources#0")) == QRect(40, 50, 320, 210)


def test_about_to_quit_saves_geometry(shell, qapp):
    from PySide6.QtCore import QRect

    from aws95.shell.window import decode_rect
    win, settings, *_ = shell
    a = win.open_program("billing")
    a.setGeometry(QRect(30, 30, 310, 220))
    qapp.aboutToQuit.emit()
    assert decode_rect(settings.geometry("billing")) == QRect(30, 30, 310, 220)


def test_offline_palette_adds_nothing(shell, qtbot):
    """2d Review Focus 5: the offline shell passes no searcher."""
    from aws95.shell.palette import SEARCHING
    win, *_ = shell
    win.show_palette()
    win.command_palette.query_edit.setText("snapshots")
    qtbot.wait(300)
    rows = [win.command_palette.list_widget.item(i).text() for i in range(win.command_palette.list_widget.count())]
    assert SEARCHING not in rows
    win.command_palette.hide()


def test_take_snapshot_is_the_first_desktop_icon(shell, qtbot):
    win, *_ = shell
    first = win.icons.icons()[0]
    assert first.toolTip() == "Take Snapshot"
    qtbot.mouseDClick(first, Qt.MouseButton.LeftButton)          # offline: explains instead of contacting AWS
    from aws95.kit.widgets.dialogs import MessageBox95
    from PySide6.QtWidgets import QApplication
    boxes = [w for w in QApplication.topLevelWidgets() if isinstance(w, MessageBox95) and w.isVisible()]
    assert any("live mode" in b.text_label.text() for b in boxes)
    for b in boxes:
        b.close()


def test_desktop_context_menu_extended(shell):
    win, *_ = shell
    menu = win.icons.build_context_menu()
    texts = [a.text() for a in menu.actions()]
    assert texts[0] == "&Arrange Icons"
    for wanted in ("&Tile", "&Cascade", "&Minimize All", "&Properties…"):
        assert wanted in texts
    assert any(a.isSeparator() for a in menu.actions())
    tile = next(a for a in menu.actions() if a.text() == "&Tile").menu()
    assert [a.text() for a in tile.actions()] == ["Tile &Horizontally", "Tile &Vertically"]
    next(a for a in menu.actions() if a.text() == "&Properties…").trigger()
    assert any(w.manifest.id == "display" for w in win.desktop.program_windows())


def test_desktop_menu_minimize_all(shell):
    win, *_ = shell
    a = win.open_program("billing")
    menu = win.icons.build_context_menu()
    next(x for x in menu.actions() if x.text() == "&Minimize All").trigger()
    assert a.is_minimized_to_taskbar


def test_minimize_restore_and_menu_sounds(shell):
    win, _s, _c, sound = shell
    w = win.open_program("billing")
    w.minimize_to_taskbar()
    w.restore_from_taskbar()
    assert sound.played[-2:] == ["minimize", "maximize"]
    win.shortcuts["Ctrl+Esc"].activated.emit()
    assert sound.played[-1] == "menu_popup" and win.start_menu.isVisible()
    win.start_menu.hide()
    assert not win.taskbar.start_button.isDown()


def test_f5_refreshes_active_content(shell):
    win, *_ = shell
    w = win.open_program("billing")
    calls = []
    w.content().refresh = partial_append(calls)
    win.shortcuts["F5"].activated.emit()
    assert calls == [1]
    win.open_program("findings")          # no refresh(): F5 is a no-op, not an error
    win.shortcuts["F5"].activated.emit()
    assert calls == [1]


def partial_append(calls):
    def _refresh():
        calls.append(1)
    return _refresh


def test_about_is_non_blocking(shell, qtbot):
    from aws95.shell.about import AboutDialog
    win, *_ = shell
    win.shortcuts["F1"].activated.emit()
    dialogs = [d for d in win.findChildren(AboutDialog) if d.isVisible()]
    assert len(dialogs) == 1
    win.show_about()                      # a second F1 re-raises, never stacks
    assert len([d for d in win.findChildren(AboutDialog) if d.isVisible()]) == 1


def test_shutdown_dialog_restart_wiring(shell, monkeypatch):
    from PySide6.QtCore import QRect

    import aws95.shell.shell as shell_mod
    from aws95.shell.window import decode_rect
    win, settings, _c, sound = shell
    calls = []
    monkeypatch.setattr(shell_mod, "perform_shutdown",
                        lambda choice, **kw: calls.append((choice, kw["overlay_target"], kw["app"])))
    w = win.open_program("billing")
    w.setGeometry(QRect(11, 12, 300, 200))
    win.shortcuts["Ctrl+Q"].activated.emit()
    dialog = win.shutdown_dialog
    assert dialog is not None and dialog.isVisible() and "question" in sound.played
    win.request_shutdown()                # Ctrl+Q again: same dialog, not a second one
    assert win.shutdown_dialog is dialog
    dialog.restart_radio.setChecked(True)
    dialog.yes_button.click()
    assert calls == [("restart", win, QApplication.instance())]
    assert decode_rect(settings.geometry("billing")) == QRect(11, 12, 300, 200)
    assert win.shutdown_dialog is None


def test_shutdown_dialog_no_does_nothing(shell, monkeypatch):
    import aws95.shell.shell as shell_mod
    win, *_ = shell
    calls = []
    monkeypatch.setattr(shell_mod, "perform_shutdown", lambda choice, **kw: calls.append(choice))
    win.request_shutdown()
    win.shutdown_dialog.no_button.click()
    assert calls == [] and win.shutdown_dialog is None


def test_global_shortcuts_ignored_under_modal(shell):
    win, *_ = shell
    win.request_shutdown()
    assert QApplication.activeModalWidget() is win.shutdown_dialog
    win.shortcuts["Ctrl+K"].activated.emit()
    assert win.command_palette is None
    win.shutdown_dialog.no_button.click()


def test_restart_tears_down_previous_shell(monkeypatch, tmp_path):
    from shiboken6 import isValid

    import aws95.app as app_mod
    import aws95.core.settings as settings_mod
    from aws95.shell.about import TipDialog
    from aws95.shell.shutdown import RESTART_CODE

    class TmpSettings(settings_mod.Settings):
        def __init__(self, qsettings=None, parent=None):
            super().__init__(QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat), parent)

    monkeypatch.setattr(settings_mod, "Settings", TmpSettings)
    codes = iter([RESTART_CODE, 0])
    shells, tips = [], []

    def fake_exec(self):
        shells.extend(w for w in QApplication.topLevelWidgets() if isinstance(w, ShellWindow) and w.isVisible())
        tips.extend(w for w in QApplication.topLevelWidgets() if isinstance(w, TipDialog) and w.isVisible())
        tips.extend(d for s in shells if isValid(s) for d in s.findChildren(TipDialog) if d.isVisible())
        return next(codes)

    monkeypatch.setattr(QApplication, "exec", fake_exec)
    TmpSettings().skip_splash = True
    assert app_mod.main([]) == 0
    assert len(shells) == 2 and shells[0] is not shells[1]
    assert not isValid(shells[0])          # the first boot's shell is gone before the second boots
    assert tips                            # the first-run tip opened without blocking (open(), not exec())


def test_splash_path_smoke(monkeypatch, tmp_path):
    import aws95.app as app_mod
    import aws95.core.settings as settings_mod

    class TmpSettings(settings_mod.Settings):
        def __init__(self, qsettings=None, parent=None):
            super().__init__(QSettings(str(tmp_path / "sp.ini"), QSettings.Format.IniFormat), parent)

    import aws95.shell.splash as splash_mod

    seen = []

    class FastSplash(splash_mod.Splash):
        # QSplashScreen.show() waits ~1 s for an expose event the offscreen platform never sends.
        def show(self):
            seen.append("show")

        def finish(self, window):
            seen.append(("finish", type(window).__name__, self.progress()))

    monkeypatch.setattr(splash_mod, "Splash", FastSplash)
    monkeypatch.setattr(settings_mod, "Settings", TmpSettings)
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    s = TmpSettings()
    s.skip_splash = False
    s.reduce_motion = True
    s.tips_seen = True
    s.show_tips = False
    assert app_mod.main([]) == 0
    assert seen == ["show", ("finish", "ShellWindow", 100)]


# ----- fix round 1 -----

def _alt_backtick(win, qtbot, shift=False):
    from PySide6.QtTest import QTest
    mods = Qt.KeyboardModifier.AltModifier | (Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier)
    target = QApplication.focusWidget() or win
    QTest.keyPress(target, Qt.Key.Key_QuoteLeft, mods)


def test_alt_backtick_cycles_open_switcher_with_real_keys(shell, qtbot):
    from PySide6.QtTest import QTest
    win, *_ = shell
    for pid in ("billing", "findings", "snapshots", "reports"):
        win.open_program(pid)
    mru = win.desktop.mru_windows()
    seen = []
    for _ in range(3):
        _alt_backtick(win, qtbot)
        assert win.switcher is not None and win.switcher.isVisible()
        seen.append(win.switcher._index)
    assert seen == [1, 2, 3]
    QTest.keyRelease(QApplication.focusWidget() or win, Qt.Key.Key_Alt, Qt.KeyboardModifier.NoModifier)
    assert not win.switcher.isVisible()
    assert win.desktop.active_program_window() is mru[3]


def test_alt_shift_backtick_cycles_backwards(shell, qtbot):
    win, *_ = shell
    for pid in ("billing", "findings", "snapshots", "reports"):
        win.open_program(pid)
    _alt_backtick(win, qtbot)
    _alt_backtick(win, qtbot)
    assert win.switcher._index == 2
    _alt_backtick(win, qtbot, shift=True)
    assert win.switcher._index == 1
    win.switcher.cancel()
    win.show_switcher(-1)                  # opening backwards selects the last window
    assert win.switcher._index == 3
    win.switcher.cancel()


def test_desktop_context_menus_are_deleted(shell, qapp):
    from PySide6.QtCore import QCoreApplication, QPoint
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtWidgets import QMenu
    win, *_ = shell
    for _ in range(20):
        win.icons.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5),
                                                     win.icons.mapToGlobal(QPoint(5, 5))))
        menus = [m for m in win.icons.findChildren(QMenu) if m.isVisible()]
        assert len(menus) == 1
        assert "&Properties…" in [a.text() for a in menus[0].actions()]   # shell extension still applied
        menus[0].hide()
        qapp.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert win.icons.findChildren(QMenu) == []


def test_desktop_menu_action_still_runs_after_hide(shell, qapp):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtWidgets import QMenu
    win, *_ = shell
    win.icons.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5),
                                                 win.icons.mapToGlobal(QPoint(5, 5))))
    menu = next(m for m in win.icons.findChildren(QMenu) if m.isVisible())
    action = next(a for a in menu.actions() if a.text() == "&Properties…")
    menu.hide()                            # QMenu hides before it triggers the clicked action
    action.trigger()
    assert any(w.manifest.id == "display" for w in win.desktop.program_windows())


def test_start_menu_toggles_and_popups_never_stack(shell):
    win, _s, _c, sound = shell
    win.show_start_menu()
    assert win.start_menu.isVisible()
    n = sound.played.count("menu_popup")
    win.shortcuts["Ctrl+Esc"].activated.emit()           # toggle closes, no second sound
    assert not win.start_menu.isVisible() and sound.played.count("menu_popup") == n
    win.show_start_menu()
    win.show_palette()                                    # palette closes the Start menu first
    assert win.command_palette.isVisible() and not win.start_menu.isVisible()
    win.command_palette.query_edit.setText("bill")
    win.shortcuts["Ctrl+K"].activated.emit()             # already open: keeps the query
    assert win.command_palette.isVisible() and win.command_palette.query_edit.text() == "bill"
    win.show_start_menu()                                 # Start menu closes the palette first
    assert win.start_menu.isVisible() and not win.command_palette.isVisible()
    win.start_menu.hide()


def test_tip_is_modeless_and_saver_still_starts(shell):
    from aws95.shell.about import TipDialog
    win, settings, clock, _ = shell
    tip = TipDialog(settings, win)
    tip.show()
    assert not tip.isModal() and QApplication.activeModalWidget() is None
    win.shortcuts["Ctrl+Shift+B"].activated.emit()        # global shortcuts keep working
    assert any(w.manifest.id == "billing" for w in win.desktop.program_windows())
    settings.saver_timeout_min = 1
    clock.t = 120
    win.idle.check()
    assert win.saver.active and not tip.isVisible()
    win.saver.dismiss()
    tip.close()                                           # closing still persists the choice
    assert settings.tips_seen is True


def _tmp_settings_cls(tmp_path, name, created=None):
    import aws95.core.settings as settings_mod

    class TmpSettings(settings_mod.Settings):
        def __init__(self, qsettings=None, parent=None):
            super().__init__(QSettings(str(tmp_path / name), QSettings.Format.IniFormat), parent)
            if created is not None:
                import weakref
                created.append(weakref.ref(self))
    return TmpSettings


def test_restarts_release_old_settings(monkeypatch, tmp_path):
    import gc

    import aws95.app as app_mod
    import aws95.core.settings as settings_mod
    from aws95.shell.shutdown import RESTART_CODE

    created = []
    cls = _tmp_settings_cls(tmp_path, "gc.ini", created)
    monkeypatch.setattr(settings_mod, "Settings", cls)
    codes = iter([RESTART_CODE, RESTART_CODE, RESTART_CODE, 0])
    monkeypatch.setattr(QApplication, "exec", lambda self: next(codes))
    s = cls()
    s.skip_splash = True
    s.tips_seen = True
    s.show_tips = False
    del s
    assert app_mod.main([]) == 0
    gc.collect()
    assert len(created) == 5
    assert [r for r in created if r() is not None] == []


def test_native_menubar_attribute_follows_setting_both_ways(monkeypatch, tmp_path):
    import aws95.app as app_mod
    import aws95.core.settings as settings_mod
    from aws95.shell.shutdown import RESTART_CODE

    attr = Qt.ApplicationAttribute.AA_DontUseNativeMenuBar
    before = QApplication.testAttribute(attr)
    cls = _tmp_settings_cls(tmp_path, "mb.ini")
    monkeypatch.setattr(settings_mod, "Settings", cls)
    s = cls()
    s.skip_splash = True
    s.tips_seen = True
    s.show_tips = False
    s.mac_native_menubar = True
    plan = iter([(False, RESTART_CODE), (True, RESTART_CODE), (None, 0)])   # next boot's setting, exit code
    seen = []

    def fake_exec(self):
        seen.append(QApplication.testAttribute(attr))
        native, code = next(plan)
        if native is not None:
            s.mac_native_menubar = native
            s.sync()
        return code

    monkeypatch.setattr(QApplication, "exec", fake_exec)
    try:
        assert app_mod.main([]) == 0
    finally:
        QApplication.setAttribute(attr, before)
    assert seen == [False, True, False]     # restarts within one QApplication follow the setting both ways


def test_failed_theming_still_tears_down_splash(monkeypatch, tmp_path):
    import aws95.app as app_mod
    import aws95.core.settings as settings_mod
    import aws95.kit.style as style_mod
    import aws95.shell.splash as splash_mod

    events = []

    class FastSplash(splash_mod.Splash):
        def show(self):
            events.append("show")

        def closeEvent(self, event):
            events.append("closed")
            super().closeEvent(event)

    def boom(*_a, **_k):
        raise RuntimeError("theming failed")

    cls = _tmp_settings_cls(tmp_path, "boom.ini")
    monkeypatch.setattr(settings_mod, "Settings", cls)
    monkeypatch.setattr(splash_mod, "Splash", FastSplash)
    monkeypatch.setattr(style_mod, "install_theming", boom)
    with pytest.raises(RuntimeError):
        app_mod.main([])
    assert events == ["show", "closed"]


# ----- final fix wave -----

class AppState:
    """Injected application-state source (offscreen Qt cannot be made inactive for real)."""

    def __init__(self, state=Qt.ApplicationState.ApplicationActive):
        self.state = state

    def __call__(self):
        return self.state


@pytest.fixture
def shell_with_state(tmp_path, qapp, qtbot):
    settings = Settings(QSettings(str(tmp_path / "st.ini"), QSettings.Format.IniFormat))
    runner = TaskRunner()
    clock, sound, state = Clock(), RecSound(), AppState()
    win = ShellWindow(settings, runner, empty_provider_set(), sound, Registry(DEFAULT_MANIFESTS),
                      idle_clock=clock, app_state=state)
    qtbot.addWidget(win)
    win.resize(1200, 800)
    win.show()
    qtbot.waitExposed(win)
    yield win, settings, clock, sound, state
    win.saver.dismiss()
    runner.shutdown()


def _saver_windows():
    from aws95.shell.screensaver.manager import SaverWindow
    return [w for w in QApplication.topLevelWidgets() if isinstance(w, SaverWindow) and w.isVisible()]


def test_background_idle_is_a_privacy_session_promoted_on_activation(shell_with_state, qapp):
    win, settings, clock, sound, state = shell_with_state
    w = win.open_program("billing")
    state.state = Qt.ApplicationState.ApplicationInactive      # the user is working in another app
    settings.saver_timeout_min = 1
    clock.t = 120
    win.idle.check()
    assert win.saver.active and win.saver.private
    assert win.isMinimized() and not win.centralWidget().isVisible()   # R15: blanked, then minimized
    assert not w.isVisible()                                   # other top-level windows stay hidden
    assert _saver_windows() == [] and win.saver.windows == []  # no cover, no stay-on-top, no activation
    assert "saver_start" not in sound.played
    state.state = Qt.ApplicationState.ApplicationActive
    qapp.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)   # the user comes back
    assert win.saver.active and not win.saver.private
    assert len(_saver_windows()) == len(win.saver.windows) >= 1
    assert win.saver.panel is not None and win.saver.panel.isVisible()        # Resume offered at once
    assert not win.centralWidget().isVisible()                 # still blank until Resume
    win.saver.panel.resume_button.click()
    assert not win.saver.active and win.isVisible() and not win.isMinimized() and w.isVisible()
    assert win.centralWidget().isVisible()
    assert _saver_windows() == []


def test_foreground_idle_still_starts_the_full_saver(shell_with_state):
    win, settings, clock, sound, _state = shell_with_state
    settings.saver_timeout_min = 1
    clock.t = 120
    win.idle.check()
    assert win.saver.active and not win.saver.private and _saver_windows()
    assert "saver_start" in sound.played


def test_privacy_session_dismiss_restores_without_promotion(shell_with_state):
    win, settings, clock, _sound, state = shell_with_state
    state.state = Qt.ApplicationState.ApplicationInactive
    settings.saver_timeout_min = 1
    clock.t = 120
    win.idle.check()
    assert win.saver.private and win.isMinimized()
    win.saver.dismiss()
    assert not win.saver.active and not win.saver.private and win.isVisible()
    clock.t = 130
    win.idle.check()                                           # re-armed by dismissal, not idle yet
    assert not win.saver.active


def test_failed_saver_start_rearms_idle(shell, caplog):
    win, settings, clock, _ = shell
    real = win.saver._hide_targets
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("enumeration failed")
        return real()

    win.saver._hide_targets = flaky
    settings.saver_timeout_min = 1
    clock.t = 120
    win.idle.check()
    assert not win.saver.active and "failed to start" in caplog.text
    clock.t = 240
    win.idle.check()
    assert win.saver.active and not win.isVisible()


def test_maximize_sound_on_real_maximize(shell, qtbot):
    win, _s, _c, sound = shell
    w = win.open_program("billing")

    def maximizes():
        return sound.played.count("maximize")

    w.showMaximized()                                      # title double-click / maximize button
    assert maximizes() == 1
    w.showMaximized()                                      # already maximized: no second sound
    assert maximizes() == 1
    w.showNormal()
    win.shortcuts["Ctrl+Alt+Up"].activated.emit()          # keyboard snap to top
    assert w.isMaximized() and maximizes() == 2
    w.showNormal()
    win.snap.apply(w, Zone.TOP)                            # drag snap to top
    assert maximizes() == 3
    w.minimize_to_taskbar()
    w.restore_from_taskbar()                               # existing restore-from-taskbar sound, once
    assert maximizes() == 4


def test_native_minimize_of_maximized_window_plays_no_maximize(shell, qtbot):
    win, _s, _c, sound = shell
    w = win.open_program("billing")
    w.showMaximized()
    n = sound.played.count("maximize")
    w.showMinimized()                                      # routed to the taskbar, re-maximized silently
    qtbot.waitUntil(lambda: w.is_minimized_to_taskbar)
    assert sound.played.count("maximize") == n and sound.played[-1] == "minimize"


def test_first_boot_tip_is_visible_and_not_modal(monkeypatch, tmp_path):
    from shiboken6 import isValid

    import aws95.app as app_mod
    import aws95.core.settings as settings_mod
    from aws95.shell.about import TipDialog

    cls = _tmp_settings_cls(tmp_path, "tip.ini")
    monkeypatch.setattr(settings_mod, "Settings", cls)
    s = cls()
    s.skip_splash = True
    s.reduce_motion = True
    s.tips_seen = False
    s.sync()
    seen = []

    def fake_exec(self):
        shells = [w for w in QApplication.topLevelWidgets() if isinstance(w, ShellWindow)]
        tips = [d for sh in shells if isValid(sh) for d in sh.findChildren(TipDialog)]
        tips += [w for w in QApplication.topLevelWidgets() if isinstance(w, TipDialog)]
        seen.append(([t.isVisible() for t in tips], [t.isModal() or t.windowModality() != Qt.WindowModality.NonModal
                                                     for t in tips], QApplication.activeModalWidget()))
        return 0

    monkeypatch.setattr(QApplication, "exec", fake_exec)
    assert app_mod.main([]) == 0
    (visible, modal, active_modal), = seen
    assert visible and all(visible)
    assert not any(modal) and active_modal is None


def test_start_settings_sounds_opens_display_on_sounds_tab(shell):
    win, *_ = shell
    settings_menu = next(a.menu() for a in win.start_menu.actions() if a.text() == "&Settings")
    sounds = next(a for a in settings_menu.actions() if a.text().replace("&", "") == "Sounds")
    sounds.trigger()
    w = next(w for w in win.desktop.program_windows() if w.manifest.id == "display")
    assert w.content().tabs.tabText(w.content().tabs.currentIndex()) == "Sounds"
    w.content().select_tab("background")
    sounds.trigger()                                       # already open: same window, Sounds tab again
    assert [x.manifest.id for x in win.desktop.program_windows()].count("display") == 1
    assert w.content().tabs.tabText(w.content().tabs.currentIndex()) == "Sounds"


# ----- R15: minimize (never hide) the shell during a privacy-only session -----

def _go_private(win, settings, clock, state):
    state.state = Qt.ApplicationState.ApplicationInactive
    settings.saver_timeout_min = 1
    clock.t = 120
    win.idle.check()
    assert win.saver.active and win.saver.private


def test_privacy_session_minimizes_a_blanked_shell_without_cover(shell_with_state):
    win, settings, clock, sound, state = shell_with_state
    _go_private(win, settings, clock, state)
    assert win.isMinimized()                    # keeps the Windows taskbar button / macOS Dock tile
    assert not win.centralWidget().isVisible()  # thumbnails, Alt+Tab and Mission Control show no data
    assert _saver_windows() == [] and win.saver.windows == []
    assert "saver_start" not in sound.played


def test_activation_promotes_and_plays_saver_start(shell_with_state, qapp):
    win, settings, clock, sound, state = shell_with_state
    _go_private(win, settings, clock, state)
    state.state = Qt.ApplicationState.ApplicationActive
    qapp.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    assert not win.saver.private and _saver_windows()
    assert sound.played.count("saver_start") == 1


@pytest.mark.parametrize("prior", [Qt.WindowState.WindowNoState, Qt.WindowState.WindowMaximized])
def test_resume_restores_window_state_and_content(shell_with_state, qapp, prior):
    win, settings, clock, _sound, state = shell_with_state
    if prior == Qt.WindowState.WindowMaximized:
        win.showMaximized()
    before = win.windowState()
    assert win.centralWidget().isVisible()
    _go_private(win, settings, clock, state)
    state.state = Qt.ApplicationState.ApplicationActive
    qapp.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    win.saver.panel.resume_button.click()
    assert not win.saver.active
    assert win.windowState() == before
    assert not win.isMinimized() and win.isVisible() and win.centralWidget().isVisible()


def test_navigate_opens_program_and_applies_params(shell):
    win, *_ = shell
    win.navigate("display", {"tab": "sounds"})
    content = win.desktop.active_program_window().content()
    assert content.tabs.tabText(content.tabs.currentIndex()) == "Sounds"


def test_ctrl_e_exports_active_content(shell):
    win, *_ = shell
    assert "Ctrl+E" in win.shortcuts
    w = win.open_program("display")
    called = []
    w.content().export = lambda: called.append(True)
    win._export_active()
    assert called == [True]


# ----- Alt mnemonics on every platform (ruling M3-R22) -----
def test_main_enables_auto_mnemonics_before_any_widget(monkeypatch, tmp_path):
    import PySide6.QtGui as qtgui

    import aws95.app as app_mod
    import aws95.core.settings as settings_mod

    order = []

    class TmpSettings(settings_mod.Settings):
        def __init__(self, qsettings=None, parent=None):
            order.append("settings")
            super().__init__(QSettings(str(tmp_path / "mn.ini"), QSettings.Format.IniFormat), parent)

    real = qtgui.qt_set_sequence_auto_mnemonic
    monkeypatch.setattr(qtgui, "qt_set_sequence_auto_mnemonic", lambda on: (order.append(("mnemonic", on)),
                                                                            real(on))[1])
    monkeypatch.setattr(settings_mod, "Settings", TmpSettings)
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    TmpSettings().skip_splash = True
    order.clear()
    assert app_mod.main([]) == 0
    assert order[0] == ("mnemonic", True)          # before the boot builds settings, splash or any window


def test_alt_mnemonics_reach_program_menus_and_buttons(shell, qtbot):
    from PySide6.QtTest import QTest

    win, *_ = shell
    other = win.open_program("resources")           # a second program: its Alt+F must not make the key ambiguous
    sub = win.open_program("activity")
    content = sub.content()
    content.tabs.setCurrentWidget(content.filter_tab)
    win.activateWindow()
    content.filter_tab.principal_edit.setFocus()
    qtbot.waitUntil(lambda: content.filter_tab.principal_edit.hasFocus())
    assert other is not sub
    QTest.keyClick(content.filter_tab.principal_edit, Qt.Key.Key_F, Qt.KeyboardModifier.AltModifier)
    qtbot.waitUntil(lambda: content.file_menu.isVisible(), timeout=2000)
    content.file_menu.close()
    clicks = []
    content.filter_tab.run_button.clicked.connect(lambda: clicks.append(1))
    content.filter_tab.principal_edit.setFocus()
    QTest.keyClick(content.filter_tab.principal_edit, Qt.Key.Key_U, Qt.KeyboardModifier.AltModifier)   # "R&un"
    qtbot.waitUntil(lambda: clicks == [1], timeout=2000)


def test_restarts_with_open_programs_release_everything(monkeypatch, tmp_path):
    """M3-R7 root cause: every program window is closed at teardown (its tasks cancelled), and a CancelScope
    drops settled handles, so restarts with programs open leave nothing behind."""
    import gc

    from PySide6.QtCore import QThreadPool
    from shiboken6 import isValid

    import aws95.app as app_mod
    import aws95.core.settings as settings_mod
    from aws95.core.tasks import TaskHandle, TaskRunner
    from aws95.programs.common import ProgramBase
    from aws95.shell.shutdown import RESTART_CODE

    def live(kind):
        return [o for o in gc.get_objects() if isinstance(o, kind) and isValid(o)]

    gc.collect()
    baseline = {k: len(live(k)) for k in (ProgramBase, TaskHandle, TaskRunner, QThreadPool)}
    created = []
    cls = _tmp_settings_cls(tmp_path, "leak.ini", created)
    monkeypatch.setattr(settings_mod, "Settings", cls)
    codes = iter([RESTART_CODE, RESTART_CODE, 0])
    opened = []

    def fake_exec(self):
        shell = next(w for w in QApplication.topLevelWidgets() if isinstance(w, ShellWindow) and w.isVisible())
        for pid in ("resources", "billing", "activity"):
            opened.append(shell.open_program(pid).content())
        QApplication.processEvents()
        return next(codes)

    monkeypatch.setattr(QApplication, "exec", fake_exec)
    closed = []
    real_close = ProgramBase.closeEvent

    def counting_close(self, event):
        closed.append(type(self).__name__)
        real_close(self, event)

    monkeypatch.setattr(ProgramBase, "closeEvent", counting_close)
    s = cls()
    s.skip_splash = True
    s.tips_seen = True
    s.show_tips = False
    del s
    assert app_mod.main([]) == 0
    assert len(opened) == 9
    assert len(closed) == 9                     # every program's closeEvent ran, so its tasks were cancelled
    del opened
    gc.collect()
    assert [r for r in created if r() is not None] == []
    after = {k: len(live(k)) for k in baseline}
    assert after == baseline, after


def _close_wizards():
    from shiboken6 import isValid

    from aws95.shell.setup_wizard import PermissionsDialog, SetupWizard, SignInDialog
    for w in QApplication.topLevelWidgets():
        if isinstance(w, (SetupWizard, PermissionsDialog, SignInDialog)) and isValid(w):
            w.close()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_open_setup_wizard_is_single_instance_and_ignores_checked(shell):
    from aws95.shell.setup_wizard import SetupWizard
    win = shell[0]
    try:
        win.open_setup_wizard()
        win.open_setup_wizard(False)                   # QAction.triggered passes checked
        wizards = [w for w in QApplication.topLevelWidgets() if isinstance(w, SetupWizard)]
        assert len(wizards) == 1 and wizards[0].isVisible()
        assert wizards[0].windowTitle() == "AWS Setup Wizard"
    finally:
        _close_wizards()


def test_check_permissions_opens_dialog(shell):
    from aws95.shell.setup_wizard import PermissionsDialog
    win = shell[0]
    try:
        win.check_permissions("dev", "123456789012")
        dialogs = [w for w in QApplication.topLevelWidgets() if isinstance(w, PermissionsDialog)]
        assert len(dialogs) == 1 and dialogs[0].isVisible()
    finally:
        _close_wizards()


def test_setup_wizard_title_marks_demo(tmp_path, qapp, qtbot):
    from aws95.providers.demo import demo_provider_set
    from aws95.shell.setup_wizard import SetupWizard
    settings = Settings(QSettings(str(tmp_path / "d.ini"), QSettings.Format.IniFormat))
    runner = TaskRunner()
    win = ShellWindow(settings, runner, demo_provider_set(), RecSound(), Registry(DEFAULT_MANIFESTS))
    qtbot.addWidget(win)
    try:
        win.open_setup_wizard()
        wiz = next(w for w in QApplication.topLevelWidgets() if isinstance(w, SetupWizard))
        assert wiz.windowTitle().endswith("[DEMO]")
    finally:
        _close_wizards()
        runner.shutdown()


def test_check_permissions_one_dialog_per_account_and_freed_on_close(shell):
    from shiboken6 import isValid

    from aws95.shell.setup_wizard import PermissionsDialog
    win = shell[0]

    def dialogs():
        return [w for w in QApplication.topLevelWidgets() if isinstance(w, PermissionsDialog) and isValid(w)]

    try:
        win.check_permissions("dev", "123456789012")
        win.check_permissions("dev", "123456789012")
        assert len(dialogs()) == 1
        first = dialogs()[0]
        first.close()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(first)
        win.check_permissions("dev", "123456789012")
        assert len(dialogs()) == 1 and dialogs()[0].isVisible()
    finally:
        _close_wizards()


def test_sign_in_service_one_dialog_per_profile_and_signal(shell, qtbot):
    from shiboken6 import isValid

    from aws95.shell.setup_wizard import SignInDialog
    win = shell[0]

    def dialogs():
        return [w for w in QApplication.topLevelWidgets() if isinstance(w, SignInDialog) and isValid(w)]

    got = []
    win.sign_in_succeeded.connect(got.append)
    try:
        win.sign_in("corp")
        win.sign_in("corp")                         # a second click reuses the dialog: no second CLI
        assert len(dialogs()) == 1 and dialogs()[0].isVisible()
        win.sign_in("other")
        assert len(dialogs()) == 2
        dialogs()[0].finished_ok.emit("corp")       # what a successful `aws sso login` reports
        assert got == ["corp"]
        first = dialogs()[0]
        first.close()                               # Close cancels the CLI and frees the dialog
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(first)
        win.sign_in(first_profile := "corp")
        assert len([d for d in dialogs() if d.profile == first_profile]) == 1
    finally:
        _close_wizards()


def test_open_setup_wizard_start_page(shell):
    from aws95.shell.setup_wizard import PAGE_IDS, SetupWizard
    win = shell[0]
    try:
        win.open_setup_wizard("welcome")
        wiz = next(w for w in QApplication.topLevelWidgets() if isinstance(w, SetupWizard))
        assert wiz.currentId() == PAGE_IDS.index("welcome")
    finally:
        _close_wizards()


def test_start_menu_and_palette_offer_snapshot_commands(shell):
    shell, *_ = shell
    from aws95.shell.palette import build_items
    programs = next(a.menu() for a in shell.start_menu.actions() if a.menu() and "Programs" in a.text())
    assert any("Take Snapshot" in a.text().replace("&", "") for a in programs.actions())
    settings_menu = next(a.menu() for a in shell.start_menu.actions() if a.menu() and "Settings" in a.text())
    assert any("Clear Local Data" in a.text().replace("&", "") for a in settings_menu.actions())
    titles = [i.title for i in build_items(shell.registry, shell, shell.settings, shell.desktop)]
    assert "Take Snapshot" in titles and "Clear Local Data…" in titles


def test_start_menu_and_palette_offer_resume(shell):
    win, *_ = shell
    from aws95.shell.palette import build_items
    programs = next(a.menu() for a in win.start_menu.actions() if a.menu() and "Programs" in a.text())
    assert "Resume Snapshot" in [a.text().replace("&", "") for a in programs.actions()]
    assert "Resume Snapshot" in [i.title for i in build_items(win.registry, win, win.settings, win.desktop)]


def test_take_snapshot_in_offline_mode_explains_and_calls_nothing(shell, qtbot):
    shell, *_ = shell
    shell.take_snapshot()
    assert shell.snapshots.prompt is None and not shell.snapshots.running


def test_boot_prompt_waits_for_the_wizard(tmp_path, qapp, qtbot):
    import dataclasses
    from datetime import datetime, timezone

    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QDialog

    from aws95.contracts.accounts import Profile, RosterEntry
    from aws95.contracts.common import Result
    from aws95.contracts.snapshots import AccountAge
    from aws95.core.settings import Settings
    from aws95.core.tasks import TaskRunner
    from aws95.programs import DEFAULT_MANIFESTS
    from aws95.providers.empty import empty_provider_set
    from aws95.shell.registry import Registry
    from aws95.shell.shell import ShellWindow

    class Snaps:
        def ages(self, scope, *, cancel=None):
            return Result.ok((AccountAge("123456789012", None),))

        def stages(self, scope, *, cancel=None):
            return Result.ok(())

    class Accounts:
        def list_profiles(self, scope=None, *, cancel=None):
            return Result.ok((Profile("dev", "profile", "us-east-1"),))

    settings = Settings(QSettings(str(tmp_path / "b.ini"), QSettings.Format.IniFormat))
    settings.set_roster((RosterEntry("dev", "123456789012"),))
    runner = TaskRunner()
    providers = dataclasses.replace(empty_provider_set(), is_live=True, snapshots=Snaps(), accounts=Accounts())
    win = ShellWindow(settings, runner, providers, RecSound(), Registry(DEFAULT_MANIFESTS))
    qtbot.addWidget(win)
    win._wizard = QDialog(win)
    win._wizard.show()
    win.boot_snapshot_prompt()
    qtbot.wait(100)
    assert win.snapshots.prompt is None
    win._wizard.done(0)
    qtbot.waitUntil(lambda: win.snapshots.prompt is not None and win.snapshots.prompt.isVisible(), timeout=3000)
    win.close()
    runner.shutdown()


def test_activity_wiring(shell):
    shell, *_ = shell
    from aws95.shell.snapshot_prompt import DATA_PROGRAMS
    assert "activity" in DATA_PROGRAMS
    assert any(o.key == "activity" for o in shell.snapshots._options)
    assert not shell.activity.running and shell.activity.busy_text() is None


def test_tray_joins_snapshot_and_activity_job_text(shell):
    shell, *_ = shell
    shell._set_job_text("snapshot", "dev: 7/19 collectors · me-south-1")
    shell._set_job_text("activity", "Activity dev: 1/7 days · eu-west-1")
    tray = shell.taskbar.tray
    assert tray.job_progress.toolTip() == ("Snapshot dev: 7/19 collectors · me-south-1 · "
                                           "Activity dev: 1/7 days · eu-west-1")
    shell._set_job_text("snapshot", "")
    assert tray.job_progress.toolTip() == "Activity dev: 1/7 days · eu-west-1"


def test_activity_refuses_while_local_data_is_cleared(shell):
    shell, *_ = shell
    assert shell._activity_refusal() is None
    shell.cleaner._clearing = True
    try:
        assert shell._activity_refusal() == "Local data is being cleared. Try again when it is done."
    finally:
        shell.cleaner._clearing = False


def test_load_activity_in_offline_mode_explains_and_calls_nothing(shell):
    shell, *_ = shell
    shell.load_activity()
    assert shell.activity.last_notice.startswith("Activity history needs live mode")


def test_f5_asks_the_window_for_user_refresh(shell, monkeypatch):
    shell, *_ = shell
    seen = []

    class Content:
        def user_refresh(self):
            seen.append("user")

        def refresh(self):
            seen.append("plain")
    monkeypatch.setattr(shell, "active_content", Content)
    shell._refresh_active()
    assert seen == ["user"]


def _outcome_result(status, message, *, cancelled=False, kind=None):
    from aws95.contracts.activity import HistoryOutcome
    from aws95.contracts.common import Result
    from aws95.contracts.snapshots import SnapshotOutcome
    data = HistoryOutcome(kind, (), cancelled) if kind else SnapshotOutcome((), cancelled)
    return Result(status, data, None, None, message)


@pytest.mark.parametrize("status, cancelled, title", [
    ("ok", False, "Snapshot complete"), ("partial", False, "Snapshot finished with gaps"),
    ("partial", True, "Snapshot cancelled")])
def test_snapshot_end_shows_a_balloon_and_plays_the_done_sound(shell, qtbot, status, cancelled, title):
    from aws95.contracts.common import Status
    win, _, _, sound = shell
    win.snapshots.finished.emit(_outcome_result(Status(status), "dev: complete · prod: partial (2 denied)",
                                                cancelled=cancelled))
    balloon = win.job_balloon
    assert balloon is not None and balloon.isVisible()
    assert balloon.title() == title and balloon.text() == "dev: complete · prod: partial (2 denied)"
    assert sound.played[-1] == "refresh_done"
    with qtbot.waitSignal(balloon.dismissed, timeout=1000):
        qtbot.mouseClick(balloon, Qt.MouseButton.LeftButton)
    [w] = [w for w in win.desktop.program_windows() if w.manifest.id == "snapshots"]
    assert w.content().tabs.currentWidget() is w.content().run_tab          # resumable-snapshots spec §7.2


def test_snapshot_failure_keeps_its_message_box_and_shows_no_balloon(shell, qtbot):
    from aws95.contracts.common import Result
    win, _, _, sound = shell
    win.snapshots.finished.emit(Result.error("nope"))
    win.snapshots.finished.emit(None)
    assert win.job_balloon is None and "refresh_done" not in sound.played


@pytest.mark.parametrize("kind, cancelled, status, title", [
    ("load", False, "ok", "Activity updated"), ("load", False, "partial", "Activity updated with gaps"),
    ("import", False, "ok", "History imported"), ("load", True, "partial", "Activity loading cancelled")])
def test_activity_end_shows_a_balloon(shell, qtbot, kind, cancelled, status, title):
    from aws95.contracts.common import Status
    win, _, _, sound = shell
    win.activity.finished.emit(_outcome_result(Status(status), "dev: complete, 12 new events", cancelled=cancelled,
                                               kind=kind))
    assert win.job_balloon.title() == title and win.job_balloon.isVisible()
    assert sound.played[-1] == "refresh_done"
    with qtbot.waitSignal(win.job_balloon.dismissed, timeout=1000):
        qtbot.mouseClick(win.job_balloon, Qt.MouseButton.LeftButton)
    assert any(w.manifest.id == "activity" for w in win.desktop.program_windows())


def test_tray_snapshot_words_open_the_run_tab(shell):
    win, *_ = shell
    win._set_job_text("snapshot", "dev: 1/42 sections · ec2 us-east-1")
    win.show_running_job()
    [w] = [w for w in win.desktop.program_windows() if w.manifest.id == "snapshots"]
    assert w.content().tabs.currentWidget() is w.content().run_tab


def test_tray_job_click_opens_the_running_job(shell):
    win, *_ = shell
    win._set_job_text("activity", "Activity dev: 1/7 days")
    win.show_running_job()
    assert any(w.manifest.id == "activity" for w in win.desktop.program_windows())


def test_shell_hears_about_a_wizard_sign_in_and_reuses_its_dialog(shell, qtbot):
    from aws95.shell.setup_wizard import SignInDialog
    win, *_ = shell
    dlg = SignInDialog(win.providers.accounts, win.runner, "corp", win, label="corp-label")
    dlg.show()
    win.track_sign_in("corp", dlg)
    assert win.sign_in_dialog_for("corp") is dlg
    win.sign_in("corp")                                        # raises the wizard's dialog; no second one
    assert win.sign_in_dialog_for("corp") is dlg
    heard = []
    win.sign_in_succeeded.connect(heard.append)
    dlg.finished_ok.emit("corp-label")
    assert heard == ["corp"]                                   # the CLI profile, as Accounts expects
    dlg.close()


def test_clear_local_data_asks_findings_for_a_running_backfill(shell):
    win, *_ = shell
    calls = []

    class Findings:
        def busy_text(self):
            calls.append(1)
            return "Backfill is running. Try again when it finishes."
    win.providers = __import__("dataclasses").replace(win.providers, findings=Findings())
    assert "Backfill is running" in (win._findings_busy() or "") and calls


def test_offline_findings_never_block_clear_local_data(shell):
    win, *_ = shell                                  # offline: the findings provider answers any name NOT_CONNECTED
    assert win._findings_busy() is None
