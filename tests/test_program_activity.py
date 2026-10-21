import re
from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QDateTime, QTimeZone
from PySide6.QtWidgets import QLabel

from aws95.contracts.common import Page, Result, Scope, Status
from aws95.kit.widgets.stateview import ViewState
from aws95.programs.activity import ActivityProgram, make_activity
from aws95.programs.activity.common import fetch_all_events
from aws95.providers.demo import demo_provider_set


@pytest.fixture
def demo(program_env, qtbot):
    w, ctx = program_env(make_activity, demo_provider_set())
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.overview.chart.point_count >= 1)
    return w, ctx


def test_empty_mode(program_env, qtbot):
    w, _ = program_env(make_activity)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.overview.series_view.state is ViewState.NOT_CONNECTED)
    qtbot.waitUntil(lambda: w.overview.principals_view.state is ViewState.NOT_CONNECTED)


def test_default_range_is_seven_days(demo):
    w, _ = demo
    assert w.range_combo.currentData() == "7d" and w.bucket() == "day"
    tr = w.scope.time_range
    assert timedelta(days=7) - timedelta(seconds=5) <= tr.end - tr.start <= timedelta(days=7)
    assert not w.from_edit.isVisible()


def test_overview_tops_and_actors(demo, qtbot):
    w, _ = demo
    qtbot.waitUntil(lambda: w.overview.actors_label.text().startswith("Human: "))
    qtbot.waitUntil(lambda: len(w.overview.principals.visible_rows()) > 0)
    counts = [r["count"] for r in w.overview.principals.visible_rows()]
    assert counts == sorted(counts, reverse=True) and len(counts) <= 10
    assert w.overview.chart.point_count >= 1


def test_overview_secondary_loads_leave_status_to_the_series(demo, qtbot):
    w, _ = demo
    qtbot.waitUntil(lambda: w.overview.actors_label.text().startswith("Human: "))
    assert w.status.counts_label.text() == ""


def test_range_24h_uses_hour_buckets(demo, qtbot):
    w, _ = demo
    w.set_range("24h")
    assert w.bucket() == "hour"
    qtbot.waitUntil(lambda: w.overview.series_view.state in (ViewState.CONTENT, ViewState.EMPTY))


def test_refresh_clears_overview_when_load_starts(demo, qtbot):
    w, _ = demo
    qtbot.waitUntil(lambda: len(w.overview.principals.visible_rows()) > 0)
    w.refresh()
    assert w.overview.chart.point_count == 0
    assert w.overview.principals.visible_rows() == [] and w.overview.actors_label.text() == ""
    qtbot.waitUntil(lambda: len(w.overview.principals.visible_rows()) > 0)


def test_custom_range(demo, qtbot):
    w, _ = demo
    w.set_range("custom")
    assert w.from_edit.isVisible() and w.to_edit.isVisible()
    end = int(datetime.now(timezone.utc).timestamp()) // 60 * 60
    w.from_edit.setDateTime(QDateTime.fromSecsSinceEpoch(end - 3 * 86400, QTimeZone.utc()))
    w.to_edit.setDateTime(QDateTime.fromSecsSinceEpoch(end, QTimeZone.utc()))
    tr = w.time_range()
    assert tr.end - tr.start == timedelta(days=3)


def test_other_tabs_marked_dirty_on_refresh(demo, qtbot):
    w, _ = demo
    w.timeline.dirty = False
    w.refresh()
    assert w.timeline.dirty
    w.tabs.setCurrentWidget(w.timeline)
    assert not w.timeline.dirty


def test_timeline_root_principal(demo, qtbot):
    w, _ = demo
    w.tabs.setCurrentWidget(w.timeline)
    qtbot.waitUntil(lambda: len(w.timeline.principals.visible_rows()) > 0)
    root = [r for r in w.timeline.principals.visible_rows() if r["name"] == "root"][0]
    assert root["kind"] == "Human"
    assert w.timeline.select_principal("root")
    qtbot.waitUntil(lambda: len(w.timeline.events.visible_rows()) == 1)
    rows = w.timeline.events.visible_rows()
    assert rows[0]["action"] == "ConsoleLogin" and rows[0]["risk"] == 3
    assert not w.timeline.select_principal("nobody")


def test_timeline_events_newest_first_and_counts(demo, qtbot):
    w, _ = demo
    w.tabs.setCurrentWidget(w.timeline)
    qtbot.waitUntil(lambda: len(w.timeline.principals.visible_rows()) > 0)
    assert w.timeline.select_principal("ops-alice")
    qtbot.waitUntil(lambda: len(w.timeline.events.visible_rows()) > 1)
    times = [r["time"] for r in w.timeline.events.visible_rows()]
    assert times == sorted(times, reverse=True)
    n = len(times)
    qtbot.waitUntil(lambda: w.status.counts_label.text() == f"{n} of {n} events")
    w.timeline.events.set_filter_text("action:ConsoleLogin")
    shown = len(w.timeline.events.visible_rows())
    qtbot.waitUntil(lambda: w.status.counts_label.text() == f"{shown} of {n} events")


