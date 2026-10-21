from datetime import timedelta

import pytest
from PySide6.QtCore import QSettings

from aws95.aws.engines.ctwatch.service import ActivityBusy
from aws95.contracts.activity import (NO_HISTORY, AccessCheck, AccountLoad, ActivityQuery, HistoryOutcome,
                                      HistoryProgress, HistoryRequest, TrailLookup)
from aws95.contracts.common import Scope, Status, TimeRange
from aws95.core.cancel import CancelToken
from aws95.providers.live.activity import LiveActivity
from tests.activity_seed import DEV, IMPORT_START, PINNED, PROD, ROSTER, seed_activity


@pytest.fixture
def store(tmp_path):
    s = seed_activity(tmp_path / "a.db")
    yield s
    s.close()                                      # -W error: an unclosed database is a ResourceWarning


def live(store, **kw):
    kw.setdefault("clock", lambda: PINNED)
    return LiveActivity(store, lambda: ROSTER, **kw)


def scope(days=7, accounts=(), regions=()):
    return Scope(tuple(accounts), tuple(regions), TimeRange(PINNED - timedelta(days=days), PINNED))


def ids(result):
    return [e.event_id for e in result.data.items]


def test_events_newest_first_with_total_and_freshness(store):
    r = live(store).events(scope())
    assert r.status is Status.OK and r.data.total == 17 and r.data.next_cursor is None
    assert ids(r)[0] == "ev-0001" and [e.time_utc for e in r.data.items] == sorted(
        (e.time_utc for e in r.data.items), reverse=True)
    assert r.freshness.observed_at == PINNED - timedelta(hours=1) and r.freshness.source == "activity"
    assert r.coverage.regions_requested == ("eu-west-1", "us-east-1") and r.coverage.complete


def test_filters(store):
    p = live(store)
    assert sorted(ids(p.events(scope(), ActivityQuery(principal="bob")))) == ["ev-0006", "ev-0008", "ev-0010"]
    assert sorted(ids(p.events(scope(), ActivityQuery(risk_min=3)))) == ["ev-0002", "ev-0012", "ev-0016"]
    assert all(e.is_human for e in p.events(scope(), ActivityQuery(human_only=True)).data.items)
    assert {e.source for e in p.events(scope(), ActivityQuery(service="iam.amazonaws.com")).data.items} == {
        "iam.amazonaws.com"}
    assert ids(p.events(scope(accounts=(PROD,)), ActivityQuery(action="ConsoleLogin"))) == ["ev-0014"]
    denied = p.events(scope(), ActivityQuery(principal="bob", action="PutRolePolicy")).data.items[0]
    assert denied.error_code == "AccessDenied"


def test_arn_filter_treats_wildcards_literally(store):
    p = live(store)
    assert ids(p.events(scope(), ActivityQuery(arn_contains="example_bucket"))) == ["ev-0003"]
    assert ids(p.events(scope(), ActivityQuery(arn_contains="%"))) == []


def test_paging(store):
    from aws95.providers.live import activity as mod
    p = live(store)
    mod_size, mod.PAGE_SIZE = mod.PAGE_SIZE, 5
    try:
        first = p.events(scope())
        second = p.events(scope(), None, first.data.next_cursor)
    finally:
        mod.PAGE_SIZE = mod_size
    assert len(first.data.items) == 5 and first.data.next_cursor == "5" and ids(second)[0] not in ids(first)


def test_principals_series_and_top(store):
    p = live(store)
    principals = p.principals(scope()).data
    counts = [x.count for x in principals]
    assert counts == sorted(counts, reverse=True) and principals[0].principal_name == "ci-deploy-role"
    day = p.time_series(scope(), "day").data
    assert day.bucket == "day" and sum(n for _, n in day.series) == 17
    assert all(t.hour == 0 and t.tzinfo is not None for t, _ in day.series)
    hour = p.time_series(scope(days=1), "hour").data
    assert all(t.minute == 0 for t, _ in hour.series) and sum(n for _, n in hour.series) == 8
    assert p.top(scope(), "principal", 2).data[0] == ("ci-deploy-role", 4)
    assert {k for k, _ in p.top(scope(), "actor_kind", 2).data} == {"human", "service"}
    assert p.top(scope(), "service", 2).data == (("ec2.amazonaws.com", 4), ("iam.amazonaws.com", 4))   # ties by name


def test_raw_event_is_the_stored_record(store):
    p = live(store)
    raw = p.raw_event(scope(), "ev-0002").data
    assert raw["eventID"] == "ev-0002" and raw["eventName"] == "CreateAccessKey"
    assert p.raw_event(scope(), "nope").status is Status.ERROR
    assert p.raw_event(scope(accounts=(PROD,)), "ev-0002").status is Status.ERROR      # another account's event


def test_not_looked_at_is_never_empty(make_store):
    p = live(make_store("empty.db"))
    for r in (p.events(scope()), p.principals(scope()), p.time_series(scope(), "day"), p.top(scope(), "principal")):
        assert r.status is Status.NOT_CONNECTED and r.message == NO_HISTORY
    nobody = LiveActivity(make_store("e2.db"), lambda: (), clock=lambda: PINNED)
    assert nobody.events(scope()).status is Status.NOT_CONNECTED and "No accounts" in nobody.events(scope()).message


