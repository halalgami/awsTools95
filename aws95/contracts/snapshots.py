from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from aws95.contracts.accounts import CollectorHealth, RosterEntry
from aws95.contracts.common import require_decimal

SnapshotStatus = Literal["complete", "partial"]
DiffKind = Literal["added", "removed", "changed", "excluded"]


@dataclass(frozen=True)
class Snapshot:
    id: int
    account: str
    caller_arn: str
    taken_at: datetime
    tool_version: str
    status: SnapshotStatus
    collectors: tuple[CollectorHealth, ...] = ()
    regions: tuple[str, ...] = ()
    sections_done: int = 0
    sections_total: int = 0


@dataclass(frozen=True)
class DiffEntry:
    kind: DiffKind
    rtype: str
    region: str
    rid: str
    deltas: Mapping[str, Any] = field(default_factory=dict)
    reason: str | None = None        # required in the UI when kind == "excluded"
    partial: bool = False


@dataclass(frozen=True)
class MetricPoint:
    name: str
    dims: Mapping[str, str]
    period: str | None
    value: Decimal
    unit: str | None
    observed_at: datetime
    restated: bool = False

    def __post_init__(self) -> None:
        require_decimal("value", self.value)


STALE_AFTER = timedelta(days=1)          # spec §4: data older than this carries Status.STALE
AccountRunStatus = Literal["complete", "partial", "failed"]


@dataclass(frozen=True)
class AccountAge:
    account_id: str
    taken_at: datetime | None            # the newest snapshot; None = never taken


@dataclass(frozen=True)
class SnapshotRequest:
    accounts: tuple[RosterEntry, ...]
    include_billing_for: frozenset[str] = frozenset()
    regions_for: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    labels: Mapping[str, str] = field(default_factory=dict)       # account id -> display name for progress


@dataclass(frozen=True)
class SnapshotProgress:
    account_id: str
    label: str
    done: int
    total: int
    region: str | None = None
    collector: str | None = None

    @property
    def text(self) -> str:
        base = f"{self.label}: {self.done}/{self.total} sections"
        where = " ".join(x for x in (self.collector, self.region) if x)
        return f"{base} · {where}" if where else base


@dataclass(frozen=True)
class AccountRun:
    account_id: str
    snapshot_id: int | None
    status: AccountRunStatus
    message: str | None = None
    denials: int = 0


@dataclass(frozen=True)
class SnapshotOutcome:
    runs: tuple[AccountRun, ...]
    cancelled: bool = False


SectionState = Literal["waiting", "running", "done", "partial", "denied", "timed_out", "failed", "stopped"]
RESUMABLE: frozenset[str] = frozenset({"waiting", "stopped", "timed_out", "failed"})
RESUME_WINDOW = timedelta(hours=24)        # resumable-snapshots spec R2: only a recent snapshot is continued
TOO_OLD = "This snapshot is too old to resume; take a new one."
NOTHING_TO_RESUME = "Nothing left to resume."


@dataclass(frozen=True)
class SnapshotSection:
    """One collector in one region of one account's snapshot (resumable-snapshots spec §3)."""
    account: str
    snapshot_id: int
    collector: str
    region: str
    state: SectionState
    started_at: datetime | None = None
    ended_at: datetime | None = None
    rows: int = 0
    denials: int = 0
    error: str | None = None

    def resumable(self, retry_denied: bool = False) -> bool:
        return self.state in RESUMABLE or (retry_denied and self.state == "denied")


@dataclass(frozen=True)
class AccountStage:
    """Where one account's latest snapshot stands (spec §7.1's per-account header)."""
    account: str
    snapshot_id: int | None
    taken_at: datetime | None
    done: int
    total: int
    resumable: int
    denied: int
    running: tuple[str, ...] = ()
    billing_unfinished: bool = False
    resumable_until: datetime | None = None

    def text(self, label: str) -> str:
        if self.snapshot_id is None:
            return f"{label} — no snapshot yet"
        if self.running:
            return f"{label} — {self.done}/{self.total} sections · running: {', '.join(self.running)}"
        if self.total and self.done == self.total:
            return f"{label} — complete ({self.done}/{self.total} sections)"
        parts = [f"{self.done}/{self.total} sections"]
        if self.resumable:
            parts.append(f"{self.resumable} to resume")
        if self.denied:
            parts.append(f"{self.denied} denied")
        return f"{label} — " + " · ".join(parts)

    def can_resume(self, now: datetime) -> bool:
        return bool(self.resumable) and self.resumable_until is not None and now < self.resumable_until


@dataclass(frozen=True)
class ResumeRequest:
    accounts: tuple[RosterEntry, ...]
    retry_denied: bool = False
    include_billing_for: frozenset[str] = frozenset()
    labels: Mapping[str, str] = field(default_factory=dict)
