"""About AWS 95 and Tip of the Day (tips are opt-in after the first run)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import aws95
from aws95.core.settings import Settings
from aws95.kit.mascot import cloudy_pixmap

TIPS: tuple[str, ...] = (
    "Press Ctrl+K to open any program, window or command by typing a few letters.",
    "Drag a window to the screen edge to snap it to half the desktop. Ctrl+Alt+arrows do the same.",
    "Alt+` switches between open windows, most recent first.",
    "The account chip on a title bar tells you which AWS account that window is looking at.",
    "Turn sounds, the splash and animations off any time in Display Properties.",
    "Closed a window by accident? Find it in the Recycle Bin.",
    "The screen saver hides all account data. Only the Resume button brings it back.",
)


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About AWS 95 Tools")
        logo = QLabel(self)
        logo.setPixmap(cloudy_pixmap(64))
        title = QLabel(f"AWS 95 Tools {aws95.__version__}", self)
        f = QFont(title.font())
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 1.3)
        title.setFont(f)
        note = QLabel("A retro desktop explorer for your AWS accounts.\n"
                      "Original sounds, icons and art. Not affiliated with Microsoft or Amazon.", self)
        note.setTextFormat(Qt.TextFormat.PlainText)
        author = QLabel(f"Made by {aws95.AUTHOR}", self)
        author.setTextFormat(Qt.TextFormat.PlainText)
        # Our own fixed URL (never provider text), so a rich-text link is safe here.
        repo = QLabel(f'<a href="{aws95.REPO_URL}">{aws95.REPO_URL.removeprefix("https://")}</a>', self)
        repo.setTextFormat(Qt.TextFormat.RichText)
        repo.setOpenExternalLinks(True)
        self.repo_label = repo
        ok = QPushButton("OK", self)
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        text = QVBoxLayout()
        text.addWidget(title)
        text.addWidget(note)
        text.addWidget(author)
        text.addWidget(repo)
        top = QHBoxLayout()
        top.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
        top.addLayout(text)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(ok, 0, Qt.AlignmentFlag.AlignRight)
        self._ok = ok

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._ok.setFocus()


def should_show_tip(settings: Settings) -> bool:
    return True if not settings.tips_seen else settings.show_tips


class TipDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Welcome to AWS 95")
        self.setWindowModality(Qt.WindowModality.NonModal)   # R11: never blocks the saver or shortcuts
        self.current_index = 0
        self._tip = QLabel(TIPS[0], self)
        self._tip.setWordWrap(True)
        self._tip.setTextFormat(Qt.TextFormat.PlainText)
        heading = QLabel("Did you know…", self)
        f = QFont(heading.font())
        f.setBold(True)
        heading.setFont(f)
        self.show_box = QCheckBox("&Show tips at startup", self)
        self.show_box.setChecked(settings.show_tips if settings.tips_seen else False)
        next_button = QPushButton("&Next Tip", self)
        next_button.clicked.connect(self.next_tip)
        self.close_button = QPushButton("&Close", self)
        self.close_button.setDefault(True)
        self.close_button.clicked.connect(self.accept)
        mascot = QLabel(self)
        mascot.setPixmap(cloudy_pixmap(48))
        body = QVBoxLayout()
        body.addWidget(heading)
        body.addWidget(self._tip)
        top = QHBoxLayout()
        top.addWidget(mascot, 0, Qt.AlignmentFlag.AlignTop)
        top.addLayout(body, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.show_box)
        buttons.addStretch(1)
        buttons.addWidget(next_button)
        buttons.addWidget(self.close_button)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(buttons)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.close_button.setFocus()

    def next_tip(self) -> None:
        self.current_index = (self.current_index + 1) % len(TIPS)
        self._tip.setText(TIPS[self.current_index])

    def done(self, result: int) -> None:
        self._settings.tips_seen = True
        self._settings.show_tips = self.show_box.isChecked()
        super().done(result)
