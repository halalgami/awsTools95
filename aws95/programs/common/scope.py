"""ScopeBar: account × region pickers shown in every program's toolbar (spec §6.6, §7)."""
from __future__ import annotations

from collections.abc import Sequence
from functools import partial

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QMenu, QToolButton, QWidget
from shiboken6 import isValid

from aws95.contracts.common import AccountRef, Scope, Status
from aws95.core.tasks import CancelScope
from aws95.shell.context import ProgramContext

REGIONS: tuple[str, ...] = (
    "us-east-1", "us-east-2", "us-west-1", "us-west-2", "ca-central-1", "eu-west-1", "eu-west-2", "eu-west-3",
    "eu-central-1", "eu-north-1", "eu-south-1", "me-south-1", "me-central-1", "ap-south-1", "ap-southeast-1",
    "ap-southeast-2", "ap-northeast-1", "sa-east-1",
)
ALL_ACCOUNTS = "All accounts"
ALL_REGIONS = "All regions"


class ScopeBar(QWidget):
    scope_changed = Signal(object)
    labels_changed = Signal()

    def __init__(self, ctx: ProgramContext, tasks: CancelScope, parent: QWidget | None = None, *,
                 regions: bool = True) -> None:
        super().__init__(parent)
        self._settings = ctx.settings
        self._ctx = ctx
        self._scope_tasks = tasks
        self._accounts: list[AccountRef] = []
        self._profiles_generation = 0      # a slow, older reload never overwrites a newer one

        self.account_combo = QComboBox(self)
        self.account_combo.setMinimumContentsLength(18)
        self.account_combo.addItem(ALL_ACCOUNTS, "")
        account_label = QLabel("&Account:", self)
        account_label.setBuddy(self.account_combo)

        self.region_button = QToolButton(self)
        self.region_button.setText(ALL_REGIONS)
        self.region_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.region_menu = QMenu(self.region_button)
        self.region_button.setMenu(self.region_menu)
        self._all_action = self.region_menu.addAction(ALL_REGIONS)
        self._all_action.setCheckable(True)
        self._all_action.setChecked(True)
        self._all_action.triggered.connect(self._select_all_regions)
        self.region_menu.addSeparator()
        self._region_actions = {}
        for code in REGIONS:
            action = self.region_menu.addAction(code)
            action.setCheckable(True)
            action.toggled.connect(self._on_region_toggled)
            self._region_actions[code] = action
        region_label = QLabel("Re&gion:", self)
        region_label.setBuddy(self.region_button)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.addWidget(account_label)
        lay.addWidget(self.account_combo)
        lay.addSpacing(6)
        lay.addWidget(region_label)
        lay.addWidget(self.region_button)
        region_label.setVisible(regions)
        self.region_button.setVisible(regions)

        self.account_combo.currentIndexChanged.connect(self._on_account_changed)
        self._settings.changed.connect(self._on_setting)
        self._load_profiles()

    # ----- accounts -----
    @property
    def accounts(self) -> tuple[AccountRef, ...]:
        return tuple(self._accounts)

    def _load_profiles(self) -> None:
        self._profiles_generation += 1
        handle = self._ctx.runner.submit(self._ctx.providers.accounts.list_profiles)
        self._scope_tasks.track(handle)
        handle.finished.connect(partial(self._on_profiles, self._profiles_generation))

    def _on_profiles(self, generation: int, result) -> None:
        if not isValid(self) or generation != self._profiles_generation:
            return
        if result.status not in (Status.OK, Status.PARTIAL, Status.STALE) or result.data is None:
            return
        # Live: only roster accounts. Demo/phase-1 fixtures have no selected flags: keep every verified profile.
        any_selected = any(p.selected for p in result.data)
        self.set_accounts([p.verified for p in result.data
                           if p.verified is not None and (p.selected or not any_selected)])

    def label_for(self, ref: AccountRef) -> tuple[str, str]:
        return self._settings.account_label(ref.account_id) or (ref.nickname, ref.color)

    def _item_text(self, ref: AccountRef) -> str:
        return f"{self.label_for(ref)[0]} ({ref.account_id})"

    def set_accounts(self, refs: Sequence[AccountRef]) -> None:
        before = self.account_combo.currentData()
        self._accounts = list(refs)
        self.account_combo.blockSignals(True)
        try:
            self.account_combo.clear()
            self.account_combo.addItem(ALL_ACCOUNTS, "")
            for ref in self._accounts:
                self.account_combo.addItem(self._item_text(ref), ref.account_id)
            target = before or self._settings.active_account
            index = self.account_combo.findData(target) if target else 0
            self.account_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self.account_combo.blockSignals(False)
        if self.account_combo.currentData() != before:
            self._emit()
        self.labels_changed.emit()      # the account list (and so nicknames) changed even if the selection did not

    def set_account(self, account_id: str) -> bool:
        index = self.account_combo.findData(account_id)
        if index < 0:
            return False
        self.account_combo.setCurrentIndex(index)
        return True

    def _on_account_changed(self, _index: int) -> None:
        self._emit()

    def _on_setting(self, key: str) -> None:
        if key == "accounts/roster":
            self._load_profiles()
            return
        if not (key.startswith("accounts/") and key.endswith("/label")):
            return
        for i in range(1, self.account_combo.count()):
            ref = next((r for r in self._accounts if r.account_id == self.account_combo.itemData(i)), None)
            if ref is not None:
                self.account_combo.setItemText(i, self._item_text(ref))
        self.labels_changed.emit()

    # ----- regions -----
    def set_regions(self, regions: Sequence[str]) -> None:
        wanted = set(regions)
        if wanted & set(self._region_actions) == set(self._selected_regions()):
            self._sync_region_ui()      # unchanged: keep the UI consistent, emit nothing
            return
        for code, action in self._region_actions.items():
            action.blockSignals(True)
            action.setChecked(code in wanted)
            action.blockSignals(False)
        self._sync_region_ui()
        self._emit()

    def _select_all_regions(self) -> None:
        self.set_regions([])

    def _on_region_toggled(self, _checked: bool) -> None:
        self._sync_region_ui()
        self._emit()

    def _selected_regions(self) -> tuple[str, ...]:
        return tuple(code for code, a in self._region_actions.items() if a.isChecked())

    def _sync_region_ui(self) -> None:
        chosen = self._selected_regions()
        self._all_action.setChecked(not chosen)
        if not chosen:
            self.region_button.setText(ALL_REGIONS)
        elif len(chosen) == 1:
            self.region_button.setText(chosen[0])
        else:
            self.region_button.setText(f"{len(chosen)} regions")

    # ----- scope -----
    def scope(self) -> Scope:
        account = self.account_combo.currentData()
        return Scope(accounts=(account,) if account else (), regions=self._selected_regions())

    def chip(self) -> tuple[str, str]:
        account = self.account_combo.currentData()
        ref = next((r for r in self._accounts if r.account_id == account), None)
        return self.label_for(ref) if ref is not None else (ALL_ACCOUNTS, "rainbow")

    def _emit(self) -> None:
        self.scope_changed.emit(self.scope())
