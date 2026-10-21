from datetime import datetime

import pytest
from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import QLabel

import aws95
from aws95.core.settings import Settings
from aws95.core.tasks import TaskRunner
from aws95.programs.recycle_bin import make_recycle_bin
from aws95.providers.empty import empty_provider_set
from aws95.shell.about import TIPS, AboutDialog, TipDialog, should_show_tip
from aws95.shell.context import NullSound, ProgramContext, RecycledItem


class FakeBin(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self._items = [RecycledItem("billing", "Billing", datetime(2026, 10, 2, 9, 41))]
        self.restored = []

    def items(self):
        return tuple(self._items)

    def restore(self, index):
        self.restored.append(self._items.pop(index).program_id)
        self.changed.emit()

    def empty(self):
        self._items.clear()
        self.changed.emit()


class FakeShell:
    def __init__(self, b):
        self.b = b

    def recycle_bin(self):
        return self.b


@pytest.fixture
def settings(tmp_path, qapp):
    return Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))


def test_recycle_bin_restore_and_empty(settings, qtbot):
    b = FakeBin()
    runner = TaskRunner()
    rb = make_recycle_bin(ProgramContext(settings, runner, empty_provider_set(), NullSound(), FakeShell(b)))
    qtbot.addWidget(rb)
    assert rb.list_widget.count() == 1 and "Billing" in rb.list_widget.item(0).text()
    rb.list_widget.setCurrentRow(0)
    rb.restore_button.click()
    assert b.restored == ["billing"] and rb.list_widget.count() == 0
    assert not rb.restore_button.isEnabled() and not rb.empty_button.isEnabled()
    runner.shutdown()


def test_about_shows_version(qtbot):
    d = AboutDialog()
    qtbot.addWidget(d)
    texts = " ".join(lbl.text() for lbl in d.findChildren(QLabel))
    assert aws95.__version__ in texts and "Not affiliated" in texts


def test_about_shows_author_and_repo(qtbot):
    d = AboutDialog()
    qtbot.addWidget(d)
    assert aws95.AUTHOR == "Algam The Dev"
    assert aws95.REPO_URL == "https://github.com/halalgami/awsTools95"
    texts = " ".join(lbl.text() for lbl in d.findChildren(QLabel))
    assert "Algam The Dev" in texts and "github.com/halalgami/awsTools95" in texts
    link = d.repo_label
    assert link.openExternalLinks() and link.textFormat() == Qt.TextFormat.RichText
    assert f'href="{aws95.REPO_URL}"' in link.text()


def test_tips_first_run_then_off_by_default(settings, qtbot):
    assert should_show_tip(settings) is True
    d = TipDialog(settings)
    qtbot.addWidget(d)
    assert not d.show_box.isChecked()
    first = d.current_index
    d.next_tip()
    assert d.current_index == (first + 1) % len(TIPS)
    d.close_button.click()
    assert settings.tips_seen is True and settings.show_tips is False
    assert should_show_tip(settings) is False


def test_tips_kept_on_when_user_opts_in(settings, qtbot):
    d = TipDialog(settings)
    qtbot.addWidget(d)
    d.show_box.setChecked(True)
    d.close_button.click()
    assert should_show_tip(settings) is True


def test_recycle_manifest():
    from aws95.programs import DEFAULT_MANIFESTS
    from aws95.shell.registry import Registry
    m = Registry(DEFAULT_MANIFESTS).get("recycle")
    assert m.desktop_icon and m.category == "system"


def test_dialogs_take_focus_on_show(settings, qtbot):
    for d in (AboutDialog(), TipDialog(settings)):
        qtbot.addWidget(d)
        d.show()
        qtbot.waitUntil(lambda d=d: d.focusWidget() is not None and d.focusWidget().isDefault())
