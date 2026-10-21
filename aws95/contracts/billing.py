from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from aws95.contracts.common import require_decimal

CostDimension = Literal["service", "account", "region", "usage_type"]
Granularity = Literal["daily", "monthly"]


@dataclass(frozen=True)
class CostPoint:
    period: str                       # "2026-09" or "2026-09-14"
    amount: Decimal
    unit: str                         # "USD"
    dims: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_decimal("amount", self.amount)


@dataclass(frozen=True)
class CostSummary:
    mtd: Decimal
    forecast: Decimal | None
    last_month: Decimal | None
    currency: str
    as_of: datetime

    def __post_init__(self) -> None:
        require_decimal("mtd", self.mtd)
        require_decimal("forecast", self.forecast, optional=True)
        require_decimal("last_month", self.last_month, optional=True)


@dataclass(frozen=True)
class FreeTierUsage:
    service: str
    metric: str
    actual: Decimal
    limit: Decimal
    forecast: Decimal | None
    unit: str
    period: str

    def __post_init__(self) -> None:
        require_decimal("actual", self.actual)
        require_decimal("limit", self.limit)
        require_decimal("forecast", self.forecast, optional=True)


# Shown beside every "Include Billing" choice. The cost collector makes 6 billed requests per account per snapshot
# (aws95.aws.engines.labsnap.collect.collectors.cost.BILLED_CALLS; one more per extra page AWS returns, one fewer on
# the last day of a month); AWS charges $0.01 each.
BILLING_COST_NOTE = "≈ $0.06 per account (AWS Cost Explorer charges)"
# The setup wizard's optional Billing probe (one Cost Explorer request).
BILLING_CHECK_NOTE = "AWS charges $0.01 per check"
# Import History… (2c): S3 requests and data transfer for the trail's log files.
IMPORT_COST_NOTE = ("Reads your CloudTrail log files from S3. AWS bills S3 requests and data transfer to your account "
                    "(usually cents for a few days of logs; larger ranges cost more)")

BILLING_NOT_ENABLED = "Billing isn't enabled for this account."     # the Billing window offers Enable… on this text
NOT_IN_RELEASE = "Not in this release."
