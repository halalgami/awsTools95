from PySide6.QtCore import QObject, Signal

from aws95.contracts.common import Result
from aws95.contracts.search import SearchHit
from aws95.shell.palette import SEARCHING, CommandPalette, PaletteItem


class Handle(QObject):
    finished = Signal(object)
    failed = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class Searcher:
    def __init__(self):
        self.calls, self.handles = [], []

    def __call__(self, text):
        self.calls.append(text)
        h = Handle()
        self.handles.append(h)
        return h


HIT = SearchHit("resource", "i-0aaa", "ec2.instance · us-east-1 · 123456789012", "resources",
                {"rtype": "ec2.instance", "filter": "rid:i-0aaa"})


def make(qtbot, searcher=None, opened=None):
    opened = [] if opened is None else opened
    pal = CommandPalette(search=searcher, open_hit=opened.append, icon_for=lambda _p: "server")
    qtbot.addWidget(pal)
    pal.set_items([PaletteItem("Programs", "Resources", lambda: None)])
    pal.show()
    return pal


def titles(pal):
    return [pal.list_widget.item(i).text() for i in range(pal.list_widget.count())]


def test_burst_of_typing_runs_one_search(qtbot):
    """Review Focus 1."""
    s = Searcher()
    pal = make(qtbot, s)
    for ch in "i-0aa":
        pal.query_edit.setText(pal.query_edit.text() + ch)
    assert SEARCHING in titles(pal)
    qtbot.waitUntil(lambda: len(s.calls) == 1, timeout=1000)
    qtbot.wait(300)
    assert s.calls == ["i-0aa"]


def test_hits_show_in_their_group_and_enter_navigates(qtbot):
    s, opened = Searcher(), []
    pal = make(qtbot, s, opened)
    pal.query_edit.setText("i-0a")
    qtbot.waitUntil(lambda: bool(s.handles), timeout=1000)
    s.handles[-1].finished.emit(Result.ok((HIT,)))
    t = titles(pal)
    assert "Resources" in t and "i-0aaa" in t and SEARCHING not in t
    hit_row = next(i for i in range(pal.list_widget.count()) if pal.list_widget.item(i).text() == "i-0aaa")
    pal.list_widget.setCurrentRow(hit_row)
    pal.run_current()
    assert opened == [HIT]


def test_stale_hits_are_dropped(qtbot):
    """Review Focus 1."""
    s = Searcher()
    pal = make(qtbot, s)
    pal.query_edit.setText("i-0a")
    qtbot.waitUntil(lambda: bool(s.handles), timeout=1000)
    old = s.handles[-1]
    pal.query_edit.setText("zz")
    old.finished.emit(Result.ok((HIT,)))
    assert "i-0aaa" not in titles(pal) and old.cancelled


def test_short_query_does_not_search(qtbot):
    s = Searcher()
    pal = make(qtbot, s)
    pal.query_edit.setText("i")
    qtbot.wait(300)
    assert s.calls == [] and SEARCHING not in titles(pal)


def test_not_connected_result_adds_nothing(qtbot):
    s = Searcher()
    pal = make(qtbot, s)
    pal.query_edit.setText("abc")
    qtbot.waitUntil(lambda: bool(s.handles), timeout=1000)
    s.handles[-1].finished.emit(Result.not_connected("nope"))
    assert SEARCHING not in titles(pal) and "nope" not in " ".join(titles(pal))


def test_demo_groups_are_marked(qtbot):
    s = Searcher()
    pal = make(qtbot, s)
    pal.query_edit.setText("abc")
    qtbot.waitUntil(lambda: bool(s.handles), timeout=1000)
    s.handles[-1].finished.emit(Result.ok((HIT,), is_demo=True))
    assert "Resources [DEMO]" in titles(pal)
