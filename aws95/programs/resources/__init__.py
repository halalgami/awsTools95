"""Resources: types by domain, filterable grid, inspector (spec §7)."""
from __future__ import annotations

from collections.abc import Mapping
from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QTreeWidget, QTreeWidgetItem, QWidget

from aws95.contracts.common import Result, Scope
from aws95.contracts.resources import ResourceRow, ResourceType
from aws95.core.cancel import CancelToken
from aws95.kit.widgets.datagrid import Column, DataGrid
from aws95.kit.widgets.inspector import Inspector
from aws95.kit.widgets.stateview import StateView
from aws95.programs.common import ProgramBase
from aws95.programs.common.paging import fetch_all_pages
from aws95.programs.resources.catalog import rtypes_for_service
from aws95.programs.resources.related import RelatedPanel

COLUMNS = [Column("badge", "", width=28), Column("name", "Name"), Column("rid", "ID"),
           Column("state", "State", "status"), Column("region", "Region"), Column("account", "Account")]
RTYPE_ROLE = int(Qt.ItemDataRole.UserRole) + 1


def fetch_all_resources(provider, scope: Scope | None, rtype: str, *, cancel: CancelToken | None = None
                        ) -> Result[tuple[ResourceRow, ...]]:
    scope = scope or Scope()
    return fetch_all_pages(partial(provider.list_resources, scope, rtype, page_size=500, cancel=cancel), cancel=cancel)