def test_timeline_refresh_clears_events(demo, qtbot):
    w, _ = demo
    w.tabs.setCurrentWidget(w.timeline)
    qtbot.waitUntil(lambda: len(w.timeline.principals.visible_rows()) > 0)
    w.timeline.select_principal("root")
    qtbot.waitUntil(lambda: len(w.timeline.events.visible_rows()) == 1)
    w.refresh()
    assert w.timeline.events.visible_rows() == [] and w.timeline.principals.visible_rows() == []


def test_event_dialog(demo, qtbot):
    w, _ = demo
    w.tabs.setCurrentWidget(w.timeline)
    qtbot.waitUntil(lambda: len(w.timeline.principals.visible_rows()) > 0)
    w.timeline.select_principal("root")
    qtbot.waitUntil(lambda: len(w.timeline.events.visible_rows()) == 1)
    dlg = w.open_event(w.timeline.events.visible_rows()[0]["event_id"])
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: '"eventName": "ConsoleLogin"' in dlg.json.toPlainText())
    assert dlg.view.state is ViewState.CONTENT
    assert not dlg.isModal()


def test_closing_program_closes_event_dialog(demo, qtbot):
    w, _ = demo
    dlg = w.open_event("evt-00260")
    qtbot.waitUntil(lambda: dlg.isVisible())
    w.close()
    qtbot.waitUntil(lambda: not dlg.isVisible())


def test_fetch_all_events_counts_everything():
    r = fetch_all_events(demo_provider_set().activity, Scope())
    assert len(r.data) == 260 and r.is_demo


class _Pager:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def events(self, scope, query=None, cursor=None, *, cancel=None):
        self.calls.append(cursor)
        return self.pages[cursor]


def test_fetch_all_events_limit_and_repeated_cursor():
    pages = {None: Result.ok(Page((1, 2), "a")), "a": Result.ok(Page((3, 4), "a"))}
    r = fetch_all_events(_Pager(pages), Scope())
    assert r.status is Status.ERROR
    pages = {None: Result.ok(Page((1, 2), "a")), "a": Result.ok(Page((3, 4), "b"))}
    r = fetch_all_events(_Pager(pages), Scope(), limit=3)
    assert r.data == (1, 2, 3) and r.message == "Showing the first 3 items."


def _letter(text):
    m = re.search(r"(?<!&)&([A-Za-z])", text.replace("&&", ""))
    return m.group(1).lower() if m else None


def test_no_duplicate_mnemonics(demo):
    w, _ = demo
    bar = [_letter(a.text()) for a in w.menu_bar.actions()]
    assert len(bar) == len(set(bar)), bar
    own = [_letter(a.text()) for a in w.program_menu.actions()]
    own = [x for x in own if x]
    assert len(own) == len(set(own))
    window = [x for x in (_letter(lb.text()) for lb in w.findChildren(QLabel)) if x]
    assert len(window) == len(set(window)), window
    assert not set(window) & set(bar), window


def test_preset_range_ends_exactly_at_clock(demo, monkeypatch):
    w, _ = demo
    fixed = datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=timezone.utc)
    monkeypatch.setattr(ActivityProgram, "clock", staticmethod(lambda: fixed))
    tr = w.time_range()
    assert tr.end == fixed and tr.end - tr.start == timedelta(days=7)


def test_switch_to_custom_seeds_from_current_preset(demo, monkeypatch):
    w, _ = demo
    fixed = datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=timezone.utc)
    monkeypatch.setattr(ActivityProgram, "clock", staticmethod(lambda: fixed))
    w.set_range("30d")
    w.set_range("custom")
    end = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    assert w.time_range().end == end and w.time_range().start == end - timedelta(days=30)


def test_empty_range_shows_empty_chart(demo, qtbot):
    w, _ = demo
    w.set_range("custom")
    w.from_edit.setDateTime(QDateTime.fromSecsSinceEpoch(946684800, QTimeZone.utc()))
    w.to_edit.setDateTime(QDateTime.fromSecsSinceEpoch(946771200, QTimeZone.utc()))
    qtbot.waitUntil(lambda: w.overview.series_view.state is ViewState.EMPTY)


def test_short_custom_range_uses_hour_buckets(demo):
    w, _ = demo
    w.set_range("custom")
    end = int(datetime.now(timezone.utc).timestamp())
    w.from_edit.setDateTime(QDateTime.fromSecsSinceEpoch(end - 48 * 3600, QTimeZone.utc()))
    w.to_edit.setDateTime(QDateTime.fromSecsSinceEpoch(end, QTimeZone.utc()))
    assert w.bucket() == "hour"
    w.from_edit.setDateTime(QDateTime.fromSecsSinceEpoch(end - 49 * 3600, QTimeZone.utc()))
    assert w.bucket() == "day"


def test_counts_reshown_when_returning_to_a_tab(demo, qtbot):
    w, _ = demo
    w.tabs.setCurrentWidget(w.timeline)
    qtbot.waitUntil(lambda: len(w.timeline.principals.visible_rows()) > 0)
    w.timeline.select_principal("root")
    qtbot.waitUntil(lambda: w.status.counts_label.text() == "1 of 1 events")
    w.tabs.setCurrentWidget(w.overview)
    assert w.status.counts_label.text() == ""
    w.tabs.setCurrentWidget(w.timeline)
    assert w.status.counts_label.text() == "1 of 1 events"
