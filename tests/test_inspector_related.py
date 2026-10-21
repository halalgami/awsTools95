from PySide6.QtWidgets import QLabel

from aws95.kit.widgets.inspector import Inspector


def test_set_related_replaces_the_placeholder(qtbot):
    ins = Inspector()
    qtbot.addWidget(ins)
    index = ins.tabs.indexOf(ins.related_view)
    panel = QLabel("mine")
    ins.set_related(panel)
    assert ins.tabs.widget(index) is panel and ins.tabs.tabText(index) == "Related" and ins.related_view is panel


def test_placeholder_stays_without_a_panel(qtbot):
    ins = Inspector()
    qtbot.addWidget(ins)
    assert ins.tabs.tabText(ins.tabs.indexOf(ins.related_view)) == "Related"
