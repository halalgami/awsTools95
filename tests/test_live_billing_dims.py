from datetime import datetime, timezone
from decimal import Decimal

from aws95.contracts.accounts import RosterEntry
from aws95.contracts.common import Scope, Status
from aws95.contracts.service_map import NO_COLLECTOR, NOT_RESOURCE
from aws95.providers.demo import demo_provider_set
from aws95.providers.live.billing import LiveBilling
from tests.store_seed import M

A, B = "123456789012", "210987654321"
NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
ROSTER = (RosterEntry("dev", A), RosterEntry("prod", B))


def snap(store, account, metrics, taken=NOW, cost=True):
    sid = store.begin_snapshot(account, "arn", "3.0.0", ("us-east-1",), taken_at=taken)
    if cost:
        store.record_collector(sid, "cost", "ok", None, 1, ["global"], ["global"], 0)
        store.write_metrics(sid, "cost", metrics, taken)
    store.finish_snapshot(sid)


def billing(store):
    return LiveBilling(store, lambda: ROSTER, clock=lambda: NOW)


def test_region_rows_per_account_exact(make_store):
    s = make_store()
    snap(s, A, [M("cost.region.mtd", Decimal("1.10"), {"region": "us-east-1"}, "2026-10", "USD"),
                M("cost.region.mtd", Decimal("1E-7"), {"region": "NoRegion"}, "2026-10", "USD")])
    snap(s, B, [M("cost.region.mtd", Decimal("20"), {"region": "us-east-1"}, "2026-10", "USD")])
    r = billing(s).breakdown(Scope(), "region")
    assert r.status is Status.OK
    rows = {(p.dims["account"], p.dims["region"]): p.amount for p in r.data}
    assert rows == {(A, "us-east-1"): Decimal("1.10"), (A, "NoRegion"): Decimal("1E-7"), (B, "us-east-1"): Decimal("20")}
    assert [p.amount for p in r.data] == sorted((p.amount for p in r.data), reverse=True)


def test_region_names_accounts_without_the_new_rows(make_store):
    """Review Focus 1."""
    s = make_store()
    snap(s, A, [M("cost.region.mtd", Decimal("1.10"), {"region": "us-east-1"}, "2026-10", "USD")])
    snap(s, B, [M("cost.service.monthly", Decimal("5"), {"service": "AWS Lambda"}, "2026-10", "USD")])
    r = billing(s).breakdown(Scope(), "region")
    assert r.status is Status.PARTIAL and f"Not collected in the latest snapshot for {B}" in r.message
    only_b = billing(s).breakdown(Scope(accounts=(B,)), "usage_type")
    assert only_b.status is Status.NOT_CONNECTED and B in only_b.message


def test_account_rows(make_store):
    s = make_store()
    snap(s, A, [M("cost.total.monthly", Decimal("301.15"), {}, "2026-10", "USD"),
                M("cost.total.monthly", Decimal("280"), {}, "2026-09", "USD")])
    snap(s, B, [M("cost.total.daily", Decimal("1.5"), {}, "2026-10-01", "USD"),
                M("cost.total.daily", Decimal("2.25"), {}, "2026-10-02", "USD")])
    r = billing(s).breakdown(Scope(), "account")
    assert {p.dims["account"]: p.amount for p in r.data} == {A: Decimal("301.15"), B: Decimal("3.75")}
    assert all(p.period == "2026-10" for p in r.data)


def test_mixed_months_is_an_error(make_store):
    """Review Focus 2."""
    s = make_store()
    snap(s, A, [M("cost.region.mtd", Decimal("1"), {"region": "us-east-1"}, "2026-10", "USD")])
    snap(s, B, [M("cost.region.mtd", Decimal("1"), {"region": "us-east-1"}, "2026-09", "USD")],
         taken=datetime(2026, 9, 30, tzinfo=timezone.utc))
    for dim in ("account", "region", "usage_type"):
        assert billing(s).breakdown(Scope(), dim).status is Status.ERROR


def test_billing_off_for_one_account_is_partial(make_store):
    """Review Focus 5."""
    s = make_store()
    snap(s, A, [M("cost.usage_type.mtd", Decimal("7.488"), {"usage_type": "BoxUsage"}, "2026-10", "USD")])
    snap(s, B, [], cost=False)
    r = billing(s).breakdown(Scope(), "usage_type")
    assert r.status is Status.PARTIAL and "Billing isn't enabled for" in r.message and B in r.message
    assert [p.dims["account"] for p in r.data] == [A]


def test_uncovered_lists_reasons_and_skips_covered(make_store):
    """Review Focus 4."""
    s = make_store()
    snap(s, A, [M("cost.service.monthly", Decimal("9.6125"), {"service": "Amazon WorkSpaces"}, "2026-10", "USD"),
                M("cost.service.monthly", Decimal("3.00"), {"service": "Tax"}, "2026-10", "USD"),
                M("cost.service.monthly", Decimal("50"), {"service": "AWS Lambda"}, "2026-10", "USD"),
                M("cost.service.monthly", Decimal("1"), {"service": "Amazon WorkSpaces"}, "2026-09", "USD")])
    r = billing(s).uncovered_spend(Scope(accounts=(A,)))
    assert r.status is Status.OK
    assert {(p.dims["service"], p.dims["reason"], p.amount) for p in r.data} == {
        ("Amazon WorkSpaces", NO_COLLECTOR, Decimal("9.6125")), ("Tax", NOT_RESOURCE, Decimal("3.00"))}


def test_unknown_dimension_is_an_error(make_store):
    assert billing(make_store()).breakdown(Scope(), "tag").status is Status.ERROR


def test_demo_answers_every_dimension_with_accounts():
    b = demo_provider_set().billing
    for dim in ("account", "region", "usage_type"):
        r = b.breakdown(Scope(), dim)
        assert r.status is Status.OK and r.is_demo and r.data and all(p.dims.get("account") for p in r.data), dim
    u = b.uncovered_spend(Scope())
    assert u.data and all(p.dims["reason"] in (NO_COLLECTOR, NOT_RESOURCE) for p in u.data)
    assert not [p for p in u.data if p.dims["service"] == "AWS Lambda"]
