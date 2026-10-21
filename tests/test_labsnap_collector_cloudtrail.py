# Upstream: labsnap/tests/test_collector_cloudtrail.py @ 1b1d437 — adapted for aws95; owned here from now on.
from botocore.exceptions import ClientError
from aws95.aws.engines.labsnap.collect.collectors.cloudtrail import CloudTrailCollector


def denied(op):
    return ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, op)


TRAILS = [
    {"Name": "trail-a", "S3BucketName": "bucket-a", "IsMultiRegionTrail": True},
    {"Name": "trail-b", "S3BucketName": "bucket-b", "IsMultiRegionTrail": False},
]


def absent(op, code="ResourceNotFoundException"):
    return ClientError({"Error": {"Code": code, "Message": "gone"}}, op)


class FakeCloudTrail:
    def __init__(self, deny_status_for=(), absent_status_for=()):
        self.deny_status_for = set(deny_status_for)
        self.absent_status_for = set(absent_status_for)

    def describe_trails(self, **kw):
        # request params matter: without includeShadowTrails=False a
        # multi-region trail is enumerated once per enabled region instead
        # of once, in its home region.
        assert kw.get("includeShadowTrails") is False
        return {"trailList": TRAILS}

    def get_trail_status(self, Name):
        if Name in self.deny_status_for:
            raise denied("GetTrailStatus")
        if Name in self.absent_status_for:
            raise absent("GetTrailStatus")
        return {"IsLogging": Name == "trail-a"}


class FakeSession:
    def __init__(self, **kw):
        self._ct = FakeCloudTrail(**kw)

    def client(self, name, **kw):
        assert name == "cloudtrail"
        return self._ct


def test_trail_attrs_populated():
    res = CloudTrailCollector().collect(FakeSession(), "us-east-1")
    trails = {r.rid: r for r in res.resources}
    assert trails["trail-a"].attrs["is_logging"] is True
    assert trails["trail-a"].attrs["is_multi_region"] is True
    assert trails["trail-a"].attrs["s3_bucket"] == "bucket-a"
    assert trails["trail-b"].attrs["is_logging"] is False


def test_region_is_carried_through():
    res = CloudTrailCollector().collect(FakeSession(), "eu-west-1")
    assert all(r.region == "eu-west-1" for r in res.resources)


def test_denied_trail_status_marks_only_that_trail_partial():
    res = CloudTrailCollector().collect(
        FakeSession(deny_status_for=["trail-a"]), "us-east-1")
    trails = {r.rid: r for r in res.resources}
    assert trails["trail-a"].partial is True
    assert "is_logging" not in trails["trail-a"].attrs
    assert trails["trail-a"].attrs["s3_bucket"] == "bucket-a"  # not lost
    assert trails["trail-b"].partial is False
    assert trails["trail-b"].attrs["is_logging"] is False
    assert res.denials == 1


def test_trail_deleted_mid_scan_does_not_crash_the_collector():
    # 3b: get_trail_status raising an ABSENT_CODES error (e.g. the trail was
    # deleted between describe_trails and this call) makes probe() return
    # (None, denied=False) - "not denied" is true, but the value is still
    # None. Subscripting it unconditionally under `if not denied` is the
    # same shape bug that killed the iam collector on a mid-scan deletion;
    # this must degrade to a missing is_logging attr, not raise.
    res = CloudTrailCollector().collect(
        FakeSession(absent_status_for=["trail-a"]), "us-east-1")
    trails = {r.rid: r for r in res.resources}
    assert "is_logging" not in trails["trail-a"].attrs
    assert trails["trail-a"].attrs["s3_bucket"] == "bucket-a"  # not lost
    assert trails["trail-b"].attrs["is_logging"] is False
