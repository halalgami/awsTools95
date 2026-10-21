# Upstream: labsnap/tests/test_collector_kms.py @ 1b1d437 — adapted for aws95; owned here from now on.
import json

from botocore.exceptions import ClientError

from aws95.aws.engines.labsnap.collect.collectors.kms import (
    KmsCollector, _account_of, _policy_allows_external)

ACCOUNT = "123456789012"


def _policy(principal, condition=None):
    stmt = {"Effect": "Allow", "Principal": principal,
            "Action": "kms:Decrypt", "Resource": "*"}
    if condition:
        stmt["Condition"] = condition
    return json.dumps({"Version": "2012-10-17", "Statement": [stmt]})


def test_policy_with_wildcard_principal_and_no_condition_is_external():
    assert _policy_allows_external(_policy("*"), ACCOUNT) is True


def test_policy_with_wildcard_principal_narrowed_by_caller_account_is_not():
    assert _policy_allows_external(
        _policy("*", {"StringEquals": {"kms:CallerAccount": ACCOUNT}}),
        ACCOUNT) is False


def test_policy_naming_only_this_account_is_not_external():
    assert _policy_allows_external(
        _policy({"AWS": f"arn:aws:iam::{ACCOUNT}:root"}), ACCOUNT) is False


def test_policy_naming_another_account_is_external():
    assert _policy_allows_external(
        _policy({"AWS": "arn:aws:iam::222222222222:root"}), ACCOUNT) is True


class FakeKms:
    def __init__(self, deny_policy=False, deny_aliases=False):
        self.deny_policy = deny_policy
        self.deny_aliases = deny_aliases
        self.rotation_calls = []

    def get_paginator(self, op):
        pages = {
            "list_keys": [{"Keys": [{"KeyId": "cmk-1"}, {"KeyId": "aws-1"}]}],
            "list_aliases": [{"Aliases": [
                {"AliasName": "alias/app", "TargetKeyId": "cmk-1"},
                {"AliasName": "alias/aws/s3", "TargetKeyId": "aws-1"}]}],
        }
        deny_aliases = self.deny_aliases

        class P:
            def paginate(self_inner, **kw):
                if op == "list_aliases" and deny_aliases:
                    raise ClientError(
                        {"Error": {"Code": "AccessDeniedException"}},
                        "ListAliases")
                return iter(pages[op])
        return P()

    def describe_key(self, KeyId):
        meta = {
            "cmk-1": {"KeyId": "cmk-1", "KeyManager": "CUSTOMER",
                      "KeyState": "Enabled", "KeySpec": "SYMMETRIC_DEFAULT",
                      "KeyUsage": "ENCRYPT_DECRYPT"},
            "aws-1": {"KeyId": "aws-1", "KeyManager": "AWS",
                      "KeyState": "Enabled", "KeySpec": "SYMMETRIC_DEFAULT",
                      "KeyUsage": "ENCRYPT_DECRYPT"},
        }[KeyId]
        return {"KeyMetadata": meta}

    def get_key_rotation_status(self, KeyId):
        self.rotation_calls.append(KeyId)
        return {"KeyRotationEnabled": False}

    def get_key_policy(self, KeyId, PolicyName):
        if self.deny_policy:
            raise ClientError({"Error": {"Code": "AccessDeniedException"}},
                              "GetKeyPolicy")
        return {"Policy": _policy("*")}


class Session:
    def __init__(self, **kw):
        self.kms = FakeKms(**kw)

    def client(self, name, **kw):
        assert name == "kms"
        return self.kms


def test_customer_key_records_rotation_and_derived_policy_flag():
    res = KmsCollector().collect(Session(), "eu-west-1")
    cmk = [r for r in res.resources if r.rid == "cmk-1"][0]
    assert cmk.attrs["manager"] == "CUSTOMER"
    assert cmk.attrs["rotation_enabled"] is False
    assert cmk.attrs["policy_allows_external"] is True
    assert cmk.attrs["alias_count"] == 1


def test_aws_managed_key_is_collected_but_carries_no_rotation_attribute():
    # Rotation of an AWS-managed key is not the operator's to change, so a
    # rotation finding on one would be permanently unactionable.
    s = Session()
    res = KmsCollector().collect(s, "eu-west-1")
    aws_key = [r for r in res.resources if r.rid == "aws-1"][0]
    assert aws_key.attrs["manager"] == "AWS"
    assert "rotation_enabled" not in aws_key.attrs
    assert s.kms.rotation_calls == ["cmk-1"]


def test_the_raw_key_policy_document_is_never_stored():
    res = KmsCollector().collect(Session(), "eu-west-1")
    for r in res.resources:
        assert "policy" not in r.attrs
        assert not any("Statement" in str(v) for v in r.attrs.values())


def test_denied_policy_read_omits_the_flag_and_marks_partial():
    res = KmsCollector().collect(Session(deny_policy=True), "eu-west-1")
    cmk = [r for r in res.resources if r.rid == "cmk-1"][0]
    assert cmk.partial is True
    assert "policy_allows_external" not in cmk.attrs
    # Rotation was readable and must survive the unrelated denial.
    assert cmk.attrs["rotation_enabled"] is False
    assert res.denials >= 1


