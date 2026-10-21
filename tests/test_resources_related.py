import dataclasses
import threading
from datetime import datetime, timedelta, timezone

from aws95.contracts.activity import ActivityEvent
from aws95.contracts.common import Page, Result
from aws95.contracts.resources import ResourceRow
from aws95.programs.resources import make_resources
from aws95.programs.resources.related import CAPPED, EMPTY, LIMIT, NOTE, PICK
from aws95.providers.demo import demo_provider_set

T0 = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
A, B = "111111111111", "222222222222"


def event(n, account=A, rid="i-1"):
    return ActivityEvent(f"e{n}", account, T0 - timedelta(minutes=n), "IAMUser", "alice", True, "ec2.amazonaws.com",
                         "StopInstances", False, None, None, (f"arn:aws:ec2:us-east-1:{account}:instance/{rid}",),
                         "us-east-1", 0, None)


class Activity:
    def __init__(self, by=None, gate=None):
        self.by, self.gate, self.calls = by or {}, gate, []

    def events(self, scope, query=None, cursor=None, *, cancel=None):
        self.calls.append((scope.accounts, query.arn_contains))
        if self.gate is not None and query.arn_contains == "slow":
            self.gate.wait(2)
        got = self.by.get((scope.accounts[0], query.arn_contains))
        if isinstance(got, Result):
            return got
        return Result.ok(Page(tuple(got or ())))

    def __getattr__(self, name):
        return getattr(demo_provider_set().activity, name)


def make(program_env, qtbot, activity):
    ps = dataclasses.replace(demo_provider_set(), activity=activity)
    w, ctx = program_env(make_resources, ps)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: bool(w.grid.visible_rows()), timeout=3000)    # the window's own first load is done
    return w, ctx


def row(rid="i-1", account=A):
    return ResourceRow(account, "ec2.instance", "us-east-1", rid, rid, "running")


def test_rows_are_the_providers_for_that_account(program_env, qtbot):
    """Review Focus 2."""
    act = Activity({(A, "i-1"): [event(2), event(1)]})
    w, _ = make(program_env, qtbot, act)
    w.related.show_for(row())
    qtbot.waitUntil(lambda: len(w.related.grid.visible_rows()) == 2, timeout=3000)
    assert act.calls[-1] == ((A,), "i-1")
    times = [r["time"] for r in w.related.grid.visible_rows()]
    assert times == sorted(times, reverse=True) and w.related.note.text() == NOTE


def test_no_history_shows_the_providers_message(program_env, qtbot):
    """Review Focus 3."""
    act = Activity({(A, "i-1"): Result.not_connected("No Activity history is loaded for 111111111111.")})
    w, _ = make(program_env, qtbot, act)
    w.related.show_for(row())
    qtbot.waitUntil(lambda: "No Activity history" in w.related.view._body.text(), timeout=3000)


def test_no_matches_says_so(program_env, qtbot):
    w, _ = make(program_env, qtbot, Activity())
    w.related.show_for(row())
    qtbot.waitUntil(lambda: w.related.view._body.text() == EMPTY, timeout=3000)


def test_cap_adds_the_note(program_env, qtbot):
    act = Activity({(A, "i-1"): [event(n) for n in range(LIMIT)]})
    w, _ = make(program_env, qtbot, act)
    w.related.show_for(row())
    qtbot.waitUntil(lambda: len(w.related.grid.visible_rows()) == LIMIT, timeout=3000)
    assert w.related.note.text() == f"{NOTE} {CAPPED}"


def test_quick_reselect_never_shows_old_rows(program_env, qtbot):
    """Review Focus 1."""
    gate = threading.Event()
    act = Activity({(A, "slow"): [event(1, rid="slow")], (A, "i-2"): [event(2, rid="i-2")]}, gate)
    w, _ = make(program_env, qtbot, act)
    w.related.show_for(row("slow"))
    w.related.show_for(row("i-2"))
    qtbot.waitUntil(lambda: len(w.related.grid.visible_rows()) == 1, timeout=3000)
    gate.set()
    qtbot.wait(200)
    assert [r["action"] for r in w.related.grid.visible_rows()] == ["StopInstances"]
    assert all("i-2" in r["arns"] for r in w.related.grid.visible_rows())


def test_clear_and_open_in_activity_log(program_env, qtbot):
    w, ctx = make(program_env, qtbot, Activity({(A, "i-1"): [event(1)]}))
    w.related.show_for(row())
    w.related.open_button.click()
    assert ctx.shell.calls[-1] == ("navigate", ("activity", {"arn": "i-1", "account": A}))
    w.related.show_for(None)
    assert not w.related.open_button.isEnabled() and w.related.view._body.text() == PICK


def test_inspector_hosts_the_panel(program_env, qtbot):
    w, _ = make(program_env, qtbot, Activity())
    assert w.inspector.related_view is w.related
