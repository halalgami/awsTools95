# Upstream: labsnap/tests/test_collector_account.py @ 1b1d437 — adapted for aws95; owned here from now on.
from botocore.exceptions import ClientError
from aws95.aws.engines.labsnap.collect.collectors.account import AccountCollector


class FakeIAM:
    def __init__(self, has_policy=True, root_mfa=1):
        self.has_policy = has_policy
        self.root_mfa = root_mfa

    def get_account_password_policy(self):
        if not self.has_policy:
            raise ClientError({"Error": {"Code": "NoSuchEntity"}}, "GetPolicy")
        return {"PasswordPolicy": {"MinimumPasswordLength": 14}}

    def get_account_summary(self):
        return {"SummaryMap": {"AccountMFAEnabled": self.root_mfa, "Users": 3}}


class FakeSession:
    def __init__(self, **kw):
        self._iam = FakeIAM(**kw)

    def client(self, name, **kw):
        return self._iam


def test_records_password_policy_and_root_mfa():
    res = AccountCollector().collect(FakeSession(), "global")
    a = res.resources[0].attrs
    assert a["has_password_policy"] is True and a["root_mfa_enabled"] is True


def test_missing_password_policy_is_absence_not_denial():
    res = AccountCollector().collect(FakeSession(has_policy=False), "global")
    assert res.resources[0].attrs["has_password_policy"] is False
    assert res.denials == 0


class DenyingSummaryIAM(FakeIAM):
    def get_account_summary(self):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}},
                          "GetAccountSummary")


class DenyingSummarySession:
    def client(self, name, **kw):
        return DenyingSummaryIAM()


def test_denied_account_summary_marks_resource_partial_and_omits_root_mfa():
    res = AccountCollector().collect(DenyingSummarySession(), "global")
    r = res.resources[0]
    assert r.partial is True
    assert "root_mfa_enabled" not in r.attrs
    assert res.denials == 1


def test_denied_account_summary_emits_no_user_count_metric():
    res = AccountCollector().collect(DenyingSummarySession(), "global")
    assert all(m.name != "iam.user_count" for m in res.metrics)
