"""Screen Savers accessory (ruling M3-R20): pick a saver, preview it, start it now; Resume ends the session."""
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest

from aws95.core.settings import Settings
from aws95.core.tasks import TaskRunner
from aws95.programs import DEFAULT_MANIFESTS
from aws95.programs.screensavers import ScreenSaversProgram, make_screensavers
from aws95.providers.empty import empty_provider_set
from aws95.shell.palette import build_items
from aws95.shell.registry import Registry
from aws95.shell.screensaver import SAVERS, saver_titles
from aws95.shell.shell import ShellWindow
from aws95.shell.startmenu import build_start_menu


@pytest.fixture
def saver_program(program_env, qtbot):
    w, ctx = program_env(make_screensavers)
    qtbot.addWidget(w)
    w.show()
    return w, ctx


def test_lists_every_saver_and_previews_the_first(saver_program):
    w, _ = saver_program
    assert isinstance(w, ScreenSaversProgram) and w.scope_bar is None
    titles = [w.saver_list.item(i).text() for i in range(w.saver_list.count())]
    assert titles == list(saver_titles().values()) and len(titles) == 5
    assert w.monitor.saver is not None and w.monitor.saver.name == w.selected_name() == next(iter(SAVERS))
    assert w.status.counts_label.text() == "5 screen savers"


def test_selecting_a_saver_updates_the_preview(saver_program):
    w, _ = saver_program
    assert w.select_saver("pipes")
    assert w.monitor.saver.name == "pipes" and w.monitor.saver.isVisible()
    w.saver_list.setCurrentRow(w.saver_list.count() - 1)
    assert w.monitor.saver.name == list(SAVERS)[-1]


def test_start_button_and_enter_start_the_chosen_saver(saver_program, qtbot):
    w, ctx = saver_program
    w.select_saver("maze")
    w.start_button.click()
    assert ctx.shell.calls[-1] == ("preview_screensaver", ("maze",))
    w.select_saver("mystify")
    w.saver_list.setFocus()
    QTest.keyClick(w.saver_list, Qt.Key.Key_Return)
    assert ctx.shell.calls[-1] == ("preview_screensaver", ("mystify",))
    w.start_action.trigger()
    assert ctx.shell.calls[-1] == ("preview_screensaver", ("mystify",))
    assert w.start_button.isDefault()


def test_menu_mnemonics_unique_and_not_reused_by_controls(saver_program):
    w, _ = saver_program
    bar = [a.text()[a.text().index("&") + 1].lower() for a in w.menu_bar.actions()]
    assert len(bar) == len(set(bar))
    assert "s" not in bar                       # &Start owns S
    assert not w.refresh_action.isEnabled() and not w.export_action.isEnabled()


def test_manifest_is_an_accessory_after_display():
    reg = Registry(DEFAULT_MANIFESTS)
    m = reg.get("screensavers")
    assert (m.title, m.icon, m.category, m.desktop_icon, m.instancing) == ("Screen Savers", "display", "accessories",
                                                                      False, "singleton")
    assert m.order > reg.get("display").order
    assert [x.id for x in reg.by_category("accessories")] == ["screensavers"]


class _FakeShell:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a))


class _NoWindows:
    def program_windows(self):
        return []


def test_palette_has_one_start_item_per_saver(tmp_path, qapp):
    settings = Settings(QSettings(str(tmp_path / "p.ini"), QSettings.Format.IniFormat))
    shell = _FakeShell()
    items = build_items(Registry(DEFAULT_MANIFESTS), shell, settings, _NoWindows())
    starts = [i for i in items if i.title.startswith("Start screen saver: ")]
    assert [i.title for i in starts] == [f"Start screen saver: {t}" for t in saver_titles().values()]
    starts[2].action()
    assert shell.calls[-1] == ("preview_screensaver", (list(SAVERS)[2],))


def test_start_menu_accessories_lists_screen_savers(qapp):
    shell = _FakeShell()
    menu = build_start_menu(Registry(DEFAULT_MANIFESTS), shell)
    accessories = next(a.menu() for a in menu.actions() if a.text() == "&Accessories")
    assert [a.text() for a in accessories.actions()] == ["&Screen Savers"]
    accessories.actions()[0].trigger()
    assert shell.calls[-1] == ("open_program", ("screensavers",))


class _Sound:
    def play(self, e):
        return True

    def preview(self, e):
        return True


def test_start_runs_a_session_and_resume_dismisses_it(tmp_path, qapp, qtbot):
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    runner = TaskRunner()
    win = ShellWindow(settings, runner, empty_provider_set(), _Sound(), Registry(DEFAULT_MANIFESTS))
    qtbot.addWidget(win)
    win.resize(1200, 800)
    win.show()
    qtbot.waitExposed(win)
    try:
        program = win.open_program("screensavers").content()
        program.select_saver("starfield")
        program.start_button.click()
        assert win.saver.active and win.saver.current_name == "starfield" and not win.isVisible()
        win.saver.panel.resume_button.click()
        assert not win.saver.active and win.isVisible()
    finally:
        win.saver.dismiss()
        runner.shutdown()


def test_display_preview_button_still_starts_a_random_saver(tmp_path, qapp, qtbot):
    settings = Settings(QSettings(str(tmp_path / "d.ini"), QSettings.Format.IniFormat))
    runner = TaskRunner()
    win = ShellWindow(settings, runner, empty_provider_set(), _Sound(), Registry(DEFAULT_MANIFESTS))
    qtbot.addWidget(win)
    win.resize(1200, 800)
    win.show()
    qtbot.waitExposed(win)
    try:
        display = win.open_program("display").content()
        display.preview_button.click()
        assert win.saver.active and win.saver.current_name in SAVERS
    finally:
        win.saver.dismiss()
        runner.shutdown()
