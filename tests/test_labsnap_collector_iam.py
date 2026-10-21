# Upstream: labsnap/tests/test_collector_iam.py @ 1b1d437 — adapted for aws95; owned here from now on.
from botocore.exceptions import ClientError
from aws95.aws.engines.labsnap.collect.collectors.iam import IamCollector


class FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kw):
        return iter(self._pages)


class FakeIAM:
    def get_paginator(self, op):
        return FakePaginator([{"Users": [{"UserName": "alice"}, {"UserName": "bob"}]}])

    def list_access_keys(self, UserName):
        keys = {"alice": [{"AccessKeyId": "AKIA1", "Status": "Active",
                           "CreateDate": _dt()}],
                "bob": []}
        return {"AccessKeyMetadata": keys[UserName]}

    def list_mfa_devices(self, UserName):
        return {"MFADevices": [{"SerialNumber": "s"}] if UserName == "alice" else []}

    def get_access_key_last_used(self, AccessKeyId):
        # Only AKIA1 exists in the base fixture; a used-since-creation shape
        # is the default so pre-existing tests that don't care about
        # last_used keep passing unmodified.
        return {"AccessKeyLastUsed": {"LastUsedDate": _dt(),
                                      "ServiceName": "s3", "Region": "us-east-1"}}


def _dt():
    from datetime import UTC, datetime
    return datetime(2026, 1, 1, tzinfo=UTC)


def _dt2():
    from datetime import UTC, datetime
    return datetime(2026, 6, 1, tzinfo=UTC)


class FakeSession:
    def client(self, name, **kw):
        return FakeIAM()


def test_collects_users_with_mfa_state():
    res = IamCollector().collect(FakeSession(), "global")
    users = {r.rid: r for r in res.resources if r.rtype == "iam.user"}
    assert users["alice"].attrs["has_mfa"] is True
    assert users["bob"].attrs["has_mfa"] is False


def test_collects_access_keys_as_their_own_resources():
    res = IamCollector().collect(FakeSession(), "global")
    keys = [r for r in res.resources if r.rtype == "iam.access_key"]
    assert [k.rid for k in keys] == ["AKIA1"]
    assert keys[0].attrs["user"] == "alice" and keys[0].attrs["status"] == "Active"


def test_no_secret_material_is_stored():
    res = IamCollector().collect(FakeSession(), "global")
    for r in res.resources:
        assert not any("secret" in k.lower() for k in r.attrs)


def test_emits_user_and_key_counts():
    res = IamCollector().collect(FakeSession(), "global")
    names = {m.name: m.value for m in res.metrics}
    assert names["iam.users_without_mfa"] == 1.0
    assert names["iam.active_access_keys"] == 1.0


def denied(op):
    return ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, op)


class DenyingIAM(FakeIAM):
    def __init__(self, deny_mfa_for=(), deny_keys_for=()):
        self.deny_mfa_for = set(deny_mfa_for)
        self.deny_keys_for = set(deny_keys_for)

    def list_mfa_devices(self, UserName):
        if UserName in self.deny_mfa_for:
            raise denied("ListMFADevices")
        return super().list_mfa_devices(UserName=UserName)

    def list_access_keys(self, UserName):
        if UserName in self.deny_keys_for:
            raise denied("ListAccessKeys")
        return super().list_access_keys(UserName=UserName)


class DenyingSession:
    def __init__(self, **kw):
        self._iam = DenyingIAM(**kw)

    def client(self, name, **kw):
        return self._iam


def test_per_user_mfa_denial_marks_only_that_user_partial_and_keeps_others():
    res = IamCollector().collect(DenyingSession(deny_mfa_for=["alice"]), "global")
    users = {r.rid: r for r in res.resources if r.rtype == "iam.user"}
    assert users["alice"].partial is True
    assert "has_mfa" not in users["alice"].attrs
    assert users["bob"].partial is False
    assert users["bob"].attrs["has_mfa"] is False
    assert res.denials == 1


def test_denied_access_keys_for_one_user_omits_only_that_users_keys():
    res = IamCollector().collect(DenyingSession(deny_keys_for=["alice"]), "global")
    keys = [r for r in res.resources if r.rtype == "iam.access_key"]
    assert keys == []  # alice's AKIA1 is unknown now; bob had none anyway
    users = {r.rid: r for r in res.resources if r.rtype == "iam.user"}
    assert users["alice"].partial is True
    assert res.denials == 1


def test_metrics_exclude_denied_users_and_keys():
    res = IamCollector().collect(
        DenyingSession(deny_mfa_for=["alice"], deny_keys_for=["alice"]), "global")
    names = {m.name: m.value for m in res.metrics}
    # alice's mfa state and keys are both unknown; only bob (known, no mfa,
    # no keys) contributes - a denial must never look like a phantom zero.
    assert names["iam.users_without_mfa"] == 1.0
    assert names["iam.active_access_keys"] == 0.0
    assert res.denials == 2


# ---------------------------------------------------------------------------
# access key last-used: used / never-used / denied
# ---------------------------------------------------------------------------

