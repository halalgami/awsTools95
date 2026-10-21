"""Taskbar: Start button, one button per window, tray (account, speaker, jobs, clock)."""
from __future__ import annotations

import html
import math
from functools import partial

from PySide6.QtCore import QEvent, QSize, Qt, QTime, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QToolButton, QWidget

from shiboken6 import isValid

from aws95.core.settings import Settings
from aws95.core.tasks import TaskRunner
from aws95.kit.icons import icon
from aws95.kit.style import BASE_POINT_SIZE
from aws95.kit.theme import current_theme
from aws95.kit.widgets.bevel import BevelPanel
from aws95.kit.widgets.progress import BlockProgress
from aws95.shell.window import ProgramWindow


MIN_BUTTON_WIDTH = 40
MAX_BUTTON_WIDTH = 160
MAX_ACCOUNT_WIDTH = 140
JOB_CHARS = 34                  # the running job's words beside the bar (about this many); full text in the tooltip
BASE_HEIGHT = 30     # the classic taskbar height at 100 % font scale


class Clock(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.refresh)
        self.refresh()

    def refresh(self, now: QTime | None = None) -> None:
        t = now or QTime.currentTime()
        self.setText(t.toString("h:mm AP"))
        if now is None:
            self._timer.start((60 - t.second()) * 1000 - t.msec() + 50)


def job_width(fm) -> int:
    """The tray's job words get room for about JOB_CHARS characters at any font size."""
    return fm.averageCharWidth() * JOB_CHARS + 16


class Tray(BevelPanel):
    def __init__(self, settings: Settings, runner: TaskRunner, shell, parent: QWidget | None = None) -> None:
        super().__init__("sunken", parent)
        self._settings = settings
        self._shell = shell
        self.account_button = QToolButton(self)
        self.account_button.setAutoRaise(True)
        self.account_button.setMaximumWidth(MAX_ACCOUNT_WIDTH)
        self.account_button.clicked.connect(partial(shell.open_program, "accounts"))
        self.speaker_button = QToolButton(self)
        self.speaker_button.setAutoRaise(True)
        self.speaker_button.clicked.connect(self._toggle_mute)
        self.job_progress = BlockProgress(self)
        self.job_progress.setFixedSize(QSize(52, 14))
        self.job_progress.set_indeterminate(True)
        self.job_progress.hide()
        self.job_button = QToolButton(self)        # what is running, in words; click opens its window
        self.job_button.setAutoRaise(True)
        self.job_button.setMaximumWidth(job_width(self.job_button.fontMetrics()))
        self.job_button.clicked.connect(self._open_job)
        self.job_button.hide()
        self.clock = Clock(self)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 1, 6, 1)
        lay.setSpacing(6)
        for w in (self.account_button, self.speaker_button, self.job_progress, self.job_button, self.clock):
            lay.addWidget(w)
        self._job_text = ""
        runner.active_changed.connect(self._on_jobs)
        settings.changed.connect(self._on_setting)
        self._on_jobs(runner.active_count)
        self._refresh()

    def _toggle_mute(self) -> None:
        self._settings.sound_muted = not self._settings.sound_muted

    def _on_jobs(self, count: int) -> None:
        self._jobs = count
        self.job_progress.setVisible(count > 0 or bool(self._job_text))
        self.job_progress.setToolTip(self._job_text or (f"{count} task(s) running" if count else ""))

    def set_job_text(self, text: str) -> None:
        """A long job's own words (e.g. "Snapshot dev: 7/42 sections · lambda me-south-1"), shown beside the bar so a
        running snapshot or Activity load is visible at a glance; "" hands back to the task count."""
        self._job_text = text
        width = job_width(self.job_button.fontMetrics())          # the font may have grown since construction
        self.job_button.setMaximumWidth(width)
        self.job_button.setText(self.job_button.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, width - 16).replace("&", "&&"))
        self.job_button.setToolTip(text)
        self.job_button.setVisible(bool(text))
        self._on_jobs(getattr(self, "_jobs", 0))

    def _open_job(self, *_args) -> None:
        self._shell.show_running_job()

    def _on_setting(self, key: str) -> None:
        if key in ("sound/muted", "accounts/active") or (key.startswith("accounts/") and key.endswith("/label")):
            self._refresh()

    def _refresh(self) -> None:
        active = self._settings.active_account
        label = self._settings.account_label(active) if active else None
        text = label[0] if label else (active or "No account")
        tip = f"{label[0]} ({active})" if label else (active or "No account")
        self.account_button.setText(self.account_button.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, MAX_ACCOUNT_WIDTH - 16).replace("&", "&&"))
        self.account_button.setToolTip(f"<qt>{html.escape(tip)}</qt>" if "<" in tip else tip)
        muted = self._settings.sound_muted
        self.speaker_button.setIcon(icon("mute" if muted else "speaker"))
        self.speaker_button.setToolTip("Sound off (click to unmute)" if muted else "Sound on (click to mute)")


