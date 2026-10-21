import re

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from aws95.kit import theme as th
from aws95.kit.style import apply_theme
from aws95.programs import DEFAULT_MANIFESTS
from aws95.shell.registry import Registry
from aws95.shell.startmenu import STRIPE_W, build_start_menu, popup_above


class FakeShell:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a))


@pytest.fixture
def menu(qapp, qtbot):
    apply_theme(qapp, th.LIGHT)
    shell = FakeShell()
    m = build_start_menu(Registry(DEFAULT_MANIFESTS), shell)
    return m, shell


def _texts(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def test_top_level_structure(menu):
    m, _ = menu
    assert _texts(m) == ["&Programs", "&Accessories", "&Documents", "&Settings", "&Find…\tCtrl+K",
                         "&Help", "&Run…", "S&witch Account…", "Sh&ut Down…"]


def test_programs_submenu_lists_registry_programs(menu):
    m, _ = menu
    programs = m.actions()[0].menu()
    reg = Registry(DEFAULT_MANIFESTS)
    titles = [a.text().replace("&", "") for a in programs.actions()]
    assert titles[:-3] == [x.title for x in reg.by_category("programs")]
    assert titles[-3:] == ["", "Take Snapshot", "Resume Snapshot"]   # a separator, then the snapshot commands


def test_triggering_calls_shell(menu):
    m, shell = menu
    m.actions()[0].menu().actions()[2].trigger()
    assert ("open_program", ("billing",)) in shell.calls
    by_text = {a.text(): a for a in m.actions()}
    by_text["&Find…\tCtrl+K"].trigger()
    by_text["Sh&ut Down…"].trigger()
    by_text["&Help"].trigger()
    assert [c[0] for c in shell.calls[-3:]] == ["show_palette", "request_shutdown", "show_about"]


def test_empty_documents_is_disabled(menu):
    m, _ = menu
    docs = m.actions()[2].menu()
    assert [a.text() for a in docs.actions()] == ["(&Empty)"] and not docs.actions()[0].isEnabled()


def test_stripe_painted(menu, qtbot):
    m, _ = menu
    m.resize(m.sizeHint())
    img = m.grab().toImage()
    stripe_px = img.pixelColor(STRIPE_W // 2, img.height() - 6).name()
    assert stripe_px != th.LIGHT.face


def test_items_do_not_overlap_stripe(menu):
    m, _ = menu
    m.resize(m.sizeHint())
    m.show()
    geo = m.actionGeometry(m.actions()[0])
    assert geo.left() >= STRIPE_W


def test_keyboard_navigation_and_escape(menu, qtbot):
    m, shell = menu
    m.popup(QPoint(10, 10))
    qtbot.waitExposed(m)
    for _ in range(6):  # Programs, Accessories, Documents, Settings, Find, Help
        QTest.keyClick(m, Qt.Key_Down)
    assert m.activeAction().text() == "&Help"
    QTest.keyClick(m, Qt.Key_Return)
    assert shell.calls[-1][0] == "show_about"
    m.popup(QPoint(10, 10))
    QTest.keyClick(m, Qt.Key_Escape)
    assert not m.isVisible()


def test_top_level_mnemonics_are_unique(menu):
    # Mnemonic key delivery is not simulated by the offscreen platform (a plain QMenu ignores it
    # too), so assert the mnemonics exist and do not clash.
    m, _ = menu
    keys = [re.search(r"&(\w)", a.text()).group(1).lower() for a in m.actions() if not a.isSeparator()]
    assert len(set(keys)) == len(keys)


def test_popup_above_stays_on_screen(menu, qtbot):
    m, _ = menu
    w = QWidget()
    qtbot.addWidget(w)
    w.setGeometry(0, 0, 80, 28)  # at top of screen: would go negative
    w.show()
    popup_above(m, w)
    avail = QGuiApplication.primaryScreen().availableGeometry()
    assert m.isVisible() and avail.contains(m.geometry().topLeft())
    m.hide()


def test_sounds_entry_navigates_to_display_tab(menu):
    m, shell = menu
    settings = next(a for a in m.actions() if a.text() == "&Settings").menu()
    next(a for a in settings.actions() if a.text() == "S&ounds").trigger()
    assert ("navigate", ("display", {"tab": "sounds"})) in shell.calls


def test_documents_lists_recent_files(qapp, qtbot, tmp_path):
    from PySide6.QtCore import QSettings
    from aws95.core.settings import Settings
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    real = tmp_path / "cost.md"
    real.write_text("x")
    settings.add_recent_document(str(tmp_path / "gone.csv"))
    settings.add_recent_document(str(real))
    opened = []
    m = build_start_menu(Registry(DEFAULT_MANIFESTS), FakeShell(), settings, opened.append)
    m.documents_menu.aboutToShow.emit()
    actions = [a for a in m.documents_menu.actions() if not a.isSeparator()]
    assert [a.text() for a in actions] == ["&cost.md", "&gone.csv (missing)"]
    assert actions[0].toolTip() == str(real) and actions[0].isEnabled() and not actions[1].isEnabled()
    actions[0].trigger()
    assert opened == [str(real)]


def test_open_document_ignores_non_files(tmp_path, monkeypatch):
    from aws95.shell import startmenu
    calls = []
    monkeypatch.setattr(startmenu.QDesktopServices, "openUrl", lambda url: calls.append(url.toString()))
    startmenu.open_document(str(tmp_path))                 # a directory
    startmenu.open_document(str(tmp_path / "missing.md"))
    real = tmp_path / "a.md"
    real.write_text("x")
    startmenu.open_document(str(real))
    assert len(calls) == 1 and calls[0].startswith("file://")


def test_documents_ampersand_is_literal(qapp, qtbot, tmp_path):
    from PySide6.QtCore import QSettings
    from aws95.core.settings import Settings
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    real = tmp_path / "Q&A report.md"
    real.write_text("x")
    settings.add_recent_document(str(real))
    m = build_start_menu(Registry(DEFAULT_MANIFESTS), FakeShell(), settings, lambda p: None)
    m.documents_menu.aboutToShow.emit()
    a = [a for a in m.documents_menu.actions() if not a.isSeparator()][0]
    assert a.text() == "&Q&&A report.md" and a.iconText() == "Q&A report.md" and a.toolTip() == str(real)


def test_documents_rebuilds_on_show(qapp, qtbot, tmp_path):
    from PySide6.QtCore import QSettings
    from aws95.core.settings import Settings
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    m = build_start_menu(Registry(DEFAULT_MANIFESTS), FakeShell(), settings, lambda p: None)
    assert _texts(m.documents_menu) == ["(&Empty)"]
    late = tmp_path / "late.md"
    late.write_text("x")
    settings.add_recent_document(str(late))
    m.documents_menu.aboutToShow.emit()
    assert _texts(m.documents_menu) == ["&late.md"]


def test_setup_wizard_entry_calls_service_without_checked(menu):
    m, shell = menu
    settings = next(a for a in m.actions() if a.text() == "&Settings").menu()
    act = next(a for a in settings.actions() if a.text().replace("&", "") == "AWS Setup Wizard…")
    act.trigger()
    assert ("open_setup_wizard", ()) in shell.calls
    assert ("open_setup_wizard", (False,)) not in shell.calls