def test_denied_aliases_read_omits_alias_count_and_marks_every_key_partial():
    # A denied list_aliases must never look like "zero aliases" on any key -
    # every key resource loses alias_count and is marked partial for it,
    # even though describe_key, rotation and policy were all readable.
    res = KmsCollector().collect(Session(deny_aliases=True), "eu-west-1")
    assert len(res.resources) == 2
    for r in res.resources:
        assert "alias_count" not in r.attrs
        assert r.partial is True
    cmk = [r for r in res.resources if r.rid == "cmk-1"][0]
    # Unrelated attributes must survive the unrelated denial.
    assert cmk.attrs["rotation_enabled"] is False
    assert cmk.attrs["policy_allows_external"] is True
    assert res.denials >= 1


# ---------------------------------------------------------------------------
# _account_of
# ---------------------------------------------------------------------------

def test_account_of_extracts_the_account_segment_from_a_well_formed_arn():
    assert _account_of({"Arn": f"arn:aws:kms:eu-west-1:{ACCOUNT}:key/cmk-1"}) == ACCOUNT


def test_account_of_returns_empty_string_for_a_malformed_or_missing_arn():
    assert _account_of({"Arn": "not-an-arn"}) == ""
    assert _account_of({}) == ""


# ---------------------------------------------------------------------------
# end-to-end: describe_key DOES return an Arn, exercising the real
# account-comparison branch of _policy_allows_external rather than the
# wildcard-principal short-circuit that the rest of this file deliberately
# stays inside of (see FakeKms above).
# ---------------------------------------------------------------------------

OTHER_ACCOUNT = "222222222222"


class FakeKmsWithArn:
    """A single customer-managed key whose describe_key response carries a
    real Arn, and whose policy names a specific foreign account rather than
    a wildcard - so the collector must actually call _account_of and compare
    accounts to reach `policy_allows_external`."""

    def get_paginator(self, op):
        pages = {
            "list_keys": [{"Keys": [{"KeyId": "cmk-arn"}]}],
            "list_aliases": [{"Aliases": []}],
        }

        class P:
            def paginate(self_inner, **kw):
                return iter(pages[op])
        return P()

    def describe_key(self, KeyId):
        return {"KeyMetadata": {
            "KeyId": "cmk-arn", "KeyManager": "CUSTOMER",
            "KeyState": "Enabled", "KeySpec": "SYMMETRIC_DEFAULT",
            "KeyUsage": "ENCRYPT_DECRYPT",
            "Arn": f"arn:aws:kms:eu-west-1:{ACCOUNT}:key/cmk-arn"}}

    def get_key_rotation_status(self, KeyId):
        return {"KeyRotationEnabled": True}

    def get_key_policy(self, KeyId, PolicyName):
        return {"Policy": _policy({"AWS": f"arn:aws:iam::{OTHER_ACCOUNT}:root"})}


class SessionWithArn:
    def client(self, name, **kw):
        assert name == "kms"
        return FakeKmsWithArn()


def test_policy_naming_a_foreign_account_is_flagged_end_to_end_via_the_real_arn():
    res = KmsCollector().collect(SessionWithArn(), "eu-west-1")
    key = [r for r in res.resources if r.rid == "cmk-arn"][0]
    assert key.attrs["policy_allows_external"] is True


# --- conditions are judged by their values, not their presence (final-review ruling, owner 2026-10-15) -----------

def test_a_condition_naming_another_account_does_not_narrow():
    assert _policy_allows_external(
        _policy("*", {"StringEquals": {"aws:SourceAccount": "222222222222"}}), ACCOUNT) is True
    assert _policy_allows_external(
        _policy("*", {"StringEquals": {"kms:CallerAccount": [ACCOUNT, "222222222222"]}}), ACCOUNT) is True


def test_a_condition_naming_this_account_narrows():
    assert _policy_allows_external(
        _policy("*", {"StringEquals": {"aws:SourceAccount": ACCOUNT}}), ACCOUNT) is False
    assert _policy_allows_external(
        _policy("*", {"ArnLike": {"aws:SourceArn": f"arn:aws:s3:::bucket-{ACCOUNT}:{ACCOUNT}:x"}}), ACCOUNT) is False


def test_a_negated_condition_never_narrows():
    assert _policy_allows_external(
        _policy("*", {"StringNotEquals": {"kms:CallerAccount": ACCOUNT}}), ACCOUNT) is True


def test_an_organisation_condition_does_not_prove_the_grant_is_internal():
    """Security review: the collector can't tell this account's organisation from another one, so an org-ID
    condition alone no longer clears a wildcard grant (fail closed: the finding asks a person to look)."""
    assert _policy_allows_external(
        _policy("*", {"StringEquals": {"aws:PrincipalOrgID": "o-abcde12345"}}), ACCOUNT) is True


def test_account_conditions_with_wildcards_do_not_narrow():
    assert _policy_allows_external(
        _policy("*", {"StringLike": {"aws:SourceAccount": "*"}}), ACCOUNT) is True
    assert _policy_allows_external(
        _policy("*", {"ArnLike": {"aws:SourceArn": f"arn:aws:s3:::*:{ACCOUNT}:*"}}), ACCOUNT) is True


def test_a_federated_principal_is_external():
    assert _policy_allows_external(_policy({"Federated": "cognito-identity.amazonaws.com"}), ACCOUNT) is True
