"""DataGrid: QTableView over a list-of-mappings model with in-model filtering and sorting."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import partial
from typing import Any, Literal

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QLineEdit, QMenu, QTableView, QVBoxLayout, QWidget

from aws95.kit.theme import current_theme, theme_bus
from aws95.kit.widgets.filter_syntax import FilterError, haystack, parse

Kind = Literal["text", "number", "money", "date", "status"]
ROW_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_RIGHT = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
_LEFT = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


FIT_PADDING = 6     # px added to size-to-contents columns: Qt's own hint leaves bold/sorted cells tight


@dataclass(frozen=True)
class Column:
    key: str
    title: str
    kind: Kind = "text"
    width: int | None = None        # pixels; -1 sizes the column to its contents after each set_rows


def format_value(value: Any, kind: Kind) -> str:
    if value is None:
        return ""
    if kind == "money":
        if isinstance(value, Decimal):
            if not value.is_finite():
                return str(value)
            # Never round a financial figure: 2 dp only when that is exact, else full digits.
            if value.as_tuple().exponent >= -2:
                return f"{value:,.2f}"
            return format(value, ",f")
        if isinstance(value, int) and not isinstance(value, bool):
            return f"{value:,.2f}"
    if kind == "number" and isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    if kind == "number" and isinstance(value, Decimal) and value.is_finite():
        return format(value, ",f")    # exact, never scientific
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return str(value)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, Decimal):
        return value.is_nan()  # NaN and sNaN; never compare a Decimal that may be sNaN
    if isinstance(value, float):
        return value != value  # NaN
    return False


def _sort_key(value: Any) -> tuple[int, Any]:
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return (0, value)
    if isinstance(value, str):
        return (1, value.lower())
    if isinstance(value, datetime):
        return (2, value.timestamp())
    return (3, str(value))


class RowTableModel(QAbstractTableModel):
    FETCH_BATCH = 5000
    filter_error = Signal(str)
    filter_applied = Signal(int, int)

    def __init__(self, columns: Sequence[Column], parent=None) -> None:
        super().__init__(parent)
        self._columns = tuple(columns)
        self._rows: list[Mapping[str, Any]] = []
        self._hay: list[str] | None = None
        self._fields: set[str] = {c.key for c in self._columns}
        self._query = parse("")
        self._view: list[int] = []
        self._loaded = 0
        self._sort: tuple[int, Qt.SortOrder] | None = None

    # ----- public API -----
    @property
    def total_count(self) -> int:
        return len(self._rows)

    @property
    def shown_count(self) -> int:
        return len(self._view)

    def row_at(self, view_row: int) -> Mapping[str, Any]:
        return self._rows[self._view[view_row]]

    @property
    def columns(self) -> tuple[Column, ...]:
        return self._columns

    def view_rows(self) -> list[Mapping[str, Any]]:
        """Every row that passes the filter, in the current sort order (not just the fetched batch)."""
        rows = self._rows
        return [rows[i] for i in self._view]

    def set_rows(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self.beginResetModel()
        try:
            self._rows = list(rows)
            self._hay = None
            self._fields = {c.key for c in self._columns} | (set(self._rows[0].keys()) if self._rows else set())
            self._recompute()
        finally:
            self.endResetModel()
        self.filter_applied.emit(len(self._view), len(self._rows))

    def set_filter(self, text: str) -> bool:
        try:
            query = parse(text, self._fields)
        except FilterError as exc:
            self.filter_error.emit(str(exc))
            return False
        self.beginResetModel()
        try:
            self._query = query
            self._recompute()
        finally:
            self.endResetModel()
        self.filter_applied.emit(len(self._view), len(self._rows))
        return True

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:  # noqa: A003
        if not 0 <= column < len(self._columns):
            return
        self.beginResetModel()
        try:
            self._sort = (column, order)
            self._apply_sort()
            self._loaded = min(len(self._view), self.FETCH_BATCH)
        finally:
            self.endResetModel()

    # ----- internals -----
    def _recompute(self) -> None:
        q = self._query
        if q.is_empty:
            view = list(range(len(self._rows)))
        else:
            if q.needs_haystack and self._hay is None:
                self._hay = [haystack(r) for r in self._rows]
            hay = self._hay
            match = q.matches
            if hay is None:
                view = [i for i, r in enumerate(self._rows) if match(r)]
            else:
                view = [i for i, r in enumerate(self._rows) if match(r, hay[i])]
        self._view = view
        if self._sort is not None:
            self._apply_sort()
        self._loaded = min(len(self._view), self.FETCH_BATCH)

    def _apply_sort(self) -> None:
        column, order = self._sort  # type: ignore[misc]
        key = self._columns[column].key
        rows = self._rows
        present = [i for i in self._view if not _is_missing(rows[i].get(key))]
        missing = [i for i in self._view if _is_missing(rows[i].get(key))]
        present.sort(key=lambda i: _sort_key(rows[i][key]), reverse=order == Qt.SortOrder.DescendingOrder)
        self._view = present + missing

    # ----- Qt model API -----
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else self._loaded

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._columns)

    def canFetchMore(self, parent: QModelIndex = QModelIndex()) -> bool:  # noqa: N802
        return not parent.isValid() and self._loaded < len(self._view)

    def fetchMore(self, parent: QModelIndex = QModelIndex()) -> None:  # noqa: N802
        if parent.isValid():
            return
        n = min(self.FETCH_BATCH, len(self._view) - self._loaded)
        if n <= 0:
            return
        self.beginInsertRows(QModelIndex(), self._loaded, self._loaded + n - 1)
        try:
            self._loaded += n
        finally:
            self.endInsertRows()

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[self._view[index.row()]]
        col = self._columns[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return format_value(row.get(col.key), col.kind)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _RIGHT if col.kind in ("number", "money") else _LEFT
        if role == Qt.ItemDataRole.ToolTipRole and col.kind == "money":
            value = row.get(col.key)
            if value is None:
                return None
            return format(value, "f") if isinstance(value, Decimal) and value.is_finite() else str(value)
        if role == ROW_ROLE:
            return row
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._columns[section].title
        return None


class DataGrid(QWidget):
    row_activated = Signal(object)
    current_row_changed = Signal(object)

    def __init__(self, columns: Sequence[Column], parent: QWidget | None = None,
                 placeholder: str = "Filter…  e.g. state:stopped region:eu-*") -> None:
        super().__init__(parent)
        self.model = RowTableModel(columns, self)
        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText(placeholder)
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setProperty("invalid", False)

        self.view = QTableView(self)
        self.view.setModel(self.model)
        self.view.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.view.setSortingEnabled(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setAlternatingRowColors(True)
        self.view.setShowGrid(False)
        self.view.setWordWrap(False)
        self.view.verticalHeader().setVisible(False)
        vh = self.view.verticalHeader()
        vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        vh.setDefaultSectionSize(self.fontMetrics().height() + 6)
        self.view.horizontalHeader().setStretchLastSection(True)
        for i, col in enumerate(columns):
            if col.width and col.width > 0:
                self.view.setColumnWidth(i, col.width)

        header = self.view.horizontalHeader()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_column_menu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(self.filter_edit)
        layout.addWidget(self.view)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(60)
        self._debounce.timeout.connect(self._apply_filter)
        self.filter_edit.textChanged.connect(self._on_text_changed)
        self.model.filter_error.connect(self._mark_invalid)
        theme_bus().changed.connect(self._on_theme_changed)
        self.model.filter_applied.connect(self._on_filter_applied)
        self.view.activated.connect(self._on_activated)
        self.view.installEventFilter(self)      # macOS item views never emit activated on Return
        self.view.selectionModel().currentRowChanged.connect(self._on_current)

    @property
    def columns(self) -> tuple[Column, ...]:
        return self.model.columns

    def _column_index(self, key: str) -> int:
        for i, col in enumerate(self.model.columns):
            if col.key == key:
                return i
        raise KeyError(key)

    def column_visible(self, key: str) -> bool:
        return not self.view.isColumnHidden(self._column_index(key))

    def set_column_visible(self, key: str, visible: bool) -> None:
        self.view.setColumnHidden(self._column_index(key), not visible)

    def visible_columns(self) -> tuple[Column, ...]:
        return tuple(c for i, c in enumerate(self.model.columns) if not self.view.isColumnHidden(i))

    def visible_rows(self) -> list[Mapping[str, Any]]:
        return self.model.view_rows()

    def current_row(self) -> Mapping[str, Any] | None:
        index = self.view.currentIndex()
        return self.model.row_at(index.row()) if index.isValid() else None

    def build_column_menu(self, parent: QWidget | None = None) -> QMenu:
        menu = QMenu(parent or self)
        visible = self.visible_columns()
        for col in self.model.columns:
            action = menu.addAction(col.title)
            action.setCheckable(True)
            shown = col in visible
            action.setChecked(shown)
            action.setEnabled(not (shown and len(visible) == 1))
            action.toggled.connect(partial(self.set_column_visible, col.key))
        return menu

    def _show_column_menu(self, pos: QPoint) -> None:
        menu = self.build_column_menu()
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup(self.view.horizontalHeader().mapToGlobal(pos))

    def set_rows(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self.model.set_rows(rows)
        self.model.set_filter(self.filter_edit.text())
        for i, col in enumerate(self.model.columns):
            if col.width == -1:
                self.view.resizeColumnToContents(i)
                self.view.setColumnWidth(i, self.view.columnWidth(i) + FIT_PADDING)

    def set_filter_text(self, text: str) -> None:
        self.filter_edit.blockSignals(True)
        self.filter_edit.setText(text)
        self.filter_edit.blockSignals(False)
        self._debounce.stop()
        self._apply_filter()

    def _apply_filter(self) -> None:
        self.model.set_filter(self.filter_edit.text())

    def _on_text_changed(self, _text: str) -> None:
        self._debounce.start()

    def _on_filter_applied(self, _shown: int, _total: int) -> None:
        self._mark_valid()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if (obj is self.view and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier)):
            index = self.view.currentIndex()
            if not index.isValid():
                return False    # nothing to activate: pass Return on (a default button, a parent dialog)
            if not event.isAutoRepeat():
                self._on_activated(index)
            return True         # handled here, so platforms that also emit activated do not fire twice
        return super().eventFilter(obj, event)

    def _on_activated(self, index: QModelIndex) -> None:
        if index.isValid():
            self.row_activated.emit(self.model.row_at(index.row()))

    def _mark_invalid(self, message: str) -> None:
        self.filter_edit.setProperty("invalid", True)
        self.filter_edit.setToolTip(message)
        pal = self.filter_edit.palette()
        pal.setColor(QPalette.ColorRole.Base, QColor(current_theme().banner_bg))
        self.filter_edit.setPalette(pal)

    def _on_theme_changed(self, _theme) -> None:
        if self.filter_edit.property("invalid"):
            pal = self.filter_edit.palette()
            pal.setColor(QPalette.ColorRole.Base, QColor(current_theme().banner_bg))
            self.filter_edit.setPalette(pal)

    def _mark_valid(self) -> None:
        self.filter_edit.setProperty("invalid", False)
        self.filter_edit.setToolTip("")
        self.filter_edit.setPalette(QPalette())

    def _on_current(self, current: QModelIndex, _previous: QModelIndex) -> None:
        self.current_row_changed.emit(self.model.row_at(current.row()) if current.isValid() else None)