class TaskButton(QPushButton):
    def __init__(self, window: ProgramWindow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.window = window
        self.setCheckable(True)
        self.setIcon(window.windowIcon())
        self.setMinimumWidth(MIN_BUTTON_WIDTH)
        self.setMaximumWidth(MAX_BUTTON_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._full_title = ""
        self._set_title(window.windowTitle())
        window.windowTitleChanged.connect(self._set_title)
        window.installEventFilter(self)   # chip colour changes arrive as DynamicPropertyChange

    def _set_title(self, title: str) -> None:
        self._full_title = title
        self.setToolTip(title)
        self._elide()

    def _elide(self) -> None:
        room = max(0, self.width() - 40)    # icon, bevel and padding
        self.setText(self.fontMetrics().elidedText(self._full_title, Qt.TextElideMode.ElideRight, room))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.DynamicPropertyChange:
            self.update()
        return False

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not isValid(self.window):
            return
        color = self.window.property("aws95ChipColor")
        if color and QColor(str(color)).isValid():
            p = QPainter(self)
            p.fillRect(4, self.height() - 5, self.width() - 8, 3, QColor(str(color)))
            p.end()


class Taskbar(QWidget):
    def __init__(self, shell, desktop, settings: Settings, runner: TaskRunner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shell = shell
        self._desktop = desktop
        self._buttons: dict[ProgramWindow, TaskButton] = {}
        self.setFixedHeight(BASE_HEIGHT)
        self.setAutoFillBackground(True)
        self.start_button = QPushButton("Start", self)
        f = QFont(self.start_button.font())
        f.setBold(True)
        self.start_button.setFont(f)
        self.start_button.setIcon(icon("app"))
        self.start_button.clicked.connect(shell.show_start_menu)
        self._button_box = QHBoxLayout()
        self._button_box.setSpacing(3)
        self._button_box.addStretch(1)      # keeps buttons left-aligned once they hit max width
        self.tray = Tray(settings, runner, shell, self)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 3, 2, 2)
        lay.setSpacing(3)
        lay.addWidget(self.start_button)
        lay.addLayout(self._button_box, 1)
        lay.addWidget(self.tray)
        desktop.windows_changed.connect(self._rebuild)
        desktop.active_window_changed.connect(self._sync_checked)
        self._rebuild()
        self._fit_height()

    def _fit_height(self) -> None:
        """30 px at 100 %; larger font scales grow the bar with the font metrics and never clip the
        Start button or the tray."""
        ref = QFont(self.font())
        ref.setPointSizeF(BASE_POINT_SIZE)
        ref_h = QFontMetricsF(ref).height()
        ratio = QFontMetricsF(self.font()).height() / ref_h if ref_h > 0 else 1.0
        height = BASE_HEIGHT
        if ratio > 1.0:
            height = max(math.ceil(BASE_HEIGHT * ratio),
                         self.start_button.sizeHint().height(), self.tray.sizeHint().height())
        if height != self.height() or self.minimumHeight() != height:
            self.setFixedHeight(height)     # the shell's QVBoxLayout honours the new fixed height

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self._fit_height()

    def buttons(self) -> list[TaskButton]:
        return [self._buttons[w] for w in self._desktop.program_windows() if w in self._buttons]

    def button_for(self, window: ProgramWindow) -> TaskButton | None:
        return self._buttons.get(window)

    def _rebuild(self) -> None:
        live = self._desktop.program_windows()
        for w in list(self._buttons):
            if not isValid(w) or w not in live:
                btn = self._buttons.pop(w)
                self._button_box.removeWidget(btn)
                btn.deleteLater()
        for w in live:
            if w not in self._buttons:
                btn = TaskButton(w, self)
                btn.clicked.connect(partial(self._on_button, w))
                self._buttons[w] = btn
                self._button_box.insertWidget(self._button_box.count() - 1, btn, 100)
        self._sync_checked(self._desktop.active_program_window())

    def _sync_checked(self, active) -> None:
        for w, btn in self._buttons.items():
            if not isValid(w):
                continue
            btn.setChecked(w is active and not w.is_minimized_to_taskbar)

    def _on_button(self, window: ProgramWindow, _checked: bool = False) -> None:
        if not isValid(window):
            return
        if self._desktop.active_program_window() is window and not window.is_minimized_to_taskbar:
            window.minimize_to_taskbar()
        else:
            self._desktop.activate(window)
        self._sync_checked(self._desktop.active_program_window())

    def build_context_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("Tile &Horizontally", partial(self._shell.tile, "horizontal"))
        menu.addAction("Tile &Vertically", partial(self._shell.tile, "vertical"))
        menu.addAction("&Cascade", self._shell.cascade)
        menu.addSeparator()
        menu.addAction("&Minimize All", self._shell.minimize_all)
        return menu

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = self.build_context_menu()
        # One menu per right-click, deleted once hidden (same pattern as the desktop icon layer).
        # popup(), not exec(): triggered actions run before the deferred delete.
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup(event.globalPos())

    def paintEvent(self, event) -> None:  # noqa: N802
        t = current_theme()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(t.face))
        p.setPen(QColor(t.hi))
        p.drawLine(0, 0, self.width(), 0)
        p.end()
