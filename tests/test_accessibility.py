"""M4 Task 2: keyboard and accessibility."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTabWidget

from aws95.kit import theme as th
from aws95.kit.widgets.datagrid import Column
from aws95.programs.billing import make_billing
from aws95.programs.findings import make_findings
from aws95.programs.snapshots import make_snapshots
from aws95.providers.demo import demo_provider_set
from tests.test_shell_integration import shell  # noqa: F401  (fixture)


def _tabs(win):
    return win.desktop.active_program_window().content().findChildren(QTabWidget)[0]


def test_ctrl_tab_switches_program_tabs(shell, qtbot):  # noqa: F811
    win, *_ = shell
    win.open_program("billing")
    tabs = _tabs(win)
    assert tabs.currentIndex() == 0
    QTest.keyClick(win, Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier)
    assert tabs.currentIndex() == 1
    QTest.keyClick(win, Qt.Key.Key_Tab,
                   Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert tabs.currentIndex() == 0
    QTest.keyClick(win, Qt.Key.Key_Tab,
                   Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert tabs.currentIndex() == tabs.count() - 1     # wraps


def test_ctrl_tab_still_cycles_windows_without_tabs(shell, qtbot):  # noqa: F811
    win, *_ = shell
    fleet = win.open_program("fleet")
    other = win.open_program("billing")
    assert win.desktop.active_program_window() is other
    win.desktop.activate(fleet)
    assert not fleet.content().findChildren(QTabWidget)
    focus = QApplication.focusWidget() or win.desktop
    QTest.keyClick(focus, Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier)
    assert win.desktop.active_program_window() is other


@pytest.mark.parametrize("t", [th.LIGHT, th.DARK], ids=["light", "dark"])
def test_contrast_pairs_meet_thresholds(t):
    for pair in (("disabled_text", "face"), ("disabled_text", "face2")):
        assert pair in th.TEXT_PAIRS or pair in th.EXEMPT_TEXT_PAIRS
    for pair in (("indicator_border", "face"), ("indicator_border", "well")):
        assert pair in th.UI_PAIRS
    for fg, bg in th.TEXT_PAIRS:
        assert th.contrast_ratio(getattr(t, fg), getattr(t, bg)) >= 4.5, (t.name, fg, bg)
    for fg, bg in th.EXEMPT_TEXT_PAIRS:
        floor = 4.5 if t is th.DARK else 3.0      # project policy: Dark disabled text is held to full AA
        assert th.contrast_ratio(getattr(t, fg), getattr(t, bg)) >= floor, (t.name, fg, bg)
    for fg, bg in th.UI_PAIRS:
        assert th.contrast_ratio(getattr(t, fg), getattr(t, bg)) >= 3.0, (t.name, fg, bg)


def _check_columns(grid, min_cols):
    cols = [(i, c) for i, c in enumerate(grid.model.columns) if c.width == -1]
    assert len(cols) >= min_cols
    fm = grid.fontMetrics()
    for i, c in cols:
        widest = max([fm.horizontalAdvance(c.title)] +
                     [fm.horizontalAdvance(str(grid.model.index(r, i).data(Qt.ItemDataRole.DisplayRole) or ""))
                      for r in range(grid.model.rowCount())])
        assert grid.view.columnWidth(i) >= widest + 12, (c.key, grid.view.columnWidth(i), widest)


@pytest.mark.parametrize("factory,attr,keys", [
    (make_findings, "grid", {"rule", "target", "first_seen", "account"}),
    (make_snapshots, "history", {"taken_at"}),
])
def test_key_columns_not_elided(program_env, qtbot, factory, attr, keys):
    w, _ = program_env(factory, demo_provider_set())
    qtbot.addWidget(w)
    w.resize(900, 600)
    w.show()
    grid = getattr(w, attr)
    qtbot.waitUntil(lambda: grid.model.rowCount() > 0)
    assert keys <= {c.key for c in grid.model.columns if c.width == -1}
    _check_columns(grid, len(keys))
    if factory is make_findings:        # Severity .. First seen must sit fully inside the 900 px viewport
        order = [c.key for c in grid.model.columns]
        i = order.index("first_seen")
        assert order.index("target") > i
        header = grid.view.horizontalHeader()
        assert header.sectionViewportPosition(i) + header.sectionSize(i) <= grid.view.viewport().width()


def test_billing_amount_columns_not_elided(program_env, qtbot):
    w, _ = program_env(make_billing, demo_provider_set())
    qtbot.addWidget(w)
    w.resize(900, 600)
    w.show()
    for key, page in w.pages.items():
        grid = getattr(page, "grid", None)
        if grid is None:
            continue
        w.tabs.setCurrentWidget(page)
        qtbot.waitUntil(lambda g=grid: g.model.rowCount() > 0, timeout=3000)
        if "amount" not in {c.key for c in grid.model.columns}:
            continue        # e.g. Free tier has no money column
        assert "amount" in {c.key for c in grid.model.columns if c.width == -1}, key
        _check_columns(grid, 1)


def _walk(menu):
    yield menu
    for a in menu.actions():
        if a.menu() is not None:
            yield from _walk(a.menu())


def _assert_mnemonics(menu):
    for m in _walk(menu):
        letters = []
        for a in m.actions():
            if a.isSeparator():
                continue
            assert a.text().replace("&&", "").count("&") == 1, (m.title(), a.text())
            letters.append(a.text().replace("&&", "").split("&")[1][0].lower())
        assert len(letters) == len(set(letters)), (m.title(), letters)


def test_shell_menus_have_unique_mnemonics(shell):  # noqa: F811
    win, *_ = shell
    _assert_mnemonics(win.start_menu)
    _assert_mnemonics(win.taskbar.build_context_menu())
    _assert_mnemonics(win.icons.build_context_menu())


def test_ctrl_tab_filter_removed_on_close(shell):  # noqa: F811
    win, *_ = shell
    assert win._tab_filter_installed is True
    win.close()
    assert win._tab_filter_installed is False


def test_ctrl_tab_with_text_field_focused_switches_tabs(shell, qtbot):  # noqa: F811
    from PySide6.QtWidgets import QLineEdit
    win, *_ = shell
    win.open_program("billing")
    tabs = _tabs(win)
    edit = win.desktop.active_program_window().content().findChildren(QLineEdit)[0]
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier)
    assert tabs.currentIndex() == 1


# -- Setup wizard, PolicyDialog and PermissionsDialog (phase 2a) ----------------------------------------------

def _wizard_env(tmp_path):
    from PySide6.QtCore import QSettings

    from aws95.core.settings import Settings
    from aws95.core.tasks import TaskRunner
    from aws95.shell.context import NullSound
    from aws95.shell.setup_wizard import SetupWizard
    from tests.account_stubs import P, StubAccounts
    runner = TaskRunner()
    acc = StubAccounts((P("dev"), P("prod")), {"dev": "123456789012", "prod": "210987654321"})
    settings = Settings(QSettings(str(tmp_path / "a.ini"), QSettings.Format.IniFormat))
    return runner, acc, SetupWizard(settings, runner, acc, NullSound())


def _close_all(widgets, runner):
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid
    for w in widgets:
        if isValid(w):
            w.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    runner.shutdown()


def _own_buttons(root):
    """Every checkbox/button owned by a page or dialog; per-row table-cell buttons (Verify, Copy, colour) are
    repeated per row, so a mnemonic cannot be unique for them and they are exempt."""
    from PySide6.QtWidgets import QAbstractButton, QAbstractItemView
    out = []
    for b in root.findChildren(QAbstractButton):
        if not b.text():
            continue
        parent, in_cell = b.parentWidget(), False
        while parent is not None and parent is not root:
            if isinstance(parent, QAbstractItemView):
                in_cell = True
                break
            parent = parent.parentWidget()
        if not in_cell:
            out.append(b)
    return out


def _assert_button_mnemonics(root, label, extra=()):
    """`extra`: the wizard's own buttons, which share the Alt-key space with every page."""
    letters = []
    for b in [*_own_buttons(root), *extra]:
        text = b.text().replace("&&", "")
        assert text.count("&") == 1, (label, b.text())
        letters.append(text.split("&")[1][0].lower())
    assert len(letters) == len(set(letters)), (label, letters)
    return len(letters)


