"""Application bootstrap: splash → theme → shell (or --gallery). Restart via Shut Down loops back."""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from aws95 import __version__


ICON_DIR = Path(__file__).resolve().parent / "assets" / "icons"

EXIT_AFTER_BOOT_ENV = "AWS95_EXIT_AFTER_BOOT_MS"
_boot_t0: float | None = None   # time.perf_counter() at the top of main(); read only by the boot-timing hook


def exit_after_boot_ms(environ: Mapping[str, str]) -> int | None:
    """AWS95_EXIT_AFTER_BOOT_MS: a delay in ms after the shell's first paint, then quit. None when unset or invalid."""
    raw = environ.get(EXIT_AFTER_BOOT_ENV, "").strip()
    if not (raw.isascii() and raw.isdecimal()):
        return None
    return int(raw)


def _quit_after_first_paint(app, delay_ms: int) -> None:
    """Packaging/perf hook: report process-start-to-first-paint, then exit 0 (never RESTART_CODE)."""
    import time

    from PySide6.QtCore import QTimer

    app.processEvents()     # the shell's first paint
    if _boot_t0 is not None and sys.stdout is not None:   # a Windows windowed build has no stdout
        print(f"{BOOT_MS_PREFIX}{(time.perf_counter() - _boot_t0) * 1000:.1f}", flush=True)
    QTimer.singleShot(delay_ms, lambda: app.exit(0))


def app_icon():
    """The application icon (taskbar, Dock, window frames): every rendered size from aws95/assets/icons."""
    from PySide6.QtGui import QIcon

    icon = QIcon()
    for png in sorted(ICON_DIR.glob("aws95-*.png")):
        icon.addFile(str(png))
    return icon


BOOT_MS_PREFIX = "AWS95_BOOT_MS="


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aws95", description="AWS 95 Tools")
    parser.add_argument("--version", action="version", version=f"AWS 95 Tools {__version__}")
    parser.add_argument("--demo", action="store_true", help="fill windows with watermarked demo data (dev only)")
    parser.add_argument("--offline", action="store_true",
                        help="never contact AWS: every window shows NOT_CONNECTED (tests, screenshots)")
    parser.add_argument("--gallery", action="store_true", help="open the widget kit gallery instead of the shell")
    return parser


def demo_enabled(args: argparse.Namespace, environ: Mapping[str, str]) -> bool:
    return bool(args.demo) or environ.get("AWS95_DEMO") == "1"


def provider_mode(args: argparse.Namespace, environ: Mapping[str, str]) -> str:
    if demo_enabled(args, environ):
        return "demo"
    if getattr(args, "offline", False) or environ.get("AWS95_OFFLINE") == "1":
        return "offline"
    return "live"


def _flush_deletes(app) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    app.processEvents()
    # deleteLater() outside a running event loop is only honoured by an explicit DeferredDelete flush.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def close_top_level_windows(skip=()) -> None:
    """Close every top-level window and schedule the deletion of the parentless ones.

    A window in `skip` was already closed by the caller: it is not closed a second time, only deleted.

    topLevelWidgets() also lists popups that another widget owns: menu-bar menus, a QToolBar's
    overflow menu, a QComboBox's popup frame. Their owners keep raw pointers to them (QToolBarLayout's
    popupMenu), so deleting one on its own leaves the owner dangling, and the owner's next
    removeAction() segfaults. An owned popup is deleted with its owner.
    """
    from PySide6.QtWidgets import QApplication
    from shiboken6 import isValid

    for w in QApplication.topLevelWidgets():
        if not isValid(w):
            continue
        if not any(w is s for s in skip):
            w.close()
        if w.parentWidget() is None:
            w.deleteLater()


def _teardown(app, window) -> None:
    """Close and delete one boot's top-level windows so the next boot starts from nothing."""
    from shiboken6 import isValid

    if window is not None and isValid(window):
        window.close()      # ShellWindow.closeEvent saves geometry and ends any saver session
        window.deleteLater()
    close_top_level_windows(skip=(window,) if window is not None and isValid(window) else ())
    _flush_deletes(app)


