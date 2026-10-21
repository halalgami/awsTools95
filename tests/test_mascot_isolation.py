"""The empty-state mascot is a process-wide global; conftest resets it for every test."""
from PySide6.QtGui import QPixmap

from aws95.kit.widgets import stateview


def test_a_test_may_set_the_mascot(qapp):
    stateview.set_mascot_pixmap(QPixmap(8, 8))
    assert stateview._mascot_pixmap is not None


def test_the_next_test_starts_without_it(qapp):
    assert stateview._mascot_pixmap is None
