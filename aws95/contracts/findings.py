from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

Severity = Literal["critical", "high", "medium", "low"]
FindingState = Literal["open", "resolved", "accepted"]


@dataclass(frozen=True)
class Mute:
    id: int
    account: str | None
    rule: str
    rid: str | None
    reason: str
    muted_at: datetime
    expires_at: datetime | None


@dataclass(frozen=True)
class Finding:
    account: str
    rule: str
    rtype: str
    region: str
    rid: str
    severity: Severity
    first_seen: datetime
    seeded: bool
    state: FindingState
    age_days: int
    detail: Mapping[str, Any] = field(default_factory=dict)
    mute: Mute | None = None
    tuned: bool = False             # its rule's severity or parameters are locally tuned (findings-tools)


@dataclass(frozen=True)
class RuleInfo:
    id: str
    title: str
    description: str
    severity: Severity
    params: Mapping[str, Any]
    source: str
    enabled: bool
    tuned: bool = False
    default_severity: str = ""
    default_params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FleetCell:
    rule: str
    account: str
    open_count: int
    collected: bool

    @property
    def symbol(self) -> str:
        if not self.collected:
            return "?"
        if self.open_count == 0:
            return "✓"
        if self.open_count == 1:
            return "✗"
        return str(self.open_count)


MuteTarget = Literal["resource", "account", "rule"]   # what an Accept covers: this resource, this rule in this account, or the rule everywhere


@dataclass(frozen=True)
class RecheckOutcome:
    snapshots: int                 # finished
    total: int                     # asked for
    open_findings: int
    cancelled: bool = False

    def text(self, since=None) -> str:
        if self.cancelled:
            return (f"Backfill cancelled after {self.snapshots} of {self.total} snapshots. What finished is kept.")
        verb = f"Backfilled {self.snapshots} snapshots since {since.isoformat()}" if since else \
            f"Re-checked {self.snapshots} snapshots"
        return f"{verb}: {self.open_findings} open findings."
