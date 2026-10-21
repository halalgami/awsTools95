# Upstream: labsnap/tests/test_collector_cost.py @ 1b1d437 — rewritten for aws95's Decimal cost collector.
import ast
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from aws95.aws.engines.labsnap.collect.collectors import cost as cost_mod
from aws95.aws.engines.labsnap.collect.collectors.cost import BILLED_CALLS, CostCollector
from aws95.contracts.billing import BILLING_COST_NOTE

ACCOUNT = "123456789012"
ONLY_ME = {"Dimensions": {"Key": "LINKED_ACCOUNT", "Values": [ACCOUNT]}}
RDS = {"Keys": ["Amazon Relational Database Service"], "Metrics": {"UnblendedCost": {"Amount": "202.91", "Unit": "USD"}}}
S3 = {"Keys": ["Amazon Simple Storage Service"], "Metrics": {"UnblendedCost": {"Amount": "636.81", "Unit": "USD"}}}
AUG = {"Start": "2026-08-01", "End": "2026-09-01"}
SEP = {"Start": "2026-09-01", "End": "2026-09-08"}
REGION_GROUPS = [{"Keys": ["us-east-1"], "Metrics": {"UnblendedCost": {"Amount": "1.10", "Unit": "USD"}}},
                 {"Keys": ["NoRegion"], "Metrics": {"UnblendedCost": {"Amount": "1E-7", "Unit": "USD"}}}]
USAGE_GROUPS = [{"Keys": ["USE1-BoxUsage:t3.micro"], "Metrics": {"UnblendedCost": {"Amount": "7.488", "Unit": "USD"}}}]


def _err(code):
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "Op")


class FakeCE:
    def __init__(self, *, total=None, forecast_error=None, tags_error=None, paged=False, repeat_token=False,
                 group_error=None):
        self.total = total or {}
        self.forecast_error = forecast_error
        self.tags_error = tags_error
        self.paged = paged
        self.repeat_token = repeat_token
        self.group_error = group_error
        self.calls = []

    def get_cost_and_usage(self, **kw):
        self.calls.append(("get_cost_and_usage", kw))
        key = (kw.get("GroupBy") or [{}])[0].get("Key")
        if key in ("REGION", "USAGE_TYPE"):
            if self.group_error and key == "REGION":
                raise _err(self.group_error)
            return {"ResultsByTime": [{"TimePeriod": SEP, "Total": {},
                                       "Groups": REGION_GROUPS if key == "REGION" else USAGE_GROUPS}]}
        if kw["Granularity"] == "MONTHLY":
            if self.total:
                return {"ResultsByTime": [{"TimePeriod": AUG, "Total": self.total, "Groups": []}]}
            if not self.paged:
                return {"ResultsByTime": [{"TimePeriod": AUG, "Total": {}, "Groups": [RDS, S3]}]}
            if kw.get("NextPageToken") == "p2" and not self.repeat_token:
                return {"ResultsByTime": [{"TimePeriod": AUG, "Total": {}, "Groups": [S3]}]}
            return {"ResultsByTime": [{"TimePeriod": AUG, "Total": {}, "Groups": [RDS]}], "NextPageToken": "p2"}
        return {"ResultsByTime": [
            {"TimePeriod": {"Start": "2026-09-01", "End": "2026-09-02"},
             "Total": {"UnblendedCost": {"Amount": "1.2E-7", "Unit": "USD"}}, "Groups": []},
            {"TimePeriod": {"Start": "2026-09-02", "End": "2026-09-03"},
             "Total": {"UnblendedCost": {"Amount": "12345678901234567890.123456789", "Unit": "USD"}},
             "Groups": []}]}

    def get_cost_forecast(self, **kw):
        self.calls.append(("get_cost_forecast", kw))
        if self.forecast_error:
            raise _err(self.forecast_error)
        return {"Total": {"Amount": "0.0000001234", "Unit": "USD"}, "ForecastResultsByTime": []}

    def list_cost_allocation_tags(self, **kw):
        self.calls.append(("list_cost_allocation_tags", kw))
        if self.tags_error:
            raise _err(self.tags_error)
        return {"CostAllocationTags": [{"TagKey": "project"}, {"TagKey": "env"}]}


class FakeSTS:
    def get_caller_identity(self):
        return {"Account": ACCOUNT, "Arn": f"arn:aws:iam::{ACCOUNT}:user/auditor"}


