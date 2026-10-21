from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QStyle, QStyleOptionButton

from aws95.contracts.common import Coverage, Freshness, Result, Status
from aws95.kit import theme
from aws95.kit.style import Win95Style
from aws95.kit.widgets.datagrid import Column, DataGrid, format_value
from aws95.kit.widgets.statusstrip import StatusStrip, coverage_text, freshness_text

COLS = [Column("name", "Name"), Column("state", "State"), Column("n", "N", "number")]
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def grid(qtbot):
    g = DataGrid(COLS)
    qtbot.addWidget(g)
    g.set_rows([{"name": f"r{i}", "state": "on" if i % 2 else "off", "n": i} for i in range(12_000)])
    return g


def test_visible_rows_returns_all_filtered_rows_not_just_fetched(grid):
    assert grid.model.rowCount() == grid.model.FETCH_BATCH          # lazily fetched
    assert len(grid.visible_rows()) == 12_000
    grid.set_filter_text("state:on")
    rows = grid.visible_rows()
    assert len(rows) == 6_000 and all(r["state"] == "on" for r in rows)


def test_visible_rows_follow_sort(grid):
    grid.model.sort(2, Qt.SortOrder.DescendingOrder)
    assert grid.visible_rows()[0]["n"] == 11_999


def test_column_visibility_and_menu(grid):
    assert [c.key for c in grid.visible_columns()] == ["name", "state", "n"]
    grid.set_column_visible("state", False)
    assert not grid.column_visible("state")
    assert [c.key for c in grid.visible_columns()] == ["name", "n"]
    menu = grid.build_column_menu()
    actions = {a.text(): a for a in menu.actions()}
    assert actions["State"].isCheckable() and not actions["State"].isChecked()
    actions["State"].trigger()
    assert grid.column_visible("state")
    with pytest.raises(KeyError):
        grid.set_column_visible("nope", True)


def test_last_visible_column_cannot_be_hidden(grid):
    grid.set_column_visible("state", False)
    grid.set_column_visible("n", False)
    menu = grid.build_column_menu()
    actions = {a.text(): a for a in menu.actions()}
    assert not actions["Name"].isEnabled()


def test_current_row(grid, qtbot):
    assert grid.current_row() is None
    grid.view.selectRow(3)
    assert grid.current_row()["name"] == "r3"


def test_coverage_and_freshness_text():
    full = Result.ok((), coverage=Coverage(("us-east-1",), ("us-east-1",), 0))
    assert coverage_text(full) == "1/1 regions"
    part = Result(Status.PARTIAL, (), Coverage(("a-b-1", "c-d-2", "e-f-3"), ("a-b-1", "c-d-2"), 14))
    assert coverage_text(part) == "2/3 regions · 14 denied · partial"
    assert coverage_text(Result.ok(())) == ""
    nc = Result.not_connected("later")
    assert freshness_text(nc, NOW) == "Not connected"
    fresh = Result.ok((), freshness=Freshness(NOW - timedelta(minutes=2), "snapshot"))
    assert freshness_text(fresh, NOW) == "Snapshot 2m ago"            # spec §4 (2b): snapshot data says so
    assert freshness_text(Result.ok((), freshness=Freshness(NOW - timedelta(seconds=5), "x")), NOW) == "Synced just now"
    assert freshness_text(Result.ok((), freshness=Freshness(NOW - timedelta(hours=3), "x")), NOW) == "Synced 3h ago"
    assert freshness_text(Result.ok((), freshness=Freshness(NOW - timedelta(days=4), "x")), NOW) == "Synced 4d ago"
    assert freshness_text(Result.ok((), freshness=Freshness(None, "fixture")), NOW) == "fixture"
    assert freshness_text(Result.ok(()), NOW) == ""


def test_status_strip_labels_are_plain(qtbot):
    s = StatusStrip()
    qtbot.addWidget(s)
    s.set_counts("<b>3</b> items")
    s.set_result(Result.not_connected("x"), NOW)
    for label in (s.counts_label, s.coverage_label, s.freshness_label):
        assert label.textFormat() == Qt.TextFormat.PlainText
    assert s.counts_label.text() == "<b>3</b> items" and s.freshness_label.text() == "Not connected"
    s.clear()
    assert s.counts_label.text() == "" and s.freshness_label.text() == ""


def _indicator(t, checked: bool) -> QImage:
    style = Win95Style(t)
    img = QImage(13, 13, QImage.Format.Format_ARGB32)
    img.fill(QColor(t.face))
    opt = QStyleOptionButton()
    opt.rect = QRect(0, 0, 13, 13)
    opt.state = QStyle.StateFlag.State_Enabled | (QStyle.StateFlag.State_On if checked else QStyle.StateFlag.State_Off)
    p = QPainter(img)
    style.drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorCheckBox, opt, p, None)
    p.end()
    return img


@pytest.mark.parametrize("t", [theme.LIGHT, theme.DARK], ids=["light", "dark"])
def test_unchecked_indicator_is_a_visible_well(qapp, t):
    img = _indicator(t, False)
    assert img.pixelColor(0, 0).name() == QColor(t.indicator_border).name()   # outline (was the lo bevel edge)
    assert img.pixelColor(6, 6).name() == QColor(t.well).name()     # well interior, no tick
    assert QColor(t.well).name() != QColor(t.face).name() or img.pixelColor(1, 1).name() == QColor(t.dark).name()


@pytest.mark.parametrize("t", [theme.LIGHT, theme.DARK], ids=["light", "dark"])
def test_checked_indicator_draws_tick(qapp, t):
    img = _indicator(t, True)
    tick = QColor(t.text).name()
    assert any(img.pixelColor(x, y).name() == tick for x in range(3, 10) for y in range(3, 10))


def test_decimal_money_tooltip_and_number_are_never_scientific(qtbot):
    from decimal import Decimal
    g = DataGrid([Column("m", "M", "money"), Column("q", "Q", "number")])
    qtbot.addWidget(g)
    g.set_rows([{"m": Decimal("0E-8"), "q": Decimal("1E+3")}, {"m": Decimal("1E+3"), "q": Decimal("1234567.891")}])
    tip = [g.model.data(g.model.index(r, 0), Qt.ItemDataRole.ToolTipRole) for r in range(2)]
    assert tip == ["0.00000000", "1000"]
    assert format_value(Decimal("1E+3"), "number") == "1,000"
    assert format_value(Decimal("1234567.891"), "number") == "1,234,567.891"
    assert "E" not in format_value(Decimal("0E-8"), "number")


@pytest.mark.parametrize("t", [theme.LIGHT, theme.DARK], ids=["light", "dark"])
def test_indicator_border_contrast(t):
    assert theme.contrast_ratio(t.indicator_border, t.face) >= 3.0
    assert theme.contrast_ratio(t.indicator_border, t.well) >= 3.0


@pytest.mark.parametrize("t", [theme.LIGHT, theme.DARK], ids=["light", "dark"])
def test_disabled_checked_tick_is_muted(qapp, t):
    style = Win95Style(t)
    img = QImage(13, 13, QImage.Format.Format_ARGB32)
    img.fill(QColor(t.face))
    opt = QStyleOptionButton()
    opt.rect = QRect(0, 0, 13, 13)
    opt.state = QStyle.StateFlag.State_On                      # not enabled
    p = QPainter(img)
    style.drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorCheckBox, opt, p, None)
    p.end()
    names = {img.pixelColor(x, y).name() for x in range(3, 10) for y in range(3, 10)}
    assert QColor(t.muted).name() in names and QColor(t.text).name() not in names
