from datetime import datetime, timezone

from aws95.contracts.accounts import RosterEntry
from aws95.contracts.snapshots import (STALE_AFTER, AccountAge, AccountRun, Snapshot, SnapshotOutcome,
                                       SnapshotProgress, SnapshotRequest)
from aws95.core.cancel import AnyToken, CancelToken


def test_progress_text():
    assert SnapshotProgress("123456789012", "dev", 7, 19, "me-south-1").text == "dev: 7/19 sections · me-south-1"
    assert SnapshotProgress("123456789012", "dev", 0, 19).text == "dev: 0/19 sections"


def test_request_defaults_and_outcome():
    req = SnapshotRequest((RosterEntry("dev", "123456789012"),))
    assert req.include_billing_for == frozenset() and dict(req.regions_for) == {} and dict(req.labels) == {}
    out = SnapshotOutcome((AccountRun("123456789012", 4, "partial", "x", 2),))
    assert out.cancelled is False and out.runs[0].denials == 2


def test_snapshot_regions_default_and_age():
    s = Snapshot(1, "123456789012", "arn", datetime(2026, 1, 1, tzinfo=timezone.utc), "3.0.0", "complete")
    assert s.regions == ()
    assert AccountAge("123456789012", None).taken_at is None
    assert STALE_AFTER.days == 1


def test_any_token():
    a, b = CancelToken(), CancelToken()
    t = AnyToken(a, None, b)
    assert not t.cancelled
    b.cancel()
    assert t.cancelled
    assert not AnyToken().cancelled
