from datetime import UTC, date, datetime

import pytest

from aws95.aws import gate
from aws95.aws.engines.ctwatch import history
from aws95.aws.engines.ctwatch import service as service_mod
from aws95.aws.engines.ctwatch.service import ActivityService
from aws95.contracts.accounts import RosterEntry
from aws95.contracts.activity import ImportPlan, TrailInfo
from tests.ctwatch_fakes import A, FakeOrgs, FakeS3, RegionSession, gz

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





# ----- trails -----
class Trails:
    def __init__(self, trails):
        self.trails, self.calls = trails, []

    def describe_trails(self, **kw):
        self.calls.append(kw)
        return {"trailList": self.trails}


RAW = {"Name": "main", "TrailARN": f"arn:aws:cloudtrail:us-east-1:{A}:trail/main", "HomeRegion": "us-east-1",
       "S3BucketName": B, "S3KeyPrefix": "/logs/", "KmsKeyId": f"arn:aws:kms:us-east-1:{A}:key/abcd",
       "IsOrganizationTrail": False, "IsMultiRegionTrail": True, "HasInsightSelectors": True}


def test_trail_info_maps_describe_trails():
    t = history.trail_info(RAW)
    assert t == TrailInfo("main", RAW["TrailARN"], "us-east-1", B, "logs", RAW["KmsKeyId"], False, A, True, True)
    assert history.trail_info({"Name": "x"}) is None


def test_find_trails_notes_org_trails_and_no_trail(make, caller):
    org_raw = {**RAW, "Name": "org", "TrailARN": f"arn:aws:cloudtrail:us-east-1:{MGMT}:trail/org",
               "IsOrganizationTrail": True}
    api = Trails([org_raw])
    _, svc, _ = make(trail_api=api)
    found = svc.find_trails(DEV, ("us-east-1",))
    assert [t.name for t in found.trails] == ["org"] and found.note == history.ORG_NOTE
    assert api.calls == [{"includeShadowTrails": True}]
    api.trails = []
    assert svc.find_trails(DEV, ("us-east-1",)).note == history.NO_TRAIL


# ----- access check -----
def test_check_ok_reads_one_byte_with_the_reader_profile(make, caller):
    s3 = FakeS3({key(D2, "a"): gz([])})
    _, svc, profiles = make(s3=s3)
    check = svc.check_import(plan(trail(kms="arn:aws:kms:us-east-1:123456789012:key/abcd"), reader="log-archive"))
    assert check.ok and check.message == history.ACCESS_OK_KMS and check.policy is None
    assert profiles == ["log-archive"]                             # org-trail profile selection
    assert ("get", {"Bucket": B, "Key": key(D2, "a"), "Range": "bytes=0-0"}) in s3.calls


def test_check_org_trail_pins_the_folder_from_describe_organization(make, caller):
    s3, orgs = FakeS3({key(D2, "a", org=ORG): gz([])}), FakeOrgs(ORG)
    _, svc, _ = make(s3=s3, orgs=orgs)
    check = svc.check_import(plan(trail(is_org=True, owner=MGMT)))
    assert check.ok and orgs.calls == [{}]
    assert all(kw["Prefix"].startswith(f"logs/AWSLogs/{ORG}/{A}/") for op, kw in s3.calls if op == "list")
    assert all("Delimiter" not in kw for _, kw in s3.calls)        # never a bucket-root listing (D4)


def test_check_org_lookup_denied_says_what_to_add_and_reads_nothing(make, caller):
    s3 = FakeS3({key(D2, "a", org=ORG): gz([])})
    _, svc, _ = make(s3=s3, orgs=FakeOrgs(deny=True))
    check = svc.check_import(plan(trail(is_org=True, owner=MGMT)))
    assert not check.ok and check.message == history.ORG_DENIED
    assert '"organizations:DescribeOrganization"' in check.policy and "s3:" not in check.policy
    assert s3.calls == []                                          # no fallback listing


def test_check_list_denied_gives_the_policy_lines(make, caller):
    _, svc, _ = make(s3=FakeS3({}, deny_list=True))
    check = svc.check_import(plan(trail(is_org=True, owner=MGMT)))
    assert not check.ok and check.message.startswith(history.LIST_DENIED.format(bucket=B))
    assert history.ORG_HINT in check.message and '"s3:ListBucket"' in check.policy
    assert '"organizations:DescribeOrganization"' in check.policy


def test_check_kms_denied_message(make, caller):
    kms = "arn:aws:kms:us-east-1:123456789012:key/abcd"
    _, svc, _ = make(s3=FakeS3({key(D2, "a"): gz([])}, kms=True))
    check = svc.check_import(plan(trail(kms=kms)))
    assert not check.ok and check.message == history.KMS_DENIED
    assert '"kms:Decrypt"' in check.policy and kms in check.policy and "s3:GetObject" not in check.policy


def test_check_get_denied_without_kms(make, caller):
    _, svc, _ = make(s3=FakeS3({key(D2, "a"): gz([])}, deny_get=True))
    check = svc.check_import(plan(trail()))
    assert not check.ok and check.message == history.GET_DENIED and '"s3:GetObject"' in check.policy


def test_check_with_no_files(make, caller):
    _, svc, _ = make(s3=FakeS3({}))
    check = svc.check_import(plan(trail()))
    assert check.ok and check.message == history.NO_FILES


def test_validators_exist_only_during_the_check(make, caller):
    _, svc, _ = make(s3=FakeS3({key(D2, "a"): gz([])}))
    svc.check_import(plan(trail()))
    assert not gate.is_allowed("s3", "get_object")
    with pytest.raises(gate.WriteBlockedError):
        gate.call(RegionSession({}), "s3", "get_object", Bucket=B, Key=key(D2, "a"))