class FakeSession:
    def __init__(self, **kw):
        self.ce = FakeCE(**kw)
        self.sts = FakeSTS()
        self.asked = []

    def client(self, name, **kw):
        self.asked.append((name, kw.get("region_name")))
        return self.sts if name == "sts" else self.ce


def _run(monkeypatch, today=date(2026, 9, 7), **kw):
    monkeypatch.setattr(cost_mod, "_today", lambda: today)
    s = FakeSession(**kw)
    return CostCollector().collect(s, "global"), s


def _named(res, name):
    return [m for m in res.metrics if m.name == name]


def _calls(s, op):
    return [kw for name, kw in s.ce.calls if name == op]


def test_monthly_total_is_an_exact_decimal_sum_of_the_service_partition(monkeypatch):
    res, _ = _run(monkeypatch)
    [total] = _named(res, "cost.total.monthly")
    assert total.value == Decimal("839.72") and isinstance(total.value, Decimal)
    assert total.period == "2026-08" and total.unit == "USD"


def test_monthly_total_uses_the_api_total_when_populated(monkeypatch):
    res, _ = _run(monkeypatch, total={"UnblendedCost": {"Amount": "839.720000001", "Unit": "USD"}})
    [total] = _named(res, "cost.total.monthly")
    assert total.value == Decimal("839.720000001")


def test_per_service_costs_are_dimensioned_not_named(monkeypatch):
    res, _ = _run(monkeypatch)
    svc = {m.dims["service"]: m.value for m in _named(res, "cost.service.monthly")}
    assert svc == {"Amazon Relational Database Service": Decimal("202.91"),
                   "Amazon Simple Storage Service": Decimal("636.81")}


def test_cost_amounts_are_exact_decimals(monkeypatch):
    """Review Focus 5: exponent and 20-digit amounts survive unchanged."""
    res, _ = _run(monkeypatch)
    daily = {m.period: m.value for m in _named(res, "cost.total.daily")}
    assert daily == {"2026-09-01": Decimal("1.2E-7"), "2026-09-02": Decimal("12345678901234567890.123456789")}
    assert _named(res, "cost.forecast.remaining")[0].value == Decimal("0.0000001234")
    assert all(isinstance(m.value, Decimal) for m in res.metrics)


def test_every_query_is_filtered_to_this_account(monkeypatch):
    """Review C2: a payer account must never report its members' spend as its own."""
    _, s = _run(monkeypatch)
    queries = _calls(s, "get_cost_and_usage") + _calls(s, "get_cost_forecast")
    assert len(queries) == 5 and all(q["Filter"] == ONLY_ME for q in queries)


def test_monthly_pages_are_followed_and_merged(monkeypatch):
    """Review I5: a month's groups split across two pages still give one exact total."""
    res, s = _run(monkeypatch, paged=True)
    monthly = [kw for kw in _calls(s, "get_cost_and_usage") if kw.get("GroupBy") == [{"Type": "DIMENSION", "Key": "SERVICE"}]]
    assert len(monthly) == 2 and "NextPageToken" not in monthly[0] and monthly[1]["NextPageToken"] == "p2"
    [total] = _named(res, "cost.total.monthly")
    assert total.value == Decimal("839.72")
    assert len(_named(res, "cost.service.monthly")) == 2


def test_a_repeated_page_token_fails_loudly(monkeypatch):
    with pytest.raises(RuntimeError, match="repeated a page token"):
        _run(monkeypatch, paged=True, repeat_token=True)


def test_request_windows_today_is_month_to_date(monkeypatch):
    _, s = _run(monkeypatch)
    monthly, daily = _calls(s, "get_cost_and_usage")[:2]
    assert monthly["TimePeriod"] == {"Start": "2025-09-01", "End": "2026-09-08"}
    assert monthly["Granularity"] == "MONTHLY" and monthly["GroupBy"] == [{"Type": "DIMENSION", "Key": "SERVICE"}]
    assert daily["TimePeriod"] == {"Start": "2026-09-01", "End": "2026-09-08"} and daily["Granularity"] == "DAILY"
    [forecast] = _calls(s, "get_cost_forecast")
    assert forecast["TimePeriod"] == {"Start": "2026-09-08", "End": "2026-10-01"}      # starts TOMORROW
    assert forecast["Metric"] == "UNBLENDED_COST" and forecast["Granularity"] == "MONTHLY"
    assert all(region == "us-east-1" for _, region in s.asked)


