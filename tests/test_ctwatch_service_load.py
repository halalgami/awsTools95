import threading
from datetime import UTC, datetime, timedelta

import pytest

from aws95.aws.engines.ctwatch import service as service_mod
from aws95.aws.engines.ctwatch.estimate import calls_needed, minutes
from aws95.aws.engines.ctwatch.fetcher import SETTLE
from aws95.aws.engines.ctwatch.scope import LOOKUP_PASSES
from aws95.aws.engines.ctwatch.service import (NO_HISTORY_UPDATE, ActivityBusy, ActivityService, days_key,
                                               regions_key)
from aws95.contracts.accounts import RosterEntry
from aws95.contracts.activity import ALL_REGIONS, HistoryRequest
from aws95.core.cancel import CancelToken
from aws95.store import EventRow
from tests.ctwatch_fakes import A, FakeTrail, RegionSession, denied, ev

NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)
SETTLED = NOW - SETTLE
DAY = timedelta(days=1)
H = timedelta(hours=1)
DEV = RosterEntry("dev", A)


@pytest.fixture
def caller(monkeypatch):
    who = {"account": A}

    def fake(session, cancel=None):
        return who["account"], f"arn:aws:iam::{who['account']}:user/auditor"
    monkeypatch.setattr(service_mod.identity, "caller", fake)
    return who


@pytest.fixture
def make(make_store):
    """make(clients, clock=...) -> (store, service); the store is closed at teardown (2b's make_store)."""
    def build(clients, clock=lambda: NOW):
        store = make_store()
        sess = RegionSession(clients)
        return store, ActivityService(store, session_factory=lambda profile: sess, clock=clock, sleep=lambda s: None)
    return build


def request(days=None, regions=("us-east-1",), mode="load", retention=90):
    return HistoryRequest((DEV,), {A: tuple(regions)}, days, mode, {A: "dev"}, retention)


def spans(store, kind="bulk", region="us-east-1"):
    return [(c.start, c.end) for c in store.coverage(A, region, kind)]


def bulk_calls(trail):
    return [c for c in trail.calls if c["LookupAttributes"][0]["AttributeKey"] == "ReadOnly"]


def window(call):
    return call["LookupAttributes"][0]["AttributeValue"], call["StartTime"], call["EndTime"]


def test_load_newest_first_records_settled_coverage_and_meta(make, caller):
    trail = FakeTrail([ev("old", NOW - 2 * DAY - H), ev("mid", NOW - DAY - H), ev("new", NOW - H),
                       ev("fresh", NOW - timedelta(minutes=5))])
    store, svc = make({("cloudtrail", "us-east-1"): trail})
    texts = []
    out = svc.load(request(days=3), progress=lambda p: texts.append(p.text))
    assert out.kind == "load" and out.runs[0].status == "complete" and out.runs[0].events_added == 4
    ends = [c["EndTime"] for c in bulk_calls(trail)]
    assert ends[0] == NOW and ends == sorted(ends, reverse=True)                # newest first, never back-tracking
    for rule in LOOKUP_PASSES:                                                 # coverage stops 15 min short (C3)
        assert spans(store, rule.key) == [(NOW - 3 * DAY, SETTLED)], rule.key
    assert store.get_meta(days_key(A)) == "3" and store.get_meta(regions_key(A)) == "us-east-1"
    assert "Activity dev: 0/3 days · us-east-1" in texts and texts[-1] == "Activity dev: 3/3 days"
    assert svc.running is None and svc.progress_text is None


def test_late_events_are_picked_up_by_the_next_update(make, caller):
    """Review C3 / Review Focus 1: CloudTrail publishes 5-15 minutes late; an update must still find the event."""
    clock = [NOW]
    trail = FakeTrail([ev("e1", NOW - H)])
    store, svc = make({("cloudtrail", "us-east-1"): trail}, clock=lambda: clock[0])
    svc.load(request(days=1))
    trail.details.append(ev("late", NOW - timedelta(minutes=10)))            # published after the first load
    clock[0] = NOW + timedelta(minutes=20)
    out = svc.load(request(mode="update"))
    assert out.runs[0].events_added == 1 and store.raw_event("late") is not None
    assert spans(store) == [(NOW - DAY, clock[0] - SETTLE)]


