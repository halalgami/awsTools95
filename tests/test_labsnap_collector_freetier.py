# Upstream: labsnap/tests/test_collector_freetier.py @ 1b1d437 — adapted for aws95; owned here from now on.
from datetime import UTC, datetime

from aws95.aws.engines.labsnap.collect.collectors.freetier import FreeTierCollector


class FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kw):
        return iter(self._pages)


USAGE_PAGE = [{"freeTierUsages": [
    {"service": "AmazonEC2", "operation": "RunInstances", "usageType": "BoxUsage:t2.micro",
     "region": "us-east-1", "actualUsageAmount": 375.0, "forecastedUsageAmount": 700.0,
     "limit": 750.0, "unit": "Hrs"},
    {"service": "AmazonS3", "operation": "PutObject", "usageType": "Requests-Tier1",
     "region": "us-east-1", "actualUsageAmount": 0.0, "forecastedUsageAmount": 0.0,
     "limit": 2000.0, "unit": "Requests"},
    {"service": "AWSLambda", "operation": "Invoke", "usageType": "Request",
     "region": "us-east-1", "actualUsageAmount": 0.0, "forecastedUsageAmount": 0.0,
     "limit": 0.0, "unit": "Requests"},  # no limit - percentage is undefined
]}]


class FakeFreeTier:
    def get_paginator(self, op):
        assert op == "get_free_tier_usage"
        return FakePaginator(USAGE_PAGE)


class FakeSession:
    def client(self, name, **kw):
        assert name == "freetier"
        return FakeFreeTier()


def test_emits_no_resources():
    res = FreeTierCollector().collect(FakeSession(), "global")
    assert res.resources == []


def test_metric_dims_and_percentage_value():
    res = FreeTierCollector().collect(FakeSession(), "global")
    by_type = {m.dims["usage_type"]: m for m in res.metrics if m.name == "freetier.usage"}
    ec2 = by_type["BoxUsage:t2.micro"]
    assert ec2.dims["service"] == "AmazonEC2"
    assert ec2.dims["region"] == "us-east-1"
    assert ec2.value == 50.0  # 375 / 750 * 100


def test_period_is_current_month_and_day_of_month_is_carried():
    res = FreeTierCollector().collect(FakeSession(), "global")
    now = datetime.now(UTC)
    expected_period = now.strftime("%Y-%m")
    for m in res.metrics:
        assert m.period == expected_period
        assert m.dims["day_of_month"] == now.day


def test_zero_limit_row_is_omitted_not_reported_as_zero_percent():
    res = FreeTierCollector().collect(FakeSession(), "global")
    usage_types = {m.dims["usage_type"] for m in res.metrics}
    assert "Request" not in usage_types   # AWSLambda row had limit=0.0
    assert len([m for m in res.metrics if m.name == "freetier.usage"]) == 2


from decimal import Decimal  # noqa: E402


def test_actual_limit_and_forecast_are_exact_decimals():
    res = FreeTierCollector().collect(FakeSession(), "global")
    ec2 = {m.name: m for m in res.metrics if m.dims["usage_type"] == "BoxUsage:t2.micro"}
    assert ec2["freetier.actual"].value == Decimal("375.0") and ec2["freetier.actual"].unit == "Hrs"
    assert ec2["freetier.limit"].value == Decimal("750.0")
    assert ec2["freetier.forecast"].value == Decimal("700.0")
    assert all(isinstance(m.value, Decimal) for m in res.metrics)


def test_percentage_rounds_down_to_two_places():
    from aws95.aws.engines.labsnap.collect.collectors.freetier import percent
    assert percent(Decimal("375.0"), Decimal("750.0")) == Decimal("50.00")
    assert percent(Decimal("2"), Decimal("3")) == Decimal("66.66")
    assert percent(Decimal("999.96"), Decimal("1000")) == Decimal("99.99")
