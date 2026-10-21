import pytest

from aws95.aws.engines.labsnap.collect.base import all_collectors
import aws95.aws.engines.labsnap.collect.collectors  # noqa: F401 - registers collectors
from aws95.aws.engines.labsnap.rules.base import DuplicateRule, RuleOverrides, load_rules, register_rule
from aws95.aws.engines.labsnap.rules.sources import RULE_COLLECTORS


def test_builtin_rules_each_with_known_sources():
    rules = load_rules()
    assert len(rules) == 44
    assert [r.id for r in rules] == sorted(r.id for r in rules)
    assert set(RULE_COLLECTORS) == {r.id for r in rules}
    known = set(all_collectors())
    assert all(v and set(v) <= known for v in RULE_COLLECTORS.values())


def test_overrides_are_a_test_hook_only():
    assert not hasattr(RuleOverrides(), "extra_dirs")
    rules = load_rules(RuleOverrides(disabled={"s3.no_lifecycle"}, severity={"iam.key_stale": "low"},
                                     params={"iam.key_stale": {"max_age_days": 30}}))
    by_id = {r.id: r for r in rules}
    assert "s3.no_lifecycle" not in by_id
    assert by_id["iam.key_stale"].severity == "low" and by_id["iam.key_stale"].params == {"max_age_days": 30}
    assert load_rules()[0].severity in {"critical", "high", "medium", "low"}     # shipped values untouched


def test_duplicate_rule_id_is_refused():
    load_rules()
    with pytest.raises(DuplicateRule):
        register_rule(type("Impostor", (), {"id": "s3.no_lifecycle"}))


from datetime import datetime, timezone  # noqa: E402
from decimal import Decimal  # noqa: E402

from aws95.aws.engines.labsnap.model import Metric, Resource  # noqa: E402
from aws95.aws.engines.labsnap.rules.view import SnapshotView  # noqa: E402
from aws95.store import Store, ts  # noqa: E402
from tests.engine_helpers import build_snapshot  # noqa: E402

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
A = "123456789012"


def test_snapshot_view_reads_decimal_metrics_and_partial_resources(tmp_path):
    s = Store(tmp_path / "x.db")
    sid = build_snapshot(s, T0, resources=[Resource("s3.bucket", "global", "b1", {}, partial=True)],
                         metrics=[Metric("s3.bucket_count", Decimal(1))])
    v = SnapshotView(s, sid)
    assert (v.snapshot_id, v.account, v.taken_at) == (sid, A, ts(T0))
    assert v.resources("s3.bucket")[0].partial is True and v.resources("ec2.instance") == []
    assert v.metrics("s3.bucket_count")[0].value == Decimal(1)


def test_snapshot_view_maps_an_empty_period_back_to_none(tmp_path):
    s = Store(tmp_path / "x.db")
    sid = build_snapshot(s, T0, metrics=[Metric("s3.bucket_count", Decimal(1)),
                                         Metric("cost.total.monthly", Decimal("1.5"), period="2026-08", unit="USD")])
    periods = {m.name: m.period for m in SnapshotView(s, sid).metrics()}
    assert periods == {"s3.bucket_count": None, "cost.total.monthly": "2026-08"}
