"""StateView: one widget, five honest states (spec §9)."""
from __future__ import annotations

import traceback
from enum import Enum
from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)

from aws95.contracts.common import Page, Result, Status
from aws95.kit.theme import current_theme, theme_bus
from aws95.kit.widgets.dialogs import Icon, icon_pixmap
from aws95.kit.widgets.progress import BlockProgress

MASCOT_GLYPH = "💾☁"
_mascot_pixmap: QPixmap | None = None


def set_mascot_pixmap(pixmap: QPixmap) -> None:
    """M2 installs Cloudy's artwork here; until then the glyph is shown."""
    global _mascot_pixmap
    _mascot_pixmap = pixmap


class ViewState(Enum):
    LOADING = "loading"
    CONTENT = "content"
    EMPTY = "empty"
    NOT_CONNECTED = "not_connected"
    ERROR = "error"


def _default_is_empty(data: Any) -> bool:
    if data is None:
        return True
    if isinstance(data, Page):
        return not data.items
    if isinstance(data, (list, tuple, dict, set)):
        return not data
    return False


_DEGRADED_FALLBACK = {
    Status.PARTIAL: "Partial data",
    Status.DENIED: "Access denied for some data",
    Status.UNREACHABLE: "Some sources were unreachable",
}


def banner_for(result: Result) -> str | None:
    parts: list[str] = []
    if result.status is Status.STALE:
        parts.append("Showing cached data")
    cov = result.coverage
    if cov is not None and not cov.complete:
        requested = set(cov.regions_requested)
        if requested - set(cov.regions_covered):
            parts.append(f"{len(set(cov.regions_covered) & requested)} of {len(requested)} regions covered")
        if cov.denials:
            parts.append(f"{cov.denials} attributes denied")
    degraded = _DEGRADED_FALLBACK.get(result.status)
    if degraded is not None and result.message:
        parts.append(result.message)
    elif result.status is Status.STALE and result.message:
        parts.append(result.message)
    if degraded is not None and len(parts) == 0:
        parts.append(degraded)
    return " · ".join(parts) if parts else None


def _plain_label(text: str, parent: QWidget) -> QLabel:
    """Labels showing provider/AWS-sourced text never render rich text."""
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


