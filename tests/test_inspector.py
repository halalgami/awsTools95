import pytest
from PySide6.QtCore import Qt

from aws95.kit import theme as th
from aws95.kit.style import apply_theme
from aws95.kit.widgets.inspector import Inspector
from aws95.kit.widgets.jsonview import JsonView
from aws95.kit.widgets.stateview import ViewState


@pytest.fixture(autouse=True)
def light(qapp):
    apply_theme(qapp, th.LIGHT)


def test_jsonview_pretty_and_unicode(qtbot):
    v = JsonView()
    qtbot.addWidget(v)
    v.set_data({"b": 1, "a": {"name": "café ☁"}})
    text = v.toPlainText()
    assert text.index('"a"') < text.index('"b"')
    assert "café ☁" in text


def test_jsonview_truncates_huge_payloads(qtbot):
    v = JsonView()
    qtbot.addWidget(v)
    v.set_data({"blob": "x" * (JsonView.MAX_CHARS + 10)})
    text = v.toPlainText()
    assert len(text) < JsonView.MAX_CHARS + 200
    assert "truncated" in text


def test_jsonview_never_raises_on_odd_input(qtbot):
    v = JsonView()
    qtbot.addWidget(v)
    # non-sortable mixed keys: sort_keys=True raises TypeError, must fall back
    v.set_data({1: "a", "b": 2, None: 3})
    text = v.toPlainText()
    assert '"b": 2' in text and '"a"' in text
    v.set_data({"s": {1, 2}, "raw": b"bytes", "nan": float("nan")})
    text = v.toPlainText()
    assert "bytes" in text and "NaN" in text
    v.set_data(None)
    assert v.toPlainText() == "null"


def test_inspector_populates_tabs(qtbot):
    ins = Inspector()
    qtbot.addWidget(ins)
    ins.set_item("batch-worker", "i-0abc", {"type": "m5.large", "net": {"vpc": "vpc-1"}},
                 {"env": "dev", "owner": "ops"}, raw={"InstanceId": "i-0abc"})
    assert ins.attrs_table.rowCount() == 2
    values = {ins.attrs_table.item(r, 0).text(): ins.attrs_table.item(r, 1).text() for r in range(2)}
    assert values["net"] == '{"vpc": "vpc-1"}'
    assert ins.tags_table.rowCount() == 2
    assert "InstanceId" in ins.json_view.toPlainText()
    assert ins.related_view.state is ViewState.NOT_CONNECTED


def test_inspector_clear(qtbot):
    ins = Inspector()
    qtbot.addWidget(ins)
    ins.set_item("x", "", {"a": 1}, {})
    ins.clear()
    assert ins.attrs_table.rowCount() == 0
    assert ins.title_label.text() == "No selection"


def test_inspector_labels_are_plain_text(qtbot):
    ins = Inspector()
    qtbot.addWidget(ins)
    ins.set_item("<b>x</b>", "<i>y</i>", {}, {})
    assert ins.title_label.text() == "<b>x</b>"
    assert ins.subtitle_label.text() == "<i>y</i>"
    assert ins.title_label.textFormat() == Qt.TextFormat.PlainText
    assert ins.subtitle_label.textFormat() == Qt.TextFormat.PlainText


class _Hostile:
    def __str__(self):
        raise RuntimeError("no str")

    def __repr__(self):
        raise RuntimeError("no repr")


def test_jsonview_survives_deep_nesting_and_hostile_objects(qtbot):
    v = JsonView()
    qtbot.addWidget(v)
    deep: list = []
    cur = deep
    for _ in range(5000):
        nxt: list = []
        cur.append(nxt)
        cur = nxt
    v.set_data(deep)
    assert v.toPlainText() != ""
    v.set_data({"k": _Hostile()})
    assert v.toPlainText() != ""
    v.set_data(_Hostile())
    assert "_Hostile" in v.toPlainText()


def test_inspector_survives_hostile_values(qtbot):
    ins = Inspector()
    qtbot.addWidget(ins)
    ins.set_item("t", "s", {"a": _Hostile(), "b": [_Hostile()]}, {"c": _Hostile()})
    assert ins.attrs_table.rowCount() == 2
    assert ins.tags_table.rowCount() == 1
