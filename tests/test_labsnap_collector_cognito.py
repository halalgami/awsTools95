# Upstream: labsnap/tests/test_collector_cognito.py @ 1b1d437 — adapted for aws95; owned here from now on.
from botocore.exceptions import ClientError

from aws95.aws.engines.labsnap.collect.collectors.cognito import CognitoCollector


class FakeCognito:
    def __init__(self, deny_describe=False, deny_clients=False):
        self.deny_describe = deny_describe
        self.deny_clients = deny_clients

    def get_paginator(self, op):
        pages = {
            "list_user_pools": [{"UserPools": [
                {"Id": "eu-west-1_Ab3Cd9Xyz", "Name": "customers"},
                {"Id": "eu-west-1_Zz9Yy8Wwv", "Name": "staff"}]}],
        }
        deny_clients = self.deny_clients

        class P:
            def paginate(self_inner, **kw):
                if op == "list_user_pool_clients":
                    if deny_clients:
                        raise ClientError(
                            {"Error": {"Code": "AccessDeniedException"}},
                            "ListUserPoolClients")
                    return iter([{"UserPoolClients": [
                        {"ClientId": "client-1",
                         "UserPoolId": kw["UserPoolId"],
                         "ClientName": "web"}]}])
                return iter(pages[op])
        return P()

    def describe_user_pool(self, UserPoolId):
        if self.deny_describe:
            raise ClientError({"Error": {"Code": "AccessDeniedException"}},
                              "DescribeUserPool")
        pools = {
            "eu-west-1_Ab3Cd9Xyz": {
                "Id": "eu-west-1_Ab3Cd9Xyz", "Name": "customers",
                "MfaConfiguration": "OFF",
                "EstimatedNumberOfUsers": 4210,
                "Policies": {"PasswordPolicy": {
                    "MinimumLength": 8, "RequireUppercase": True,
                    "RequireLowercase": True, "RequireNumbers": True,
                    "RequireSymbols": False,
                    "TemporaryPasswordValidityDays": 7}},
                "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": False},
                "UserPoolAddOns": {"AdvancedSecurityMode": "OFF"}},
            "eu-west-1_Zz9Yy8Wwv": {
                "Id": "eu-west-1_Zz9Yy8Wwv", "Name": "staff",
                "MfaConfiguration": "ON",
                "EstimatedNumberOfUsers": 42,
                "Policies": {"PasswordPolicy": {
                    "MinimumLength": 14, "RequireUppercase": True,
                    "RequireLowercase": True, "RequireNumbers": True,
                    "RequireSymbols": True,
                    "TemporaryPasswordValidityDays": 1}},
                "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True}},
        }[UserPoolId]
        return {"UserPool": pools}

    def describe_user_pool_client(self, UserPoolId, ClientId):
        return {"UserPoolClient": {
            "ClientId": ClientId, "UserPoolId": UserPoolId,
            "ExplicitAuthFlows": ["ALLOW_USER_PASSWORD_AUTH",
                                  "ALLOW_REFRESH_TOKEN_AUTH"],
            "CallbackURLs": ["https://internal.example/callback",
                             "https://admin.example/cb"],
            "GenerateSecret": False,
            "PreventUserExistenceErrors": "ENABLED"}}


class Session:
    def __init__(self, **kw):
        self.idp = FakeCognito(**kw)

    def client(self, name, **kw):
        assert name == "cognito-idp"
        return self.idp


def test_pool_policy_and_mfa_are_recorded_against_the_pool_id():
    res = CognitoCollector().collect(Session(), "eu-west-1")
    pools = {r.rid: r.attrs for r in res.resources
             if r.rtype == "cognito.user_pool"}
    # The rid is the pool ID, not the name: a renamed pool must not read as
    # one deletion plus one creation.
    assert set(pools) == {"eu-west-1_Ab3Cd9Xyz", "eu-west-1_Zz9Yy8Wwv"}
    customers = pools["eu-west-1_Ab3Cd9Xyz"]
    assert customers["mfa_configuration"] == "OFF"
    assert customers["password_min_length"] == 8
    assert customers["password_requires_symbols"] is False
    assert customers["self_signup_allowed"] is True
    assert customers["advanced_security_mode"] == "OFF"
    assert customers["estimated_users"] == 4210
    assert pools["eu-west-1_Zz9Yy8Wwv"]["self_signup_allowed"] is False


def test_callback_urls_are_counted_never_stored():
    res = CognitoCollector().collect(Session(), "eu-west-1")
    client = [r for r in res.resources
              if r.rtype == "cognito.user_pool_client"][0]
    assert client.attrs["callback_url_count"] == 2
    assert "callback_urls" not in client.attrs
    assert not any("https://" in str(v) for v in client.attrs.values())


def test_client_auth_flows_are_recorded():
    res = CognitoCollector().collect(Session(), "eu-west-1")
    client = [r for r in res.resources
              if r.rtype == "cognito.user_pool_client"][0]
    assert client.attrs["explicit_auth_flows"] == ["ALLOW_USER_PASSWORD_AUTH",
                                                   "ALLOW_REFRESH_TOKEN_AUTH"]
    assert client.attrs["has_client_secret"] is False


