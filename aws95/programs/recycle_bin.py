"""Recycle Bin: windows closed this session; restore reopens the program."""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QListWidget, QPushButton, QVBoxLayout, QWidget

from aws95.kit.icons import icon
from aws95.shell.context import ProgramContext


class RecycleBin(QWidget):
    def __init__(self, ctx: ProgramContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bin = ctx.shell.recycle_bin()
        self.list_widget = QListWidget(self)
        self.restore_button = QPushButton("&Restore", self)
        self.empty_button = QPushButton("&Empty Recycle Bin", self)
        self.restore_button.clicked.connect(self._restore)
        self.empty_button.clicked.connect(self._bin.empty)
        self.list_widget.currentRowChanged.connect(self._sync_buttons)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.restore_button)
        buttons.addWidget(self.empty_button)
        lay = QVBoxLayout(self)
        lay.addWidget(self.list_widget)
        lay.addLayout(buttons)
        self._bin.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self.list_widget.clear()
        for item in self._bin.items():
            self.list_widget.addItem(f"{item.title}    (closed {item.closed_at:%H:%M})")
            self.list_widget.item(self.list_widget.count() - 1).setIcon(icon("window"))
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        self._sync_buttons()

    def _sync_buttons(self, *_args) -> None:
        has = self.list_widget.count() > 0
        self.restore_button.setEnabled(has and self.list_widget.currentRow() >= 0)
        self.empty_button.setEnabled(has)

    def _restore(self) -> None:
        row = self.list_widget.currentRow()
        if row >= 0:
            self._bin.restore(row)


def make_recycle_bin(ctx: ProgramContext) -> RecycleBin:
    return RecycleBin(ctx)
