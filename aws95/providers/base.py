"""Provider Protocols: what program windows ask for. Live providers implement them over the local store and the
engines; demo providers over fixtures; empty ones answer NOT_CONNECTED."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from aws95.contracts.accounts import CapabilityCheck, CollectorHealth, ConfigSource, Profile, RosterEntry
from aws95.contracts.activity import (AccessCheck, ActivityEvent, ActivityQuery, Bucket, Bucketed, HistoryEstimate,
                                      HistoryOutcome, HistoryProgress, HistoryRequest, HistoryStatus, ImportPlan,
                                      PrincipalSummary, TopDimension, TrailLookup)
from aws95.contracts.billing import CostDimension, CostPoint, CostSummary, FreeTierUsage, Granularity
from aws95.contracts.common import AccountRef, Page, Result, Scope
from aws95.contracts.findings import Finding, FindingState, FleetCell, Mute, MuteTarget, RecheckOutcome, RuleInfo
from aws95.contracts.insights import InsightKind, InsightReport, ModelChoice
from aws95.contracts.reports import RenderedReport, ReportType
from aws95.contracts.resources import ResourceRow, ResourceType
from aws95.contracts.search import SearchHit
from aws95.contracts.snapshots import (AccountAge, AccountStage, DiffEntry, MetricPoint, ResumeRequest, Snapshot,
                                       SnapshotOutcome, SnapshotProgress, SnapshotRequest, SnapshotSection)
from aws95.core.cancel import CancelToken

PromptCallback = Callable[[str, str], None]          # (verification url, device code)


@runtime_checkable
class AccountsProvider(Protocol):
    def list_profiles(self, scope: Scope | None = None, *, cancel: CancelToken | None = None) -> Result[tuple[Profile, ...]]: ...
    def verify_profile(self, scope: Scope, profile: str = "", *, cancel: CancelToken | None = None) -> Result[AccountRef]: ...
    def collector_health(self, scope: Scope, *, cancel: CancelToken | None = None) -> Result[tuple[CollectorHealth, ...]]: ...
    def config_sources(self, *, cancel: CancelToken | None = None) -> Result[tuple[ConfigSource, ...]]: ...
    def check_permissions(self, scope: Scope, profile: str = "", include_billing: bool = False, *,
                          only_billing: bool = False, cancel: CancelToken | None = None) -> Result[tuple[CapabilityCheck, ...]]: ...
    def sign_in(self, profile: str = "", on_prompt: PromptCallback | None = None, *,
                cancel: CancelToken | None = None) -> Result[None]: ...
    def required_policy(self, *, cancel: CancelToken | None = None) -> Result[str]: ...


@runtime_checkable
class ResourcesProvider(Protocol):
    def resource_types(self, scope: Scope, *, cancel: CancelToken | None = None) -> Result[tuple[ResourceType, ...]]: ...
    def list_resources(self, scope: Scope, rtype: str = "", cursor: str | None = None, *, page_size: int = 500,
                       cancel: CancelToken | None = None) -> Result[Page[ResourceRow]]: ...


@runtime_checkable
class BillingProvider(Protocol):
    def summary(self, scope: Scope, *, cancel: CancelToken | None = None) -> Result[CostSummary]: ...
    def breakdown(self, scope: Scope, dimension: CostDimension = "service", *, cancel: CancelToken | None = None) -> Result[tuple[CostPoint, ...]]: ...
    def trend(self, scope: Scope, granularity: Granularity = "monthly", *, cancel: CancelToken | None = None) -> Result[tuple[CostPoint, ...]]: ...
    def free_tier(self, scope: Scope, *, cancel: CancelToken | None = None) -> Result[tuple[FreeTierUsage, ...]]: ...
    def uncovered_spend(self, scope: Scope, *, cancel: CancelToken | None = None) -> Result[tuple[CostPoint, ...]]: ...


@runtime_checkable
class FindingsProvider(Protocol):
    def list_findings(self, scope: Scope, state: FindingState = "open", cursor: str | None = None, *,
                      cancel: CancelToken | None = None) -> Result[Page[Finding]]: ...
    def rules(self, scope: Scope, *, cancel: CancelToken | None = None) -> Result[tuple[RuleInfo, ...]]: ...
    def fleet(self, scope: Scope, *, cancel: CancelToken | None = None) -> Result[tuple[FleetCell, ...]]: ...
    def accept(self, scope: Scope, finding: Finding | None = None, target: MuteTarget = "resource", reason: str = "",
               expires_at: datetime | None = None, *, cancel: CancelToken | None = None) -> Result[Mute]:
        """Accept (mute) a finding locally, with a reason and an optional expiry. Never writes to AWS."""
        ...
    def unaccept(self, scope: Scope, mute_id: int = 0, *, cancel: CancelToken | None = None) -> Result[None]: ...
    # Findings tools (local only; demo answers NOT_CONNECTED).
    def set_rule(self, scope: Scope, rule_id: str = "", *, enabled: bool = True, severity: str | None = None,
                 params: Mapping[str, Any] | None = None, cancel: CancelToken | None = None) -> Result[RecheckOutcome]: ...
    def reset_rule(self, scope: Scope, rule_id: str = "", *, cancel: CancelToken | None = None) -> Result[RecheckOutcome]: ...
    def seed(self, scope: Scope, finding: Finding | None = None, first_seen: date | None = None, note: str = "", *,
             cancel: CancelToken | None = None) -> Result[None]: ...
    def unseed(self, scope: Scope, finding: Finding | None = None, *, cancel: CancelToken | None = None) -> Result[None]: ...
    def backfill(self, scope: Scope, since: date | None = None, *, progress: Callable[[int, int], None] | None = None,
                 stop: CancelToken | None = None, cancel: CancelToken | None = None) -> Result[RecheckOutcome]: ...


@runtime_checkable
class SnapshotsProvider(Protocol):
    def list_snapshots(self, scope: Scope, *, cancel: CancelToken | None = None) -> Result[tuple[Snapshot, ...]]: ...
    def diff(self, scope: Scope, a_id: int = 0, b_id: int = 0, *, cancel: CancelToken | None = None) -> Result[tuple[DiffEntry, ...]]: ...
    def metric_names(self, scope: Scope, *, cancel: CancelToken | None = None) -> Result[tuple[str, ...]]: ...
    def metric_series(self, scope: Scope, name: str = "", *, cancel: CancelToken | None = None) -> Result[tuple[MetricPoint, ...]]: ...
    def ages(self, scope: Scope, *, cancel: CancelToken | None = None) -> Result[tuple[AccountAge, ...]]: ...
    def take_snapshot(self, scope: Scope, request: SnapshotRequest | None = None, *,
                      progress: Callable[[SnapshotProgress], None] | None = None, stop: CancelToken | None = None,
                      cancel: CancelToken | None = None) -> Result[SnapshotOutcome]:
        """The only provider call that reaches AWS. `stop` is the user's Cancel: what finished is saved and the
        outcome is still returned. `cancel` is the runner's token (window or app closing)."""
        ...
    def sections(self, scope: Scope, snapshot_id: int | None = None, *,
                 cancel: CancelToken | None = None) -> Result[tuple[SnapshotSection, ...]]:
        """The sections of one snapshot, or of each scoped account's latest snapshot when snapshot_id is None."""
        ...
    def stages(self, scope: Scope, *, cancel: CancelToken | None = None) -> Result[tuple[AccountStage, ...]]: ...
    def resume_snapshot(self, scope: Scope, request: ResumeRequest | None = None, *,
                        progress: Callable[[SnapshotProgress], None] | None = None, stop: CancelToken | None = None,
                        cancel: CancelToken | None = None) -> Result[SnapshotOutcome]:
        """Runs only the unfinished sections of each account's latest snapshot (spec §6). Reaches AWS."""
        ...


