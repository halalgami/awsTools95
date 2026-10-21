import os
from datetime import timedelta
from decimal import Decimal

import pytest

from aws95.contracts.common import Scope, Status
from aws95.contracts.snapshots import AccountRun, SnapshotOutcome, SnapshotRequest
from aws95.providers.base import ResourcesProvider, SnapshotsProvider
from aws95.providers.live.common import NO_ACCOUNTS, NO_SNAPSHOT
from aws95.providers.live.resources import LiveResources
from aws95.providers.live.snapshots import LiveSnapshots
from tests.store_seed import DEV, PINNED, PROD, ROSTER, seed_store

ALL = Scope()


@pytest.fixture
def store(tmp_path):
    s = seed_store(tmp_path / "aws95.db")
    yield s
    s.close()


def roster():
    return ROSTER


def snaps(store, clock=lambda: PINNED, **kw):
    return LiveSnapshots(store, roster, clock=clock, **kw)


def res(store, clock=lambda: PINNED):
    return LiveResources(store, roster, clock=clock)


def test_protocols_are_satisfied(store):
    assert isinstance(snaps(store), SnapshotsProvider)
    assert isinstance(res(store), ResourcesProvider)


def test_reads_never_touch_aws(store, monkeypatch):
    import aws95.aws.session as session_mod
    monkeypatch.setattr(session_mod, "session_for", lambda *a, **k: pytest.fail("a read opened an AWS session"))
    s = snaps(store, service_factory=lambda st: pytest.fail("a read built the snapshot service"))
    for r in (s.list_snapshots(ALL), s.ages(ALL), s.metric_names(ALL), s.metric_series(ALL, "cost.total.monthly"),
              res(store).resource_types(ALL), res(store).list_resources(ALL, "ec2.instance")):
        assert r.status is not Status.ERROR, r.message


def test_list_snapshots_with_regions_and_health(store):
    r = snaps(store).list_snapshots(ALL)
    assert r.status is Status.OK and r.freshness.source == "snapshot"
    by_account = {}
    for snap in r.data:
        by_account.setdefault(snap.account, []).append(snap)
    assert [len(by_account[DEV]), len(by_account[PROD])] == [2, 1]
    newest_dev = by_account[DEV][0]
    assert newest_dev.taken_at == PINNED - timedelta(hours=2) and newest_dev.regions == ("us-east-1", "eu-west-1")
    prod_health = {h.collector: h for h in by_account[PROD][0].collectors}
    assert prod_health["iam"].status is Status.DENIED
    assert prod_health["ec2"].status is Status.PARTIAL and prod_health["ec2"].coverage.regions_covered == ("us-east-1",)
    assert {h.collector: h.coverage.denials for h in newest_dev.collectors}["s3"] == 1

def test_ages_cover_every_roster_account(make_store, store):
    ages = {a.account_id: a.taken_at for a in snaps(store).ages(ALL).data}
    assert ages == {DEV: PINNED - timedelta(hours=2), PROD: PINNED - timedelta(hours=5)}
    empty = LiveSnapshots(make_store("empty.db"), roster, clock=lambda: PINNED).ages(ALL).data
    assert [a.taken_at for a in empty] == [None, None]

def test_diff_maps_every_kind(store):
    dev = sorted((s for s in snaps(store).list_snapshots(Scope(accounts=(DEV,))).data), key=lambda s: s.taken_at)
    entries = snaps(store).diff(ALL, dev[0].id, dev[1].id).data
    kinds = {(e.kind, e.rid) for e in entries}
    assert ("removed", "i-0ccc") in kinds and ("added", "i-0bbb") in kinds and ("added", "alice") in kinds
    prod = snaps(store).list_snapshots(Scope(accounts=(PROD,))).data[0]
    assert snaps(store).diff(ALL, dev[0].id, prod.id).status is Status.ERROR

def test_metric_series_marks_restatements(store):
    points = snaps(store).metric_series(Scope(accounts=(DEV,)), "cost.total.monthly").data
    sept = [p for p in points if p.period == "2026-09"]
    assert [(p.value, p.restated) for p in sept] == [(Decimal("1234.50"), False), (Decimal("1234.56"), True)]
    assert all(p.dims["account"] == DEV for p in points)
    assert "cost.total.monthly" in snaps(store).metric_names(ALL).data

def test_no_accounts_and_no_snapshot(make_store):
    empty = make_store("e.db")
    assert LiveSnapshots(empty, lambda: (), clock=lambda: PINNED).list_snapshots(ALL).message == NO_ACCOUNTS
    r = LiveResources(empty, roster, clock=lambda: PINNED).resource_types(ALL)
    assert r.status is Status.NOT_CONNECTED and r.message == NO_SNAPSHOT

