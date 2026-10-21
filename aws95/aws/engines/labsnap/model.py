# Upstream: labsnap/labsnap/model.py @ 1b1d437 — adapted for aws95; owned here from now on.
"""Engine value types. Metric values are Decimal (money and counts alike): a float never reaches the store."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class Resource:
    rtype: str
    region: str
    rid: str
    attrs: dict
    partial: bool = False


@dataclass
class Metric:
    name: str
    value: Decimal
    dims: dict = field(default_factory=dict)
    period: str | None = None
    unit: str | None = None


@dataclass
class Finding:
    rule: str
    rtype: str
    region: str
    rid: str
    severity: str
    detail: dict | None = None


@dataclass
class CollectorResult:
    resources: list[Resource]
    metrics: list[Metric]
    regions_covered: list[str]
    denials: int = 0