def _run_once(app, args) -> int:
    from PySide6.QtCore import QPropertyAnimation, Qt, QTimer
    from PySide6.QtWidgets import QApplication

    import aws95.core.settings as settings_mod
    from aws95.core.tasks import TaskRunner
    from aws95.kit.style import install_theming
    from aws95.kit.theme import ThemeManager
    from aws95.providers.demo import demo_provider_set
    from aws95.providers.empty import empty_provider_set

    settings = settings_mod.Settings()
    # Every boot, both ways: a restart in the same QApplication must follow a changed setting.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeMenuBar, not settings.mac_native_menubar)
    splash = manager = runner = window = None
    try:
        if not args.gallery and not settings.skip_splash:
            from aws95.shell.splash import Splash
            splash = Splash()
            splash.show()
            splash.set_progress(10, "Starting AWS 95…")
            app.processEvents()
        manager = ThemeManager(settings, app)
        install_theming(app, manager, settings)
        runner = TaskRunner()
        mode = provider_mode(args, os.environ)
        if mode == "demo":
            providers = demo_provider_set()
        elif mode == "offline":
            providers = empty_provider_set()
        else:
            from aws95.providers.live import live_provider_set
            providers = live_provider_set(settings)
        if args.gallery:
            from aws95.gallery import KitGallery
            window = KitGallery(settings, runner, providers)
            window.resize(1280, 900)
            window.show()
            return app.exec()

        from aws95.kit.mascot import cloudy_pixmap
        from aws95.kit.widgets.stateview import set_mascot_pixmap
        from aws95.programs import DEFAULT_MANIFESTS
        from aws95.shell.about import TipDialog, should_show_tip
        from aws95.shell.registry import Registry
        from aws95.shell.shell import ShellWindow
        from aws95.shell.sound import SoundBank

        if splash is not None:
            splash.set_progress(40, "Loading sounds…")
        sound = SoundBank(settings)
        set_mascot_pixmap(cloudy_pixmap(48))
        if splash is not None:
            splash.set_progress(70, "Building the desktop…")
        window = shell = ShellWindow(settings, runner, providers, sound, Registry(DEFAULT_MANIFESTS))
        sound.setParent(shell)  # its QSoundEffects are torn down with this boot's shell
        shell.resize(1280, 860)
        if not settings.reduce_motion:
            shell.setWindowOpacity(0.0)
        shell.show()
        if splash is not None:
            splash.set_progress(100, "Ready")
            splash.finish(shell)
        if not settings.reduce_motion:
            fade = QPropertyAnimation(shell, b"windowOpacity", shell)
            fade.setDuration(400)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        if settings.play_startup_sound:
            sound.play("startup")
        shell.start_idle(app)
        exit_ms = exit_after_boot_ms(os.environ)
        if exit_ms is not None:
            _quit_after_first_paint(app, exit_ms)
        from aws95.shell.setup_wizard import should_autostart_wizard
        if should_autostart_wizard(settings, providers):
            QTimer.singleShot(0, shell, shell.open_setup_wizard)   # after the first paint; this boot skips the tip
        elif should_show_tip(settings):
            TipDialog(settings, shell).show()   # modeless (R11): the idle saver and shortcuts keep working
        QTimer.singleShot(0, shell, shell.boot_snapshot_prompt)   # queued after the wizard's own singleShot
        return app.exec()
    finally:
        if runner is not None:
            runner.shutdown()
        _teardown(app, window)   # closes the splash too; closing the shell saves geometry, so sync after it
        if manager is not None:
            manager.deleteLater()    # takes its theming binding and app/settings connections with it
        _flush_deletes(app)
        settings.sync()


def main(argv: list[str] | None = None) -> int:
    global _boot_t0
    import time

    _boot_t0 = time.perf_counter()
    args = build_parser().parse_args(argv)

    from PySide6 import QtGui
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from aws95.shell.shutdown import RESTART_CODE

    # Alt+letter mnemonics on every platform, macOS included (spec section 10, ruling M3-R22). Set before any
    # widget exists: menu bars and buttons read it when they register their mnemonic shortcuts.
    QtGui.qt_set_sequence_auto_mnemonic(True)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeMenuBar, True)  # each boot re-applies the setting
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("AWS 95 Tools")
    app.setOrganizationName("AWS95")
    app.setWindowIcon(app_icon())
    while True:
        code = _run_once(app, args)
        if code != RESTART_CODE:
            return code
