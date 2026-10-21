"""Start menu: vertical AWS95 stripe, generated Programs, settings, Find/Help/Run, Shut Down."""
from __future__ import annotations

from functools import partial
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QLinearGradient, QPainter
from PySide6.QtWidgets import QMenu, QWidget

from aws95.kit.icons import icon
from aws95.kit.theme import current_theme
from aws95.shell.registry import Registry

STRIPE_W = 22


class StartMenu(QMenu):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setContentsMargins(STRIPE_W + 2, 2, 2, 2)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        t = current_theme()
        p = QPainter(self)
        r = QRect(2, 2, STRIPE_W, self.height() - 4)
        grad = QLinearGradient(r.bottomLeft(), r.topLeft())
        grad.setColorAt(0.0, QColor(t.title_active_start))
        grad.setColorAt(1.0, QColor(t.title_active_end))
        p.fillRect(r, grad)
        p.translate(r.left() + STRIPE_W - 5, r.bottom() - 6)
        p.rotate(-90)
        f = QFont(self.font())
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 1.25)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(0, 0, "AWS")
        p.setPen(QColor("#c0c0c0"))
        p.drawText(p.fontMetrics().horizontalAdvance("AWS"), 0, "95")
        p.end()


def _used_letters(menu: QMenu) -> set[str]:
    used = set()
    for a in menu.actions():
        text = a.text().replace("&&", "")
        if "&" in text:
            used.add(text.split("&", 1)[1][:1].lower())
    return used


def with_mnemonic(menu: QMenu, text: str) -> str:
    """`text` with one `&` on a letter no other action in `menu` uses; text that already has one is kept."""
    if "&" in text.replace("&&", ""):
        return text
    used = _used_letters(menu)
    for i, ch in enumerate(text):
        if ch.isalnum() and ch.lower() not in used:
            return text[:i] + "&" + text[i:]
    return text     # every letter taken: leave it without a mnemonic rather than duplicate one


def _empty(menu: QMenu) -> None:
    a = menu.addAction("(&Empty)")
    a.setEnabled(False)


def _submenu(menu: QMenu, icon_name: str, text: str) -> QMenu:
    sub = QMenu(text, menu)  # parented: owned by `menu`, survives the Python reference
    sub.setIcon(icon(icon_name))
    menu.addMenu(sub)
    return sub


def _add(menu: QMenu, icon_name: str, text: str, slot) -> None:
    a = menu.addAction(icon(icon_name), with_mnemonic(menu, text))
    a.triggered.connect(slot)


def _open_on_tab(shell, program_id: str, tab: str, *_args) -> None:
    shell.navigate(program_id, {"tab": tab})


def _open_wizard(shell, *_args) -> None:
    shell.open_setup_wizard()


def open_document(path: str) -> None:
    """Hand an existing regular file to the OS; anything else is ignored."""
    if Path(path).is_file():
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def _fill_documents(menu: QMenu, settings, opener) -> None:
    menu.clear()
    docs = settings.recent_documents() if settings is not None else ()
    if not docs:
        _empty(menu)
        return
    for path in docs:
        name = Path(path).name
        exists = Path(path).is_file()
        label = (name if exists else f"{name} (missing)").replace("&", "&&")
        action = menu.addAction(icon("folder"), with_mnemonic(menu, label))
        action.setToolTip(path)
        action.setEnabled(exists)
        action.triggered.connect(partial(opener, path))


def build_start_menu(registry: Registry, shell, settings=None, opener=None) -> StartMenu:
    menu = StartMenu()
    programs = _submenu(menu, "folder", "&Programs")
    for m in registry.by_category("programs"):
        _add(programs, m.icon, m.title, partial(shell.open_program, m.id))
    programs.addSeparator()
    _add(programs, "camera", "Take Snapshot", shell.take_snapshot)
    _add(programs, "camera", "Resume Snapshot", partial(shell.resume_snapshot, (), False))
    accessories = _submenu(menu, "folder", "&Accessories")
    acc = registry.by_category("accessories")
    if acc:
        for m in acc:
            _add(accessories, m.icon, m.title, partial(shell.open_program, m.id))
    else:
        _empty(accessories)
    documents = _submenu(menu, "folder", "&Documents")
    documents.setToolTipsVisible(True)
    menu.documents_menu = documents
    opener = opener or open_document
    documents.aboutToShow.connect(partial(_fill_documents, documents, settings, opener))
    _fill_documents(documents, settings, opener)
    settings_menu = _submenu(menu, "gear", "&Settings")
    for m in registry.by_category("settings"):
        _add(settings_menu, m.icon, m.title, partial(shell.open_program, m.id))
    _add(settings_menu, "speaker", "S&ounds", partial(_open_on_tab, shell, "display", "sounds"))
    _add(settings_menu, "computer", "My Accounts…", partial(shell.open_program, "accounts"))
    _add(settings_menu, "gear", "AWS Setup &Wizard…", partial(_open_wizard, shell))
    _add(settings_menu, "recycle", "Clear &Local Data…", shell.clear_local_data)
    _add(menu, "magnifier", "&Find…\tCtrl+K", shell.show_palette)
    _add(menu, "help", "&Help", shell.show_about)
    _add(menu, "run", "&Run…", shell.show_palette)
    menu.addSeparator()
    _add(menu, "computer", "S&witch Account…", partial(shell.open_program, "accounts"))
    _add(menu, "power", "Sh&ut Down…", shell.request_shutdown)
    return menu


def popup_above(menu: QMenu, widget: QWidget) -> None:
    """Show `menu` with its bottom-left at the widget's top-left, clamped to the screen."""
    size = menu.sizeHint()
    top_left = widget.mapToGlobal(QPoint(0, 0))
    screen = widget.screen()
    avail = screen.availableGeometry()
    x = min(max(top_left.x(), avail.left()), max(avail.left(), avail.right() - size.width() + 1))
    y = top_left.y() - size.height()
    y = min(max(y, avail.top()), max(avail.top(), avail.bottom() - size.height() + 1))
    menu.popup(QPoint(x, y))
