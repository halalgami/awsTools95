# Upstream: labsnap/tests/test_collector_s3.py @ 1b1d437 — adapted for aws95; owned here from now on.
from botocore.exceptions import ClientError
from aws95.aws.engines.labsnap.collect.collectors.s3 import S3Collector


def denied(op):
    return ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, op)


class FakeS3:
    def __init__(self, deny=()):
        self.deny = set(deny)

    def list_buckets(self):
        return {"Buckets": [{"Name": "b-open"}, {"Name": "b-locked"}]}

    def get_public_access_block(self, Bucket):
        if "pab" in self.deny:
            raise denied("GetPublicAccessBlock")
        if Bucket == "b-open":
            raise ClientError(
                {"Error": {"Code": "NoSuchPublicAccessBlockConfiguration"}},
                "GetPublicAccessBlock")
        return {"PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True, "IgnorePublicAcls": True,
            "BlockPublicPolicy": True, "RestrictPublicBuckets": True}}

    def get_bucket_policy_status(self, Bucket):
        return {"PolicyStatus": {"IsPublic": Bucket == "b-open"}}

    def get_bucket_lifecycle_configuration(self, Bucket):
        raise ClientError(
            {"Error": {"Code": "NoSuchLifecycleConfiguration"}}, "GetLifecycle")

    def get_bucket_encryption(self, Bucket):
        return {"ServerSideEncryptionConfiguration": {"Rules": [{}]}}


class FakeSession:
    def __init__(self, deny=()):
        self._s3 = FakeS3(deny)

    def client(self, name, **kw):
        return self._s3


def test_collects_buckets_with_public_and_lifecycle_state():
    res = S3Collector().collect(FakeSession(), "global")
    by_id = {r.rid: r for r in res.resources}
    assert by_id["b-open"].attrs["public_access_block"] is False
    assert by_id["b-open"].attrs["policy_public"] is True
    assert by_id["b-locked"].attrs["public_access_block"] is True
    assert by_id["b-open"].attrs["has_lifecycle"] is False
    assert by_id["b-open"].attrs["encrypted"] is True


def test_bucket_count_is_emitted_as_a_metric():
    res = S3Collector().collect(FakeSession(), "global")
    assert [m for m in res.metrics if m.name == "s3.bucket_count"][0].value == 2


def test_access_denied_marks_resource_partial_and_counts_a_denial():
    # This is the case that silently corrupts diffs if mishandled: the bucket
    # exists, but we could not read one of its attributes.
    res = S3Collector().collect(FakeSession(deny=["pab"]), "global")
    assert all(r.partial for r in res.resources)
    assert res.denials == 2
    assert "public_access_block" not in res.resources[0].attrs


def test_no_object_contents_are_ever_stored():
    res = S3Collector().collect(FakeSession(), "global")
    for r in res.resources:
        assert not any(k in r.attrs for k in ("objects", "keys", "contents"))


def test_denied_single_call_still_leaves_other_attributes_populated():
    # A denial (unlike an unreachable endpoint, see below) must not
    # short-circuit the rest of the bucket's calls.
    res = S3Collector().collect(FakeSession(deny=["pab"]), "global")
    by_id = {r.rid: r for r in res.resources}
    assert "public_access_block" not in by_id["b-open"].attrs
    assert by_id["b-open"].attrs["policy_public"] is True
    assert by_id["b-open"].attrs["has_lifecycle"] is False
    assert by_id["b-open"].attrs["encrypted"] is True


# ---------------------------------------------------------------------------
# BUG 2 - a ConnectTimeoutError (me-south-1 unreachable from the operator's
# network) is not a ClientError and escaped probe(), sinking the whole
# collector. It must be treated as unreadable, and - since all four calls
# for a bucket hit the same endpoint - the remaining calls for that bucket
# must be skipped once the first one proves unreachable, rather than each
# separately paying the multi-minute timeout.
# ---------------------------------------------------------------------------

