"""Display Properties: Background, Screen Saver (live CRT preview), Appearance, Sounds. Changes apply live."""
from __future__ import annotations

import sys
from functools import partial
from typing import Literal

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QRadioButton, QSlider, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget)

from aws95.core.settings import ThemeMode
from aws95.kit.paint import draw_bevel
from aws95.kit.theme import current_theme
from aws95.shell.context import ProgramContext
from aws95.shell.screensaver import SAVERS, saver_titles
from aws95.shell.sound import DEFAULT_OFF, EVENTS

_WALLPAPERS = (("none", "(None)"), ("dots", "Dots"), ("bricks", "Bricks"))
_TABS = {"background": 0, "screensaver": 1, "appearance": 2, "sounds": 3}


class MonitorPreview(QWidget):
    SCREEN = QRect(22, 18, 136, 98)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(180, 170)
        self.saver = None

    def set_saver(self, name: str | None) -> None:
        if self.saver is not None:
            self.saver.hide()  # stops its timer immediately; deleteLater is deferred
            self.saver.setParent(None)
            self.saver.deleteLater()
            self.saver = None
        if name in SAVERS:
            self.saver = SAVERS[name](self, seed=1)
            self.saver.setGeometry(self.SCREEN)
            self.saver.show()

    def paintEvent(self, event) -> None:  # noqa: N802
        t = current_theme()
        p = QPainter(self)
        draw_bevel(p, QRect(10, 6, 160, 122), t, kind="panel", fill=t.face)
        draw_bevel(p, self.SCREEN.adjusted(-2, -2, 2, 2), t, kind="field", fill="#000000")
        draw_bevel(p, QRect(70, 128, 40, 10), t, kind="panel", fill=t.face)
        draw_bevel(p, QRect(45, 138, 90, 12), t, kind="panel", fill=t.face)
        p.end()


