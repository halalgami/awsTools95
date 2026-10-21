from datetime import UTC, date, datetime, timedelta

import pytest

from aws95.aws.engines.ctwatch import history
from aws95.aws.engines.ctwatch import service as service_mod
from aws95.aws.engines.ctwatch.service import ActivityService
from aws95.contracts.accounts import RosterEntry
from aws95.contracts.activity import ImportPlan, TrailInfo
from aws95.core.cancel import CancelToken
from aws95.store import EventRow
from tests.ctwatch_fakes import A, FakeOrgs, FakeS3, RegionSession, ev, gz

B = "example-cloudtrail-bucket"
ORG = "o-abcde12345"
MGMT = "111111111111"
D1, D2 = date(2026, 10, 1), date(2026, 10, 2)
NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
DEV = RosterEntry("dev", A)


def trail(*, kms=None, is_org=False, insights=False, owner=A):
    return TrailInfo("main", f"arn:aws:cloudtrail:us-east-1:{owner}:trail/main", "us-east-1", B, "logs", kms, is_org,
                     owner, True, insights)


def key(day, name, *, org=None, insight=False, region="us-east-1"):
    kind = "CloudTrail-Insight" if insight else "CloudTrail"
    org_part = f"{org}/" if org else ""
    return (f"logs/AWSLogs/{org_part}{A}/{kind}/{region}/{day:%Y/%m/%d}/"
            f"{A}_{kind}_{region}_{day:%Y%m%d}T0000Z_{name}.json.gz")


def at(day, hour):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def plan(t, *, reader="dev", start=D1, end=D2, regions=("us-east-1",)):
    return ImportPlan(DEV, reader, t, start, end, regions, "dev")


@pytest.fixture
def caller(monkeypatch):
    def fake(session, cancel=None):
        return A, f"arn:aws:iam::{A}:user/auditor"
    monkeypatch.setattr(service_mod.identity, "caller", fake)


@pytest.fixture
def make(make_store):
    """make(s3=..., trail_api=..., orgs=...) -> (store, service, profiles asked for); the store closes at teardown."""
    def build(s3=None, trail_api=None, orgs=None):
        store = make_store()
        profiles = []
        clients = {("s3", "us-east-1"): s3, ("cloudtrail", "us-east-1"): trail_api,
                   ("organizations", "us-east-1"): orgs or FakeOrgs()}

        def factory(profile):
            profiles.append(profile)
            return RegionSession(clients)
        return store, ActivityService(store, session_factory=factory, clock=lambda: NOW, sleep=lambda s: None), profiles
    return build


def ids(store):
    with store.reader() as db:
        return sorted(r[0] for r in db.execute("SELECT event_id FROM events"))




# ----- import -----
def files():
    change = ev("c1", at(D2, 9), "PutBucketPolicy", source="s3.amazonaws.com")
    service_read = ev("s1", at(D2, 9), "GetObject", ptype="AssumedRole", source="s3.amazonaws.com", read_only=True)
    denied_read = ev("d1", at(D2, 10), "GetObject", ptype="AssumedRole", source="s3.amazonaws.com", read_only=True,
                     error="AccessDenied")
    root_read = ev("r1", at(D1, 8), "ListBuckets", ptype="Root", source="s3.amazonaws.com", read_only=True)
    insight = {"eventID": "i1", "eventTime": at(D1, 7).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "eventType": "AwsCloudTrailInsight", "eventSource": "iam.amazonaws.com", "eventName": "AttachUserPolicy"}
    return {key(D2, "a"): gz([change, service_read]), key(D2, "b"): gz([denied_read, change]),   # c1 twice
            key(D1, "a"): gz([root_read]), key(D1, "i", insight=True): gz([insight])}


def test_import_keeps_scope_dedupes_and_records_days(make, caller):
    s3 = FakeS3(files())
    store, svc, _ = make(s3=s3)
    texts = []
    out = svc.import_history(plan(trail(insights=True)), progress=lambda p: texts.append(p.text))
    run = out.runs[0]
    assert out.kind == "import" and run.status == "complete" and run.events_added == 4
    assert ids(store) == ["c1", "d1", "i1", "r1"]                 # s1 (a plain service read) dropped
    with store.reader() as db:
        assert {r[0] for r in db.execute("SELECT origin FROM events")} == {"import"}
    cov = [(c.start, c.end) for c in store.coverage(A, "us-east-1", "import")]
    assert cov == [(at(D1, 0), at(D2, 0) + timedelta(days=1))]    # two adjacent days, merged
    assert texts[0] == "Import dev: 0/2 days" and "Import dev: 0/2 days · us-east-1" in texts
    assert texts[-1] == "Import dev: 2/2 days"
    gets = [kw["Key"] for op, kw in s3.calls if op == "get"]
    assert gets.index(key(D2, "a")) < gets.index(key(D1, "a"))      # newest day first


def test_org_trail_imports_from_the_pinned_org_folder(make, caller):
    s3 = FakeS3({key(D2, "a", org=ORG): gz([ev("c1", at(D2, 9))])})
    store, svc, _ = make(s3=s3, orgs=FakeOrgs(ORG))
    run = svc.import_history(plan(trail(is_org=True, owner=MGMT), start=D2)).runs[0]
    assert run.status == "complete" and ids(store) == ["c1"]
    assert all(kw["Prefix"] != "logs/AWSLogs/" for op, kw in s3.calls if op == "list")


