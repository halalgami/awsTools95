"""LiveAccountsProvider: profiles from the AWS config files, identity and permission probes, SSO sign-in."""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from aws95.aws import discover, identity, permissions, sso
from aws95.aws.session import region_for, session_for
from aws95.contracts.accounts import NOT_IN_CONFIG, Profile, RosterEntry
from aws95.contracts.common import AccountRef, Result, Scope, Status
from aws95.core.cancel import CancelToken, raise_if_cancelled
from aws95.providers.live.common import NO_SNAPSHOT, collector_health, freshness_of, latest_snapshots, utcnow
from aws95.providers.live.guard import guarded
from aws95.store import parse_ts

NEUTRAL = "#c0c0c0"
NO_PROFILE = "No profile selected."
_SIGN_IN = {"bad_name": "This profile name can't be passed to the AWS CLI. Run the command yourself.",
            "timeout": "Sign-in timed out.", "cancelled": "Sign-in cancelled."}


def _blank(name: str) -> bool:
    return not name or not name.strip()


class LiveAccountsProvider:
    def __init__(self, session_factory: Callable[[str], Any] = session_for, *, store=None, clock=None) -> None:
        self._session_for = session_factory
        self._store = store
        self._clock = clock or utcnow
        self._lock = threading.Lock()
        self._roster: tuple[RosterEntry, ...] = ()

    def set_roster(self, entries: tuple[RosterEntry, ...]) -> None:
        with self._lock:
            self._roster = tuple(entries)

    def roster(self) -> tuple[RosterEntry, ...]:
        """The thread-safe roster the store-backed providers read."""
        return self._roster_snapshot()

    def _roster_snapshot(self) -> tuple[RosterEntry, ...]:
        with self._lock:
            return self._roster

    @staticmethod
    def _profile(name: str) -> Profile | None:
        return next((p for p in discover.list_profiles() if p.name == name), None)

    @classmethod
    def _never(cls, name: str):
        found = cls._profile(name)
        if found is not None and found.verify == "never":
            return Result.not_connected(found.issue or "This profile can't be used yet.")
        return None

    @guarded()
    def list_profiles(self, scope: Scope | None = None, *, cancel: CancelToken | None = None):
        raise_if_cancelled(cancel)
        roster = {e.profile: e for e in self._roster_snapshot()}
        out: list[Profile] = []
        for p in discover.list_profiles():
            entry = roster.pop(p.name, None)
            ref = AccountRef(entry.account_id, p.name, NEUTRAL, p.name) if entry else None
            out.append(Profile(p.name, p.kind, p.region, ref, entry is not None, p.issue, p.verify, p.sign_in_profile))
        for missing in roster.values():
            out.append(Profile(missing.profile, "profile", None, None, True, NOT_IN_CONFIG, "never"))
        return Result.ok(tuple(out))

    @guarded("sts", "GetCallerIdentity")
    def verify_profile(self, scope: Scope, profile: str = "", *, cancel: CancelToken | None = None):
        if _blank(profile):
            return Result.error(NO_PROFILE)
        if (blocked := self._never(profile)) is not None:
            return blocked
        account, _arn = identity.caller(self._session_for(profile), cancel)
        return Result.ok(AccountRef(account, profile, NEUTRAL, profile))

    @guarded()
    def collector_health(self, scope: Scope, *, cancel: CancelToken | None = None):
        if self._store is None or not scope.accounts:
            return Result.not_connected("Collector health appears after the first snapshot.")
        acct = scope.accounts[0]
        with self._store.reader() as db:
            snap = latest_snapshots(db, [acct]).get(acct)
            if snap is None:
                return Result.not_connected(NO_SNAPSHOT)
            runs = db.execute("SELECT * FROM collector_runs WHERE snapshot_id=? ORDER BY collector",
                              (snap["id"],)).fetchall()
        fresh, stale = freshness_of([parse_ts(snap["taken_at"])], self._clock())
        return Result(Status.STALE if stale else Status.OK, tuple(collector_health(r) for r in runs), None, fresh,
                      stale)

    @guarded()
    def config_sources(self, *, cancel: CancelToken | None = None):
        return Result.ok(discover.config_sources())

    @guarded()
    def check_permissions(self, scope: Scope, profile: str = "", include_billing: bool = False, *,
                          only_billing: bool = False, cancel: CancelToken | None = None):
        if _blank(profile):
            return Result.error(NO_PROFILE)
        found = self._profile(profile)
        if found is not None and found.verify == "never":
            return Result.not_connected(found.issue or "This profile can't be used yet.")
        region = region_for(found.region if found else None)
        only = {"billing"} if only_billing else None
        return Result.ok(permissions.check(self._session_for(profile), region, include_billing, cancel, only))

    @guarded()
    def sign_in(self, profile: str = "", on_prompt=None, *, cancel: CancelToken | None = None):
        if _blank(profile):
            return Result.error(NO_PROFILE)
        found = self._profile(profile)
        target = (found.sign_in_profile if found else None) or profile
        code, status = sso.sign_in(target, cancel, on_prompt)
        if status == "ok":
            return Result.ok(None)
        if status == "no_cli":
            return Result.not_connected(f"AWS CLI not found. Install it from {sso.INSTALL_URL} or run: "
                                        f"{sso.manual_command(target)}")
        if status == "failed":
            return Result.error(f"Sign-in did not complete (exit {code}).")
        return Result.error(_SIGN_IN[status])

    @guarded()
    def required_policy(self, *, cancel: CancelToken | None = None):
        return Result.ok(permissions.policy_json())
