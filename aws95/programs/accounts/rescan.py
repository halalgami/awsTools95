"""Rescan helpers for My Accounts: a bounded verify queue and the "New Profiles Found" dialog (spec §6.2)."""
from __future__ import annotations

from collections import deque
from functools import partial
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
                               QWidget)
from shiboken6 import isValid

from aws95.contracts.accounts import RosterEntry
from aws95.contracts.common import Result, Scope

MAX_PARALLEL = 4
DEMO_TIP = "Not available in demo mode"
_KIND = Qt.ItemDataRole.UserRole


class VerifyQueue:
    """Verifies profiles with at most MAX_PARALLEL running; every handle is tracked in the window's CancelScope.

    Results and the final callback are dropped once the queue is cancelled or the owning window is gone."""

    def __init__(self, owner: QWidget, runner, tasks, fn: Callable, names: list[str],
                 on_result: Callable[[str, Result], None], on_done: Callable[[], None]) -> None:
        self._owner, self._runner, self._tasks, self._fn = owner, runner, tasks, fn
        self._queue = deque(names)
        self._total = len(names)
        self._settled = 0
        self._running = 0
        self._dead = False
        self._on_result, self._on_done = on_result, on_done
        self._handles: list = []

    @property
    def total(self) -> int:
        return self._total

    @property
    def settled(self) -> int:
        return self._settled

    def start(self) -> None:
        if not self._queue:
            self._on_done()
            return
        self._pump()

    def cancel(self) -> None:
        self._dead = True
        self._queue.clear()
        for h in self._handles:
            h.cancel()
        self._handles.clear()

    def _pump(self) -> None:
        while self._queue and self._running < MAX_PARALLEL and not self._dead:
            name = self._queue.popleft()
            self._running += 1
            handle = self._runner.submit(self._fn, Scope(), name)
            self._handles.append(handle)
            self._tasks.track(handle)
            handle.finished.connect(partial(self._deliver, name))
            handle.failed.connect(partial(self._failed, name))

    def _deliver(self, name: str, result: Result) -> None:
        if self._dead or not isValid(self._owner):
            return
        self._running -= 1
        self._settled += 1
        self._on_result(name, result)
        self._next()

    def _failed(self, name: str, message: str, _details: str) -> None:
        self._deliver(name, Result.error(f"Unexpected error: {message.split(':')[0]}"))   # class name only

    def _next(self) -> None:
        if self._dead or not isValid(self._owner):
            return
        if self._queue:
            self._pump()
        elif self._running == 0:
            self._dead = True
            self._on_done()


class NewProfilesDialog(QDialog):
    """Non-modal list of new and changed profiles, all ticked. Nothing changes until Add Selected."""

    def __init__(self, program, new: list[RosterEntry], changed: list[tuple[str, str, str]]) -> None:
        super().__init__(program)
        self.program = program
        self.setWindowTitle("New Profiles Found")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(460, 300)
        self._demo = bool(program.ctx.providers.is_demo)
        lay = QVBoxLayout(self)
        intro = QLabel("Tick the profiles to add or update. Nothing changes until you press Add Selected.", self)
        intro.setTextFormat(Qt.TextFormat.PlainText)
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.list = QListWidget(self)
        for e in new:
            self._add_item(f"{e.profile}: {e.account_id}", ("new", e.profile, e.account_id, ""))
        for profile, now, was in changed:
            self._add_item(f"{profile}: now {now} (was {was})", ("changed", profile, now, was))
        lay.addWidget(self.list, 1)
        self.error_label = QLabel("", self)
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        lay.addWidget(self.error_label)
        row = QHBoxLayout()
        row.addStretch(1)
        self.add_button = QPushButton("&Add Selected", self)
        self.add_button.setDefault(True)
        self.add_button.clicked.connect(self.add_selected)
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.clicked.connect(self.close)
        if self._demo:
            self.add_button.setEnabled(False)
            self.add_button.setToolTip(DEMO_TIP)
        row.addWidget(self.add_button)
        row.addWidget(self.cancel_button)
        lay.addLayout(row)

    def _add_item(self, text: str, data: tuple) -> None:
        item = QListWidgetItem(text)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        item.setData(_KIND, data)
        self.list.addItem(item)

    def text_dump(self) -> str:
        return "\n".join(self.list.item(i).text() for i in range(self.list.count()))

    def add_selected(self, *_args) -> None:
        if self._demo:
            self.error_label.setText(DEMO_TIP)
            return
        settings = self.program.ctx.settings
        ticked = [self.list.item(i).data(_KIND) for i in range(self.list.count())
                  if self.list.item(i).checkState() == Qt.CheckState.Checked]
        roster = list(settings.roster())
        for kind, profile, account, _was in ticked:
            if kind == "changed":
                roster = [RosterEntry(e.profile, account) if e.profile == profile else e for e in roster]
        for kind, profile, account, _was in ticked:
            if kind == "new" and all(e.account_id != account and e.profile != profile for e in roster):
                roster.append(RosterEntry(profile, account))
        try:
            settings.set_roster(roster)
        except ValueError as exc:
            self.error_label.setText(str(exc))
            return
        self.close()
