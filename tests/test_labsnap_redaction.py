import json
from datetime import datetime, timezone

from aws95.aws.engines.labsnap.collect import redact
from aws95.aws.engines.labsnap.collect.collectors.cognito import CognitoCollector
from aws95.aws.engines.labsnap.collect.collectors.ecs import EcsCollector
from aws95.aws.engines.labsnap.collect.collectors.lambda_ import LambdaCollector
from aws95.store import Store

SECRET = "s3cr3t-do-not-store"


class Pages:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kw):
        return iter(self._pages)


class OneClient:
    def __init__(self, client):
        self._client = client

    def client(self, name, **kw):
        return self._client


def _dump(result) -> str:
    return json.dumps([[r.rtype, r.region, r.rid, r.attrs] for r in result.resources]
                      + [[m.name, m.dims, str(m.value)] for m in result.metrics], default=str)


def _assert_never_stored(tmp_path, result):
    s = Store(tmp_path / "r.db")
    sid = s.begin_snapshot("123456789012", "arn:aws:iam::123456789012:user/a", "3.0.0", ["us-east-1"])
    s.write_resources(sid, "x", result.resources)
    s.write_metrics(sid, "x", result.metrics, datetime.now(timezone.utc))
    s.close()
    raw = b"".join(p.read_bytes() for p in tmp_path.glob("r.db*"))
    assert SECRET.encode() not in raw


class FakeLambda:
    def __init__(self):
        self.page = {"Functions": [{"FunctionName": "fn-a", "Runtime": "python3.12", "MemorySize": 128,
                                    "Timeout": 3, "Environment": {"Variables": {"DB_PASSWORD": SECRET}}}]}

    def get_paginator(self, op):
        assert op == "list_functions"
        return Pages([self.page])

    def list_provisioned_concurrency_configs(self, FunctionName):
        return {"ProvisionedConcurrencyConfigs": []}


def test_lambda_environment_is_removed_before_anything_reads_it(tmp_path):
    fake = FakeLambda()
    res = LambdaCollector().collect(OneClient(fake), "us-east-1")
    assert "Environment" not in fake.page["Functions"][0]
    assert SECRET not in _dump(res)
    _assert_never_stored(tmp_path, res)


class FakeCognito:
    def __init__(self):
        self.client_detail = {"UserPoolClient": {"ClientId": "c1", "ClientSecret": SECRET, "GenerateSecret": True}}

    def get_paginator(self, op):
        if op == "list_user_pools":
            return Pages([{"UserPools": [{"Id": "us-east-1_zzzzabc123"}]}])
        assert op == "list_user_pool_clients"
        return Pages([{"UserPoolClients": [{"ClientId": "c1"}]}])

    def describe_user_pool(self, UserPoolId):
        return {"UserPool": {}}

    def describe_user_pool_client(self, UserPoolId, ClientId):
        return self.client_detail


def test_cognito_client_secret_is_removed(tmp_path):
    fake = FakeCognito()
    res = CognitoCollector().collect(OneClient(fake), "us-east-1")
    assert "ClientSecret" not in fake.client_detail["UserPoolClient"]
    client = next(r for r in res.resources if r.rtype == "cognito.user_pool_client")
    assert client.attrs["has_client_secret"] is True
    assert SECRET not in _dump(res)
    _assert_never_stored(tmp_path, res)


class FakeEcs:
    def __init__(self):
        self.td = {"taskDefinition": {"family": "web", "revision": 7, "containerDefinitions": [{
            "name": "app",
            "environment": [{"name": "DB_PASSWORD", "value": SECRET}],
            "secrets": [{"name": "TOKEN",
                         "valueFrom": f"arn:aws:ssm:eu-west-1:123456789012:parameter/{SECRET}"}]}]}}

    def get_paginator(self, op):
        if op == "list_clusters":
            return Pages([{"clusterArns": ["arn:aws:ecs:eu-west-1:123456789012:cluster/prod"]}])
        assert op == "list_services"
        return Pages([{"serviceArns": ["arn:aws:ecs:eu-west-1:123456789012:service/prod/web"]}])

    def describe_clusters(self, clusters):
        return {"clusters": [{"clusterName": "prod"}]}

    def describe_services(self, cluster, services):
        return {"services": [{"serviceName": "web",
                              "taskDefinition": "arn:aws:ecs:eu-west-1:123456789012:task-definition/web:7"}]}

    def describe_task_definition(self, taskDefinition):
        return self.td


def test_ecs_environment_and_secret_values_are_removed(tmp_path):
    fake = FakeEcs()
    res = EcsCollector().collect(OneClient(fake), "eu-west-1")
    td = next(r for r in res.resources if r.rtype == "ecs.task_definition")
    assert td.attrs["plaintext_env_count"] == 1
    assert td.attrs["secret_sources"] == ["ssm"]
    assert SECRET not in json.dumps(fake.td)
    assert SECRET not in _dump(res)
    _assert_never_stored(tmp_path, res)


def test_redactors_pass_none_and_odd_shapes_through():
    assert redact.lambda_functions(None) is None
    assert redact.user_pool_client(None) is None
    assert redact.task_definition(None) is None
    assert redact.task_definition({"taskDefinition": {}}) == {"taskDefinition": {}}
    out = redact.task_definition({"taskDefinition": {"containerDefinitions": [
        {"secrets": [{"name": "X", "valueFrom": "plain-parameter-name"}]}]}})
    assert out["taskDefinition"]["containerDefinitions"][0]["secrets"] == [{"name": redact.REDACTED, "valueFrom": ""}]