def test_org_lookup_denied_stops_the_import(make, caller):
    s3 = FakeS3({key(D2, "a", org=ORG): gz([ev("c1", at(D2, 9))])})
    store, svc, _ = make(s3=s3, orgs=FakeOrgs(deny=True))
    run = svc.import_history(plan(trail(is_org=True, owner=MGMT), start=D2)).runs[0]
    assert run.status == "failed" and run.message == history.ORG_STOP and s3.calls == [] and ids(store) == []


def test_records_for_another_account_are_skipped(make, caller):
    """Review Minor 4 / Review Focus 3."""
    other = {**ev("x1", at(D2, 9)), "recipientAccountId": "210987654321"}
    store, svc, _ = make(s3=FakeS3({key(D2, "a"): gz([ev("c1", at(D2, 9)), other])}))
    run = svc.import_history(plan(trail(), start=D2)).runs[0]
    assert ids(store) == ["c1"] and "1 record(s) named another account" in run.message


def test_imported_duplicate_survives_retention(make, caller):
    """Review Focus 5."""
    s3 = FakeS3({key(D2, "a"): gz([ev("c1", at(D2, 9))])})
    store, svc, _ = make(s3=s3)
    looked = EventRow("c1", A, "us-east-1", at(D2, 9), "IAMUser", "alice", True, "iam.amazonaws.com", "CreateUser",
                      False, None, None, None, (), 3, "iam-escalation", "lookup", "{}")
    store.insert_events([looked])
    run = svc.import_history(plan(trail(), start=D2)).runs[0]
    assert run.events_added == 0 and ids(store) == ["c1"]
    store.prune_events(at(D2, 0) + timedelta(days=30))
    assert ids(store) == ["c1"]


def test_bad_file_skips_coverage_for_that_day(make, caller):
    """Review Focus 5: corrupt and truncated files are skipped and reported; the day is not marked imported."""
    objs = {key(D2, "good"): gz([ev("c1", at(D2, 9))]), key(D2, "bad"): b"not gzip at all",
            key(D2, "cut"): gz([ev("c2", at(D2, 9))])[:20], key(D1, "a"): gz([ev("c3", at(D1, 9))])}
    store, svc, _ = make(s3=FakeS3(objs))
    run = svc.import_history(plan(trail())).runs[0]
    assert run.status == "partial" and "2 log file(s) or folders could not be read" in run.message
    assert ids(store) == ["c1", "c3"]
    assert [(c.start, c.end) for c in store.coverage(A, "us-east-1", "import")] == [(at(D1, 0), at(D2, 0))]


def test_oversized_file_is_refused(make, caller, monkeypatch):
    monkeypatch.setattr(history, "MAX_JSON", 10)
    store, svc, _ = make(s3=FakeS3({key(D2, "a"): gz([ev("c1", at(D2, 9))])}))
    run = svc.import_history(plan(trail(), start=D2)).runs[0]
    assert run.status == "partial" and "too large" in run.message and ids(store) == []


def test_foreign_key_in_listing_is_never_fetched(make, caller):
    """Review Focus 5: the validator stops a listed key outside this account's tree before any client call."""
    foreign = f"logs/AWSLogs/{A}/CloudTrail/us-east-1/2026/10/02/../../../x.json.gz"
    s3 = FakeS3({key(D2, "a"): gz([ev("c1", at(D2, 9))])}, extra_keys=[foreign])
    store, svc, _ = make(s3=s3)
    run = svc.import_history(plan(trail(), start=D2)).runs[0]
    assert run.status == "partial" and ids(store) == ["c1"]
    assert all(kw["Key"] != foreign for op, kw in s3.calls if op == "get")


def test_denied_import_stops_with_a_pointer_to_check_access(make, caller):
    store, svc, _ = make(s3=FakeS3({key(D2, "a"): gz([])}, deny_get=True))
    run = svc.import_history(plan(trail())).runs[0]
    assert run.status == "failed" and run.message == history.DENIED_STOP and store.coverage(A) == []


def test_cancel_keeps_finished_days(make, caller):
    token = CancelToken()
    objs = {key(D2, "a"): gz([ev("c1", at(D2, 9))]), key(D1, "a"): gz([ev("c2", at(D1, 9))])}

    def progress(p):
        if p.done_days == 1:
            token.cancel()
    store, svc, _ = make(s3=FakeS3(objs))
    out = svc.import_history(plan(trail()), cancel=token, progress=progress)
    assert out.cancelled and out.runs[0].status == "partial" and ids(store) == ["c1"]
    assert [(c.start, c.end) for c in store.coverage(A, "us-east-1", "import")] == [
        (at(D2, 0), at(D2, 0) + timedelta(days=1))]


def test_mark_imported_promotes_lookup_rows(make_store):
    store = make_store("m.db")
    row = EventRow("x", A, "us-east-1", NOW, "IAMUser", "alice", True, "iam.amazonaws.com", "CreateUser", False, None,
                   None, None, (), 0, None, "lookup", "{}")
    store.insert_events([row])
    assert store.mark_imported(["x", "x", "missing"]) == 1 and store.mark_imported(["x"]) == 0