class LastUsedIAM(FakeIAM):
    def get_paginator(self, op):
        return FakePaginator([{"Users": [{"UserName": "alice"}, {"UserName": "carol"}]}])

    def list_access_keys(self, UserName):
        keys = {"alice": [{"AccessKeyId": "AKIA-USED", "Status": "Active",
                           "CreateDate": _dt()}],
                "carol": [{"AccessKeyId": "AKIA-NEVER", "Status": "Active",
                          "CreateDate": _dt()}]}
        return {"AccessKeyMetadata": keys[UserName]}

    def list_mfa_devices(self, UserName):
        return {"MFADevices": []}

    def get_access_key_last_used(self, AccessKeyId):
        if AccessKeyId == "AKIA-USED":
            return {"AccessKeyLastUsed": {"LastUsedDate": _dt2(),
                                          "ServiceName": "s3", "Region": "us-east-1"}}
        if AccessKeyId == "AKIA-NEVER":
            # AWS omits LastUsedDate entirely (returns ServiceName/Region
            # "N/A") when a key has never been used - no exception, no
            # sentinel date, just an absent key in the response.
            return {"AccessKeyLastUsed": {"ServiceName": "N/A", "Region": "N/A"}}
        raise AssertionError(f"unexpected AccessKeyId {AccessKeyId!r}")


class DenyingLastUsedIAM(LastUsedIAM):
    def __init__(self, deny_for=()):
        self.deny_for = set(deny_for)

    def get_access_key_last_used(self, AccessKeyId):
        if AccessKeyId in self.deny_for:
            raise denied("GetAccessKeyLastUsed")
        return super().get_access_key_last_used(AccessKeyId=AccessKeyId)


class LastUsedSession:
    def __init__(self, iam):
        self._iam = iam

    def client(self, name, **kw):
        return self._iam


def test_access_key_last_used_records_used_and_never_used_states():
    res = IamCollector().collect(LastUsedSession(LastUsedIAM()), "global")
    keys = {r.rid: r for r in res.resources if r.rtype == "iam.access_key"}
    assert keys["AKIA-USED"].attrs["last_used"] == "2026-06-01T00:00:00Z"
    assert keys["AKIA-USED"].partial is False
    # Never-used is a real, meaningful None - not a missing attribute.
    assert "last_used" in keys["AKIA-NEVER"].attrs
    assert keys["AKIA-NEVER"].attrs["last_used"] is None
    assert keys["AKIA-NEVER"].partial is False


def test_access_key_last_used_denial_omits_attribute_and_marks_partial():
    res = IamCollector().collect(
        LastUsedSession(DenyingLastUsedIAM(deny_for=["AKIA-USED"])), "global")
    keys = {r.rid: r for r in res.resources if r.rtype == "iam.access_key"}
    # A denial must never be conflated with "never used" - the attribute is
    # absent entirely, and the resource is marked partial.
    assert "last_used" not in keys["AKIA-USED"].attrs
    assert keys["AKIA-USED"].partial is True
    assert keys["AKIA-NEVER"].attrs["last_used"] is None
    assert keys["AKIA-NEVER"].partial is False
    assert res.denials >= 1


# ---------------------------------------------------------------------------
# I1 - probe() returns (None, False) for an ABSENT_CODES error (e.g. a user
# or key deleted mid-scan), not just for a denial. Every consumer of probe()
# must handle that None without crashing.
# ---------------------------------------------------------------------------

def absent(op):
    return ClientError({"Error": {"Code": "NoSuchEntity", "Message": "gone"}}, op)


class GoneMidScanIAM(FakeIAM):
    """alice is deleted between list_users and each follow-up call - every
    follow-up genuinely has nothing to return, not a permission denial."""

    def list_mfa_devices(self, UserName):
        if UserName == "alice":
            raise absent("ListMFADevices")
        return super().list_mfa_devices(UserName=UserName)

    def list_access_keys(self, UserName):
        if UserName == "alice":
            raise absent("ListAccessKeys")
        return super().list_access_keys(UserName=UserName)


class GoneMidScanSession:
    def client(self, name, **kw):
        return GoneMidScanIAM()


def test_mid_scan_deletion_does_not_crash_the_collector():
    # Before the fix, `mfa["MFADevices"]` and `keys["AccessKeyMetadata"]`
    # subscripted probe()'s None return directly, raising TypeError instead
    # of treating a mid-scan deletion as an ordinary absence.
    res = IamCollector().collect(GoneMidScanSession(), "global")
    users = {r.rid: r for r in res.resources if r.rtype == "iam.user"}
    assert "alice" in users
    # An absence is not a denial: no permission was denied, so this must not
    # count against denials or mark the resource partial.
    assert users["alice"].partial is False
    assert "has_mfa" not in users["alice"].attrs
    assert res.denials == 0
    # bob is unaffected.
    assert users["bob"].attrs["has_mfa"] is False


class GoneLastUsedIAM(LastUsedIAM):
    def get_access_key_last_used(self, AccessKeyId):
        raise absent("GetAccessKeyLastUsed")


def test_mid_scan_key_deletion_before_last_used_does_not_crash():
    res = IamCollector().collect(
        LastUsedSession(GoneLastUsedIAM()), "global")
    keys = {r.rid: r for r in res.resources if r.rtype == "iam.access_key"}
    assert "last_used" not in keys["AKIA-USED"].attrs
    assert keys["AKIA-USED"].partial is False
    assert res.denials == 0