class StateView(QStackedWidget):
    retry_requested = Signal()

    def __init__(self, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = ViewState.CONTENT
        self.banner_text: str | None = None

        # loading page
        self._loading = QWidget(self)
        self._progress = BlockProgress(self._loading)
        self._loading_label = _plain_label("Reading…", self._loading)
        ll = QVBoxLayout(self._loading)
        ll.addStretch(1)
        ll.addWidget(self._loading_label, 0, Qt.AlignmentFlag.AlignHCenter)
        ll.addWidget(self._progress, 0, Qt.AlignmentFlag.AlignHCenter)
        ll.addStretch(1)

        # content page
        self._content_page = QWidget(self)
        self._banner = _plain_label("", self._content_page)
        self._banner.setVisible(False)
        self._banner.setAutoFillBackground(True)
        self._banner.setContentsMargins(6, 3, 6, 3)
        cl = QVBoxLayout(self._content_page)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(self._banner)
        cl.addWidget(content, 1)

        # message page (empty / not connected / error)
        self._message = QWidget(self)
        self._icon = QLabel(self._message)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title = _plain_label("", self._message)
        title_font = QFont(self._title.font())
        title_font.setBold(True)
        self._title.setFont(title_font)
        self._body = _plain_label("", self._message)
        self._body.setWordWrap(True)
        self._body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.retry_button = QPushButton("Retry", self._message)
        self.retry_button.clicked.connect(self.retry_requested)
        self._details_button = QPushButton("Details >>", self._message)
        self._details = QPlainTextEdit(self._message)
        self._details.setReadOnly(True)
        self._details.setVisible(False)
        self._details_button.clicked.connect(self._toggle_details)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.retry_button)
        buttons.addWidget(self._details_button)
        buttons.addStretch(1)
        ml = QVBoxLayout(self._message)
        ml.addStretch(1)
        ml.addWidget(self._icon)
        ml.addWidget(self._title, 0, Qt.AlignmentFlag.AlignHCenter)
        ml.addWidget(self._body)
        ml.addLayout(buttons)
        ml.addWidget(self._details)
        ml.addStretch(1)

        for page in (self._loading, self._content_page, self._message):
            self.addWidget(page)
        self.setCurrentWidget(self._content_page)
        theme_bus().changed.connect(self._on_theme_changed)

    def _toggle_details(self) -> None:
        self._details.setVisible(not self._details.isVisibleTo(self._message))

    # ----- states -----
    def _leave_loading(self) -> None:
        self._progress.set_indeterminate(False)
        self.unsetCursor()

    def show_loading(self, text: str = "Reading…") -> None:
        self._loading_label.setText(text)
        self._progress.set_indeterminate(True)
        self.setCursor(Qt.CursorShape.WaitCursor)
        self.setCurrentWidget(self._loading)
        self.state = ViewState.LOADING

    def _paint_banner(self) -> None:
        t = current_theme()
        pal = self._banner.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(t.banner_bg))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(t.banner_text))
        self._banner.setPalette(pal)

    def _on_theme_changed(self, _theme) -> None:
        if self.banner_text:
            self._paint_banner()

    def show_content(self, banner: str | None = None) -> None:
        self._leave_loading()
        self.banner_text = banner
        if banner:
            self._paint_banner()
            self._banner.setText(f"⚠ {banner}")
        self._banner.setVisible(bool(banner))
        self.setCurrentWidget(self._content_page)
        self.state = ViewState.CONTENT

    def _show_message(self, state: ViewState, title: str, body: str, *, icon: Icon | None,
                      retry: bool, details: str | None) -> None:
        self._leave_loading()
        if icon is not None:
            self._icon.setPixmap(icon_pixmap(icon, 32))
        elif _mascot_pixmap is not None:
            self._icon.setPixmap(_mascot_pixmap)
        else:
            self._icon.setText(MASCOT_GLYPH)
        self._title.setText(title)
        self._body.setText(body)
        self.retry_button.setVisible(retry)
        self._details_button.setVisible(bool(details))
        self._details.setPlainText(details or "")
        self._details.setVisible(False)
        self.setCurrentWidget(self._message)
        self.state = state

    def show_empty(self, message: str) -> None:
        self._show_message(ViewState.EMPTY, "Nothing here", message, icon=None, retry=False, details=None)

    def show_not_connected(self, message: str) -> None:
        self._show_message(ViewState.NOT_CONNECTED, "Not connected yet", message, icon=None, retry=False, details=None)

    def show_error(self, message: str, details: str | None = None) -> None:
        self._show_message(ViewState.ERROR, "Something went wrong", message, icon=Icon.ERROR, retry=True, details=details)

    # ----- results -----
    def apply_result(self, result: Result, render: Callable[[Any], None],
                     is_empty: Callable[[Any], bool] | None = None) -> None:
        empty = is_empty or _default_is_empty
        if result.status is Status.NOT_CONNECTED:
            self.show_not_connected(result.message or "Not connected.")
            return
        if result.status is Status.ERROR:
            self.show_error(result.message or "The request failed.")
            return
        if result.data is None:
            if result.status is Status.OK:
                self.show_empty(result.message or "Nothing here.")
            else:
                self.show_error(result.message or f"No data ({result.status.value}).")
            return
        if empty(result.data) and result.status is Status.OK:
            self.show_empty(result.message or "Nothing here.")
            return
        try:
            render(result.data)
        except Exception as exc:  # noqa: BLE001 - surface any render failure honestly
            self.show_error(f"Could not display data: {exc}", details=traceback.format_exc())
            return
        self.show_content(banner_for(result))