@runtime_checkable
class ActivityProvider(Protocol):
    def events(self, scope: Scope, query: ActivityQuery | None = None, cursor: str | None = None, *,
               cancel: CancelToken | None = None) -> Result[Page[ActivityEvent]]: ...
    def principals(self, scope: Scope, *, cancel: CancelToken | None = None) -> Result[tuple[PrincipalSummary, ...]]: ...
    def time_series(self, scope: Scope, bucket: Bucket = "day", *, cancel: CancelToken | None = None) -> Result[Bucketed]: ...
    def top(self, scope: Scope, dimension: TopDimension = "principal", limit: int = 10, *,
            cancel: CancelToken | None = None) -> Result[tuple[tuple[str, int], ...]]: ...
    def raw_event(self, scope: Scope, event_id: str = "", *, cancel: CancelToken | None = None) -> Result[dict[str, Any]]: ...
    def history_status(self, scope: Scope, *, cancel: CancelToken | None = None) -> Result[HistoryStatus]: ...
    def estimate_history(self, scope: Scope, request: HistoryRequest | None = None, *,
                         cancel: CancelToken | None = None) -> Result[tuple[HistoryEstimate, ...]]:
        """One lookup_events call per region over the last hour (spec §5.1). Reaches AWS: only when the user asks."""
        ...
    def load_history(self, scope: Scope, request: HistoryRequest | None = None, *,
                     progress: Callable[[HistoryProgress], None] | None = None, stop: CancelToken | None = None,
                     cancel: CancelToken | None = None) -> Result[HistoryOutcome]:
        """Load, resume or update (request.mode). `stop` is the user's Cancel: what finished is kept."""
        ...
    def find_trails(self, scope: Scope, account: RosterEntry | None = None, regions: tuple[str, ...] = (), *,
                    cancel: CancelToken | None = None) -> Result[TrailLookup]: ...
    def check_import(self, scope: Scope, plan: ImportPlan | None = None, *,
                     cancel: CancelToken | None = None) -> Result[AccessCheck]: ...
    def import_history(self, scope: Scope, plan: ImportPlan | None = None, *,
                       progress: Callable[[HistoryProgress], None] | None = None, stop: CancelToken | None = None,
                       cancel: CancelToken | None = None) -> Result[HistoryOutcome]: ...


