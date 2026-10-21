"""Activity Log contracts (first-drop spec §5). Qt-free and frozen; `HistoryStatus` replaces phase 1's AthenaStatus."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

from aws95.contracts.accounts import RosterEntry

Bucket = Literal["hour", "day"]
TopDimension = Literal["principal", "action", "service", "actor_kind"]
JobKind = Literal["load", "import"]
LoadMode = Literal["load", "update"]
AccountLoadStatus = Literal["complete", "partial", "failed"]

HISTORY_DAYS: tuple[int, ...] = (7, 30, 90)           # spec §5.1 choices; 7 is recommended and the default
DEFAULT_HISTORY_DAYS = 7
GLOBAL_REGION = "us-east-1"                           # always loaded: global services log there (spec §5.2)
ALL_REGIONS = "All regions"
LOAD_WORDING = ("Large ranges take a while. Loading runs in the background — keep working; "
                "the newest events appear first.")
DENIED_NOTE = "Denied change attempts: covered. Denied read attempts: from imported history only."
NO_HISTORY = "No activity history yet. Use Load Activity History… to choose how much to load."


def activity_regions(saved: Sequence[str], profile_region: str | None) -> tuple[str, ...]:
    """Spec F5 + §5.2: the account's default regions (else the profile's region), always plus us-east-1."""
    base = list(saved) if saved else [profile_region or GLOBAL_REGION]
    return tuple(dict.fromkeys([*base, GLOBAL_REGION]))


@dataclass(frozen=True)
class ActivityEvent:
    event_id: str
    account: str
    time_utc: datetime
    principal_type: str
    principal_name: str
    is_human: bool
    source: str
    name: str
    read_only: bool
    source_ip: str | None
    user_agent: str | None
    resource_arns: tuple[str, ...]
    region: str
    risk_level: int
    risk_rule: str | None
    error_code: str | None = None          # e.g. AccessDenied: a denied attempt (spec §5.3 addition 2)

    def __post_init__(self) -> None:
        if not 0 <= self.risk_level <= 3:
            raise ValueError(f"risk_level must be 0..3, got {self.risk_level}")


@dataclass(frozen=True)
class PrincipalSummary:
    principal_type: str
    principal_name: str
    is_human: bool
    count: int


@dataclass(frozen=True)
class Bucketed:
    bucket: Bucket
    series: tuple[tuple[datetime, int], ...]


@dataclass(frozen=True)
class ActivityQuery:
    principal: str | None = None
    service: str | None = None
    action: str | None = None
    arn_contains: str | None = None
    human_only: bool = False
    risk_min: int = 0

    def __post_init__(self) -> None:
        if self.risk_min is not None and not (0 <= self.risk_min <= 3):
            raise ValueError("risk_min must be between 0 and 3 (the ActivityEvent.risk_level range)")


# ----- what the local history covers (spec §5.5) -----
@dataclass(frozen=True)
class CoverageSpan:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class RegionHistory:
    region: str
    looked_up: tuple[CoverageSpan, ...] = ()      # change events loaded through LookupEvents (the bulk pass)
    imported: tuple[CoverageSpan, ...] = ()       # days imported from the trail's S3 files


@dataclass(frozen=True)
class AccountHistory:
    account: str
    days_requested: int | None = None              # the chosen history length; None = never chosen
    regions: tuple[RegionHistory, ...] = ()
    unfinished: bool = False                       # the chosen range still has gaps: Resume Loading fills them

    @property
    def has_history(self) -> bool:
        return any(r.looked_up or r.imported for r in self.regions)


@dataclass(frozen=True)
class HistoryStatus:
    accounts: tuple[AccountHistory, ...] = ()
    denied_changes_since: datetime | None = None   # earliest looked-up coverage: denied change attempts are in it
    denied_reads_since: datetime | None = None     # earliest imported coverage: denied reads exist only there
    running: JobKind | None = None
    progress: str | None = None                    # "Activity dev: 23/90 days · me-south-1"

    @property
    def has_history(self) -> bool:
        return any(a.has_history for a in self.accounts)

    @property
    def unfinished(self) -> bool:
        return any(a.unfinished for a in self.accounts)


# ----- loading (spec §5.1, §5.2) -----
@dataclass(frozen=True)
class HistoryEstimate:
    account: str                                   # "" on the grand-total row
    region: str                                    # ALL_REGIONS on the grand-total row
    minutes: Mapping[int, int]                     # days -> estimated minutes
    at_least: bool = False                         # the sample was a full page (or a region failed): a lower bound
    error: str | None = None

    def text(self, days: int) -> str:
        if self.error is not None or days not in self.minutes:
            return "unknown"
        return f"≈ {self.minutes[days]} min" + (" or more" if self.at_least else "")


@dataclass(frozen=True)
class HistoryRequest:
    accounts: tuple[RosterEntry, ...]
    regions_for: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    days: int | None = None                        # None: keep each account's saved choice (Resume)
    mode: LoadMode = "load"                        # "update": only what is newer than the stored coverage (F5)
    labels: Mapping[str, str] = field(default_factory=dict)
    retention_days: int = 90


@dataclass(frozen=True)
class HistoryProgress:
    kind: JobKind
    label: str
    done_days: int
    total_days: int
    region: str | None = None

    @property
    def text(self) -> str:
        verb = "Activity" if self.kind == "load" else "Import"
        base = f"{verb} {self.label}: {self.done_days}/{self.total_days} days"
        return f"{base} · {self.region}" if self.region else base


@dataclass(frozen=True)
class AccountLoad:
    account_id: str
    status: AccountLoadStatus
    events_added: int = 0
    message: str | None = None


@dataclass(frozen=True)
class HistoryOutcome:
    kind: JobKind
    runs: tuple[AccountLoad, ...]
    cancelled: bool = False


# ----- Import History… (spec §5.4) -----
@dataclass(frozen=True)
class TrailInfo:
    name: str
    arn: str
    home_region: str
    bucket: str
    prefix: str                                    # S3KeyPrefix without surrounding slashes; "" when none
    kms_key_id: str | None
    is_org: bool
    trail_account: str                             # the account that owns the trail (from its ARN)
    multi_region: bool
    insights: bool

    def describe(self) -> str:
        where = f"{self.bucket}/{self.prefix}" if self.prefix else self.bucket
        parts = [f"{self.name} · s3://{where}", "encrypted with KMS" if self.kms_key_id else "not KMS-encrypted"]
        if self.is_org:
            parts.append("organisation trail")
        if self.insights:
            parts.append("Insights on")
        return " · ".join(parts)


@dataclass(frozen=True)
class TrailLookup:
    account: str
    trails: tuple[TrailInfo, ...]
    note: str | None = None


@dataclass(frozen=True)
class ImportPlan:
    account: RosterEntry                           # whose history
    reader_profile: str                            # the profile that reads the bucket (org trail: log archive)
    trail: TrailInfo
    start_day: date                                # inclusive, UTC
    end_day: date                                  # inclusive, UTC
    regions: tuple[str, ...]
    label: str = ""

    def __post_init__(self) -> None:
        if self.start_day > self.end_day:
            raise ValueError("the import range starts after it ends")

    @property
    def days(self) -> tuple[date, ...]:
        """Newest first (spec §5.2's order applies to imports too)."""
        span = (self.end_day - self.start_day).days
        return tuple(self.end_day - timedelta(days=i) for i in range(span + 1))


@dataclass(frozen=True)
class AccessCheck:
    ok: bool
    message: str
    policy: str | None = None                      # the exact IAM statements to add when something is missing
