"""Recycle Bin: programs closed this session, restorable (whimsy, spec §6.2)."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from PySide6.QtCore import QObject, Signal

from aws95.shell.context import RecycledItem


class RecycleBinStore(QObject):
    changed = Signal()

    def __init__(self, open_program: Callable[[str], object], limit: int = 20, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._open = open_program
        self._limit = limit
        self._items: list[RecycledItem] = []

    def add(self, program_id: str, title: str) -> None:
        self._items.insert(0, RecycledItem(program_id, title, datetime.now()))
        del self._items[self._limit:]
        self.changed.emit()

    def items(self) -> tuple[RecycledItem, ...]:
        return tuple(self._items)

    def restore(self, index: int) -> None:
        if not 0 <= index < len(self._items):
            return
        item = self._items.pop(index)
        self.changed.emit()
        self._open(item.program_id)

    def empty(self) -> None:
        self._items.clear()
        self.changed.emit()
