"""Live provider set: Accounts, Resources, Billing, Findings and Snapshots (2b) read the local store; Activity (2c)
reads the local store too; Reports and Search (2d part 1) read the store through the other providers; Insights
(2d part 2) previews aggregates from the store and calls Bedrock only on Analyze. Opening a window never calls AWS."""
from __future__ import annotations

from functools import partial

from aws95.providers.base import DOMAINS, ProviderSet
from aws95.providers.composed import ComposedReports, ComposedSearch
from aws95.providers.empty import NotConnectedProvider
from aws95.providers.live.accounts import LiveAccountsProvider
from aws95.providers.live.activity import LiveActivity
from aws95.providers.live.billing import LiveBilling
from aws95.providers.live.findings import LiveFindings
from aws95.providers.live.insights import LiveInsights
from aws95.providers.live.common import utcnow
from aws95.providers.live.resources import LiveResources
from aws95.providers.live.search import store_resource_hits
from aws95.providers.live.snapshots import LiveSnapshots
from aws95.core.jobs import JobLock
from aws95.store import Store


def _sync_roster(provider: LiveAccountsProvider, settings, key: str) -> None:
    if key == "accounts/roster":
        provider.set_roster(settings.roster())


def _sync_retention(provider: LiveActivity, settings, key: str) -> None:
    if key == "activity/retention_days":
        provider.set_retention_days(settings.activity_retention_days)


def _insights_region(settings, account_id: str) -> str:
    """Bedrock runs in the account's first chosen snapshot region (ai-insights plan ruling), else us-east-1."""
    return (settings.account_regions(account_id) or ("us-east-1",))[0]


def live_provider_set(settings, *, store: Store | None = None, clock=None, session_factory=None) -> ProviderSet:
    store = store if store is not None else Store()          # lazy: no file until the first read or write
    now = clock or utcnow
    accounts = LiveAccountsProvider(store=store, clock=now)
    accounts.set_roster(settings.roster())
    settings.changed.connect(partial(_sync_roster, accounts, settings))
    activity = LiveActivity(store, accounts.roster, clock=now, retention_days=settings.activity_retention_days)
    settings.changed.connect(partial(_sync_retention, activity, settings))
    resources = LiveResources(store, accounts.roster, clock=now)
    billing = LiveBilling(store, accounts.roster, clock=now)
    jobs = JobLock()                                         # one long job at a time: snapshot or backfill
    findings = LiveFindings(store, accounts.roster, clock=now, jobs=jobs)
    snapshots = LiveSnapshots(store, accounts.roster, clock=now, jobs=jobs)
    implemented = {"accounts": accounts,
                   "activity": activity,
                   "resources": resources,
                   "billing": billing,
                   "findings": findings,
                   "snapshots": snapshots,
                   "reports": ComposedReports(resources, billing, findings, activity, snapshots),
                   "insights": LiveInsights(resources, findings, billing, activity, snapshots, accounts.roster,
                                            model_for=settings.insights_model,
                                            region_for=partial(_insights_region, settings),
                                            session_factory=session_factory, clock=now),
                   "search": ComposedSearch(resources, findings, activity, billing,
                                            resource_hits=partial(store_resource_hits, store, accounts.roster))}
    others = {attr: NotConnectedProvider(proto, f"{label} isn't available.")
              for attr, (proto, label) in DOMAINS.items() if attr not in implemented}
    return ProviderSet(**implemented, **others, is_demo=False, is_live=True, store=store)
