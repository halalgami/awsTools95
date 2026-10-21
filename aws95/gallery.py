"""Kit gallery: every M1 widget on one page, for visual QA in Light and Dark. Demo data is watermarked."""
from __future__ import annotations

import random
from functools import partial
from datetime import datetime, timezone
from decimal import Decimal

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMainWindow, QProgressBar, QPushButton, QRadioButton,
                               QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from aws95.core.settings import Settings, ThemeMode
from aws95.core.tasks import TaskRunner
from aws95.providers.base import ProviderSet
from aws95.kit.widgets.balloon import Balloon
from aws95.kit.widgets.bevel import BevelPanel
from aws95.kit.widgets.buttons import Chip
from aws95.kit.widgets.charts import BarChart, LineChart
from aws95.kit.widgets.datagrid import Column, DataGrid
from aws95.kit.widgets.dialogs import Icon, MessageBox95
from aws95.kit.widgets.inspector import Inspector
from aws95.kit.widgets.progress import BlockProgress
from aws95.kit.widgets.stateview import StateView
from aws95.kit.widgets.watermark import attach_watermark

GRID_COLUMNS = [Column("name", "Name", width=160), Column("rtype", "Type", width=120),
                Column("region", "Region", width=100), Column("state", "State", "status", width=90),
                Column("cpu", "vCPU", "number", width=60), Column("cost", "Demo cost (USD)", "money")]
_STATES = ("running", "stopped", "pending", None)
_REGIONS = ("us-east-1", "us-west-2", "eu-west-1", "me-south-1", "ap-southeast-1")
_TYPES = ("ec2.instance", "s3.bucket", "lambda.function", "rds.instance")


def synthetic_rows(n: int, seed: int = 95) -> list[dict]:
    rng = random.Random(seed)
    return [{"name": f"demo-{i:06d}", "rtype": rng.choice(_TYPES), "region": rng.choice(_REGIONS),
             "state": rng.choice(_STATES), "cpu": rng.choice((1, 2, 4, 8, 16)),
             "cost": Decimal(rng.randint(0, 50_000)) / Decimal(100)} for i in range(n)]


def _group(title: str, widget: QWidget) -> QGroupBox:
    box = QGroupBox(title)
    lay = QVBoxLayout(box)
    lay.addWidget(widget)
    return box