from botocore.exceptions import ConnectTimeoutError  # noqa: E402


def unreachable(op):
    return ConnectTimeoutError(endpoint_url="https://s3.me-south-1.amazonaws.com")


class FakeS3WithUnreachableBucket:
    def __init__(self):
        self.calls = []

    def list_buckets(self):
        return {"Buckets": [{"Name": "b-unreachable"}, {"Name": "b-ok"}]}

    def get_public_access_block(self, Bucket):
        self.calls.append(("pab", Bucket))
        if Bucket == "b-unreachable":
            raise unreachable("GetPublicAccessBlock")
        return {"PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True, "IgnorePublicAcls": True,
            "BlockPublicPolicy": True, "RestrictPublicBuckets": True}}

    def get_bucket_policy_status(self, Bucket):
        self.calls.append(("policy", Bucket))
        return {"PolicyStatus": {"IsPublic": False}}

    def get_bucket_lifecycle_configuration(self, Bucket):
        self.calls.append(("lifecycle", Bucket))
        raise ClientError(
            {"Error": {"Code": "NoSuchLifecycleConfiguration"}}, "GetLifecycle")

    def get_bucket_encryption(self, Bucket):
        self.calls.append(("encryption", Bucket))
        return {"ServerSideEncryptionConfiguration": {"Rules": [{}]}}


class FakeSessionWithUnreachableBucket:
    def __init__(self):
        self._s3 = FakeS3WithUnreachableBucket()
        self.client_kwargs = None

    def client(self, name, **kw):
        self.client_kwargs = kw
        return self._s3


def test_unreachable_bucket_is_recorded_partial_with_no_attributes():
    res = S3Collector().collect(FakeSessionWithUnreachableBucket(), "global")
    by_id = {r.rid: r for r in res.resources}
    assert by_id["b-unreachable"].partial is True
    assert by_id["b-unreachable"].attrs == {}


def test_unreachable_bucket_short_circuits_remaining_calls_for_that_bucket():
    session = FakeSessionWithUnreachableBucket()
    S3Collector().collect(session, "global")
    calls_for_unreachable = [c for c in session._s3.calls if c[1] == "b-unreachable"]
    assert calls_for_unreachable == [("pab", "b-unreachable")]


def test_unreachable_bucket_counts_a_single_unreadable_not_four():
    res = S3Collector().collect(FakeSessionWithUnreachableBucket(), "global")
    assert res.denials == 1


def test_other_buckets_still_collected_normally_alongside_an_unreachable_one():
    res = S3Collector().collect(FakeSessionWithUnreachableBucket(), "global")
    by_id = {r.rid: r for r in res.resources}
    assert by_id["b-ok"].partial is False
    assert by_id["b-ok"].attrs["policy_public"] is False
    assert by_id["b-ok"].attrs["has_lifecycle"] is False
    assert by_id["b-ok"].attrs["encrypted"] is True


def test_s3_client_built_with_short_connect_timeout_and_single_attempt():
    # Measured on the live account: a single call to an unreachable region
    # takes over two minutes under botocore's default retries. A short
    # connect timeout and a single attempt make an unreachable region cost
    # seconds instead.
    session = FakeSessionWithUnreachableBucket()
    S3Collector().collect(session, "global")
    config = session.client_kwargs.get("config")
    assert config is not None
    assert config.connect_timeout == 5
    assert config.read_timeout == 10
    assert config.retries == {"max_attempts": 1}


class RegionSession(FakeSessionWithUnreachableBucket):
    region_name = "eu-west-1"


def test_s3_client_uses_the_profile_region():
    """Review Minor 11: like upstream, the client is built in the profile's region (us-east-1 only when it has none)."""
    session = RegionSession()
    S3Collector().collect(session, "global")
    assert session.client_kwargs["region_name"] == "eu-west-1"
    plain = FakeSessionWithUnreachableBucket()
    S3Collector().collect(plain, "global")
    assert plain.client_kwargs["region_name"] == "us-east-1"
