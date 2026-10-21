import statistics
import time

from PySide6.QtWidgets import QApplication

from aws95.kit import theme as th
from aws95.kit.style import apply_theme
from aws95.kit.widgets.charts import BarChart
from aws95.kit.widgets.datagrid import Column, DataGrid
from aws95.programs import DEFAULT_MANIFESTS
from aws95.providers.demo import demo_provider_set
from aws95.shell.desktop import Desktop
from aws95.shell.registry import Registry

REG = Registry(DEFAULT_MANIFESTS)
PROGRAMS = [m.id for m in DEFAULT_MANIFESTS if m.category == "programs"]


def test_theme_switch_all_programs_budget_and_colours(qapp, qtbot, program_env):
    _, ctx = program_env(lambda c: None, demo_provider_set())
    desk = Desktop(REG, lambda: ctx, ctx.settings)
    qtbot.addWidget(desk)
    desk.resize(1200, 800)
    desk.show()
    wins = [desk.open_program(pid) for pid in PROGRAMS]
    qtbot.waitUntil(lambda: all(w.content().is_demo for w in wins), timeout=10000)
    times = []
    try:
        for i in range(6):
            theme = th.DARK if i % 2 == 0 else th.LIGHT
            start = time.perf_counter()
            apply_theme(qapp, theme)
            QApplication.processEvents()
            times.append((time.perf_counter() - start) * 1000)
        assert statistics.median(times[1:]) <= 200, f"theme switch {times}"
        # pinned colours follow: every open chart's background uses the current theme
        apply_theme(qapp, th.DARK)
        QApplication.processEvents()
        assert th.current_theme() is th.DARK
        charts = [c for w in wins for c in w.content().findChildren(BarChart)]
        assert charts, "no BarChart found in the open programs"
        for chart in charts:
            assert chart.backgroundBrush().color().name() == th.DARK.well
        grid = DataGrid([Column("cpu", "CPU")])
        qtbot.addWidget(grid)
        grid.set_rows([{"cpu": 1}])
        grid.set_filter_text("cpu>")                  # an invalid filter expression
        apply_theme(qapp, th.LIGHT)
        apply_theme(qapp, th.DARK)
        assert grid.filter_edit.palette().base().color().name() == th.DARK.banner_bg
        fleet = wins[PROGRAMS.index("fleet")].content()
        item = next(fleet.matrix.item(r, c) for r in range(fleet.matrix.rowCount())
                    for c in range(fleet.matrix.columnCount()) if fleet.matrix.item(r, c))
        assert item.foreground().color().name() in {th.DARK.ok, th.DARK.bad, th.DARK.muted}
    finally:
        apply_theme(qapp, th.LIGHT)


def test_style_has_no_python_stylehint_override():
    from aws95.kit.style import Win95Style
    assert "styleHint" not in Win95Style.__dict__


def test_no_app_wide_stylesheet(qapp):
    apply_theme(qapp, th.DARK)
    try:
        assert qapp.styleSheet() == ""
        assert QApplication.palette().toolTipBase().color().name() == th.DARK.tooltip_bg
    finally:
        apply_theme(qapp, th.LIGHT)


def test_first_open_frame_budget_warm(qapp, qtbot, program_env):
    _, ctx = program_env(lambda c: None, demo_provider_set())
    REG.get("fleet").load_factory()(ctx).deleteLater()      # warm Qt, fonts and icons once
    for pid in PROGRAMS:
        factory = REG.get(pid).load_factory()
        start = time.perf_counter()
        w = factory(ctx)
        qtbot.addWidget(w)
        w.resize(900, 600)
        w.show()
        QApplication.processEvents()
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed <= 150, f"{pid} first frame {elapsed:.0f} ms"


def test_program_windows_stay_square_after_theme_switches(qapp, qtbot, program_env):
    _, ctx = program_env(lambda c: None, demo_provider_set())
    desk = Desktop(REG, lambda: ctx, ctx.settings)
    qtbot.addWidget(desk)
    desk.resize(1200, 800)
    desk.show()
    w = desk.open_program("fleet")
    assert w.mask().isEmpty()
    try:
        for theme in (th.DARK, th.LIGHT, th.DARK):
            apply_theme(qapp, theme)
            QApplication.processEvents()
            assert w.mask().isEmpty(), f"rounded-corner mask returned after {theme}"
    finally:
        apply_theme(qapp, th.LIGHT)