def test_no_forecast_request_on_the_last_day_of_a_month(monkeypatch):
    res, s = _run(monkeypatch, today=date(2026, 12, 31))
    assert _calls(s, "get_cost_and_usage")[0]["TimePeriod"] == {"Start": "2025-12-01", "End": "2027-01-01"}
    assert _calls(s, "get_cost_forecast") == [] and _named(res, "cost.forecast.remaining") == []
    assert len(s.ce.calls) == BILLED_CALLS - 1


def test_exactly_the_billed_calls_and_the_note_matches(monkeypatch):
    _, s = _run(monkeypatch)
    assert len(s.ce.calls) == BILLED_CALLS == 6
    assert "$0.06" in BILLING_COST_NOTE


def test_no_forecast_yet_is_not_a_failure(monkeypatch):
    res, _ = _run(monkeypatch, forecast_error="DataUnavailableException")
    assert _named(res, "cost.forecast.remaining") == [] and res.denials == 0


def test_denied_forecast_counts_a_denial_and_keeps_the_rest(monkeypatch):
    res, _ = _run(monkeypatch, forecast_error="AccessDeniedException")
    assert _named(res, "cost.forecast.remaining") == [] and res.denials == 1
    assert _named(res, "cost.total.monthly")


def test_active_allocation_tags_metric(monkeypatch):
    res, _ = _run(monkeypatch)
    assert _named(res, "cost.active_allocation_tags")[0].value == Decimal(2)


def test_denied_allocation_tags_omits_the_metric(monkeypatch):
    res, _ = _run(monkeypatch, tags_error="AccessDeniedException")
    assert _named(res, "cost.active_allocation_tags") == [] and res.denials == 1


def test_cost_emits_no_resources_and_is_billed(monkeypatch):
    res, _ = _run(monkeypatch)
    assert res.resources == [] and res.regions_covered == ["global"]
    assert CostCollector.billed is True


def test_no_float_in_the_cost_collector():
    tree = ast.parse(Path(cost_mod.__file__).read_text())
    assert not [n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "float"]


def test_exact_sum_never_rounds():
    from aws95.contracts.common import exact_add, exact_sum
    a, b = Decimal("0.0000001234"), Decimal("12345678901234567890.123456789")
    assert exact_sum([a, b]) == Decimal("12345678901234567890.1234569124")
    assert a + b != exact_add(a, b)              # the default 28-digit context would have rounded


def test_region_and_usage_type_month_to_date(monkeypatch):
    res, s = _run(monkeypatch)
    grouped = [kw for kw in _calls(s, "get_cost_and_usage")
               if kw.get("GroupBy", [{}])[0].get("Key") in ("REGION", "USAGE_TYPE")]
    assert [g["GroupBy"][0]["Key"] for g in grouped] == ["REGION", "USAGE_TYPE"]
    assert all(g["TimePeriod"] == {"Start": "2026-09-01", "End": "2026-09-08"} and g["Granularity"] == "MONTHLY"
               and g["Filter"] == ONLY_ME and g["Metrics"] == ["UnblendedCost"] for g in grouped)
    region = {m.dims["region"]: m for m in _named(res, "cost.region.mtd")}
    assert region["us-east-1"].value == Decimal("1.10") and region["NoRegion"].value == Decimal("1E-7")
    assert region["us-east-1"].period == "2026-09" and region["us-east-1"].unit == "USD"
    [usage] = _named(res, "cost.usage_type.mtd")
    assert usage.dims == {"usage_type": "USE1-BoxUsage:t3.micro"} and usage.value == Decimal("7.488")


def test_denied_region_request_counts_once_and_keeps_the_rest(monkeypatch):
    """Review Focus 3."""
    res, _ = _run(monkeypatch, group_error="AccessDeniedException")
    assert res.denials == 1 and _named(res, "cost.region.mtd") == []
    assert _named(res, "cost.usage_type.mtd") and _named(res, "cost.service.monthly")


def test_other_errors_in_the_new_requests_propagate(monkeypatch):
    with pytest.raises(ClientError):
        _run(monkeypatch, group_error="ThrottlingException")
