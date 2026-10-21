from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QLabel, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from aws95.kit.widgets.jsonview import JsonView
from aws95.kit.widgets.stateview import StateView


def _safe_str(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        try:
            return repr(value)
        except Exception:
            return f"<unrepresentable object: {type(value).__name__}>"


def _cell(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, default=str, ensure_ascii=False)
        except Exception:
            return _safe_str(value)
    return "" if value is None else _safe_str(value)


def _kv_table(parent: QWidget) -> QTableWidget:
    table = QTableWidget(0, 2, parent)
    table.setHorizontalHeaderLabels(["Name", "Value"])
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)
    return table


def _fill(table: QTableWidget, data: Mapping[str, Any]) -> None:
    table.setRowCount(0)
    for key in sorted(data, key=_safe_str):
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(_safe_str(key)))
        table.setItem(row, 1, QTableWidgetItem(_cell(data[key])))


def _plain_label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)  # provider/AWS-sourced text must never render as rich text
    return label


class Inspector(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title_label = _plain_label("No selection", self)
        f = QFont(self.title_label.font())
        f.setBold(True)
        self.title_label.setFont(f)
        self.subtitle_label = _plain_label("", self)
        self.tabs = QTabWidget(self)
        self.attrs_table = _kv_table(self)
        self.tags_table = _kv_table(self)
        self.json_view = JsonView(self)
        self.related_view = StateView(QLabel(""), self)
        self.related_view.show_not_connected("Nothing related to show.")
        self.tabs.addTab(self.attrs_table, "Attributes")
        self.tabs.addTab(self.tags_table, "Tags")
        self.tabs.addTab(self.json_view, "Raw JSON")
        self.tabs.addTab(self.related_view, "Related")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.title_label)
        layout.addWidget(self.subtitle_label)
        layout.addWidget(self.tabs, 1)

    def set_related(self, widget: QWidget) -> None:
        """A program supplies the Related tab's content (the kit stays provider-free)."""
        index = self.tabs.indexOf(self.related_view)
        old = self.related_view
        self.tabs.removeTab(index)
        old.hide()
        old.deleteLater()
        self.related_view = widget
        self.tabs.insertTab(index, widget, "Related")

    def set_item(self, title: str, subtitle: str, attrs: Mapping[str, Any], tags: Mapping[str, Any],
                 raw: Any = None) -> None:
        self.title_label.setText(title)
        self.subtitle_label.setText(subtitle)
        _fill(self.attrs_table, attrs)
        _fill(self.tags_table, tags)
        self.json_view.set_data(raw if raw is not None else dict(attrs))

    def clear(self) -> None:
        self.title_label.setText("No selection")
        self.subtitle_label.setText("")
        self.attrs_table.setRowCount(0)
        self.tags_table.setRowCount(0)
        self.json_view.clear()
