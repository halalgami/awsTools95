# Upstream: labsnap/tests/test_collector_ecr.py @ 1b1d437 — adapted for aws95; owned here from now on.
import json

from botocore.exceptions import ClientError

from aws95.aws.engines.labsnap.collect.collectors.ecr import EcrCollector, _policy_allows_public


def test_policy_with_wildcard_principal_is_public():
    assert _policy_allows_public(json.dumps({"Statement": [
        {"Effect": "Allow", "Principal": "*", "Action": "ecr:BatchGetImage"}]}))


def test_policy_naming_a_specific_account_is_not_public():
    assert not _policy_allows_public(json.dumps({"Statement": [
        {"Effect": "Allow",
         "Principal": {"AWS": "arn:aws:iam::123456789012:root"},
         "Action": "ecr:BatchGetImage"}]}))


def test_deny_statement_with_wildcard_principal_is_not_public():
    assert not _policy_allows_public(json.dumps({"Statement": [
        {"Effect": "Deny", "Principal": "*", "Action": "ecr:BatchGetImage"}]}))


class FakeEcr:
    """Response keys are lowercase - that is what the ECR API actually returns.

    Every other collector here sees PascalCase. Copying that habit into this
    fake would make the test agree with the mistake instead of the API.
    """

    def __init__(self, deny_policy=False, no_policy=False, no_lifecycle=True):
        self.deny_policy = deny_policy
        self.no_policy = no_policy
        self.no_lifecycle = no_lifecycle

    def get_paginator(self, op):
        class P:
            def paginate(self_inner, **kw):
                return iter([{"repositories": [{
                    "repositoryArn": "arn:aws:ecr:eu-west-1:123456789012:"
                                     "repository/api",
                    "repositoryName": "api",
                    "imageTagMutability": "MUTABLE",
                    "imageScanningConfiguration": {"scanOnPush": False},
                    "encryptionConfiguration": {"encryptionType": "AES256"},
                }]}])
        return P()

    def get_repository_policy(self, repositoryName):
        if self.deny_policy:
            raise ClientError({"Error": {"Code": "AccessDeniedException"}},
                              "GetRepositoryPolicy")
        if self.no_policy:
            raise ClientError(
                {"Error": {"Code": "RepositoryPolicyNotFoundException"}},
                "GetRepositoryPolicy")
        return {"policyText": json.dumps({"Statement": [
            {"Effect": "Allow", "Principal": "*",
             "Action": "ecr:BatchGetImage"}]})}

    def get_lifecycle_policy(self, repositoryName):
        if self.no_lifecycle:
            raise ClientError(
                {"Error": {"Code": "LifecyclePolicyNotFoundException"}},
                "GetLifecyclePolicy")
        return {"lifecyclePolicyText": "{}"}


class Session:
    def __init__(self, **kw):
        self.ecr = FakeEcr(**kw)

    def client(self, name, **kw):
        assert name == "ecr"
        return self.ecr


def test_repository_configuration_is_recorded_from_lowercase_keys():
    res = EcrCollector().collect(Session(), "eu-west-1")
    repo = res.resources[0]
    assert repo.rid == "api"
    assert repo.attrs["scan_on_push"] is False
    assert repo.attrs["tag_mutability"] == "MUTABLE"
    assert repo.attrs["encryption_type"] == "AES256"


def test_wildcard_repository_policy_is_recorded_as_public():
    res = EcrCollector().collect(Session(), "eu-west-1")
    assert res.resources[0].attrs["policy_allows_public"] is True


def test_absent_repository_policy_is_not_public_and_not_partial():
    # No policy attached is an ANSWER, not a gap. probe() returns it as
    # readable-and-empty because Task 1 put the code in ABSENT_CODES.
    res = EcrCollector().collect(Session(no_policy=True), "eu-west-1")
    repo = res.resources[0]
    assert repo.attrs["policy_allows_public"] is False
    assert repo.partial is False
    assert res.denials == 0


def test_absent_lifecycle_policy_is_recorded_as_false():
    res = EcrCollector().collect(Session(), "eu-west-1")
    assert res.resources[0].attrs["has_lifecycle_policy"] is False


def test_denied_policy_read_omits_the_flag_and_marks_partial():
    res = EcrCollector().collect(Session(deny_policy=True), "eu-west-1")
    repo = res.resources[0]
    assert repo.partial is True
    assert "policy_allows_public" not in repo.attrs
    # Configuration from the list call must survive an unrelated denial.
    assert repo.attrs["scan_on_push"] is False
    assert res.denials >= 1


def test_no_image_tags_are_collected():
    res = EcrCollector().collect(Session(), "eu-west-1")
    for r in res.resources:
        assert "images" not in r.attrs
        assert "tags" not in r.attrs


def test_region_is_carried_and_counted():
    res = EcrCollector().collect(Session(), "us-east-1")
    assert res.resources[0].region == "us-east-1"
    counts = {m.name: m.value for m in res.metrics}
    assert counts["ecr.repository_count"] == 1.0
