"""StubAccounts: a scripted AccountsProvider for wizard and Accounts-window tests (no AWS)."""
from __future__ import annotations

import threading

from aws95.contracts.accounts import CapabilityCheck, ConfigSource, Profile
from aws95.contracts.common import AccountRef, Result, Status

POLICY = '{"Version": "2012-10-17", "Statement": []}'


def P(name, kind="profile", *, selected=False, account=None, issue=None, verify="auto", sign_in_profile=None):
    ref = AccountRef(account, name, "#c0c0c0", name) if account else None
    if kind == "sso" and sign_in_profile is None:
        sign_in_profile = name
    return Profile(name, kind, "us-east-1", ref, selected, issue, verify, sign_in_profile)


class FakeSignal:
    """A minimal stand-in for Signal(str): connect() records the slot, emit() calls them."""

    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


class StubAccounts:
    def __init__(self, profiles, accounts, *, sources=None, gate: threading.Event | None = None, is_demo=False):
        self.profiles = tuple(profiles)
        self.accounts = dict(accounts)          # name -> account id | Status (failure)
        self.sources = sources or (ConfigSource("/home/u/.aws/config", "config", True, len(self.profiles)),
                                   ConfigSource("/home/u/.aws/credentials", "credentials", False, 0))
        self.gate = gate
        self.is_demo = is_demo
        self.verified, self.checked, self.signed_in = [], [], []

    def _r(self, data):
        return Result.ok(data, is_demo=self.is_demo)

    def list_profiles(self, scope=None, *, cancel=None):
        return self._r(self.profiles)

    def config_sources(self, *, cancel=None):
        if isinstance(self.sources, Result):
            return self.sources
        return self._r(self.sources)

    def verify_profile(self, scope, profile="", *, cancel=None):
        if self.gate is not None:
            self.gate.wait(3)
        self.verified.append(profile)
        got = self.accounts.get(profile, Status.ERROR)
        if isinstance(got, Status):
            return Result(got, None, message=f"{profile} failed", is_demo=self.is_demo)
        return self._r(AccountRef(got, profile, "#c0c0c0", profile))

    def check_permissions(self, scope, profile="", include_billing=False, *, only_billing=False, cancel=None):
        self.checked.append((profile, include_billing, only_billing))
        billing = CapabilityCheck("billing", "Billing", Status.OK if include_billing else Status.NOT_CONNECTED, (),
                                  "Verified." if include_billing else "Not checked (AWS charges $0.01 per check).",
                                  True, True)
        if only_billing:
            return self._r((billing,))
        return self._r((CapabilityCheck("identity", "Identity", Status.OK, (), "Verified.", False, True),
                        CapabilityCheck("activity", "Activity Log", Status.DENIED, ("cloudtrail:LookupEvents",),
                                        "Missing: cloudtrail:LookupEvents (needed by Activity Log, arriving later).",
                                        False, True),
                        billing))

    def sign_in(self, profile="", on_prompt=None, *, cancel=None):
        self.signed_in.append(profile)
        if on_prompt is not None:
            on_prompt("https://device.sso.us-east-1.amazonaws.com/", "ABCD-EFGH")
        self.accounts[profile] = "444444444444"
        return Result.ok(None)

    def required_policy(self, *, cancel=None):
        return self._r(POLICY)

    def collector_health(self, scope, *, cancel=None):
        return Result.not_connected("n/a")