def test_denied_describe_leaves_the_pool_partial_with_no_guessed_policy():
    res = CognitoCollector().collect(Session(deny_describe=True), "eu-west-1")
    pool = [r for r in res.resources if r.rtype == "cognito.user_pool"][0]
    assert pool.partial is True
    assert "mfa_configuration" not in pool.attrs
    assert "password_min_length" not in pool.attrs
    assert res.denials >= 1


def test_denied_client_listing_leaves_client_count_absent_not_zero():
    # Zero clients and "we could not list the clients" are different facts.
    res = CognitoCollector().collect(Session(deny_clients=True), "eu-west-1")
    pool = [r for r in res.resources if r.rtype == "cognito.user_pool"][0]
    assert pool.partial is True
    assert "client_count" not in pool.attrs
    # A denial at one level (clients) must not poison another level (the
    # pool's own describe_user_pool attributes), read cleanly here.
    assert pool.attrs["mfa_configuration"] == "OFF"


def test_region_is_carried_and_counted():
    res = CognitoCollector().collect(Session(), "us-east-1")
    assert all(r.region == "us-east-1" for r in res.resources)
    counts = {m.name: m.value for m in res.metrics}
    assert counts["cognito.user_pool_count"] == 2.0


class TwoClientFakeCognito:
    """One pool, two clients; describe_user_pool_client denies one of them.

    Exercises the third fan-out level (pool -> clients -> per-client
    detail): a denial on one client's detail call must be isolated to that
    one client resource, must not touch its sibling, and must not erase
    anything the pool-level calls already read cleanly.
    """

    POOL_ID = "eu-west-1_Ab3Cd9Xyz"

    def get_paginator(self, op):
        if op == "list_user_pools":
            pages = [{"UserPools": [{"Id": self.POOL_ID, "Name": "customers"}]}]
        elif op == "list_user_pool_clients":
            pages = [{"UserPoolClients": [
                {"ClientId": "client-1", "UserPoolId": self.POOL_ID,
                 "ClientName": "web"},
                {"ClientId": "client-2", "UserPoolId": self.POOL_ID,
                 "ClientName": "mobile"}]}]
        else:
            raise AssertionError(f"unexpected paginator op: {op}")

        class P:
            def paginate(self_inner, **kw):
                return iter(pages)
        return P()

    def describe_user_pool(self, UserPoolId):
        return {"UserPool": {
            "Id": UserPoolId, "Name": "customers",
            "MfaConfiguration": "OFF",
            "EstimatedNumberOfUsers": 4210,
            "Policies": {"PasswordPolicy": {
                "MinimumLength": 8, "RequireUppercase": True,
                "RequireLowercase": True, "RequireNumbers": True,
                "RequireSymbols": False,
                "TemporaryPasswordValidityDays": 7}},
            "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": False},
            "UserPoolAddOns": {"AdvancedSecurityMode": "OFF"}}}

    def describe_user_pool_client(self, UserPoolId, ClientId):
        if ClientId == "client-2":
            raise ClientError({"Error": {"Code": "AccessDeniedException"}},
                              "DescribeUserPoolClient")
        return {"UserPoolClient": {
            "ClientId": ClientId, "UserPoolId": UserPoolId,
            "ExplicitAuthFlows": ["ALLOW_USER_PASSWORD_AUTH"],
            "CallbackURLs": ["https://internal.example/callback"],
            "GenerateSecret": False,
            "PreventUserExistenceErrors": "ENABLED"}}


class TwoClientSession:
    def __init__(self):
        self.idp = TwoClientFakeCognito()

    def client(self, name, **kw):
        assert name == "cognito-idp"
        return self.idp


def test_one_denied_client_detail_does_not_poison_its_sibling_or_the_pool():
    res = CognitoCollector().collect(TwoClientSession(), "eu-west-1")
    clients = {r.rid: r for r in res.resources
               if r.rtype == "cognito.user_pool_client"}
    assert set(clients) == {"client-1", "client-2"}

    denied = clients["client-2"]
    assert denied.partial is True
    assert denied.attrs == {"pool_id": TwoClientFakeCognito.POOL_ID}

    sibling = clients["client-1"]
    assert sibling.partial is False
    assert sibling.attrs["explicit_auth_flows"] == ["ALLOW_USER_PASSWORD_AUTH"]
    assert sibling.attrs["callback_url_count"] == 1
    assert sibling.attrs["has_client_secret"] is False

    pool = [r for r in res.resources if r.rtype == "cognito.user_pool"][0]
    # The pool's own cleanly-read attributes and its client_count are
    # untouched by the denial down at the per-client level.
    assert pool.attrs["mfa_configuration"] == "OFF"
    assert pool.attrs["password_min_length"] == 8
    assert pool.attrs["client_count"] == 2
    assert res.denials == 1
