# Upstream: labsnap/labsnap/collect/collectors/freetier.py @ 1b1d437 — adapted for aws95; owned here from now on.
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Context, Decimal

from aws95.aws import gate
from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.model import CollectorResult, Metric


def _dec(value) -> Decimal:
    """botocore parses Free Tier amounts as JSON doubles; str() returns the shortest text that round-trips."""
    return Decimal(str(value))


_PCT = Context(prec=60)
_CENT = Decimal("0.01")


def percent(actual: Decimal, limit: Decimal) -> Decimal:
    """A usage percentage, not money (review Minor 7): computed in a 60-digit context, then rounded DOWN to two
    places, so 99.996 % reads 99.99 % and never fires freetier.exceeded early. The exact actual and limit are
    stored beside it."""
    return _PCT.divide(_PCT.multiply(actual, Decimal(100)), limit).quantize(_CENT, rounding=ROUND_DOWN)


@register
class FreeTierCollector:
    name = "freetier"
    scope = "global"
    timeout_s = 60
    volatile_keys = frozenset()

    def collect(self, session, region, cancel=None):
        ft = gate.bind(session, "freetier", "us-east-1", cancel=cancel)
        now = datetime.now(UTC)
        period = now.strftime("%Y-%m")
        # The only call; a denial is a whole-collector failure, the honest outcome for a sole listing call.
        metrics = []
        for page in ft.pages("get_free_tier_usage"):
            for row in page["freeTierUsages"]:
                limit = row.get("limit")
                # A missing or zero limit makes the percentage undefined, not zero: the row is omitted.
                if not limit:
                    continue
                actual, cap = _dec(row["actualUsageAmount"]), _dec(limit)
                # day_of_month rides along: a monthly allowance's percentage only means something next to the day.
                dims = {"service": row["service"], "usage_type": row["usageType"], "region": row["region"],
                        "day_of_month": now.day}
                unit = row.get("unit")
                metrics.append(Metric("freetier.usage", percent(actual, cap), dims=dict(dims), period=period, unit="%"))
                metrics.append(Metric("freetier.actual", actual, dims=dict(dims), period=period, unit=unit))
                metrics.append(Metric("freetier.limit", cap, dims=dict(dims), period=period, unit=unit))
                if row.get("forecastedUsageAmount") is not None:
                    metrics.append(Metric("freetier.forecast", _dec(row["forecastedUsageAmount"]), dims=dict(dims),
                                          period=period, unit=unit))
        return CollectorResult([], metrics, ["global"])