def test_targeted_passes_and_root_attribute(make, caller):
    trail = FakeTrail([ev("a1", NOW - H, "AssumeRole", source="sts.amazonaws.com", read_only=True),
                       ev("a2", NOW - H, "AssumeRole", ptype="AWSService", source="sts.amazonaws.com", read_only=True),
                       ev("r1", NOW - 2 * H, "ListBuckets", ptype="Root", source="s3.amazonaws.com", read_only=True),
                       ev("c1", NOW - 3 * H, "ConsoleLogin", source="signin.amazonaws.com", error="Failed")])
    store, svc = make({("cloudtrail", "us-east-1"): trail})
    svc.load(request(days=1))
    with store.reader() as db:
        ids = sorted(r[0] for r in db.execute("SELECT event_id FROM events"))
    assert ids == ["a1", "c1", "r1"]
    attrs = {(c["LookupAttributes"][0]["AttributeKey"], c["LookupAttributes"][0]["AttributeValue"]) for c in trail.calls}
    assert ("Username", "root") in attrs and ("EventName", "ConsoleLogin") in attrs and len(attrs) == 14


def test_resume_after_cancel_fills_only_the_gaps(make, caller):
    """Review Focus 1: nothing settled is fetched twice; only the unsettled last 15 minutes repeat."""
    token = CancelToken()
    count = [0]

    def stop_after_ten(kw):
        count[0] += 1
        if count[0] == 10:
            token.cancel()
        return None
    trail = FakeTrail([ev("e1", NOW - H), ev("e2", NOW - DAY - H)], fail=stop_after_ten)
    store, svc = make({("cloudtrail", "us-east-1"): trail})
    first = svc.load(request(days=2), cancel=token)
    assert first.cancelled and first.runs[0].status == "partial" and "Resume Loading" in first.runs[0].message
    assert len(trail.calls) == 10
    seen = [window(c) for c in trail.calls]
    trail.fail, trail.calls = None, []
    second = svc.load(request())                                   # days=None: the saved choice (2)
    assert second.runs[0].status == "complete"
    again = [window(c) for c in trail.calls]
    for attr, start, end in again:                                 # an overlap may only be the unsettled tail
        for attr0, start0, end0 in seen:
            if attr == attr0 and min(end, end0) > max(start, start0):
                assert max(start, start0) >= SETTLED, (attr, start, end)
    # 6 bulk + 4 targeted windows were done; those 5 passes repeat only their unsettled tail, the other 9 passes
    # still need day 1, and day 2 needs all 19 windows.
    assert len(again) == 5 + 9 + (6 + 13)
    for rule in LOOKUP_PASSES:
        assert spans(store, rule.key) == [(NOW - 2 * DAY, SETTLED)], rule.key


def test_coverage_never_claims_a_failed_window(make, caller):
    """Review Focus 1: a throttled window stays a gap; what was stored before it stays."""
    def throttle(kw):
        if kw["LookupAttributes"][0]["AttributeKey"] == "ReadOnly" and kw["EndTime"] == NOW - 8 * H:
            return denied("ThrottlingException")
        return None
    trail = FakeTrail([ev("e1", NOW - H), ev("e2", NOW - 9 * H)], fail=throttle)
    store, svc = make({("cloudtrail", "us-east-1"): trail})
    run = svc.load(request(days=1)).runs[0]
    assert run.status == "partial" and run.events_added == 1
    assert run.message == "us-east-1: AWS error: ThrottlingException"
    assert spans(store) == [(NOW - 8 * H, SETTLED)]
    assert store.raw_event("e2") is None


def test_load_identity_mismatch_writes_nothing(make, caller):
    """Review Focus 3."""
    caller["account"] = "210987654321"
    trail = FakeTrail([ev("e1", NOW - H)])
    store, svc = make({("cloudtrail", "us-east-1"): trail})
    run = svc.load(request(days=7)).runs[0]
    assert run.status == "failed" and "different account" in run.message and "Nothing was saved" in run.message
    assert trail.calls == [] and store.coverage(A) == [] and store.get_meta(days_key(A)) is None


