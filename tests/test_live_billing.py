from datetime import timedelta
from decimal import Decimal

import pytest

from aws95.contracts.billing import BILLING_NOT_ENABLED
from aws95.contracts.common import Scope, Status
from aws95.providers.base import BillingProvider
from aws95.providers.live.accounts import LiveAccountsProvider
from aws95.providers.live.billing import LiveBilling
from aws95.providers.live.common import NO_SNAPSHOT
from tests.engine_helpers import build_snapshot
from tests.store_seed import DEV, PINNED, PROD, ROSTER, M, seed_store

ALL = Scope()


@pytest.fixture
def store(tmp_path):
    s = seed_store(tmp_path / "aws95.db")
    yield s
    s.close()


def roster():
    return ROSTER


def bill(store, clock=lambda: PINNED):
    return LiveBilling(store, roster, clock=clock)


def test_billing_protocol_is_satisfied(store):
    assert isinstance(bill(store), BillingProvider)


def test_billing_reads_never_touch_aws(store, monkeypatch):
    import aws95.aws.session as session_mod
    monkeypatch.setattr(session_mod, "session_for", lambda *a, **k: pytest.fail("a read opened an AWS session"))
    for r in (bill(store).summary(ALL), bill(store).trend(ALL), bill(store).breakdown(ALL), bill(store).free_tier(ALL)):
        assert r.status is not Status.ERROR, r.message


def test_billing_summary_from_the_snapshot(store):
    r = bill(store).summary(Scope(accounts=(DEV,)))
    s = r.data
    assert (s.mtd, s.last_month, s.forecast, s.currency) == (Decimal("45.6789"), Decimal("1234.56"),
                                                             Decimal("1345.6789"), "USD")
    assert r.status is Status.OK and r.freshness.source == "snapshot"

def test_billing_not_enabled_and_partial(store):
    assert bill(store).summary(Scope(accounts=(PROD,))).message == BILLING_NOT_ENABLED
    r = bill(store).summary(ALL)
    assert r.status is Status.PARTIAL and PROD in r.message and r.data.mtd == Decimal("45.6789")

def test_billing_summary_sums_accounts_exactly(make_store):
    """Review Focus 5."""
    s = make_store("b.db")
    t = PINNED - timedelta(hours=1)
    for account, amount in ((DEV, "0.0000001234"), (PROD, "12345678901234567890.123456789")):
        build_snapshot(s, t, account=account, collector="cost",
                       metrics=[M("cost.total.monthly", Decimal(amount), period="2026-10", unit="USD")])
    r = LiveBilling(s, roster, clock=lambda: PINNED).summary(ALL)
    assert r.data.mtd == Decimal("12345678901234567890.1234569124")
    assert r.data.forecast is None and r.data.last_month is None

def test_billing_breakdowns_trend_and_free_tier(store):
    dev = Scope(accounts=(DEV,))
    points = bill(store).breakdown(dev, "service").data
    assert [(p.dims["service"], p.amount) for p in points] == [
        ("Amazon Elastic Compute Cloud - Compute", Decimal("30.1234")), ("Amazon Simple Storage Service", Decimal("15.5555"))]
    [acct] = bill(store).breakdown(dev, "account").data                # billing-complete: real rows now
    assert (acct.dims["account"], acct.amount) == (DEV, Decimal("45.6789"))
    for dim in ("region", "usage_type"):                                 # seeded before region/usage existed
        r = bill(store).breakdown(dev, dim)
        assert r.status is Status.NOT_CONNECTED and "Not collected in the latest snapshot" in r.message
    assert bill(store).uncovered_spend(dev).data == ()                   # EC2 and S3 are both collected
    monthly = bill(store).trend(dev, "monthly").data
    assert [(p.period, p.amount) for p in monthly] == [("2026-09", Decimal("1234.56")), ("2026-10", Decimal("45.6789"))]
    assert [p.period for p in bill(store).trend(dev, "daily").data] == ["2026-10-01", "2026-10-02"]
    [ft] = bill(store).free_tier(dev).data
    assert (ft.service, ft.actual, ft.limit, ft.forecast, ft.unit) == ("AmazonEC2", Decimal("375.0"), Decimal("750.0"),
                                                                       Decimal("700.0"), "Hrs")

def test_billing_refuses_to_add_different_months(make_store):
    s = make_store("m.db")
    build_snapshot(s, PINNED, account=DEV, collector="cost",
                   metrics=[M("cost.total.monthly", Decimal("1"), period="2026-10", unit="USD")])
    build_snapshot(s, PINNED - timedelta(days=20), account=PROD, collector="cost",
                   metrics=[M("cost.total.monthly", Decimal("1"), period="2026-09", unit="USD")])
    r = LiveBilling(s, roster, clock=lambda: PINNED).summary(ALL)
    assert r.status is Status.ERROR and "different months" in r.message

def test_collector_health_from_the_latest_snapshot(store):
    p = LiveAccountsProvider(store=store, clock=lambda: PINNED)
    rows = {h.collector: h for h in p.collector_health(Scope(accounts=(PROD,))).data}
    assert len(rows) == 18 and rows["iam"].status is Status.DENIED and "cost" not in rows
    assert p.collector_health(Scope(accounts=("444444444444",))).message == NO_SNAPSHOT


def test_no_forecast_on_the_last_day_shows_none(make_store):
    """Review Minor 2: no forecast metric (last day of a month) gives no month-end forecast, never MTD alone."""
    s = make_store("last.db")
    build_snapshot(s, PINNED, account=DEV, collector="cost",
                   metrics=[M("cost.total.monthly", Decimal("10.00"), period="2026-10", unit="USD")])
    assert LiveBilling(s, roster, clock=lambda: PINNED).summary(Scope(accounts=(DEV,))).data.forecast is None


def test_live_provider_set_wires_billing_and_store_backed_health(tmp_path, qapp, store):
    from PySide6.QtCore import QSettings

    from aws95.core.settings import Settings
    from aws95.providers.live import live_provider_set
    settings = Settings(QSettings(str(tmp_path / "w.ini"), QSettings.Format.IniFormat))
    settings.set_roster(ROSTER)
    ps = live_provider_set(settings, store=store, clock=lambda: PINNED)
    assert ps.store is store and ps.is_live and isinstance(ps.billing, LiveBilling)
    assert ps.accounts.collector_health(Scope(accounts=(PROD,))).status is Status.OK