def test_wizard_pages_have_unique_alt_mnemonics(qapp, tmp_path):
    from aws95.shell.setup_wizard import PAGE_IDS
    runner, _, w = _wizard_env(tmp_path)
    try:
        total = 0
        from PySide6.QtWidgets import QWizard
        nav = [w.button(getattr(QWizard.WizardButton, n)) for n in
               ("BackButton", "NextButton", "CancelButton", "FinishButton")]
        for i, name in enumerate(PAGE_IDS):
            total += _assert_button_mnemonics(w.page(i), name, nav) - len(nav)
        assert total == 6          # welcome 1, sources 2, choose 2, finish 1; the permissions panel is built on entry (covered via PermissionsDialog)
        # The permissions page builds its panel on entry: check a panel against the wizard buttons too.
        from aws95.shell.setup_wizard import PermissionsPanel
        panel = PermissionsPanel(w.accounts, runner, [("dev", "123456789012")])
        assert _assert_button_mnemonics(panel, "permissions panel", nav) - len(nav) == 3
    finally:
        _close_all([w], runner)


def test_dialogs_have_unique_alt_mnemonics(qapp, qtbot, tmp_path):
    from aws95.shell.setup_wizard import PermissionsDialog, PolicyDialog
    runner, acc, w = _wizard_env(tmp_path)
    policy = PolicyDialog(acc, runner)
    perms = PermissionsDialog(acc, runner, "dev", "123456789012")
    try:
        assert _assert_button_mnemonics(policy, "policy") == 2
        assert _assert_button_mnemonics(perms, "permissions") == 3
    finally:
        _close_all([w, policy, perms], runner)


