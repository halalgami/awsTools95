"""What the local activity history covers (spec §5.5), as plain text: shared by the History Coverage dialog and
(Task 9) the strip above the Activity tabs."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from aws95.contracts.activity import DENIED_NOTE, NO_HISTORY, AccountHistory, CoverageSpan, HistoryStatus
from aws95.contracts.common import Status
from aws95.kit.widgets.stateview import StateView

Label = Callable[[str], str]


def when(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _spans(spans: tuple[CoverageSpan, ...]) -> str:
    return ", ".join(f"{when(s.start)} → {when(s.end)}" for s in spans)


def account_line(a: AccountHistory, label: str) -> str:
    looked = [s for r in a.regions for s in r.looked_up]
    imported = [s for r in a.regions for s in r.imported]
    if not looked and not imported:
        return f"{label}: no history yet."
    parts = []
    if looked:
        n = sum(1 for r in a.regions if r.looked_up)
        parts.append(f"looked up {when(min(s.start for s in looked))} → {when(max(s.end for s in looked))} "
                     f"in {n} region{'s' if n != 1 else ''}")
    if imported:
        parts.append(f"imported {when(min(s.start for s in imported))} → {when(max(s.end for s in imported))}")
    text = f"{label}: " + "; ".join(parts)
    if a.unfinished:
        text += " (unfinished: Resume Loading continues it)"
    return text


def summary_lines(status: HistoryStatus, label: Label) -> list[str]:
    if not status.has_history:
        lines = [NO_HISTORY]
    else:
        lines = [account_line(a, label(a.account)) for a in status.accounts]
        lines.append(DENIED_NOTE)
    if status.progress:
        lines.append(status.progress)
    return lines


def region_lines(status: HistoryStatus, label: Label) -> list[str]:
    out = []
    for a in status.accounts:
        for r in a.regions:
            line = f"{label(a.account)} · {r.region}: {_spans(r.looked_up) or 'not loaded'}"
            if r.imported:
                line += f"; imported {_spans(r.imported)}"
            out.append(line)
    return out


class HistoryDialog(QDialog):
    """Activity ▸ History Coverage…: per-region coverage, the denied-attempts note and any running job."""

    def __init__(self, program, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("History coverage")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.status_label = QLabel("")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.view = StateView(self.status_label)
        QVBoxLayout(self).addWidget(self.view)
        self.resize(600, 340)
        self._label = program.account_name
        program.load(self.view, program.ctx.providers.activity.history_status, program.scope,
                     render=self._render, status=False)      # a dialog load: the tabs own the status bar

    def _render(self, status: HistoryStatus) -> None:
        lines = summary_lines(status, self._label)
        regions = region_lines(status, self._label)
        self.status_label.setText("\n".join([*lines, "", *regions] if regions else lines))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.activateWindow()
        self.setFocus()


class HistoryPanel(QWidget):
    """The strip above the Activity tabs (spec §5.1, §5.5): what the local history covers, the denied-attempts
    note, and the one Activity action that fits now — Load Activity History… (nothing loaded yet), Resume Loading
    (the chosen range has gaps), Stop Loading (a job is running). Demo shows coverage without actions; offline hides
    the strip. The load's StateView stays hidden: this strip never shows a full-page message."""
    ACTIONS = {"load": "Loa&d Activity History…", "resume": "Resume Loa&ding", "stop": "Stop Loa&ding"}

    def __init__(self, program) -> None:
        super().__init__(program)
        self.program = program
        self.action: str | None = None
        self.summary_label = QLabel("", self)
        self.summary_label.setTextFormat(Qt.TextFormat.PlainText)
        self.summary_label.setWordWrap(True)
        self.view = StateView(QLabel(""), self)          # the load's token only; never shown
        self.view.hide()
        self.action_button = QPushButton(self.ACTIONS["load"], self)
        self.action_button.hide()
        self.action_button.clicked.connect(self._act)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.addWidget(self.summary_label, 1)
        lay.addWidget(self.action_button, 0, Qt.AlignmentFlag.AlignTop)
        # Preferred, not Maximum: Maximum caps the strip at a size hint computed for some other width, which clips
        # the wrapped lines at large fonts. The tabs below take the stretch, so the strip gets its wrapped height.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.hide()

    def reload(self) -> None:
        p = self.program
        p.load(self.view, p.ctx.providers.activity.history_status, p.scope, render=self._render,
               on_result=self._on_result, status=False)

    def _render(self, status: HistoryStatus) -> None:
        self.summary_label.setText("\n".join(summary_lines(status, self.program.account_name)))
        if status.running:
            action = "stop"
        elif not self.program.ctx.providers.is_live:
            action = None
        elif not status.has_history:
            action = "load"
        elif status.unfinished:
            action = "resume"
        else:
            action = None
        self._set_action(action)

    def _on_result(self, result) -> None:
        if result.status is Status.OK:
            self.show()
            return
        if not self.program.ctx.providers.is_live:
            self.hide()                                   # offline: every tab already says "not connected"
            return
        self.summary_label.setText(result.message or "The activity history couldn't be read.")
        self._set_action(None)
        self.show()

    def _set_action(self, action: str | None) -> None:
        self.action = action
        if action is not None:
            self.action_button.setText(self.ACTIONS[action])
        self.action_button.setVisible(action is not None)

    def _act(self, *_args) -> None:
        shell, accounts = self.program.ctx.shell, self.program.scope_account_ids()
        if self.action == "load":
            shell.load_activity(accounts)
        elif self.action == "resume":
            shell.resume_activity(accounts)
        elif self.action == "stop":
            shell.cancel_activity()
