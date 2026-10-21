# Upstream: labsnap/labsnap/collect/collectors/cost.py @ 1b1d437 — rewritten for aws95 (Decimal money, this account only, paging, daily, forecast, tag count); owned here.
"""Cost Explorer figures for the Billing window (spec F2, §3.2). Every request is billed ($0.01), so this collector
runs only for accounts whose owner opted in.

- This account only: a payer account sees its whole organisation's spend, so every query carries a LINKED_ACCOUNT
  filter for the caller's own account. A payer and its member in one roster are never counted twice (review C2).
- Every page: GetCostAndUsage pages with NextPageToken (review I5). Groups of one month can be split across pages,
  so pages are merged per period before any total is taken.
- Today belongs to month-to-date (its spend is still accruing); the forecast covers tomorrow to the end of the month.
  On the last day of a month there is nothing left to forecast, so no forecast request is made (review Minor 2).
- Money stays exact: every amount is Decimal(AWS's own string); a total is AWS's Total, or the exact Decimal sum of
  the SERVICE partition.
- Region and usage type: this month to date, one request each, for the Billing tabs (billing-complete spec §3)."""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from aws95.aws import gate
from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.collect.probe import DENIED_CODES, error_code, probe
from aws95.aws.engines.labsnap.model import CollectorResult, Metric
from aws95.contracts.common import exact_sum

# Billed requests per snapshot: monthly by service, daily this month, region and usage type this month, forecast,
# active cost-allocation tags. One more per extra page AWS returns; one fewer on the last day of a month (no forecast).
BILLED_CALLS = 6
NO_FORECAST_CODES = frozenset({"DataUnavailableException"})   # too little history: AWS has no forecast yet


def _today() -> date:
    return datetime.now(UTC).date()


def _window(today: date, months: int = 13) -> tuple[str, str]:
    """`months` whole months up to and including this one. Month arithmetic, not a day approximation."""
    year, month = today.year, today.month - (months - 1)
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1).isoformat(), (today + timedelta(days=1)).isoformat()


def _month_to_date(today: date) -> tuple[str, str]:
    """First of the month through today (End is exclusive, so it is tomorrow)."""
    return today.replace(day=1).isoformat(), (today + timedelta(days=1)).isoformat()


def _rest_of_month(today: date) -> tuple[str, str] | None:
    """Tomorrow through the end of the month; None on the last day (today is month-to-date, never forecast)."""
    tomorrow = today + timedelta(days=1)
    first_next = date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)
    if tomorrow >= first_next:
        return None
    return tomorrow.isoformat(), first_next.isoformat()


def _amount(block: dict) -> tuple[Decimal, str]:
    return Decimal(str(block["Amount"])), str(block["Unit"])


def _cost_pages(ce, **params) -> list[dict]:
    """Every page of one GetCostAndUsage answer. A repeated token is a loud failure, never a silent stop."""
    pages: list[dict] = []
    seen: set[str] = set()
    token: str | None = None
    while True:
        response = ce.get_cost_and_usage(**params, **({"NextPageToken": token} if token else {}))
        pages.append(response)
        token = response.get("NextPageToken")
        if not token:
            return pages
        if token in seen:
            raise RuntimeError("Cost Explorer repeated a page token")
        seen.add(token)