class ResourcesProgram(ProgramBase):
    TITLE = "Resources"
    PROGRAM_MENU = "&Resources"
    REFRESH_ON_SNAPSHOT = True
    EXPORT_NAME = "resources.csv"

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self._pending: dict[str, str] = {}
        self._rtype: str | None = None
        self._grid_follows = False

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree_view = StateView(self.tree)
        self.tree.currentItemChanged.connect(self._on_tree_item)

        self.grid = DataGrid(COLUMNS, placeholder="Filter…  e.g. state:stopped region:eu-* name~web")
        self.grid_view = StateView(self.grid)
        self.grid_view.show_empty("Pick a resource type on the left.")
        self.grid.current_row_changed.connect(self._on_row)
        self.grid.model.filter_applied.connect(self._on_filter_applied)

        self.inspector = Inspector()
        self.related = RelatedPanel(self)
        self.inspector.set_related(self.related)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.tree_view)
        split.addWidget(self.grid_view)
        split.addWidget(self.inspector)
        split.setStretchFactor(1, 3)
        split.setStretchFactor(2, 2)
        self.set_content(split)

        self.inspector_action = self.view_menu.addAction("&Inspector")
        self.inspector_action.setCheckable(True)
        self.inspector_action.setChecked(True)
        self.inspector_action.toggled.connect(self.inspector.setVisible)
        self.program_menu.addAction("Focus Fil&ter").triggered.connect(self.grid.filter_edit.setFocus)

    @property
    def primary_view(self) -> StateView:
        """The grid load owns the status bar (M3-R26); the tree stands in only when no grid load follows from it."""
        return self.grid_view if self._grid_follows else self.tree_view

    # ----- types -----
    def refresh(self) -> None:
        self._reset_grid()
        # The grid load owns the status bar (M3-R10b, M3-R26). The type tree writes it only when no grid load
        # follows from it: the list failed, is not connected, or holds nothing to select.
        self._grid_follows = False
        self.load(self.tree_view, self.ctx.providers.resources.resource_types, self.scope,
                  render=self._render_types, on_result=self._after_types, status=False)

    def _after_types(self, result) -> None:
        if not self._grid_follows:
            self.status.set_result(result)

    def _reset_grid(self) -> None:
        self.invalidate(self.grid_view)
        self.grid.set_rows([])
        self._clear_inspector()
        self.grid_view.show_empty("Pick a resource type on the left.")

    def _render_types(self, types: tuple[ResourceType, ...]) -> None:
        wanted = self._pending.get("rtype") or self._rtype
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            domains: dict[str, QTreeWidgetItem] = {}
            for t in types:
                parent = domains.get(t.domain)
                if parent is None:
                    parent = QTreeWidgetItem([t.domain])
                    self.tree.addTopLevelItem(parent)
                    domains[t.domain] = parent
                label = t.label if t.count is None else f"{t.label} ({t.count:,})"
                item = QTreeWidgetItem([label])
                item.setData(0, RTYPE_ROLE, t.rtype)
                parent.addChild(item)
            self.tree.expandAll()
        finally:
            self.tree.blockSignals(False)
        if wanted and self.select_rtype(wanted):
            self._grid_follows = True
            return
        if self._pending:
            self._pending = {}
            self.status.set_counts(f"No resource type {wanted} in this scope")
        if types:
            self._grid_follows = self.select_rtype(types[0].rtype)

    def _items(self):
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                yield top.child(j)

    def select_rtype(self, rtype: str) -> bool:
        for item in self._items():
            if item.data(0, RTYPE_ROLE) == rtype:
                if self.tree.currentItem() is item:
                    self._load_rows(rtype)
                else:
                    self.tree.setCurrentItem(item)
                return True
        return False

    def current_rtype(self) -> str | None:
        return self._rtype

    def _on_tree_item(self, current: QTreeWidgetItem | None, _previous) -> None:
        rtype = current.data(0, RTYPE_ROLE) if current is not None else None
        if rtype:
            self._load_rows(rtype)

    # ----- rows -----
    def _load_rows(self, rtype: str) -> None:
        self._rtype = rtype
        self.grid.set_rows([])
        self._clear_inspector()
        self.load(self.grid_view, fetch_all_resources, self.ctx.providers.resources, self.scope, rtype,
                  render=self._render_rows)

    def _render_rows(self, rows: tuple[ResourceRow, ...]) -> None:
        self.grid.set_rows([{"badge": "⚠" if r.partial else "", "name": r.name, "rid": r.rid, "state": r.state,
                             "region": r.region, "account": r.account, "row": r} for r in rows])
        flt = self._pending.pop("filter", None)
        self._pending.pop("rtype", None)
        if flt is not None:
            self.grid.set_filter_text(flt)

    def _on_filter_applied(self, shown: int, total: int) -> None:
        self.status.set_counts(f"{shown:,} of {total:,} resources")

    def _on_row(self, row: Mapping | None) -> None:
        if row is None:
            self._clear_inspector()
            return
        r: ResourceRow = row["row"]
        raw = {"account": r.account, "rtype": r.rtype, "region": r.region, "rid": r.rid, "name": r.name,
               "state": r.state, "attrs": dict(r.attrs), "tags": dict(r.tags), "partial": r.partial}
        self.inspector.set_item(r.name, f"{r.rtype} · {r.region} · {r.account}", r.attrs, r.tags, raw=raw)
        self.related.show_for(r)

    def _clear_inspector(self) -> None:
        self.inspector.clear()
        self.related.show_for(None)

    def export_grid(self) -> DataGrid:
        return self.grid

    # ----- navigation -----
    def _clear_selection(self) -> None:
        """Unmapped navigation target: drop in-flight rows so they cannot land after this (M3-R4)."""
        self._pending = {}
        self._rtype = None
        self.invalidate(self.grid_view)
        self.tree.blockSignals(True)
        try:
            self.tree.setCurrentItem(None)
        finally:
            self.tree.blockSignals(False)
        self.grid.set_rows([])
        self._clear_inspector()
        self.grid_view.show_empty("Pick a resource type on the left.")

    def apply_navigation(self, params: Mapping[str, str]) -> None:
        rtype = params.get("rtype")
        service = params.get("service")
        if service and not rtype:
            mapped = rtypes_for_service(service)
            if not mapped:
                self._clear_selection()
                self.status.set_counts(f"No resource type is collected for {service}")
                return
            rtype = mapped[0]
        if not rtype:
            return
        self._pending = {"rtype": rtype, **({"filter": params["filter"]} if "filter" in params else {})}
        if self.tree.topLevelItemCount() and not self.select_rtype(rtype):
            self._pending = {}
            self.status.set_counts(f"No resource type {rtype} in this scope")


def make_resources(ctx) -> ResourcesProgram:
    return ResourcesProgram(ctx)
