import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from PySide6.QtCore import Qt

from aws95.kit import theme as th
from aws95.kit.style import apply_theme
from aws95.kit.widgets.datagrid import ROW_ROLE, Column, DataGrid, RowTableModel, format_value

COLS = [Column("name", "Name"), Column("state", "State", "status"),
        Column("cpu", "vCPU", "number"), Column("cost", "Cost (USD)", "money")]


@pytest.fixture(autouse=True)
def light(qapp):
    apply_theme(qapp, th.LIGHT)


def rows(n):
    return [{"name": f"host-{i:06d}", "state": "stopped" if i % 10 == 0 else "running",
             "cpu": i % 16, "cost": Decimal(i % 500) / Decimal(4)} for i in range(n)]


def test_format_value():
    assert format_value(Decimal("1234.5"), "money") == "1,234.50"
    assert format_value(12345, "number") == "12,345"
    assert format_value(None, "text") == ""
    assert format_value(datetime(2026, 10, 2, 9, 41, tzinfo=timezone.utc), "date") == "2026-10-02 09:41"
    assert format_value({"a": 1}, "text") == '{"a": 1}'


def test_model_counts_and_fetch_more(qapp):
    m = RowTableModel(COLS)
    m.set_rows(rows(12_000))
    assert m.total_count == 12_000 and m.shown_count == 12_000
    assert m.rowCount() == RowTableModel.FETCH_BATCH
    assert m.canFetchMore()
    m.fetchMore()
    assert m.rowCount() == 2 * RowTableModel.FETCH_BATCH