def test_take_snapshot_delegates_and_reports(store):
    calls = []

    class FakeService:
        def take(self, accounts, billing, regions, cancel=None, progress=None, labels=None):
            calls.append((accounts, billing, dict(regions), dict(labels or {})))
            return SnapshotOutcome((AccountRun(DEV, 9, "complete"), AccountRun(PROD, 10, "partial", None, 3)))

    s = snaps(store, service_factory=lambda st: FakeService())
    req = SnapshotRequest(ROSTER, frozenset({DEV}), {DEV: ("us-east-1",), PROD: ("us-east-1",)}, {DEV: "dev", PROD: "prod"})
    r = s.take_snapshot(ALL, req)
    assert r.status is Status.PARTIAL and r.data.runs[1].denials == 3
    assert r.message == "dev: complete · prod: partial (3 denied)"
    assert calls[0][1] == frozenset({DEV})
    assert s.take_snapshot(ALL, None).status is Status.ERROR

def test_take_snapshot_busy_is_an_error(store):
    from aws95.aws.engines.labsnap.service import SnapshotBusy

    class Busy:
        def take(self, *a, **k):
            raise SnapshotBusy("A snapshot is already running.")
    r = snaps(store, service_factory=lambda st: Busy()).take_snapshot(ALL, SnapshotRequest(ROSTER))
    assert r.status is Status.ERROR and r.message == "A snapshot is already running."

def test_resource_types_count_latest_snapshot_per_account(store):
    types = {t.rtype: t for t in res(store).resource_types(ALL).data}
    assert types["ec2.instance"].count == 3                  # i-0aaa, i-0bbb (dev) + i-0ddd (prod)
    assert types["s3.bucket"].count == 3 and types["s3.bucket"].domain == "Storage"
    assert types["kms.key"].count == 0                       # kms looked and found none: listed with 0

def test_uncovered_region_is_not_collected_not_empty(store):
    """Review Focus 3."""
    r = res(store).list_resources(Scope(accounts=(PROD,), regions=("eu-west-1",)), "ec2.instance")
    assert r.status is Status.NOT_CONNECTED and r.message.startswith("Not collected in eu-west-1")
    r = res(store).list_resources(Scope(regions=("us-east-1", "eu-west-1")), "ec2.instance")
    assert r.status is Status.PARTIAL and "Not collected in eu-west-1" in r.message
    assert sorted(x.rid for x in r.data.items) == ["i-0aaa", "i-0bbb", "i-0ddd"]
    r = res(store).list_resources(Scope(accounts=(DEV,), regions=("ap-south-1",)), "ec2.instance")
    assert r.status is Status.NOT_CONNECTED and "ap-south-1" in r.message

def test_global_types_ignore_the_region_filter_and_mark_partial(store):
    r = res(store).list_resources(Scope(accounts=(DEV,), regions=("eu-west-1",)), "s3.bucket")
    rows = {x.rid: x for x in r.data.items}
    assert set(rows) == {"example-logs", "example-site"} and rows["example-site"].partial
    assert r.status is Status.PARTIAL and r.coverage.denials == 1

def test_looked_and_found_nothing_is_an_honest_empty(store):
    r = res(store).list_resources(Scope(accounts=(DEV,)), "kms.key")
    assert r.status is Status.OK and r.data.items == ()

def test_paging(store):
    first = res(store).list_resources(ALL, "ec2.instance", page_size=2)
    assert len(first.data.items) == 2 and first.data.next_cursor == "2" and first.data.total == 3
    second = res(store).list_resources(ALL, "ec2.instance", "2", page_size=2)
    assert len(second.data.items) == 1 and second.data.next_cursor is None

def test_stale_after_a_day(store):
    r = res(store, clock=lambda: PINNED + timedelta(days=3)).resource_types(ALL)
    assert r.status is Status.STALE and r.message.startswith("Snapshot is 3 days old")

def test_a_broken_store_is_an_error_not_empty(tmp_path, make_store):
    bad = tmp_path / "bad.db"          # make_store("bad.db") opens this same path
    bad.write_bytes(b"this is not a sqlite database" * 100)
    if os.name == "posix":
        os.chmod(bad, 0o600)
    r = LiveSnapshots(make_store("bad.db"), roster, clock=lambda: PINNED).list_snapshots(ALL)
    assert r.status is Status.ERROR and "local data" in r.message