def test_denied_region_is_named_and_others_finish(make, caller):
    ok = FakeTrail([ev("e1", NOW - H)])
    no = FakeTrail(fail=lambda kw: denied())
    store, svc = make({("cloudtrail", "us-east-1"): ok, ("cloudtrail", "eu-west-1"): no})
    run = svc.load(request(days=1, regions=("eu-west-1", "us-east-1"))).runs[0]
    assert run.status == "partial" and run.message == "eu-west-1: Permission denied for cloudtrail:LookupEvents."
    assert len(no.calls) == 1                                      # skipped for the rest of the run
    assert spans(store) == [(NOW - DAY, SETTLED)] and spans(store, region="eu-west-1") == []


def test_update_fetches_only_newer_than_coverage(make, caller):
    trail = FakeTrail([ev("old", NOW - 5 * H), ev("new", NOW - H)])
    store, svc = make({("cloudtrail", "us-east-1"): trail})
    store.record_coverage(A, "us-east-1", "bulk", NOW - 2 * DAY, NOW - 3 * H)
    out = svc.load(request(mode="update"))
    assert out.runs[0].status == "complete" and out.runs[0].events_added == 1
    assert trail.calls and all(c["StartTime"] >= NOW - 3 * H for c in trail.calls)
    assert {c["LookupAttributes"][0]["AttributeKey"] for c in trail.calls} == {"ReadOnly"}   # only covered passes
    assert spans(store) == [(NOW - 2 * DAY, SETTLED)]


def test_update_without_history_says_so(make, caller):
    trail = FakeTrail()
    store, svc = make({("cloudtrail", "us-east-1"): trail})
    run = svc.load(request(mode="update")).runs[0]
    assert run.status == "failed" and run.message == NO_HISTORY_UPDATE and trail.calls == []


def test_retention_caps_the_range_and_prunes(make, caller):
    trail = FakeTrail()
    store, svc = make({("cloudtrail", "us-east-1"): trail})
    old = EventRow("old", A, "us-east-1", NOW - 100 * DAY, "IAMUser", "alice", True, "iam.amazonaws.com",
                   "CreateUser", False, None, None, None, (), 3, "iam-escalation", "lookup", "{}")
    store.insert_events([old])
    svc.load(request(days=30, retention=3))
    assert spans(store) == [(NOW - 3 * DAY, SETTLED)] and store.raw_event("old") is None
    assert store.get_meta(days_key(A)) == "30"                     # the choice is kept; retention caps the range


def test_one_job_at_a_time(make, caller):
    gate_open = threading.Event()
    trail = FakeTrail(fail=lambda kw: (gate_open.wait(5), None)[1])
    store, svc = make({("cloudtrail", "us-east-1"): trail})
    t = threading.Thread(target=svc.load, args=(request(days=1),))
    t.start()
    try:
        for _ in range(500):
            if svc.running == "load":
                break
            threading.Event().wait(0.002)
        with pytest.raises(ActivityBusy):
            svc.load(request(days=1))
    finally:
        gate_open.set()
        t.join(5)
    assert svc.running is None


def test_estimate_is_one_call_per_region(make, caller):
    quiet = FakeTrail([ev(f"q{i}", NOW - timedelta(minutes=i + 1)) for i in range(10)])
    busy = FakeTrail([ev(f"b{i}", NOW - timedelta(seconds=i + 1)) for i in range(60)])
    no = FakeTrail(fail=lambda kw: denied())
    store, svc = make({("cloudtrail", "us-east-1"): quiet, ("cloudtrail", "eu-west-1"): busy,
                       ("cloudtrail", "me-south-1"): no})
    rows = svc.estimate(request(regions=("us-east-1", "eu-west-1", "me-south-1")))
    assert len(quiet.calls) == len(busy.calls) == len(no.calls) == 1
    by = {r.region: r for r in rows}
    assert dict(by["us-east-1"].minutes) == {d: minutes(calls_needed(10, d, 13)) for d in (7, 30, 90)}
    assert not by["us-east-1"].at_least and by["eu-west-1"].at_least
    assert by["me-south-1"].error == "Permission denied for cloudtrail:LookupEvents."
    total = by[ALL_REGIONS]
    assert total.account == "" and total.at_least
    assert dict(total.minutes) == {d: minutes(calls_needed(10, d, 13) + calls_needed(50, d, 13)) for d in (7, 30, 90)}
    assert store.coverage(A) == []                                 # an estimate writes nothing
