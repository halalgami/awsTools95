"""Ctrl+K palette: fuzzy search over programs, open windows and commands (spec §6.5)."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from typing import Callable

from PySide6.QtCore import QCoreApplication, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPalette
from shiboken6 import isValid
from PySide6.QtWidgets import (QApplication, QDialog, QLineEdit, QListWidget, QListWidgetItem, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout, QWidget)

from aws95.contracts.common import Status
from aws95.contracts.search import SearchHit
from aws95.core.settings import Settings, ThemeMode
from aws95.kit.icons import icon
from aws95.kit.theme import current_theme
from aws95.shell.screensaver import saver_titles
from aws95.shell.registry import Registry

_WORD_BREAKS = " _-./›:"
_NAV_KEYS = (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape,
             Qt.Key.Key_Backspace, Qt.Key.Key_Delete, Qt.Key.Key_Left, Qt.Key.Key_Right)
_ITEM_ROLE = int(Qt.ItemDataRole.UserRole) + 7
_DETAIL_ROLE = int(Qt.ItemDataRole.UserRole) + 8


def fuzzy_score(query: str, text: str) -> float | None:
    q = query.strip().lower()
    if not q:
        return 0.0
    t = text.lower()
    score, start, prev = 0.0, 0, -2
    for ch in q:
        idx = t.find(ch, start)
        if idx < 0:
            return None
        bonus = 1.0
        if idx == prev + 1:
            bonus += 2.0
        if idx == 0 or t[idx - 1] in _WORD_BREAKS:
            bonus += 3.0
        score += bonus
        prev, start = idx, idx + 1
    return score - 0.01 * len(t)


@dataclass(frozen=True)
class PaletteItem:
    group: str
    title: str
    action: Callable[[], None] = field(compare=False)
    shortcut: str = ""
    keywords: tuple[str, ...] = ()
    icon: str = ""


def rank(items: list[PaletteItem], query: str, limit: int = 50) -> list[PaletteItem]:
    if not query.strip():
        return list(items)[:limit]
    scored = []
    for order, item in enumerate(items):
        scores = [s for s in (fuzzy_score(query, item.title), *(fuzzy_score(query, k) for k in item.keywords))
                  if s is not None]
        if scores:
            scored.append((-max(scores), order, item))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [item for _s, _o, item in scored[:limit]]


SEARCH_DELAY_MS = 200
SEARCHING = "Searching local data…"
HIT_GROUPS = {"resource": "Resources", "finding": "Findings", "principal": "Principals", "service": "Services"}
_USABLE = (Status.OK, Status.PARTIAL, Status.STALE)


def hit_items(result, open_hit: Callable[[SearchHit], None], icon_for: Callable[[str], str]) -> list[PaletteItem]:
    """Store hits as palette rows (2d spec §4.2). A failed or not-connected search adds nothing: the palette is not
    the place for errors."""
    if result is None or getattr(result, "status", None) not in _USABLE or not result.data:
        return []
    tag = " [DEMO]" if result.is_demo else ""
    return [PaletteItem(HIT_GROUPS.get(h.kind, "Other") + tag, h.title, partial(open_hit, h), h.subtitle, (),
                        icon_for(h.target_program)) for h in result.data]


def _set_theme(settings: Settings, mode: ThemeMode) -> None:
    settings.theme_mode = mode


def _set_mute(settings: Settings, muted: bool) -> None:
    settings.sound_muted = muted


def _activate_window(desktop, window) -> None:
    """Activate a window captured earlier; skip it if it was closed in the meantime."""
    if not isValid(window) or window.is_closing or window not in desktop.program_windows():
        return
    desktop.activate(window)


def build_items(registry: Registry, shell, settings: Settings, desktop) -> list[PaletteItem]:
    items = [PaletteItem("Programs", m.title, partial(shell.open_program, m.id), m.shortcut or "", m.keywords, m.icon)
             for m in registry.all()]
    for w in desktop.program_windows():
        items.append(PaletteItem("Windows", f"Switch to {w.windowTitle()}", partial(_activate_window, desktop, w),
                                 icon=w.manifest.icon))
    items += [
        PaletteItem("Commands", "Theme: Light", partial(_set_theme, settings, ThemeMode.LIGHT), icon="display"),
        PaletteItem("Commands", "Theme: Dark", partial(_set_theme, settings, ThemeMode.DARK), icon="display"),
        PaletteItem("Commands", "Theme: Follow OS", partial(_set_theme, settings, ThemeMode.SYSTEM), icon="display"),
        PaletteItem("Commands", "Unmute sounds" if settings.sound_muted else "Mute sounds",
                    partial(_set_mute, settings, not settings.sound_muted), icon="speaker"),
        PaletteItem("Commands", "Tile Horizontally", partial(shell.tile, "horizontal"), icon="window"),
        PaletteItem("Commands", "Tile Vertically", partial(shell.tile, "vertical"), icon="window"),
        PaletteItem("Commands", "Cascade Windows", shell.cascade, icon="window"),
        PaletteItem("Commands", "Minimize All", shell.minimize_all, icon="window"),
        PaletteItem("Commands", "Take Snapshot", shell.take_snapshot, keywords=("snapshot", "collect", "refresh data"),
                    icon="camera"),
        PaletteItem("Commands", "Resume Snapshot", partial(shell.resume_snapshot, (), False),
                    keywords=("snapshot", "continue", "unfinished"), icon="camera"),
        PaletteItem("Commands", "Clear Local Data…", shell.clear_local_data, keywords=("delete", "database", "reset"),
                    icon="recycle"),
        PaletteItem("Commands", "Preview Screen Saver", shell.preview_screensaver, icon="display"),
        *(PaletteItem("Commands", f"Start screen saver: {title}", partial(shell.preview_screensaver, name),
                      keywords=("screensaver", "saver"), icon="display") for name, title in saver_titles().items()),
        PaletteItem("Commands", "About AWS 95", shell.show_about, "F1", icon="help"),
        PaletteItem("Commands", "Shut Down…", shell.request_shutdown, "Ctrl+Q", icon="power"),
    ]
    return items


def fit_detail(fm: QFontMetrics, text_width: int, title: str, detail: str, gap: int = 16) -> str:
    """The detail as it fits beside a whole title: full, elided, or nothing. The title is never shortened."""
    room = text_width - fm.horizontalAdvance(title) - gap
    if not detail or room <= fm.horizontalAdvance("…"):
        return ""
    return fm.elidedText(detail, Qt.TextElideMode.ElideRight, room)


class _DetailDelegate(QStyledItemDelegate):
    """Title left; the detail (a shortcut or a search subtitle) right-aligned and muted, like a Win95 menu."""

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        detail = index.data(_DETAIL_ROLE) or ""
        if not detail:
            return
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget).adjusted(0, 0, -4, 0)
        shown = fit_detail(opt.fontMetrics, rect.width(), opt.text, detail)
        if not shown:
            return
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setPen(opt.palette.color(QPalette.ColorRole.HighlightedText) if selected
                       else QColor(current_theme().muted))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), shown)
        painter.restore()


class CommandPalette(QDialog):
    def __init__(self, parent: QWidget | None = None, *, search=None, open_hit=None, icon_for=None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setMinimumWidth(460)
        self._items: list[PaletteItem] = []
        self._forwarding = False
        self.query_edit = QLineEdit(self)
        self.query_edit.setPlaceholderText("Type a program, window or command…")
        self.list_widget = QListWidget(self)
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list_widget.setItemDelegate(_DetailDelegate(self.list_widget))
        self.list_widget.setMaximumHeight(320)
        self.setFocusProxy(self.query_edit)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.query_edit)
        lay.addWidget(self.list_widget)
        self.query_edit.textChanged.connect(self._on_text)
        self.list_widget.itemActivated.connect(self._run_item)
        self.list_widget.itemClicked.connect(self._run_item)
        self.query_edit.installEventFilter(self)
        self._search = search if open_hit is not None else None
        self._open_hit = open_hit
        self._icon_for = icon_for or (lambda _program: "")
        self._hits: list[PaletteItem] = []
        self._searching = False
        self._generation = 0
        self._handle = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SEARCH_DELAY_MS)
        self._timer.timeout.connect(self._start_search)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.query_edit.clear()
        self._refresh("")
        self.query_edit.setFocus()
        QTimer.singleShot(0, self._focus_query)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # Keys that reach the dialog itself (not the focused edit) are routed to the edit.
        if self._forwarding:
            event.ignore()
            return
        mods = event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
                                    | Qt.KeyboardModifier.MetaModifier)
        key = event.key()
        wanted = key in _NAV_KEYS or (not mods and bool(event.text()) and event.text().isprintable())
        if not wanted:
            super().keyPressEvent(event)
            return
        self._forwarding = True
        try:
            QCoreApplication.sendEvent(self.query_edit, event)
        finally:
            self._forwarding = False

    def _focus_query(self) -> None:
        if self.isVisible():
            self.query_edit.setFocus()

    def set_items(self, items: list[PaletteItem]) -> None:
        self._items = list(items)
        self.query_edit.clear()
        self._refresh("")

    def visible_items(self) -> list[PaletteItem]:
        out = []
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row).data(_ITEM_ROLE)
            if item is not None:
                out.append(item)
        return out

    def _drop_search(self) -> None:
        self._generation += 1
        self._timer.stop()
        if self._handle is not None and not getattr(self._handle, "done", False):
            self._handle.cancel()
        self._handle = None

    def _on_text(self, text: str) -> None:
        self._drop_search()
        self._hits = []
        self._searching = self._search is not None and len(text.strip()) >= 2
        if self._searching:
            self._timer.start()
        self._refresh(text)

    def _start_search(self) -> None:
        if not self._searching or not self.isVisible():
            return
        generation = self._generation
        handle = self._search(self.query_edit.text())
        self._handle = handle
        handle.finished.connect(partial(self._on_hits, generation))
        handle.failed.connect(partial(self._on_search_failed, generation))

    def _on_hits(self, generation: int, result) -> None:
        if generation != self._generation or not isValid(self):
            return                                       # an older query's answer: dropped
        self._searching = False
        self._hits = hit_items(result, self._open_hit, self._icon_for)
        self._refresh(self.query_edit.text(), keep=self._current_item())

    def _on_search_failed(self, generation: int, *_args) -> None:
        if generation == self._generation and isValid(self):
            self._searching = False
            self._refresh(self.query_edit.text(), keep=self._current_item())

    def _current_item(self):
        row = self.list_widget.currentItem()
        return row.data(_ITEM_ROLE) if row is not None else None

    def hideEvent(self, event) -> None:  # noqa: N802
        self._drop_search()
        self._searching = False
        super().hideEvent(event)

    def _refresh(self, text: str, keep=None) -> None:
        self.list_widget.clear()
        groups: dict[str, list[PaletteItem]] = {}
        for item in [*rank(self._items, text), *self._hits]:
            groups.setdefault(item.group, []).append(item)
        bold = QFont(self.list_widget.font())
        bold.setBold(True)
        for group, members in groups.items():
            header = QListWidgetItem(group)
            header.setFont(bold)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list_widget.addItem(header)
            for item in members:
                row = QListWidgetItem(icon(item.icon) if item.icon else icon("window"), item.title)
                row.setData(_DETAIL_ROLE, item.shortcut)
                row.setData(_ITEM_ROLE, item)
                self.list_widget.addItem(row)
        if self._searching:
            waiting = QListWidgetItem(SEARCHING)
            waiting.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list_widget.addItem(waiting)
        if keep is not None:
            for r in range(self.list_widget.count()):
                if self.list_widget.item(r).data(_ITEM_ROLE) == keep:
                    self.list_widget.setCurrentRow(r)
                    return
        self._select_first()

    def _select_first(self) -> None:
        for row in range(self.list_widget.count()):
            if self.list_widget.item(row).data(_ITEM_ROLE) is not None:
                self.list_widget.setCurrentRow(row)
                return

    def _move(self, step: int) -> None:
        row = self.list_widget.currentRow()
        n = self.list_widget.count()
        while 0 <= row + step < n:
            row += step
            if self.list_widget.item(row).data(_ITEM_ROLE) is not None:
                self.list_widget.setCurrentRow(row)
                return

    def run_current(self) -> None:
        current = self.list_widget.currentItem()
        if current is not None:
            self._run_item(current)

    def _run_item(self, row: QListWidgetItem) -> None:
        item = row.data(_ITEM_ROLE)
        if item is None:
            return
        self.hide()
        item.action()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.query_edit and event.type() == event.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                self._move(1)
                return True
            if key == Qt.Key.Key_Up:
                self._move(-1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.run_current()
                return True
            if key == Qt.Key.Key_Escape:
                self.hide()
                return True
        return super().eventFilter(obj, event)
