"""Event JSON viewer: a non-modal dialog over one raw CloudTrail record."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

from aws95.kit.widgets.jsonview import JsonView
from aws95.kit.widgets.stateview import StateView


class EventDialog(QDialog):
    def __init__(self, program, event_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Event {event_id}")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.json = JsonView(self)
        self.view = StateView(self.json)
        lay = QVBoxLayout(self)
        lay.addWidget(self.view)
        self.resize(640, 480)
        program.load(self.view, program.ctx.providers.activity.raw_event, program.scope, event_id,
                     render=self.json.set_data, status=False)  # a dialog load: the tabs own the status bar

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.activateWindow()
        self.json.setFocus()


class RetentionDialog(QDialog):
    """Activity ▸ Keep Activity For… (spec §3.3 retention): looked-up events older than N days are deleted from
    this computer; imported history stays until Clear Local Data…. Demo mode never writes settings."""
    NOTE = ("Looked-up activity older than this is deleted from this computer. Imported history is kept until you "
            "clear local data.")
    DEMO_NOTE = "Demo mode never writes settings."

    def __init__(self, program, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keep Activity")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._program = program
        demo = program.ctx.providers.is_demo
        self.days_spin = QSpinBox(self)
        self.days_spin.setRange(1, 3650)
        self.days_spin.setSuffix(" days")
        self.days_spin.setValue(program.ctx.settings.activity_retention_days)
        label = QLabel("&Keep activity for:", self)
        label.setBuddy(self.days_spin)
        self.note_label = QLabel(self.NOTE + (f" {self.DEMO_NOTE}" if demo else ""), self)
        self.note_label.setTextFormat(Qt.TextFormat.PlainText)
        self.note_label.setWordWrap(True)
        self.save_button = QPushButton("&Save", self)
        self.save_button.setEnabled(not demo)
        self.cancel_button = QPushButton("Cancel", self)
        self.save_button.clicked.connect(self._save)
        self.cancel_button.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(label)
        row.addWidget(self.days_spin)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.cancel_button)
        lay = QVBoxLayout(self)
        lay.addLayout(row)
        lay.addWidget(self.note_label)
        lay.addLayout(buttons)
        self.resize(420, 160)

    def _save(self, *_args) -> None:
        if self._program.ctx.providers.is_demo:
            return
        self._program.ctx.settings.activity_retention_days = self.days_spin.value()
        self._program.ctx.shell.prune_activity()
        self.accept()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.activateWindow()
        self.days_spin.setFocus()
