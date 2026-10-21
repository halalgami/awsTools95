import zlib
from datetime import datetime, timedelta, timezone

import pytest

from aws95.store import EventRow

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _event(eid, when, origin="lookup", account="123456789012"):
    return EventRow(eid, account, "us-east-1", when, "IAMUser", "alice", True, "s3.amazonaws.com", "PutBucketPolicy",
                    False, None, "203.0.113.5", "console", ("arn:aws:s3:::example-bucket",), 2, "exposure", origin,
                    '{"eventID": "%s", "detail": "x"}' % eid)


def test_events_dedupe_compress_and_raw(make_store):
    s = make_store()
    assert s.insert_events([_event("e1", T0), _event("e1", T0), _event("e2", T0)]) == 2
    assert s.insert_events([_event("e1", T0)]) == 0
    with s.reader() as db:
        blob = db.execute("SELECT raw FROM events WHERE event_id='e1'").fetchone()[0]
    assert isinstance(blob, bytes) and blob[:1] == b"\x78"          # a zlib stream, not the plain JSON
    assert zlib.decompress(blob).decode() == s.raw_event("e1")
    assert s.raw_event("missing") is None


def test_coverage_merges_overlapping_and_adjacent(make_store):
    s = make_store()
    a = "123456789012"
    s.record_coverage(a, "us-east-1", "bulk", T0, T0 + timedelta(hours=4))
    s.record_coverage(a, "us-east-1", "bulk", T0 + timedelta(hours=4), T0 + timedelta(hours=8))
    s.record_coverage(a, "us-east-1", "bulk", T0 + timedelta(days=2), T0 + timedelta(days=3))
    s.record_coverage(a, "us-east-1", "root", T0, T0 + timedelta(hours=1))
    got = [(c.kind, c.start, c.end) for c in s.coverage(a, "us-east-1")]
    assert got == [("bulk", T0, T0 + timedelta(hours=8)), ("bulk", T0 + timedelta(days=2), T0 + timedelta(days=3)),
                   ("root", T0, T0 + timedelta(hours=1))]
    with pytest.raises(ValueError):
        s.record_coverage(a, "us-east-1", "bulk", T0, T0 - timedelta(hours=1))


def test_prune_keeps_imported_events_and_trims_lookup_coverage(make_store):
    s = make_store()
    a = "123456789012"
    s.insert_events([_event("old", T0 - timedelta(days=100)), _event("new", T0),
                     _event("imp", T0 - timedelta(days=100), origin="import")])
    s.record_coverage(a, "us-east-1", "bulk", T0 - timedelta(days=100), T0)
    s.record_coverage(a, "us-east-1", "import", T0 - timedelta(days=100), T0 - timedelta(days=99))
    assert s.prune_events(T0 - timedelta(days=90)) == 1
    with s.reader() as db:
        assert sorted(r[0] for r in db.execute("SELECT event_id FROM events")) == ["imp", "new"]
    cov = {c.kind: (c.start, c.end) for c in s.coverage(a)}
    assert cov["bulk"] == (T0 - timedelta(days=90), T0)
    assert cov["import"] == (T0 - timedelta(days=100), T0 - timedelta(days=99))