def test_display_and_row_role(qapp):
    m = RowTableModel(COLS)
    m.set_rows(rows(3))
    idx = m.index(1, 3)
    assert m.data(idx, Qt.ItemDataRole.DisplayRole) == "0.25"
    assert m.data(idx, ROW_ROLE)["name"] == "host-000001"
    assert m.headerData(3, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "Cost (USD)"


def test_filter_and_error_keeps_previous_view(qapp, qtbot):
    m = RowTableModel(COLS)
    m.set_rows(rows(100))
    assert m.set_filter("state:stopped")
    assert m.shown_count == 10
    with qtbot.waitSignal(m.filter_error):
        ok = m.set_filter("cost>abc")
    assert not ok and m.shown_count == 10


def test_sort_handles_none_and_mixed_types(qapp):
    m = RowTableModel(COLS)
    m.set_rows([{"name": "b", "cpu": 2}, {"name": None, "cpu": "n/a"}, {"name": "a", "cpu": None}, {"name": "c", "cpu": 1}])
    m.sort(0, Qt.SortOrder.AscendingOrder)
    assert [m.row_at(i)["name"] for i in range(4)] == ["a", "b", "c", None]
    m.sort(0, Qt.SortOrder.DescendingOrder)
    assert [m.row_at(i)["name"] for i in range(4)] == ["c", "b", "a", None]
    m.sort(2, Qt.SortOrder.AscendingOrder)  # numbers before text, None last, no TypeError
    assert [m.row_at(i)["cpu"] for i in range(4)] == [1, 2, "n/a", None]


def test_filter_100k_rows_within_budget(qapp):
    m = RowTableModel(COLS)
    m.set_rows(rows(100_000))
    t0 = time.perf_counter()
    m.set_filter("state:stopped")
    elapsed = time.perf_counter() - t0
    assert m.shown_count == 10_000
    # Spec budget is 100 ms on a packaged build; 250 ms here absorbs CI noise. M4 measures the real budget.
    assert elapsed < 0.25, f"filter took {elapsed*1000:.0f} ms"


def test_grid_emits_row_signals(qtbot):
    grid = DataGrid(COLS)
    qtbot.addWidget(grid)
    grid.set_rows(rows(5))
    with qtbot.waitSignal(grid.row_activated) as blocker:
        grid.view.activated.emit(grid.model.index(2, 0))
    assert blocker.args[0]["name"] == "host-000002"


def test_grid_filter_box_flags_errors(qtbot):
    grid = DataGrid(COLS)
    qtbot.addWidget(grid)
    grid.set_rows(rows(20))
    grid.set_filter_text("state:stopped")
    assert grid.model.shown_count == 2
    grid.set_filter_text("cpu>")
    assert grid.filter_edit.property("invalid") is True
    assert grid.model.shown_count == 2
    grid.set_filter_text("")
    assert grid.filter_edit.property("invalid") is False


def test_fresh_grid_keeps_input_order_until_sorted(qtbot):
    grid = DataGrid(COLS)
    qtbot.addWidget(grid)
    grid.set_rows(rows(20))
    assert [grid.model.row_at(i)["name"] for i in range(3)] == ["host-000000", "host-000001", "host-000002"]
    grid.model.sort(0, Qt.SortOrder.DescendingOrder)
    assert grid.model.row_at(0)["name"] == "host-000019"


def test_sort_treats_nan_like_missing(qapp):
    m = RowTableModel(COLS)
    m.set_rows([{"cpu": Decimal("NaN")}, {"cpu": 3}, {"cpu": float("nan")}, {"cpu": None}, {"cpu": 1}])
    m.sort(2, Qt.SortOrder.AscendingOrder)
    assert [m.row_at(i)["cpu"] for i in range(2)] == [1, 3]
    m.sort(2, Qt.SortOrder.DescendingOrder)
    assert [m.row_at(i)["cpu"] for i in range(2)] == [3, 1]


def test_money_is_never_rounded():
    assert format_value(Decimal("0.0000123"), "money") == "0.0000123"
    assert format_value(Decimal("0.125"), "money") == "0.125"
    assert format_value(Decimal("1234.5678"), "money") == "1,234.5678"
    assert format_value(Decimal("1234.5"), "money") == "1,234.50"
    assert format_value(Decimal("NaN"), "money") == "NaN"
    assert format_value(5, "money") == "5.00"
    assert format_value(0, "money") == "0.00"


def test_money_tooltip_is_exact(qapp):
    m = RowTableModel(COLS)
    m.set_rows([{"name": "a", "cost": Decimal("0.0000123")}, {"name": "b"}])
    assert m.data(m.index(0, 3), Qt.ItemDataRole.ToolTipRole) == "0.0000123"
    assert m.data(m.index(1, 3), Qt.ItemDataRole.ToolTipRole) is None
    assert m.data(m.index(0, 0), Qt.ItemDataRole.ToolTipRole) is None


def test_set_filter_text_stops_pending_debounce(qtbot):
    grid = DataGrid(COLS)
    qtbot.addWidget(grid)
    grid.set_rows(rows(20))
    grid.filter_edit.setText("state:stopped")
    assert grid._debounce.isActive()
    grid.set_filter_text("")
    assert not grid._debounce.isActive()
    assert grid.model.shown_count == 20


def test_snan_rows_can_be_set_and_sorted(qapp):
    model = RowTableModel(COLS)
    data = [{"name": "a", "cost": Decimal("sNaN")}, {"name": "b", "cost": Decimal("2")},
            {"name": "c", "cost": Decimal("NaN")}, {"name": "d", "cost": Decimal("1")}]
    model.set_rows(data)
    model.sort(3, Qt.SortOrder.AscendingOrder)
    assert [model.row_at(i)["name"] for i in range(2)] == ["d", "b"]
    model.sort(3, Qt.SortOrder.DescendingOrder)
    assert [model.row_at(i)["name"] for i in range(2)] == ["b", "d"]


def test_return_activates_current_row_once(qtbot):
    g = DataGrid(COLS)
    qtbot.addWidget(g)
    g.set_rows(rows(5))
    g.show()
    g.view.selectRow(2)
    got = []
    g.row_activated.connect(got.append)
    qtbot.keyClick(g.view, Qt.Key.Key_Return)
    assert [r["name"] for r in got] == ["host-000002"]
    qtbot.keyClick(g.view, Qt.Key.Key_Enter)
    assert len(got) == 2
