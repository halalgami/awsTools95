import gc
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from PySide6.QtWidgets import QApplication

from aws95.kit import theme as th
from aws95.kit.style import apply_theme
from aws95.kit.widgets.charts import BarChart, LineChart


@pytest.fixture(autouse=True)
def light(qapp):
    apply_theme(qapp, th.LIGHT)
    yield
    apply_theme(qapp, th.LIGHT)


def test_bar_chart_accepts_decimal(qtbot):
    chart = BarChart()
    qtbot.addWidget(chart)
    chart.set_data(["EC2", "S3", "Lambda"], [Decimal("120.50"), Decimal("17.47"), 4])
    assert chart.bar_count == 3 and chart.labels == ("EC2", "S3", "Lambda")
    chart.set_data([], [])
    assert chart.bar_count == 0


def test_bar_chart_rejects_length_mismatch(qtbot):
    chart = BarChart()
    qtbot.addWidget(chart)
    with pytest.raises(ValueError):
        chart.set_data(["a"], [1, 2])


@pytest.mark.parametrize("bad", [float("nan"), None, float("inf"), Decimal("NaN")])
def test_bar_chart_rejects_non_finite(qtbot, bad):
    chart = BarChart()
    qtbot.addWidget(chart)
    chart.set_data(["a"], [1])
    with pytest.raises(ValueError):
        chart.set_data(["a", "b"], [1, bad])
    assert chart.bar_count == 1 and chart.labels == ("a",)  # previous data kept


@pytest.mark.parametrize("bad", [float("nan"), None, float("inf"), Decimal("NaN")])
def test_line_chart_rejects_non_finite(qtbot, bad):
    chart = LineChart()
    qtbot.addWidget(chart)
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    chart.set_series([(ts, 1)])
    with pytest.raises(ValueError):
        chart.set_series([(ts, 1), (ts, bad)])
    assert chart.point_count == 1


def test_line_chart_series(qtbot):
    chart = LineChart()
    qtbot.addWidget(chart)
    pts = [(datetime(2026, m, 1, tzinfo=timezone.utc), Decimal(m * 10)) for m in range(1, 10)]
    chart.set_series(pts)
    assert chart.point_count == 9


def test_line_chart_marks_points(qtbot, qapp):
    chart = LineChart()
    qtbot.addWidget(chart)
    pts = [(datetime(2026, m, 1, tzinfo=timezone.utc), Decimal(m)) for m in range(1, 5)]
    chart.set_series(pts, marked=[1, 1, 3])
    assert chart.point_count == 4 and chart.marked_count == 2
    with pytest.raises(ValueError):
        chart.set_series(pts[:2], marked=[5])
    assert chart.marked_count == 2 and chart.point_count == 4      # previous series kept
    apply_theme(qapp, th.DARK)
    assert chart.marked_count == 2 and chart._mark_item is not None
    chart.set_series(pts)
    assert chart.marked_count == 0 and chart._mark_item is None


def test_chart_follows_theme(qtbot, qapp):
    chart = BarChart()
    qtbot.addWidget(chart)
    assert chart.backgroundBrush().color().name() == th.LIGHT.well
    apply_theme(qapp, th.DARK)
    assert chart.backgroundBrush().color().name() == th.DARK.well


def test_deleted_chart_does_not_break_theme_switch(qtbot, qapp):
    chart = BarChart()
    chart.set_data(["a"], [1])
    chart.show()
    chart.close()
    chart.deleteLater()
    del chart
    qapp.sendPostedEvents(None, 0)  # DeferredDelete
    QApplication.processEvents()
    gc.collect()
    apply_theme(qapp, th.DARK)
    apply_theme(qapp, th.LIGHT)


def test_chart_close_is_idempotent(qtbot):
    import warnings

    chart = BarChart()
    qtbot.addWidget(chart)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        chart.close()
        chart.close()
    assert not [w for w in caught if issubclass(w.category, RuntimeWarning)]
    apply_theme(QApplication.instance(), th.DARK)  # theme switch after close raises nothing
