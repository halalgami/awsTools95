from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from aws95.aws.engines.labsnap.analyze.diff import CrossAccountDiff, diff_snapshots
from aws95.aws.engines.labsnap.model import Metric, Resource
from aws95.store import Store
from tests.engine_helpers import build_snapshot

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def r(rid, region="global", **attrs):
    return Resource("s3.bucket", region, rid, attrs)


def test_detects_added_removed_and_changed(tmp_path):
    s = Store(tmp_path / "x.db")
    a = build_snapshot(s, T0, resources=[r("keep", enc=True), r("gone", enc=True)])
    b = build_snapshot(s, T0 + timedelta(days=1), resources=[r("keep", enc=False), r("new", enc=True)])
    d = diff_snapshots(s, a, b)
    assert [x.rid for x in d.added] == ["new"] and [x.rid for x in d.removed] == ["gone"]
    assert d.changed[0].rid == "keep" and d.changed[0].deltas == {"enc": (True, False)}
    assert d.excluded == []


def test_failed_collector_produces_no_removals_and_says_why(tmp_path):
    s = Store(tmp_path / "x.db")
    a = build_snapshot(s, T0, resources=[r("b1"), r("b2")])
    b = build_snapshot(s, T0 + timedelta(days=1), status="failed", covered=())
    d = diff_snapshots(s, a, b)
    assert d.removed == [] and d.added == [] and d.changed == []
    assert [(x.rid, x.reason) for x in d.excluded] == [
        ("b1", f"Not looked at: s3 failed in snapshot #{b}."), ("b2", f"Not looked at: s3 failed in snapshot #{b}.")]


def test_unscanned_region_is_excluded_not_removed(tmp_path):
    s = Store(tmp_path / "x.db")
    regions = ("us-east-1", "eu-west-1")
    a = build_snapshot(s, T0, requested=regions, covered=regions, resources=[r("euro", region="eu-west-1")])
    b = build_snapshot(s, T0 + timedelta(days=1), requested=regions, covered=("us-east-1",))
    d = diff_snapshots(s, a, b)
    assert d.removed == []
    assert [(x.rid, x.reason) for x in d.excluded] == [
        ("euro", f"Not looked at: s3 did not cover eu-west-1 in snapshot #{b}.")]
    assert any("eu-west-1" in n for n in d.not_compared)


def test_partial_resource_on_both_sides_is_changed_partial(tmp_path):
    s = Store(tmp_path / "x.db")
    a = build_snapshot(s, T0, resources=[Resource("s3.bucket", "global", "b1", {"x": 1}, partial=True)])
    b = build_snapshot(s, T0 + timedelta(days=1), resources=[r("b1", x=2)])
    [c] = diff_snapshots(s, a, b).changed
    assert c.partial is True and c.deltas == {}


def test_volatile_keys_are_not_changes(tmp_path):
    s = Store(tmp_path / "x.db")
    a = build_snapshot(s, T0, resources=[r("b1", creation_date="2026-01-01")])
    b = build_snapshot(s, T0 + timedelta(days=1), resources=[r("b1", creation_date="2026-02-02")])
    assert diff_snapshots(s, a, b).changed == []


def test_cross_account_is_refused(tmp_path):
    s = Store(tmp_path / "x.db")
    a = build_snapshot(s, T0)
    b = build_snapshot(s, T0 + timedelta(days=1), account="111111111111")
    with pytest.raises(CrossAccountDiff):
        diff_snapshots(s, a, b)


def test_metric_restatement_uses_an_exact_cent_floor(tmp_path):
    s = Store(tmp_path / "x.db")
    m = lambda v: Metric("cost.total.monthly", Decimal(v), period="2026-08", unit="USD")  # noqa: E731
    a = build_snapshot(s, T0, collector="cost", metrics=[m("839.72")])
    b = build_snapshot(s, T0 + timedelta(days=1), collector="cost", metrics=[m("839.725")])
    c = build_snapshot(s, T0 + timedelta(days=2), collector="cost", metrics=[m("840.00")])
    assert diff_snapshots(s, a, b).metric_changes == []
    [ch] = diff_snapshots(s, a, c).metric_changes
    assert (ch.before, ch.after, ch.restatement) == (Decimal("839.72"), Decimal("840.00"), True)
