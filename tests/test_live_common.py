from datetime import datetime, timedelta, timezone

from aws95.contracts.accounts import RosterEntry
from aws95.contracts.common import Result, Scope, Status
from aws95.providers.demo import demo_provider_set
from aws95.providers.live.common import freshness_of, join_messages, scope_accounts
from aws95.providers.live.guard import guarded
from aws95.store import StoreError

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


def test_scope_accounts_falls_back_to_the_roster():
    roster = (RosterEntry("dev", "123456789012"), RosterEntry("prod", "210987654321"))
    assert scope_accounts(Scope(), roster) == ("123456789012", "210987654321")
    assert scope_accounts(Scope(accounts=("210987654321",)), roster) == ("210987654321",)


def test_freshness_is_the_oldest_and_stale_after_a_day():
    fresh, stale = freshness_of([NOW - timedelta(hours=2), NOW - timedelta(days=3, hours=1)], NOW)
    assert fresh.source == "snapshot" and fresh.observed_at == NOW - timedelta(days=3, hours=1)
    assert stale == "Snapshot is 3 days old. Take Snapshot to update."
    assert freshness_of([NOW - timedelta(hours=2)], NOW)[1] is None
    assert freshness_of([], NOW) == (None, None)
    assert join_messages(None, "a", "", "b") == "a · b" and join_messages(None) is None


def test_guard_turns_store_errors_into_honest_errors():
    import sqlite3

    @guarded()
    def broken():
        raise sqlite3.DatabaseError("file is not a database")

    @guarded()
    def busy():
        raise StoreError("The local data is in use.")
    assert broken().status is Status.ERROR and "local data" in broken().message
    assert busy().message == "The local data is in use."


def test_demo_never_snapshots_or_accepts():
    ps = demo_provider_set()
    for r in (ps.snapshots.ages(Scope()), ps.snapshots.take_snapshot(Scope()), ps.findings.accept(Scope()),
              ps.findings.unaccept(Scope())):
        assert r.status is Status.NOT_CONNECTED and r.is_demo


def test_catalog_matches_the_collector_registry():
    import aws95.aws.engines.labsnap.collect.collectors  # noqa: F401
    from aws95.aws.engines.labsnap.collect.base import all_collectors
    from aws95.providers.live.catalog import CATALOG, GLOBAL_COLLECTORS
    registry = all_collectors()
    assert {c[1] for c in CATALOG} <= set(registry)
    assert GLOBAL_COLLECTORS == {n for n, cls in registry.items() if cls.scope == "global"}
