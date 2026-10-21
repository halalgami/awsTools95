"""Window teardown must never delete a popup that another widget owns (segv root cause, M3)."""
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QMenuBar, QToolBar, QToolButton, QVBoxLayout, QWidget
from shiboken6 import isValid

from aws95.app import close_top_level_windows


def _flush_deletes(obj=None) -> None:
    QCoreApplication.sendPostedEvents(obj, QEvent.Type.DeferredDelete)


def test_close_top_level_windows_leaves_owned_popups_to_their_owner(qapp):
    # ProgramBase's shape: a menu-bar action shared with an overflowing toolbar, plus a combo.
    host = QWidget()
    bar = QMenuBar(host)
    bar.setNativeMenuBar(False)
    file_menu = bar.addMenu("&File")
    refresh = file_menu.addAction("&Refresh")       # owned by file_menu, also shown on the toolbar
    toolbar = QToolBar(host)
    toolbar.addAction(refresh)
    toolbar.addWidget(QLabel("x" * 200))             # too wide: the toolbar builds its overflow popup
    combo = QComboBox(host)
    combo.addItem("All accounts")
    layout = QVBoxLayout(host)
    layout.setMenuBar(bar)
    layout.addWidget(toolbar)
    layout.addWidget(combo)
    host.resize(200, 120)
    host.show()
    qapp.processEvents()

    # topLevelWidgets() lists owned popups too: the menu-bar menu and the toolbar's overflow menu.
    owned = [w for w in QApplication.topLevelWidgets() if w.parentWidget() is not None]
    assert file_menu in owned
    assert any(isinstance(w.parentWidget(), QToolButton) and w.parentWidget().parentWidget() is toolbar
               for w in owned)

    close_top_level_windows()
    # Deferred deletes run in post order, which follows topLevelWidgets()' hash order. Force the worst
    # order: if any owned popup was scheduled, it dies now, before its owner. QToolBarLayout keeps a raw
    # pointer to its overflow menu, so deleting the host afterwards would segfault in
    # QToolBarLayout::takeAt -> QWidget::removeAction.
    for w in owned:
        _flush_deletes(w)
    assert all(isValid(w) for w in owned), "an owned popup was deleted ahead of its owner"

    _flush_deletes()
    assert not isValid(host) and not isValid(toolbar) and not isValid(file_menu)
