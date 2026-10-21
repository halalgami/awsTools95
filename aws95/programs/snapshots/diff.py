"""Diff window: Added / Removed / Changed / Excluded, and Excluded always says why (spec §7)."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QFormLayout, QTabWidget, QVBoxLayout, QWidget

from aws95.contracts.common import Scope
from aws95.contracts.snapshots import DiffEntry, Snapshot
from aws95.kit.widgets.datagrid import Column, DataGrid
from aws95.kit.widgets.stateview import StateView

KINDS = (("added", "Added"), ("removed", "Removed"), ("changed", "Changed"), ("excluded", "Excluded"))
BASE = [Column("badge", "", width=28), Column("rtype", "Type"), Column("region", "Region"), Column("rid", "ID")]
EXTRA = {"changed": [Column("deltas", "Changes")], "excluded": [Column("reason", "Reason")]}
NO_REASON = "reason not reported"


def delta_text(deltas: Mapping[str, Any]) -> str:
    parts = []
    for field, change in deltas.items():
        if isinstance(change, (list, tuple)) and len(change) == 2:
            parts.append(f"{field}: {change[0]} → {change[1]}")
        else:
            parts.append(f"{field}: {change}")
    return "; ".join(parts)


def _label(s: Snapshot) -> str:
    return f"#{s.id}  {s.taken_at.strftime('%Y-%m-%d %H:%M')}  ({s.status})"


class DiffDialog(QDialog):
    def __init__(self, program, snapshots: Sequence[Snapshot], a_id: int, b_id: int,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Compare snapshots")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._program = program
        self._taken = {s.id: s.taken_at for s in snapshots}
        self.from_combo, self.to_combo = QComboBox(self), QComboBox(self)
        for combo in (self.from_combo, self.to_combo):
            for s in snapshots:
                combo.addItem(_label(s), s.id)
        self.from_combo.setCurrentIndex(self.from_combo.findData(a_id))
        self.to_combo.setCurrentIndex(self.to_combo.findData(b_id))
        form = QFormLayout()
        form.addRow("&From:", self.from_combo)
        form.addRow("&To:", self.to_combo)

        self.tabs = QTabWidget()
        self.grids: dict[str, DataGrid] = {}
        for kind, title in KINDS:
            grid = DataGrid([*BASE, *EXTRA.get(kind, [])])
            self.grids[kind] = grid
            self.tabs.addTab(grid, title)
        self.view = StateView(self.tabs)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.view, 1)
        self.resize(760, 460)
        self.from_combo.currentIndexChanged.connect(self.reload)
        self.to_combo.currentIndexChanged.connect(self.reload)
        self.reload()

    def _clear(self) -> None:
        for i, (kind, title) in enumerate(KINDS):
            self.grids[kind].set_rows([])
            self.tabs.setTabText(i, title)

    def reload(self, *_args) -> None:
        self._clear()                                   # never leave the previous pair's rows on screen
        a, b = self.from_combo.currentData(), self.to_combo.currentData()
        if a is None or b is None or not self._taken[a] < self._taken[b]:
            self._program.invalidate(self.view)          # an in-flight diff must not land on this message
            self.view.show_empty("Pick an earlier snapshot in From than in To.")
            return
        self._program.load(self.view, self._program.ctx.providers.snapshots.diff, Scope(), a, b,
                           render=self._render, is_empty=_never_empty, status=False)

    def _render(self, entries: tuple[DiffEntry, ...]) -> None:
        built = {kind: [{"badge": "⚠" if e.partial else "", "rtype": e.rtype, "region": e.region, "rid": e.rid,
                         "deltas": delta_text(e.deltas), "reason": (e.reason or "").strip() or NO_REASON,
                         "entry": e} for e in entries if e.kind == kind] for kind, _ in KINDS}
        for i, (kind, title) in enumerate(KINDS):
            self.grids[kind].set_rows(built[kind])
            self.tabs.setTabText(i, f"{title} ({len(built[kind])})")

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.activateWindow()
        self.from_combo.setFocus()


def _never_empty(_data) -> bool:
    return False          # an empty diff is the answer: four "(0)" tabs
