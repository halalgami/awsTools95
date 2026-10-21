"""Screen Savers (Accessories): pick one of the shell's savers, watch it in a little monitor, start it now."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from aws95.programs.common import ProgramBase
from aws95.programs.display import MonitorPreview
from aws95.shell.screensaver import saver_titles

NAME_ROLE = Qt.ItemDataRole.UserRole
_START_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter)


class ScreenSaversProgram(ProgramBase):
    TITLE = "Screen Savers"
    PROGRAM_MENU = "Sc&reen Savers"     # S belongs to the Start button

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent, scope_bar=False)
        # Nothing here is data: there is nothing to refresh or export.
        self.refresh_action.setEnabled(False)
        self.export_action.setEnabled(False)

        self.saver_list = QListWidget()
        for name, title in saver_titles().items():
            item = QListWidgetItem(title)
            item.setData(NAME_ROLE, name)
            self.saver_list.addItem(item)
        self.saver_list.installEventFilter(self)       # macOS item views never emit activated on Return
        self.saver_list.currentRowChanged.connect(self._on_row)
        list_label = QLabel("Ch&oose a screen saver:")
        list_label.setBuddy(self.saver_list)
        self.monitor = MonitorPreview()
        self.start_button = QPushButton("&Start")
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self.start)
        self.start_action = self.program_menu.addAction("&Start Screen Saver\tEnter")
        self.start_action.triggered.connect(self.start)

        left = QVBoxLayout()
        left.addWidget(list_label)
        left.addWidget(self.saver_list, 1)
        right = QVBoxLayout()
        right.addWidget(self.monitor, 0, Qt.AlignmentFlag.AlignHCenter)
        right.addWidget(self.start_button, 0, Qt.AlignmentFlag.AlignHCenter)
        right.addStretch(1)
        page = QWidget()
        row = QHBoxLayout(page)
        row.addLayout(left, 1)
        row.addLayout(right)
        self.set_content(page)
        self.status.set_counts(f"{self.saver_list.count()} screen savers")
        self.saver_list.setCurrentRow(0)
        self.setFocusProxy(self.saver_list)

    def selected_name(self) -> str | None:
        item = self.saver_list.currentItem()
        return item.data(NAME_ROLE) if item is not None else None

    def select_saver(self, name: str) -> bool:
        for i in range(self.saver_list.count()):
            if self.saver_list.item(i).data(NAME_ROLE) == name:
                self.saver_list.setCurrentRow(i)
                return True
        return False

    def _on_row(self, _row: int) -> None:
        self.monitor.set_saver(self.selected_name())

    def start(self) -> None:
        name = self.selected_name()
        if name is not None:
            self.ctx.shell.preview_screensaver(name)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.saver_list and event.type() == QEvent.Type.KeyPress and event.key() in _START_KEYS \
                and not event.isAutoRepeat():
            self.start()
            return True
        return super().eventFilter(obj, event)


def make_screensavers(ctx) -> ScreenSaversProgram:
    return ScreenSaversProgram(ctx)
