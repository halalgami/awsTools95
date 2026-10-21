"""Dev-only demo provider set. Every Result it returns carries is_demo=True (spec D6)."""
from __future__ import annotations

from aws95.contracts.accounts import CapabilityCheck, CollectorHealth, ConfigSource, Profile
from aws95.contracts.common import Page, Result, Scope, Status
from aws95.contracts.resources import ResourceRow, ResourceType
from aws95.core.cancel import CancelToken, raise_if_cancelled
from aws95.providers.base import DOMAINS, ProviderSet
from aws95.providers.demo.activity import DemoActivity
from aws95.providers.demo.billing import DemoBilling
from aws95.providers.demo.builder import build
from aws95.providers.demo.findings import DemoFindings
from aws95.providers.demo.insights import DemoInsights
from aws95.providers.composed import ComposedSearch
from aws95.providers.demo.reports import DemoReports
from aws95.providers.demo.snapshots import DemoSnapshots
from aws95.providers.demo.store import DEMO_FRESHNESS, FIXTURE_DIR, in_scope, load
from aws95.providers.empty import NotConnectedProvider

__all__ = ["FIXTURE_DIR", "demo_provider_set"]


POLICY_DEMO = '{\n  "Version": "2012-10-17",\n  "Statement": [\n    {\n      "Sid": "AWS95ToolsReadOnly",\n' \
              '      "Effect": "Allow",\n      "Action": ["sts:GetCallerIdentity"],\n      "Resource": "*"\n    }\n  ]\n}'


def _ok(data):
    return Result.ok(data, freshness=DEMO_FRESHNESS, is_demo=True)


class DemoAccounts:
    def list_profiles(self, scope: Scope | None = None, *, cancel: CancelToken | None = None) -> Result[tuple[Profile, ...]]:
        raise_if_cancelled(cancel)
        return _ok(tuple(build(Profile, p) for p in load("accounts.json")["profiles"]))

    def verify_profile(self, scope: Scope, profile: str = "", *, cancel: CancelToken | None = None):
        for p in self.list_profiles(cancel=cancel).data:
            if p.name == profile and p.verified is not None:
                return _ok(p.verified)
        return Result.error(f"Demo profile {profile!r} has no verified account", is_demo=True)

    def collector_health(self, scope: Scope, *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        rows = load("health.json")["collectors"]
        picked = [r["health"] for r in rows if not scope.accounts or r["account"] in scope.accounts]
        return _ok(tuple(build(CollectorHealth, h) for h in picked))

    def config_sources(self, *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        return _ok((ConfigSource("~/.aws/config", "config", True, 3),
                    ConfigSource("~/.aws/credentials", "credentials", False, 0)))

    def check_permissions(self, scope: Scope, profile: str = "", include_billing: bool = False, *,
                          only_billing: bool = False, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        billing = (CapabilityCheck("billing", "Billing", Status.OK, (), "Verified.", True, True) if include_billing else
                   CapabilityCheck("billing", "Billing", Status.NOT_CONNECTED, (),
                                   "Not checked (AWS charges $0.01 per check).", True, True))
        if only_billing:
            return _ok((billing,))
        return _ok((CapabilityCheck("identity", "Identity", Status.OK, (), "Verified.", False, True),
                    CapabilityCheck("resources", "Resources, Findings, Snapshots", Status.OK, (),
                                    "Verified.", False, True),
                    CapabilityCheck("activity", "Activity Log", Status.DENIED, ("cloudtrail:LookupEvents",),
                                    "Permission denied for cloudtrail:LookupEvents.",
                                    False, True),
                    billing,
                    CapabilityCheck("insights", "AI Insights", Status.OK, (), "Verified.", False, True)))

    def sign_in(self, profile: str = "", on_prompt=None, *, cancel: CancelToken | None = None):
        return Result.not_connected("Sign-in is not available in demo mode.", is_demo=True)

    def required_policy(self, *, cancel: CancelToken | None = None):
        return _ok(POLICY_DEMO)


class DemoResources:
    def _rows(self) -> tuple[ResourceRow, ...]:
        return tuple(build(ResourceRow, r) for r in load("resources.json")["rows"])

    def resource_types(self, scope: Scope, *, cancel: CancelToken | None = None) -> Result[tuple[ResourceType, ...]]:
        raise_if_cancelled(cancel)
        rows = [r for r in self._rows() if in_scope(r.account, r.region, scope)]
        types = []
        for t in load("resources.json")["types"]:
            count = sum(1 for r in rows if r.rtype == t["rtype"])
            types.append(ResourceType(rtype=t["rtype"], domain=t["domain"], label=t["label"], count=count))
        return _ok(tuple(types))

    def list_resources(self, scope: Scope, rtype: str = "", cursor: str | None = None, *, page_size: int = 500,
                       cancel: CancelToken | None = None) -> Result[Page[ResourceRow]]:
        raise_if_cancelled(cancel)
        rows = [r for r in self._rows() if r.rtype == rtype and in_scope(r.account, r.region, scope)]
        start = int(cursor) if cursor else 0
        chunk = tuple(rows[start:start + page_size])
        nxt = start + page_size
        return _ok(Page(items=chunk, next_cursor=str(nxt) if nxt < len(rows) else None, total=len(rows)))


def demo_provider_set() -> ProviderSet:
    resources, billing, findings, activity = DemoResources(), DemoBilling(), DemoFindings(), DemoActivity()
    snapshots = DemoSnapshots()
    implemented = {
        "accounts": DemoAccounts(), "resources": resources, "billing": billing, "findings": findings,
        "snapshots": snapshots, "activity": activity, "insights": DemoInsights(resources, billing, findings, activity),
        "reports": DemoReports(resources, billing, findings, activity, snapshots),
        "search": ComposedSearch(resources, findings, activity, billing),
    }
    providers = {attr: implemented.get(attr) or NotConnectedProvider(proto, f"{label} isn't available in demo mode.", is_demo=True)
                 for attr, (proto, label) in DOMAINS.items()}
    return ProviderSet(**providers, is_demo=True)