def test_uncovered_region_is_not_loaded_not_empty(store):
    """Review Focus 3."""
    p = live(store)
    only = p.events(scope(regions=("ap-south-1",)))
    assert only.status is Status.NOT_CONNECTED and "isn't loaded for ap-south-1" in only.message
    mixed = p.events(scope(regions=("us-east-1", "ap-south-1")))
    assert mixed.status is Status.PARTIAL and mixed.coverage.regions_covered == ("us-east-1",)
    assert mixed.data.total == 13


def test_range_before_history_is_partial(store):
    """Review Focus 3."""
    r = live(store).events(scope(days=30, accounts=(PROD,)))
    assert r.status is Status.PARTIAL and "History starts 2026-09-29 12:00 UTC" in r.message
    assert r.data.total == 4                                      # still shows what it has


def test_stale_history_says_so(store):
    r = live(store, clock=lambda: PINNED + timedelta(days=3)).events(scope())
    assert r.status is Status.STALE and "last updated 3 days ago. Press F5 to update." in r.message


def test_history_status(store):
    st = live(store).history_status(scope()).data
    by = {a.account: a for a in st.accounts}
    assert not by[DEV].unfinished and by[PROD].unfinished and by[DEV].days_requested == 7
    assert [r.region for r in by[DEV].regions] == ["eu-west-1", "us-east-1"]
    assert by[DEV].regions[1].imported[0].start == IMPORT_START
    assert st.denied_changes_since == PINNED - timedelta(days=7) and st.denied_reads_since == IMPORT_START
    assert st.running is None and st.progress is None
    shorter = live(store, retention_days=2).history_status(scope()).data
    assert not shorter.unfinished                                  # retention caps the range Resume would fill


class FakeService:
    def __init__(self, busy=False):
        self.busy, self.seen, self.running, self.progress_text = busy, [], None, None

    def load(self, request, *, cancel=None, progress=None):
        self.seen.append(("load", request, cancel))
        if self.busy:
            raise ActivityBusy("Activity is already loading.")
        progress(HistoryProgress("load", "dev", 1, 7, "us-east-1"))
        return HistoryOutcome("load", (AccountLoad(DEV, "complete", 3),
                                       AccountLoad(PROD, "failed", 0, "Permission denied for sts:GetCallerIdentity.")))

    def estimate(self, request, *, cancel=None):
        self.seen.append(("estimate", request))
        return ()

    def find_trails(self, entry, regions, *, cancel=None):
        self.seen.append(("trails", entry, regions))
        return TrailLookup(entry.account_id, ())

    def check_import(self, plan, *, cancel=None):
        return AccessCheck(True, "ok")

    def import_history(self, plan, *, cancel=None, progress=None):
        return HistoryOutcome("import", (AccountLoad(DEV, "complete", 2),))


def test_load_history_passes_both_tokens_and_reports(store):
    fake = FakeService()
    p = live(store, service_factory=lambda s: fake)
    stop, got = CancelToken(), []
    req = HistoryRequest(ROSTER, {DEV: ("us-east-1",), PROD: ("us-east-1",)}, 7, labels={DEV: "dev", PROD: "prod"})
    r = p.load_history(scope(), req, progress=got.append, stop=stop)
    assert r.status is Status.PARTIAL and r.data.runs[0].events_added == 3
    assert r.message == "dev: complete (3 new events) · prod: failed (Permission denied for sts:GetCallerIdentity.)"
    token = fake.seen[0][2]
    assert not token.cancelled and (stop.cancel() or token.cancelled)
    assert got[0].text == "Activity dev: 1/7 days · us-east-1"
    fake.running, fake.progress_text = "load", "Activity dev: 1/7 days · us-east-1"
    st = p.history_status(scope()).data
    assert st.running == "load" and st.progress == "Activity dev: 1/7 days · us-east-1"


def test_busy_and_missing_requests_are_errors(store):
    p = live(store, service_factory=lambda s: FakeService(busy=True))
    assert p.load_history(scope(), HistoryRequest(ROSTER[:1])).message == "Activity is already loading."
    assert p.load_history(scope()).status is Status.ERROR and p.estimate_history(scope()).status is Status.ERROR
    assert p.check_import(scope()).status is Status.ERROR and p.import_history(scope()).status is Status.ERROR


def test_find_trails_defaults_to_the_first_scoped_account(store):
    fake = FakeService()
    p = live(store, service_factory=lambda s: fake)
    assert p.find_trails(scope(accounts=(PROD,))).data.account == PROD
    assert fake.seen[-1] == ("trails", ROSTER[1], ("us-east-1",))


def test_live_set_wires_activity_and_retention(tmp_path, qapp):
    from aws95.core.settings import Settings
    from aws95.providers.live import live_provider_set
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    settings.set_roster(ROSTER)
    store = seed_activity(tmp_path / "a.db")
    try:
        ps = live_provider_set(settings, store=store, clock=lambda: PINNED)
        assert isinstance(ps.activity, LiveActivity) and ps.activity.retention_days == 90
        settings.activity_retention_days = 30
        assert ps.activity.retention_days == 30
        assert ps.activity.events(scope()).status is Status.OK
    finally:
        store.close()
