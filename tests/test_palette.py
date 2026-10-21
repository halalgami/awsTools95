import pytest
from PySide6.QtCore import QSettings, Qt

from aws95.core.settings import Settings, ThemeMode
from aws95.programs import DEFAULT_MANIFESTS
from aws95.shell.palette import CommandPalette, PaletteItem, build_items, fuzzy_score, rank
from aws95.shell.registry import Registry


def test_fuzzy_score_basics():
    assert fuzzy_score("", "Billing") == 0.0
    assert fuzzy_score("xyz", "Billing") is None
    assert fuzzy_score("bil", "Billing") > fuzzy_score("bil", "Mobile Insights Log")
    assert fuzzy_score("al", "Activity Log") > fuzzy_score("al", "Billing alpha zzz")   # word starts


def test_rank_orders_and_limits():
    items = [PaletteItem("Programs", t, lambda: None) for t in ["Billing", "Activity Log", "Snapshots"]]
    assert [i.title for i in rank(items, "log")] == ["Activity Log"]
    assert [i.title for i in rank(items, "")] == ["Billing", "Activity Log", "Snapshots"]
    assert len(rank(items * 30, "", limit=10)) == 10


def test_keywords_match():
    item = PaletteItem("Programs", "Activity Log", lambda: None, keywords=("cloudtrail",))
    assert rank([item], "cloudtr") == [item]


class FakeShell:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a))


class FakeDesktop:
    def program_windows(self):
        return []


@pytest.fixture
def settings(tmp_path, qapp):
    return Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))


def test_build_items_groups_and_actions(settings):
    shell = FakeShell()
    items = build_items(Registry(DEFAULT_MANIFESTS), shell, settings, FakeDesktop())
    groups = {i.group for i in items}
    assert groups >= {"Programs", "Commands"}
    by_title = {i.title: i for i in items}
    by_title["Billing"].action()
    assert ("open_program", ("billing",)) in shell.calls
    by_title["Theme: Dark"].action()
    assert settings.theme_mode is ThemeMode.DARK
    by_title["Mute sounds"].action()
    assert settings.sound_muted is True
    by_title["Cascade Windows"].action()
    assert ("cascade", ()) in shell.calls


def test_palette_keyboard_flow(qtbot):
    ran = []
    items = [PaletteItem("Programs", "Billing", lambda: ran.append("billing")),
             PaletteItem("Programs", "Findings", lambda: ran.append("findings"))]
    pal = CommandPalette()
    qtbot.addWidget(pal)
    pal.set_items(items)
    pal.show()
    qtbot.keyClicks(pal.query_edit, "find")
    assert [i.title for i in pal.visible_items()] == ["Findings"]
    qtbot.keyClick(pal.query_edit, Qt.Key.Key_Return)
    assert ran == ["findings"] and not pal.isVisible()


def test_headers_not_selectable(qtbot):
    pal = CommandPalette()
    qtbot.addWidget(pal)
    pal.set_items([PaletteItem("Programs", "Billing", lambda: None)])
    header = pal.list_widget.item(0)
    assert not (header.flags() & Qt.ItemFlag.ItemIsSelectable)
    assert pal.list_widget.currentRow() == 1


class _Win:
    def __init__(self, closing=False):
        self.closing = closing

    @property
    def is_closing(self):
        return self.closing


class _Desk:
    def __init__(self, live):
        self.live = live
        self.activated = []

    def program_windows(self):
        return list(self.live)

    def activate(self, w):
        self.activated.append(w)


def test_window_item_skips_closed_windows():
    from aws95.shell.palette import _activate_window
    w = _Win()
    desk = _Desk([w])
    _activate_window(desk, w)
    assert desk.activated == [w]
    w.closing = True
    desk.live = []
    _activate_window(desk, w)
    assert desk.activated == [w]


def test_palette_escape_closes(qtbot):
    pal = CommandPalette()
    qtbot.addWidget(pal)
    pal.set_items([PaletteItem("Programs", "Billing", lambda: None)])
    pal.show()
    qtbot.keyClick(pal.query_edit, Qt.Key.Key_Escape)
    assert not pal.isVisible()


def test_items_hashable():
    a = PaletteItem("Programs", "Billing", lambda: None)
    b = PaletteItem("Programs", "Billing", lambda: 1)
    assert a == b and len({a, b}) == 1


def _two(ran=None):
    ran = ran if ran is not None else []
    return [PaletteItem("Programs", "Billing", lambda: ran.append("billing")),
            PaletteItem("Programs", "Findings", lambda: ran.append("findings"))]


def test_typing_into_dialog_focuses_edit(qtbot):
    ran = []
    pal = CommandPalette()
    qtbot.addWidget(pal)
    pal.set_items(_two(ran))
    pal.show()
    qtbot.keyClicks(pal, "find")
    assert pal.query_edit.text() == "find"
    assert [i.title for i in pal.visible_items()] == ["Findings"]
    qtbot.keyClick(pal, Qt.Key.Key_Return)
    assert ran == ["findings"] and not pal.isVisible()
    pal.show()
    qtbot.keyClick(pal, Qt.Key.Key_Escape)
    assert not pal.isVisible()


def test_reopen_resets_query(qtbot):
    pal = CommandPalette()
    qtbot.addWidget(pal)
    pal.set_items(_two())
    pal.show()
    qtbot.keyClicks(pal, "find")
    pal.hide()
    pal.show()
    assert pal.query_edit.text() == ""
    assert len(pal.visible_items()) == 2


def test_single_click_runs_item_not_header(qtbot):
    ran = []
    pal = CommandPalette()
    qtbot.addWidget(pal)
    pal.set_items(_two(ran))
    pal.show()
    pal.list_widget.itemClicked.emit(pal.list_widget.item(0))
    assert ran == [] and pal.isVisible()
    pal.list_widget.itemClicked.emit(pal.list_widget.item(2))
    assert ran == ["findings"]


def test_list_height_capped(qtbot):
    pal = CommandPalette()
    qtbot.addWidget(pal)
    assert pal.list_widget.maximumHeight() <= 320


def test_unhandled_keys_do_not_recurse(qtbot, monkeypatch):
    pal = CommandPalette()
    qtbot.addWidget(pal)
    pal.set_items(_two())
    pal.show()
    calls = []
    orig = CommandPalette.keyPressEvent

    def counting(self, event):
        calls.append(1)
        orig(self, event)

    monkeypatch.setattr(CommandPalette, "keyPressEvent", counting)
    ctrl = Qt.KeyboardModifier.ControlModifier
    for target in (pal, pal.query_edit):
        for key, mod in ((Qt.Key.Key_F5, Qt.KeyboardModifier.NoModifier), (Qt.Key.Key_K, ctrl),
                         (Qt.Key.Key_A, ctrl)):
            calls.clear()
            qtbot.keyClick(target, key, mod)
            assert len(calls) <= 2