class DisplayProperties(QWidget):
    def __init__(self, ctx: ProgramContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._settings = ctx.settings
        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._background_tab(), "Background")
        self.tabs.addTab(self._saver_tab(), "Screen Saver")
        self.tabs.addTab(self._appearance_tab(), "Appearance")
        self.tabs.addTab(self._sounds_tab(), "Sounds")
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs)
        self._settings.changed.connect(self._sync_from_settings)

    def _sync_from_settings(self, _key: str = "") -> None:
        """Reflect external setting changes (palette, other windows) without echoing them back."""
        s = self._settings
        widgets = (self.wallpaper_list, self.saver_combo, self.timeout_spin, self.font_spin,
                   self.reduce_motion_box, self.native_menubar_box, self.show_splash_box, self.mute_box,
                   self.startup_sound_box, self.volume_slider, self.event_list, *self.theme_buttons.values())
        for w in widgets:
            w.blockSignals(True)
        try:
            self.wallpaper_list.setCurrentRow([k for k, _ in _WALLPAPERS].index(s.wallpaper))
            self.saver_combo.setCurrentIndex(max(0, self.saver_combo.findData(s.saver_choice)))
            self.timeout_spin.setValue(s.saver_timeout_min)
            self.theme_buttons[s.theme_mode].setChecked(True)
            self.font_spin.setValue(s.font_scale)
            self.reduce_motion_box.setChecked(s.reduce_motion)
            self.native_menubar_box.setChecked(s.mac_native_menubar)
            self.show_splash_box.setChecked(not s.skip_splash)
            self.mute_box.setChecked(s.sound_muted)
            self.startup_sound_box.setChecked(s.play_startup_sound)
            self.volume_slider.setValue(s.sound_volume)
            for i in range(self.event_list.count()):
                item = self.event_list.item(i)
                event = item.data(Qt.ItemDataRole.UserRole)
                on = s.sound_event_enabled(event, event not in DEFAULT_OFF)
                item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        finally:
            for w in widgets:
                w.blockSignals(False)
        self._sync_monitor()

    def select_tab(self, name: Literal["background", "screensaver", "appearance", "sounds"]) -> None:
        self.tabs.setCurrentIndex(_TABS[name])

    def apply_navigation(self, params) -> None:
        tab = params.get("tab")
        if tab in _TABS:              # an unknown tab name from a link is ignored, never a KeyError
            self.select_tab(tab)

    # ----- Background -----
    def _background_tab(self) -> QWidget:
        w = QWidget()
        self.wallpaper_list = QListWidget(w)
        for key, label in _WALLPAPERS:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.wallpaper_list.addItem(item)
        current = [k for k, _ in _WALLPAPERS].index(self._settings.wallpaper)
        self.wallpaper_list.setCurrentRow(current)
        self.wallpaper_list.currentRowChanged.connect(self._on_wallpaper)
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Wallpaper:"))
        lay.addWidget(self.wallpaper_list)
        return w

    def _on_wallpaper(self, row: int) -> None:
        if row >= 0:
            self._settings.wallpaper = _WALLPAPERS[row][0]

    # ----- Screen Saver -----
    def _saver_tab(self) -> QWidget:
        w = QWidget()
        self.monitor = MonitorPreview(w)
        self.saver_combo = QComboBox(w)
        self.saver_combo.addItem("(Random)", "random")
        for name, title in saver_titles().items():
            self.saver_combo.addItem(title, name)
        idx = self.saver_combo.findData(self._settings.saver_choice)
        self.saver_combo.setCurrentIndex(max(0, idx))
        self.saver_combo.currentIndexChanged.connect(self._on_saver)
        self.timeout_spin = QSpinBox(w)
        self.timeout_spin.setRange(0, 60)
        self.timeout_spin.setSpecialValueText("None")
        self.timeout_spin.setSuffix(" minutes")
        self.timeout_spin.setValue(self._settings.saver_timeout_min)
        self.timeout_spin.valueChanged.connect(self._on_timeout)
        self.preview_button = QPushButton("Pre&view", w)
        self.preview_button.clicked.connect(self._preview)
        self._sync_monitor()
        form = QFormLayout()
        form.addRow("Screen Saver:", self.saver_combo)
        form.addRow("Wait:", self.timeout_spin)
        lay = QVBoxLayout(w)
        lay.addWidget(self.monitor, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addLayout(form)
        lay.addWidget(self.preview_button, 0, Qt.AlignmentFlag.AlignRight)
        return w

    def _preview(self) -> None:
        self.ctx.shell.preview_screensaver()        # no name: the saver chosen above (clicked's bool never leaks in)

    def _on_saver(self, _index: int) -> None:
        self._settings.saver_choice = self.saver_combo.currentData()
        self._sync_monitor()

    def _sync_monitor(self) -> None:
        choice = self.saver_combo.currentData()
        want = choice if choice in SAVERS else "starfield"
        if self.monitor.saver is not None and self.monitor.saver.name == want:
            return
        self.monitor.set_saver(want)

    def _on_timeout(self, value: int) -> None:
        self._settings.saver_timeout_min = value

    # ----- Appearance -----
    def _appearance_tab(self) -> QWidget:
        w = QWidget()
        group = QButtonGroup(w)
        self.theme_buttons: dict[ThemeMode, QRadioButton] = {}
        row = QHBoxLayout()
        for mode, label in ((ThemeMode.LIGHT, "Classic+ &Light"), (ThemeMode.DARK, "Classic+ &Dark"),
                            (ThemeMode.SYSTEM, "&Follow OS")):
            btn = QRadioButton(label, w)
            btn.setChecked(self._settings.theme_mode is mode)
            btn.clicked.connect(partial(self._set_theme, mode))
            group.addButton(btn)
            row.addWidget(btn)
            self.theme_buttons[mode] = btn
        self.font_spin = QSpinBox(w)
        self.font_spin.setRange(100, 150)
        self.font_spin.setSingleStep(10)
        self.font_spin.setSuffix(" %")
        self.font_spin.setValue(self._settings.font_scale)
        self.font_spin.valueChanged.connect(self._on_font)
        self.reduce_motion_box = QCheckBox("&Reduce motion", w)
        self.reduce_motion_box.setChecked(self._settings.reduce_motion)
        self.reduce_motion_box.toggled.connect(self._on_reduce_motion)
        self.native_menubar_box = QCheckBox("Use native macOS menu bar (restart required)", w)
        self.native_menubar_box.setChecked(self._settings.mac_native_menubar)
        self.native_menubar_box.setEnabled(sys.platform == "darwin")
        self.native_menubar_box.toggled.connect(self._on_native_menubar)
        self.show_splash_box = QCheckBox("Show &splash screen at startup", w)
        self.show_splash_box.setChecked(not self._settings.skip_splash)
        self.show_splash_box.toggled.connect(self._on_show_splash)
        form = QFormLayout(w)
        form.addRow("Scheme:", row)
        form.addRow("Font size:", self.font_spin)
        form.addRow(self.reduce_motion_box)
        form.addRow(self.native_menubar_box)
        form.addRow(self.show_splash_box)
        return w

    def _set_theme(self, mode: ThemeMode, _checked: bool = False) -> None:
        self._settings.theme_mode = mode

    def _on_font(self, value: int) -> None:
        self._settings.font_scale = value

    def _on_reduce_motion(self, on: bool) -> None:
        self._settings.reduce_motion = on

    def _on_native_menubar(self, on: bool) -> None:
        self._settings.mac_native_menubar = on

    def _on_show_splash(self, on: bool) -> None:
        self._settings.skip_splash = not on

    # ----- Sounds -----
    def _sounds_tab(self) -> QWidget:
        w = QWidget()
        self.mute_box = QCheckBox("&Mute all sounds", w)
        self.mute_box.setChecked(self._settings.sound_muted)
        self.mute_box.toggled.connect(self._on_mute)
        self.startup_sound_box = QCheckBox("Play s&tartup sound", w)
        self.startup_sound_box.setChecked(self._settings.play_startup_sound)
        self.startup_sound_box.toggled.connect(self._on_startup_sound)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal, w)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self._settings.sound_volume)
        self.volume_slider.valueChanged.connect(self._on_volume)
        self.event_list = QListWidget(w)
        for event in EVENTS:
            item = QListWidgetItem(event.replace("_", " ").title())
            item.setData(Qt.ItemDataRole.UserRole, event)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            on = self._settings.sound_event_enabled(event, event not in DEFAULT_OFF)
            item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            self.event_list.addItem(item)
        self.event_list.itemChanged.connect(self._on_event_toggled)
        self.sound_preview_button = QPushButton("&Preview", w)
        self.sound_preview_button.clicked.connect(self._preview_sound)
        form = QFormLayout()
        form.addRow(self.mute_box)
        form.addRow(self.startup_sound_box)
        form.addRow("Volume:", self.volume_slider)
        lay = QVBoxLayout(w)
        lay.addLayout(form)
        lay.addWidget(QLabel("Events:"))
        lay.addWidget(self.event_list)
        lay.addWidget(self.sound_preview_button, 0, Qt.AlignmentFlag.AlignRight)
        return w

    def _on_mute(self, on: bool) -> None:
        self._settings.sound_muted = on

    def _on_startup_sound(self, on: bool) -> None:
        self._settings.play_startup_sound = on

    def _on_volume(self, value: int) -> None:
        self._settings.sound_volume = value

    def _on_event_toggled(self, item: QListWidgetItem) -> None:
        self._settings.set_sound_event_enabled(item.data(Qt.ItemDataRole.UserRole),
                                               item.checkState() == Qt.CheckState.Checked)

    def _preview_sound(self) -> None:
        item = self.event_list.currentItem()
        if item is not None:
            self.ctx.sound.preview(item.data(Qt.ItemDataRole.UserRole))


def make_display(ctx: ProgramContext) -> DisplayProperties:
    return DisplayProperties(ctx)
