"""Shut Down… dialog, CRT power-off animation and the restart exit code (spec §6.1)."""
from __future__ import annotations

import weakref
from typing import Literal

from PySide6.QtCore import QEvent, QObject, QVariantAnimation, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QRadioButton, QVBoxLayout, QWidget

from aws95.core.settings import Settings
from aws95.kit.icons import icon_pixmap

RESTART_CODE = 95
Choice = Literal["shutdown", "restart"]


class ShutdownDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Shut Down AWS 95")
        self.setModal(True)
        self.choice: Choice | None = None
        pic = QLabel(self)
        pic.setPixmap(icon_pixmap("computer", 32))
        prompt = QLabel("Are you sure you want to:", self)
        self.shutdown_radio = QRadioButton("&Shut down AWS 95", self)
        self.restart_radio = QRadioButton("&Restart AWS 95", self)
        self.shutdown_radio.setChecked(True)
        self.yes_button = QPushButton("&Yes", self)
        self.no_button = QPushButton("&No", self)
        self.yes_button.setDefault(True)
        self.yes_button.clicked.connect(self._accept_choice)
        self.no_button.clicked.connect(self.reject)
        options = QVBoxLayout()
        options.addWidget(prompt)
        options.addWidget(self.shutdown_radio)
        options.addWidget(self.restart_radio)
        top = QHBoxLayout()
        top.addWidget(pic, 0, Qt.AlignmentFlag.AlignTop)
        top.addLayout(options)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.yes_button)
        buttons.addWidget(self.no_button)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(buttons)
        for child in (self.shutdown_radio, self.restart_radio, self.yes_button, self.no_button):
            child.installEventFilter(self)
        self.shutdown_radio.setFocus()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self._toggle_choice()
            return True
        return super().eventFilter(obj, event)

    def _toggle_choice(self) -> None:
        (self.restart_radio if self.shutdown_radio.isChecked() else self.shutdown_radio).setChecked(True)

    def _accept_choice(self) -> None:
        self.choice = "restart" if self.restart_radio.isChecked() else "shutdown"
        self.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self._toggle_choice()
            return
        super().keyPressEvent(event)


class CrtOff(QWidget):
    finished = Signal()

    def __init__(self, target: QWidget) -> None:
        super().__init__(target)
        # Opaque to the mouse and focusable: the overlay swallows input while it plays.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        target.installEventFilter(self)
        self._phase = 0.0
        self._done = False
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._on_value)
        self._anim.finished.connect(self._on_finished)
        self.hide()

    def play(self, duration_ms: int = 650) -> None:
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()
        self.setFocus()
        self.grabKeyboard()
        self._anim.setDuration(max(1, duration_ms))
        self._anim.start()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self.setGeometry(obj.rect())
            self.raise_()
        return False

    def keyPressEvent(self, event) -> None:  # noqa: N802
        event.accept()      # swallowed either way: nothing behind the overlay may react
        self.skip()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        event.accept()
        self.skip()

    def skip(self) -> None:
        """Any key or click finishes the animation now, through the normal finished path (once)."""
        if self._done or self._anim.state() != QVariantAnimation.State.Running:
            return
        self._anim.stop()
        self._on_finished()

    def _on_finished(self) -> None:
        if self._done:
            return
        self._done = True
        self.releaseKeyboard()
        self.hide()
        self.finished.emit()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.releaseKeyboard()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.releaseKeyboard()
        super().closeEvent(event)

    def _on_value(self, value) -> None:
        self._phase = float(value)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000000"))
        w, h = self.width(), self.height()
        t = self._phase
        if t < 0.6:
            band = max(2, int(h * (1 - t / 0.6)))
            p.fillRect(0, (h - band) // 2, w, band, QColor(255, 255, 255, 230))
        else:
            k = (t - 0.6) / 0.4
            line_w = max(2, int(w * (1 - k)))
            alpha = int(255 * (1 - k))
            p.fillRect((w - line_w) // 2, h // 2 - 1, line_w, 2, QColor(255, 255, 255, alpha))
        p.end()


# Guard per overlay target (the shell window): a restart builds a new shell on the same
# QApplication, so the app itself must not be the key. Entries are discarded once exit is issued.
_in_progress: "weakref.WeakSet" = weakref.WeakSet()


class _ShutdownJob:
    def __init__(self, app, code: int, target: QWidget) -> None:
        self.app, self.code, self.target = app, code, target
        self.crt: CrtOff | None = None

    def finish(self) -> None:
        try:
            self.app.exit(self.code)
        finally:
            _in_progress.discard(self.target)
            if self.crt is not None:
                self.crt.deleteLater()


def perform_shutdown(choice: Choice, *, settings: Settings, sound, app, overlay_target: QWidget | None,
                     duration_ms: int = 650) -> None:
    code = RESTART_CODE if choice == "restart" else 0
    if overlay_target is not None and overlay_target in _in_progress:
        return  # rapid double call within one session: exit fires once
    if sound is not None:
        sound.play("shutdown")
    if settings.reduce_motion or overlay_target is None:
        app.exit(code)
        return
    _in_progress.add(overlay_target)
    job = _ShutdownJob(app, code, overlay_target)
    job.crt = crt = CrtOff(overlay_target)
    crt._job = job  # keep alive for the signal
    crt.finished.connect(job.finish)
    crt.play(duration_ms)
