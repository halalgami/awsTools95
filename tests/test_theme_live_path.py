from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLabel
from aws95.kit.qss import build_qss

from aws95.contracts.common import Result, Status
from aws95.core.settings import Settings, ThemeMode
from aws95.kit import theme as th
from aws95.kit.style import install_theming
from aws95.kit.widgets.charts import BarChart
from aws95.kit.widgets.datagrid import Column, DataGrid
from aws95.kit.widgets.stateview import StateView


def test_live_switch_restyles_after_app_style(qtbot, qapp, tmp_path):
    settings = Settings(QSettings(str(tmp_path / "live.ini"), QSettings.Format.IniFormat))
    settings.theme_mode = ThemeMode.LIGHT
    manager = th.ThemeManager(settings, qapp)
    install_theming(qapp, manager, settings)

    sv = StateView(QLabel("x"))
    qtbot.addWidget(sv)
    sv.apply_result(Result(Status.PARTIAL, [1], message="eu-west-1 timed out"), lambda d: None)
    grid = DataGrid([Column("cpu", "CPU")])
    qtbot.addWidget(grid)
    grid.set_rows([{"cpu": 1}])
    grid.set_filter_text("cpu>")
    chart = BarChart()
    qtbot.addWidget(chart)

    seen = []

    def on_bus(theme):
        # qapp.style() is a QStyleSheetStyle wrapper whose Win95Style base is not reachable from
        # Python, so use the app palette/stylesheet (set right after setStyle) as the restyle witness.
        restyled = (qapp.palette().window().color().name() == theme.face
                    and qapp.styleSheet() == build_qss(theme))
        seen.append((theme, restyled))

    th.theme_bus().changed.connect(on_bus)
    try:
        for mode, t in ((ThemeMode.DARK, th.DARK), (ThemeMode.LIGHT, th.LIGHT)):
            settings.theme_mode = mode
            assert sv._banner.palette().window().color().name() == t.banner_bg
            assert grid.filter_edit.palette().base().color().name() == t.banner_bg
            assert chart.backgroundBrush().color().name() == t.well
            assert seen[-1] == (t, True)  # last emit came after the app style was the new theme
    finally:
        th.theme_bus().changed.disconnect(on_bus)
