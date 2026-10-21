from datetime import datetime, timedelta, timezone

from aws95.contracts.accounts import RosterEntry
from aws95.contracts.common import Scope, Status
from aws95.contracts.snapshots import ResumeRequest, SnapshotOutcome, AccountRun
from aws95.providers.live.snapshots import LiveSnapshots, outcome_text

A, B = "123456789012", "210987654321"
NOW = datetime(2026, 10, 16, 9, tzinfo=timezone.utc)
ROSTER = (RosterEntry("dev", A), RosterEntry("prod", B))


def seed(store, account, states, taken_at=NOW - timedelta(hours=1)):
    sid = store.begin_snapshot(account, "arn", "3.0.0", ("us-east-1",), taken_at=taken_at)
    store.plan_sections(sid, [(c, "us-east-1") for c, _ in states])
    for c, state in states:
        if state == "running":
            store.start_section(sid, c, "us-east-1", NOW)
        elif state != "waiting":
            store.end_section(sid, c, "us-east-1", state, ended_at=NOW, error=None if state == "done" else "x")
    store.finish_snapshot(sid)
    return sid


def provider(store, service=None):
    return LiveSnapshots(store, lambda: ROSTER, clock=lambda: NOW, service_factory=(lambda s: service) if service else None)


def test_sections_of_latest_snapshot_per_account(make_store):
    s = make_store()
    seed(s, A, [("ec2", "done"), ("lambda", "timed_out")])
    secs = provider(s).sections(Scope()).data
    assert {(x.account, x.collector, x.state) for x in secs} == {(A, "ec2", "done"), (A, "lambda", "timed_out")}


def test_stages_report_progress_resume_window_and_billing(make_store):
    s = make_store()
    seed(s, A, [("ec2", "done"), ("lambda", "running"), ("cost", "failed")])
    seed(s, B, [("ec2", "denied"), ("kms", "denied")], taken_at=NOW - timedelta(hours=30))
    st = {x.account: x for x in provider(s).stages(Scope()).data}
    assert (st[A].done, st[A].total, st[A].running, st[A].billing_unfinished) == (1, 3, ("lambda us-east-1",), True)
    assert st[A].can_resume(NOW) and not st[B].can_resume(NOW) and st[B].denied == 2


def test_history_carries_section_counts(make_store):
    s = make_store()
    seed(s, A, [("ec2", "done"), ("lambda", "failed")])
    [snap] = provider(s).list_snapshots(Scope(accounts=(A,))).data
    assert (snap.sections_done, snap.sections_total) == (1, 2)


def test_resume_snapshot_calls_service_resume(make_store):
    calls = []

    class Svc:
        def resume(self, accounts, include, cancel=None, progress=None, labels=None, *, retry_denied=False):
            calls.append((accounts, include, retry_denied))
            return SnapshotOutcome((AccountRun(A, 1, "complete", "complete (2/2 sections)"),))
    s = make_store()
    r = provider(s, Svc()).resume_snapshot(Scope(), ResumeRequest((ROSTER[0],), True, frozenset({A}), {A: "dev"}))
    assert r.status is Status.OK and calls == [((ROSTER[0],), frozenset({A}), True)]
    assert r.message == "dev: complete (2/2 sections)"


def test_outcome_text_uses_the_section_summary():
    out = SnapshotOutcome((AccountRun(A, 1, "complete", "complete (3/3 sections)"),
                           AccountRun(B, 2, "partial", "2/42 sections done · 40 denied")), False)
    assert outcome_text(out, {A: "dev", B: "prod"}) == "dev: complete (3/3 sections) · prod: 2/42 sections done · 40 denied"
