from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QButtonGroup, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)


class Icon(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    QUESTION = "question"


def icon_pixmap(icon: Icon, size: int = 32) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    r = QRectF(1, 1, size - 2, size - 2)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(int(size * 0.6))
    p.setFont(font)
    if icon is Icon.WARNING:
        tri = QPolygonF([QPointF(size / 2, 1), QPointF(size - 1, size - 2), QPointF(1, size - 2)])
        p.setPen(QPen(QColor("#000000"), 1))
        p.setBrush(QColor("#ffd400"))
        p.drawPolygon(tri)
        p.drawText(r.adjusted(0, size * 0.15, 0, 0), Qt.AlignmentFlag.AlignCenter, "!")
    elif icon is Icon.ERROR:
        p.setPen(QPen(QColor("#000000"), 1))
        p.setBrush(QColor("#d40000"))
        p.drawEllipse(r)
        p.setPen(QPen(QColor("#ffffff"), max(2, size // 10)))
        m = size * 0.3
        p.drawLine(QPointF(m, m), QPointF(size - m, size - m))
        p.drawLine(QPointF(size - m, m), QPointF(m, size - m))
    else:
        p.setPen(QPen(QColor("#000000"), 1))
        p.setBrush(QColor("#1060d0"))
        p.drawEllipse(r)
        p.setPen(QColor("#ffffff"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, "i" if icon is Icon.INFO else "?")
    p.end()
    return pm


class MessageBox95(QDialog):
    def __init__(self, parent: QWidget | None, title: str, text: str, icon: Icon = Icon.INFO,
                 buttons: tuple[str, ...] = ("OK",), default: int = 0, details: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.choice: int | None = None

        icon_label = QLabel(self)
        icon_label.setPixmap(icon_pixmap(icon, 32))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        text_label = QLabel(self)
        text_label.setTextFormat(Qt.TextFormat.PlainText)
        text_label.setText(text)
        self.text_label = text_label
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        top = QHBoxLayout()
        top.addWidget(icon_label)
        top.addSpacing(10)
        top.addWidget(text_label, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.buttons: list[QPushButton] = []
        self._group = QButtonGroup(self)
        self._group.idClicked.connect(self._choose)
        for i, label in enumerate(buttons):
            btn = QPushButton(label, self)
            btn.setMinimumWidth(75)
            self._group.addButton(btn, i)
            row.addWidget(btn)
            self.buttons.append(btn)
        self.buttons[default].setDefault(True)
        self.buttons[default].setFocus()

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        self.details_button: QPushButton | None = None
        self.details_view: QPlainTextEdit | None = None
        if details:
            self.details_button = QPushButton("Details >>", self)
            self.details_view = QPlainTextEdit(details, self)
            self.details_view.setReadOnly(True)
            self.details_view.setVisible(False)
            self.details_button.clicked.connect(self._toggle_details)
            row.insertWidget(0, self.details_button)
        layout.addSpacing(8)
        layout.addLayout(row)
        if self.details_view is not None:
            layout.addWidget(self.details_view)

    def _choose(self, index: int) -> None:
        self.choice = index
        self.accept()

    def _toggle_details(self) -> None:
        assert self.details_view is not None and self.details_button is not None
        show = not self.details_view.isVisible()
        self.details_view.setVisible(show)
        self.details_button.setText("<< Details" if show else "Details >>")

    @staticmethod
    def info(parent: QWidget | None, title: str, text: str) -> None:
        MessageBox95(parent, title, text, Icon.INFO).exec()

    @staticmethod
    def error(parent: QWidget | None, title: str, text: str, details: str | None = None) -> None:
        MessageBox95(parent, title, text, Icon.ERROR, details=details).exec()

    @staticmethod
    def ask(parent: QWidget | None, title: str, text: str) -> bool:
        box = MessageBox95(parent, title, text, Icon.QUESTION, ("Yes", "No"), default=0)
        box.exec()
        return box.choice == 0
