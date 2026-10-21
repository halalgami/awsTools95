"""Demo data shown in a program's dialogs carries the watermark and the [DEMO] title (spec §9)."""
import pytest
from PySide6.QtCore import Qt

from aws95.kit.widgets.watermark import Watermark
from aws95.programs.activity import make_activity
from aws95.programs.findings import make_findings
from aws95.programs.snapshots import make_snapshots
from aws95.providers.demo import demo_provider_set


def _findings_ready(w):
    return len(w.grid.visible_rows()) == 13


def _mute(w):
    w.grid.view.selectRow(0)
    return w.open_mute_dialog()


def _activity_ready(w):
    return w.is_demo


def _snapshots_ready(w):
    return len(w.history.visible_rows()) == 6


def _diff(w):
    w.select_snapshot(203)
    return w.compare_previous()


CASES = [
    (make_findings, _findings_ready, _mute, "Accept finding"),
    (make_findings, _findings_ready, lambda w: w.open_rules_dialog(), "Rules"),
    (make_findings, _findings_ready, lambda w: (w.grid.view.selectRow(0), w.open_seed_dialog())[1], "Seed finding"),
    (make_findings, _findings_ready, lambda w: w.open_backfill_dialog(), "Backfill findings"),
    (make_activity, _activity_ready, lambda w: w.open_event("evt-00260"), "Event evt-00260"),
    (make_activity, _activity_ready, lambda w: w.open_history(), "History coverage"),
    (make_snapshots, _snapshots_ready, _diff, "Compare snapshots"),
]


def _marked(dlg, title):
    return dlg.windowTitle() == f"{title} [DEMO]" and dlg.findChild(
        Watermark, options=Qt.FindChildOption.FindDirectChildrenOnly) is not None


@pytest.mark.parametrize("factory, ready, open_dialog, title", CASES,
                         ids=[c[3].split()[0].lower() for c in CASES])
def test_demo_dialog_is_watermarked_and_titled(program_env, qtbot, factory, ready, open_dialog, title):
    w, _ = program_env(factory, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: ready(w))
    dlg = open_dialog(w)
    assert dlg is not None
    qtbot.waitUntil(lambda: _marked(dlg, title))
    assert len(dlg.findChildren(Watermark)) == 1
    w.mark_demo()                                           # marking twice adds nothing
    assert dlg.windowTitle() == f"{title} [DEMO]" and len(dlg.findChildren(Watermark)) == 1
    dlg.close()


def test_dialog_marked_when_demo_is_learned_from_its_own_load(program_env, qtbot):
    w, _ = program_env(make_activity, demo_provider_set())
    qtbot.addWidget(w)                                      # never shown: the program has loaded nothing yet
    assert not w.is_demo
    dlg = w.open_history()
    assert dlg.windowTitle() == "History coverage"
    qtbot.waitUntil(lambda: _marked(dlg, "History coverage"))
    dlg.close()


def test_real_data_dialog_is_not_marked(program_env, qtbot):
    w, _ = program_env(make_findings)
    qtbot.addWidget(w)
    dlg = w.open_backfill_dialog()               # Seed needs a selected finding; offline has none
    assert dlg.windowTitle() == "Backfill findings" and not dlg.findChildren(Watermark)
    dlg.close()