class KitGallery(QMainWindow):
    def __init__(self, settings: Settings, runner: TaskRunner, providers: ProviderSet) -> None:
        super().__init__()
        self.setWindowTitle("AWS 95 Tools — Kit Gallery (M1)")
        self._settings = settings
        self._runner = runner
        self._providers = providers

        page = QWidget()
        grid = QGridLayout(page)
        grid.addWidget(self._theme_bar(), 0, 0, 1, 2)
        grid.addWidget(_group("Buttons && chips", self._buttons()), 1, 0)
        grid.addWidget(_group("Bevels", self._bevels()), 1, 1)
        grid.addWidget(_group("Inputs", self._inputs()), 2, 0)
        grid.addWidget(_group("Progress", self._progress()), 2, 1)
        grid.addWidget(_group("DataGrid — 100,000 demo rows" if providers.is_demo else "DataGrid", self._grid()), 3, 0)
        grid.addWidget(_group("Inspector", self._inspector()), 3, 1)
        grid.addWidget(_group("States", self._states()), 4, 0)
        grid.addWidget(_group("Charts (demo)", self._charts()), 4, 1)
        grid.addWidget(_group("Dialogs && balloon", self._dialogs()), 5, 0, 1, 2)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.setCentralWidget(scroll)
        self._load_rows()

    # ----- sections -----
    def _theme_bar(self) -> QWidget:
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.addWidget(QLabel("Theme:"))
        group = QButtonGroup(bar)
        self.theme_buttons: dict[ThemeMode, QRadioButton] = {}
        for mode, label in ((ThemeMode.LIGHT, "☀ Light"), (ThemeMode.DARK, "☾ Dark"), (ThemeMode.SYSTEM, "Follow OS")):
            btn = QRadioButton(label)
            btn.setChecked(self._settings.theme_mode is mode)
            btn.clicked.connect(partial(self._set_theme, mode))
            group.addButton(btn)
            lay.addWidget(btn)
            self.theme_buttons[mode] = btn
        lay.addSpacing(20)
        lay.addWidget(QLabel("Font scale %:"))
        scale = QSpinBox()
        scale.setRange(100, 150)
        scale.setSingleStep(10)
        scale.setValue(self._settings.font_scale)
        scale.valueChanged.connect(self._set_font_scale)
        lay.addWidget(scale)
        lay.addStretch(1)
        lay.addWidget(QLabel("Providers: " + ("DEMO" if self._providers.is_demo else "Empty (not connected)")))
        return bar

    def _buttons(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        ok = QPushButton("OK")
        ok.setDefault(True)
        disabled = QPushButton("Disabled")
        disabled.setEnabled(False)
        toggle = QPushButton("Toggle")
        toggle.setCheckable(True)
        for b in (ok, QPushButton("Cancel"), disabled, toggle):
            lay.addWidget(b)
        for text, color in (("lab-sandbox", "#ffd34d"), ("dev-shared", "#7ec8ff"), ("prod", "#b00000"), ("All accounts", "rainbow")):
            lay.addWidget(Chip(text, color))
        return w

    def _bevels(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        for look in ("raised", "sunken", "well"):
            panel = BevelPanel(look)
            panel.setMinimumSize(90, 50)
            inner = QVBoxLayout(panel)
            inner.addWidget(QLabel(look))
            lay.addWidget(panel)
        return w

    def _inputs(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        edit = QLineEdit()
        edit.setPlaceholderText("Type here…")
        combo = QComboBox()
        combo.addItems(["us-east-1", "eu-west-1", "me-south-1"])
        row = QHBoxLayout()
        row.addWidget(QCheckBox("Human only"))
        row.addWidget(QRadioButton("Open"))
        row.addWidget(QRadioButton("Resolved"))
        tabs = QTabWidget()
        tabs.addTab(QLabel("Overview tab"), "Overview")
        tabs.addTab(QLabel("Timeline tab"), "Timeline")
        lay.addWidget(edit)
        lay.addWidget(combo)
        lay.addLayout(row)
        lay.addWidget(tabs)
        return w

    def _progress(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        determinate = BlockProgress()
        determinate.set_value(65)
        marquee = BlockProgress()
        marquee.set_indeterminate(True)
        qbar = QProgressBar()
        qbar.setValue(40)
        qbar.setTextVisible(False)
        for x in (determinate, marquee, qbar):
            lay.addWidget(x)
        return w

    def _grid(self) -> QWidget:
        self.grid = DataGrid(GRID_COLUMNS)
        self.grid.setMinimumHeight(320)
        self.grid_state = StateView(self.grid)
        if self._providers.is_demo:
            attach_watermark(self.grid)
        return self.grid_state

    def _inspector(self) -> QWidget:
        self.inspector = Inspector()
        if self._providers.is_demo:
            attach_watermark(self.inspector)
        self.grid.current_row_changed.connect(self._show_row)
        return self.inspector

    def _states(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        demo = StateView(QLabel("Content goes here"))
        demo.setMinimumHeight(160)
        buttons = QHBoxLayout()
        actions = (("Loading", "show_loading", ()),
                   ("Content", "show_content", ()),
                   ("Partial", "show_content", ("2 of 3 regions covered · 14 attributes denied",)),
                   ("Empty", "show_empty", ("No stopped instances in this scope.",)),
                   ("Not connected", "show_not_connected", ("Findings appear after the first snapshot.",)),
                   ("Error", "show_error", ("AccessDenied on ce:GetCostAndUsage", "Traceback (demo)")))
        for label, method, args in actions:
            b = QPushButton(label)
            b.clicked.connect(partial(self._run_state, demo, method, args))
            buttons.addWidget(b)
        lay.addLayout(buttons)
        lay.addWidget(demo)
        return w

    def _charts(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.bar_chart = BarChart()
        self.line_chart = LineChart()
        if self._providers.is_demo:  # demo data only behind --demo / AWS95_DEMO=1
            self.bar_chart.set_data(["EC2", "S3", "Lambda", "RDS", "CE"], [Decimal("120.50"), Decimal("17.47"),
                                                                         Decimal("4.12"), Decimal("88.00"), Decimal("0.30")])
            self.line_chart.set_series([(datetime(2026, m, 1, tzinfo=timezone.utc), Decimal(100 + m * 7)) for m in range(1, 13)])
        for c in (self.bar_chart, self.line_chart):
            c.setMinimumHeight(140)
            lay.addWidget(c)
        self.charts_container = w
        if self._providers.is_demo:
            attach_watermark(w)
        return w

    def _dialogs(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        for label, icon in (("Info…", Icon.INFO), ("Warning…", Icon.WARNING), ("Error…", Icon.ERROR), ("Question…", Icon.QUESTION)):
            b = QPushButton(label)
            b.clicked.connect(partial(self._show_dialog, label, icon))
            lay.addWidget(b)
        balloon_btn = QPushButton("Show balloon")
        balloon_btn.clicked.connect(self._balloon)
        lay.addWidget(balloon_btn)
        lay.addStretch(1)
        return w

    # ----- behaviour -----
    def _load_rows(self) -> None:
        if not self._providers.is_demo:
            self.grid_state.show_not_connected("Demo rows appear when started with --demo.")
            return
        self.grid_state.show_loading("Generating 100,000 demo rows…")
        handle = self._runner.submit(synthetic_rows, 100_000)
        handle.finished.connect(self._rows_ready)
        handle.failed.connect(self._rows_failed)

    def _rows_failed(self, message: str, details: str) -> None:
        self.grid_state.show_error(message, details)

    def _set_theme(self, mode: ThemeMode, _checked: bool = False) -> None:
        self._settings.theme_mode = mode

    def _set_font_scale(self, value: int) -> None:
        self._settings.font_scale = value

    @staticmethod
    def _run_state(demo: StateView, method: str, args: tuple, _checked: bool = False) -> None:
        getattr(demo, method)(*args)

    def _show_dialog(self, label: str, icon: Icon, _checked: bool = False) -> None:
        name = label.rstrip("…")
        MessageBox95(self, name, f"This is a {name.lower()} message.", icon,
                     ("Yes", "No") if icon is Icon.QUESTION else ("OK",),
                     details="Details (demo)" if icon is Icon.ERROR else None).exec()

    def _rows_ready(self, rows: list[dict]) -> None:
        self.grid.set_rows(rows)
        self.grid_state.show_content()

    def _show_row(self, row: dict | None) -> None:
        if row is None:
            self.inspector.clear()
            return
        self.inspector.set_item(row["name"], f"{row['rtype']} · {row['region']}", row, {"source": "demo"})

    def _balloon(self) -> None:
        b = Balloon("Refresh complete", "lab-sandbox: demo refresh finished. 1 region unreachable.", parent=self)
        b.show_at(self.mapToGlobal(QPoint(self.width() - 20, self.height() - 20)))