def test_escape_closes_wizard_and_both_dialogs(qapp, qtbot, tmp_path):
    from shiboken6 import isValid

    from aws95.shell.setup_wizard import PermissionsDialog, PolicyDialog
    runner, acc, w = _wizard_env(tmp_path)
    policy = PolicyDialog(acc, runner)
    perms = PermissionsDialog(acc, runner, "dev", "123456789012")
    shown = [w, policy, perms]
    try:
        for widget in shown:
            widget.show()
            qtbot.waitUntil(widget.isVisible, timeout=2000)
            QTest.keyClick(widget, Qt.Key.Key_Escape)
            qtbot.waitUntil(lambda wd=widget: not isValid(wd) or not wd.isVisible(), timeout=2000)
    finally:
        _close_all(shown, runner)


@pytest.mark.parametrize("theme_name", ["light", "dark"])
def test_radio_indicator_outline_is_visible(qapp, theme_name):
    """Dark legibility (Important): an unchecked radio's ring uses indicator_border (>= 3:1 on face and well), like
    the checkbox; Fusion's own ring all but vanished on Dark's grey."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QStyle, QStyleOptionButton
    from aws95.kit.style import apply_theme
    t = th.DARK if theme_name == "dark" else th.LIGHT
    style = apply_theme(qapp, t)
    try:
        img = QImage(20, 20, QImage.Format.Format_ARGB32)
        img.fill(QColor(t.face))
        opt = QStyleOptionButton()
        opt.rect = QRect(2, 2, 13, 13)
        opt.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Off
        p = QPainter(img)
        style.drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorRadioButton, opt, p)
        p.end()
        ring = QColor(t.indicator_border)
        found = any(QColor(img.pixel(x, y)) == ring for x in range(20) for y in range(20))
        assert found, "the radio ring is not drawn in indicator_border"
    finally:
        apply_theme(qapp, th.LIGHT)