@register
class CostCollector:
    name = "cost"
    scope = "global"
    timeout_s = 120
    volatile_keys = frozenset()
    billed = True

    def collect(self, session, region, cancel=None):
        ce = gate.bind(session, "ce", "us-east-1", cancel=cancel)
        sts = gate.bind(session, "sts", "us-east-1", cancel=cancel)
        account_id = sts.get_caller_identity()["Account"]          # free; the filter below needs it
        only_this_account = {"Dimensions": {"Key": "LINKED_ACCOUNT", "Values": [account_id]}}
        today = _today()
        metrics: list[Metric] = []
        denials = 0

        start, end = _window(today)
        by_period: dict[str, dict] = {}
        for page in _cost_pages(ce, TimePeriod={"Start": start, "End": end}, Granularity="MONTHLY",
                                Metrics=["UnblendedCost"], GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
                                Filter=only_this_account):
            for r in page["ResultsByTime"]:
                period = r["TimePeriod"]["Start"][:7]      # what the money is ABOUT; observed_at is when we read it
                slot = by_period.setdefault(period, {"total": None, "groups": []})
                total = (r.get("Total") or {}).get("UnblendedCost")
                if total:
                    slot["total"] = total
                slot["groups"].extend(r.get("Groups") or [])
        for period, slot in sorted(by_period.items()):
            groups = slot["groups"]
            if slot["total"]:
                amount, unit = _amount(slot["total"])
                metrics.append(Metric("cost.total.monthly", amount, period=period, unit=unit))
            elif groups:
                # Cost Explorer returns an empty Total whenever GroupBy is used. A SERVICE breakdown is a clean
                # partition of the month, so its exact Decimal sum is the total - never rounded.
                parts = [_amount(g["Metrics"]["UnblendedCost"]) for g in groups]
                units = {unit for _, unit in parts}
                if len(units) == 1:
                    metrics.append(Metric("cost.total.monthly", exact_sum(a for a, _ in parts),
                                          period=period, unit=units.pop()))
            for g in groups:
                amount, unit = _amount(g["Metrics"]["UnblendedCost"])
                metrics.append(Metric("cost.service.monthly", amount, dims={"service": g["Keys"][0]},
                                      period=period, unit=unit))

        dstart, dend = _month_to_date(today)
        for page in _cost_pages(ce, TimePeriod={"Start": dstart, "End": dend}, Granularity="DAILY",
                                Metrics=["UnblendedCost"], Filter=only_this_account):
            for r in page["ResultsByTime"]:
                total = (r.get("Total") or {}).get("UnblendedCost")
                if total:
                    amount, unit = _amount(total)
                    metrics.append(Metric("cost.total.daily", amount, period=r["TimePeriod"]["Start"][:10],
                                          unit=unit))

        month = today.strftime("%Y-%m")
        for key, name, dim in (("REGION", "cost.region.mtd", "region"),
                               ("USAGE_TYPE", "cost.usage_type.mtd", "usage_type")):
            try:
                pages = _cost_pages(ce, TimePeriod={"Start": dstart, "End": dend}, Granularity="MONTHLY",
                                    Metrics=["UnblendedCost"], GroupBy=[{"Type": "DIMENSION", "Key": key}],
                                    Filter=only_this_account)
            except Exception as exc:  # noqa: BLE001 - a denial is an answer; anything else propagates
                if error_code(exc) in DENIED_CODES:
                    denials += 1
                    continue
                raise
            for page in pages:
                for r in page["ResultsByTime"]:
                    for g in r.get("Groups") or []:
                        amount, unit = _amount(g["Metrics"]["UnblendedCost"])
                        metrics.append(Metric(name, amount, dims={dim: g["Keys"][0]}, period=month, unit=unit))

        window = _rest_of_month(today)
        forecast = None
        if window is not None:
            try:
                forecast = ce.get_cost_forecast(TimePeriod={"Start": window[0], "End": window[1]},
                                                Metric="UNBLENDED_COST", Granularity="MONTHLY",
                                                Filter=only_this_account)
            except Exception as exc:  # noqa: BLE001 - "no forecast yet" and a denial are answers; the rest propagates
                code = error_code(exc)
                if code in DENIED_CODES:
                    denials += 1
                elif code not in NO_FORECAST_CODES:
                    raise
        if forecast and (forecast.get("Total") or {}).get("Amount") is not None:
            amount, unit = _amount(forecast["Total"])
            metrics.append(Metric("cost.forecast.remaining", amount, period=today.strftime("%Y-%m"), unit=unit))

        tags, unreadable = probe(ce.list_cost_allocation_tags, Status="Active")
        denials += int(unreadable)
        if not unreadable and tags is not None:
            metrics.append(Metric("cost.active_allocation_tags", Decimal(len(tags["CostAllocationTags"]))))

        return CollectorResult([], metrics, ["global"], denials)
