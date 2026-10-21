import json

from botocore.exceptions import ClientError

from aws95.aws.engines.labsnap.collect.collectors.apigateway import ApiGatewayCollector

SECRET = "sk-live-do-not-store"


class FakeRest:
    def __init__(self, deny_stages=False):
        self.deny_stages = deny_stages

    def get_paginator(self, op):
        class P:
            def paginate(self_inner, **kw):
                return iter([{"items": [{"id": "a1b2c3", "name": "orders",
                                         "endpointConfiguration": {"types": ["REGIONAL"]}}]}])
        return P()

    def get_stages(self, restApiId):
        if self.deny_stages:
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "GetStages")
        return {"item": [{"stageName": "prod", "tracingEnabled": True, "variables": {"apiKey": SECRET},
                          "webAclArn": "arn:aws:wafv2:eu-west-1:123456789012:regional/webacl/x/1"},
                         {"stageName": "dev", "accessLogSettings": {"destinationArn": "arn:aws:logs:x"},
                          "variables": {"token": SECRET}}]}


class FakeV2:
    def get_paginator(self, op):
        class P:
            def paginate(self_inner, **kw):
                if op == "get_apis":
                    return iter([{"Items": [{"ApiId": "h1", "Name": "chat", "ProtocolType": "WEBSOCKET"}]}])
                return iter([{"Items": [{"StageName": "$default", "StageVariables": {"k": SECRET}}]}])
        return P()


class FakeSession:
    def __init__(self, rest, v2):
        self.clients = {"apigateway": rest, "apigatewayv2": v2}

    def client(self, name, **kw):
        return self.clients[name]


def collect(**kw):
    return ApiGatewayCollector().collect(FakeSession(FakeRest(**kw), FakeV2()), "eu-west-1")


def test_apis_and_stages():
    res = collect()
    apis = {r.rid: r.attrs for r in res.resources if r.rtype == "apigateway.api"}
    stages = {r.rid: r.attrs for r in res.resources if r.rtype == "apigateway.stage"}
    assert apis == {"a1b2c3": {"name": "orders", "kind": "rest", "endpoint_type": "REGIONAL", "stage_count": 2},
                    "h1": {"name": "chat", "kind": "websocket", "stage_count": 1}}
    assert stages["a1b2c3/prod"] == {"api_id": "a1b2c3", "kind": "rest", "access_logging": False, "tracing": True,
                                     "waf": True}
    assert stages["a1b2c3/dev"]["access_logging"] is True and stages["h1/$default"]["access_logging"] is False


def test_stage_variables_are_never_stored():
    """Review Focus 1."""
    res = collect()
    assert SECRET not in json.dumps([r.attrs for r in res.resources])


def test_denied_stages_mark_the_api_partial():
    """Review Focus 2."""
    res = collect(deny_stages=True)
    api = {r.rid: r for r in res.resources}["a1b2c3"]
    assert api.partial and "stage_count" not in api.attrs and res.denials == 1
    assert not [r for r in res.resources if r.rid.startswith("a1b2c3/")]