@runtime_checkable
class InsightsProvider(Protocol):
    def preview_payload(self, scope: Scope, kind: InsightKind = "insights", *, cancel: CancelToken | None = None) -> Result[dict[str, Any]]: ...
    def analyze(self, scope: Scope, kind: InsightKind = "insights", payload: Mapping[str, Any] | None = None, *,
                cancel: CancelToken | None = None) -> Result[InsightReport]:
        """`payload` is exactly the preview the user reviewed; a provider sends that and nothing else."""
        ...
    def models(self, scope: Scope, account_id: str = "", *,
               cancel: CancelToken | None = None) -> Result[tuple[ModelChoice, ...]]: ...


@runtime_checkable
class ReportsProvider(Protocol):
    def report_types(self, scope: Scope, *, cancel: CancelToken | None = None) -> Result[tuple[ReportType, ...]]: ...
    def render(self, scope: Scope, report_id: str = "", *, cancel: CancelToken | None = None) -> Result[RenderedReport]: ...


@runtime_checkable
class SearchProvider(Protocol):
    def search(self, scope: Scope, text: str = "", limit: int = 50, *, cancel: CancelToken | None = None) -> Result[tuple[SearchHit, ...]]: ...


@dataclass(frozen=True)
class ProviderSet:
    accounts: AccountsProvider
    resources: ResourcesProvider
    billing: BillingProvider
    findings: FindingsProvider
    snapshots: SnapshotsProvider
    activity: ActivityProvider
    insights: InsightsProvider
    reports: ReportsProvider
    search: SearchProvider
    is_demo: bool = False
    is_live: bool = False
    store: Any = None          # the live local store (aws95.store.Store); None in demo and offline


DOMAINS: dict[str, tuple[type, str]] = {
    "accounts": (AccountsProvider, "Account"),
    "resources": (ResourcesProvider, "Resources"),
    "billing": (BillingProvider, "Billing"),
    "findings": (FindingsProvider, "Findings"),
    "snapshots": (SnapshotsProvider, "Snapshots"),
    "activity": (ActivityProvider, "Activity Log"),
    "insights": (InsightsProvider, "AI Insights"),
    "reports": (ReportsProvider, "Reports"),
    "search": (SearchProvider, "Search"),
}
