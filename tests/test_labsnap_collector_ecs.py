# Upstream: labsnap/tests/test_collector_ecs.py @ 1b1d437 — adapted for aws95; owned here from now on.
from botocore.exceptions import ClientError

from aws95.aws.engines.labsnap.collect.collectors.ecs import EcsCollector, _secret_sources


def test_secret_sources_records_the_store_not_the_arn():
    containers = [{"secrets": [
        {"name": "DB_PASSWORD",
         "valueFrom": "arn:aws:secretsmanager:eu-west-1:123456789012:"
                      "secret:app/db-AbCdEf"},
        {"name": "API_KEY",
         "valueFrom": "arn:aws:ssm:eu-west-1:123456789012:parameter/api-key"}]}]
    assert _secret_sources(containers) == ["secretsmanager", "ssm"]


def test_secret_sources_is_empty_when_no_container_reads_a_secret():
    assert _secret_sources([{"name": "web"}]) == []


class FakeEcs:
    def __init__(self, deny_task_def=False):
        self.deny_task_def = deny_task_def
        self.describe_services_batches = []

    def get_paginator(self, op):
        pages = {
            "list_clusters": [{"clusterArns": [
                "arn:aws:ecs:eu-west-1:123456789012:cluster/prod"]}],
            "list_services": [{"serviceArns": [
                "arn:aws:ecs:eu-west-1:123456789012:service/prod/web"]}],
        }

        class P:
            def paginate(self_inner, **kw):
                return iter(pages[op])
        return P()

    def describe_clusters(self, clusters):
        return {"clusters": [{
            "clusterArn": clusters[0], "clusterName": "prod", "status": "ACTIVE",
            "activeServicesCount": 1, "runningTasksCount": 3,
            "registeredContainerInstancesCount": 0}]}

    def describe_services(self, cluster, services):
        self.describe_services_batches.append(len(services))
        return {"services": [{
            "serviceArn": services[0], "serviceName": "web", "status": "ACTIVE",
            "launchType": "FARGATE", "desiredCount": 3, "runningCount": 3,
            "taskDefinition": "arn:aws:ecs:eu-west-1:123456789012:"
                              "task-definition/web:7",
            "networkConfiguration": {"awsvpcConfiguration": {
                "subnets": ["subnet-1", "subnet-2"],
                "securityGroups": ["sg-1"],
                "assignPublicIp": "ENABLED"}}}]}

    def describe_task_definition(self, taskDefinition):
        if self.deny_task_def:
            raise ClientError({"Error": {"Code": "AccessDeniedException"}},
                              "DescribeTaskDefinition")
        return {"taskDefinition": {
            "taskDefinitionArn": taskDefinition,
            "family": "web", "revision": 7, "networkMode": "awsvpc",
            "cpu": "512", "memory": "1024",
            "containerDefinitions": [
                {"name": "app", "privileged": True,
                 "readonlyRootFilesystem": False,
                 "environment": [{"name": "DB_PASSWORD", "value": "hunter2"},
                                 {"name": "STAGE", "value": "prod"}],
                 "secrets": [{"name": "TOKEN",
                              "valueFrom": "arn:aws:ssm:eu-west-1:"
                                           "123456789012:parameter/token"}]},
                {"name": "sidecar", "readonlyRootFilesystem": True,
                 "environment": []}]}}


class Session:
    def __init__(self, **kw):
        self.ecs = FakeEcs(**kw)

    def client(self, name, **kw):
        assert name == "ecs"
        return self.ecs


def test_cluster_and_service_configuration_are_recorded():
    res = EcsCollector().collect(Session(), "eu-west-1")
    cluster = [r for r in res.resources if r.rtype == "ecs.cluster"][0]
    assert cluster.rid == "prod"
    assert cluster.attrs["active_service_count"] == 1

    service = [r for r in res.resources if r.rtype == "ecs.service"][0]
    assert service.rid == "prod/web"
    assert service.attrs["launch_type"] == "FARGATE"
    assert service.attrs["assign_public_ip"] == "ENABLED"
    assert service.attrs["subnet_count"] == 2
    assert service.attrs["security_groups"] == ["sg-1"]


def test_task_definition_flags_are_derived_across_all_containers():
    res = EcsCollector().collect(Session(), "eu-west-1")
    td = [r for r in res.resources if r.rtype == "ecs.task_definition"][0]
    assert td.rid == "web:7"
    assert td.attrs["container_count"] == 2
    # any_privileged is true if ANY container is privileged.
    assert td.attrs["any_privileged"] is True
    # all_readonly_root is true only if EVERY container is.
    assert td.attrs["all_readonly_root"] is False
    assert td.attrs["plaintext_env_count"] == 2
    assert td.attrs["secret_sources"] == ["ssm"]


def test_environment_variable_values_are_never_stored():
    res = EcsCollector().collect(Session(), "eu-west-1")
    for r in res.resources:
        blob = str(r.attrs)
        assert "hunter2" not in blob
        assert "DB_PASSWORD" not in blob
        assert "environment" not in r.attrs
        # The secret reference itself is never stored either - only the
        # store it points at (see _secret_sources). A raw ARN nested
        # anywhere in attrs would still show up in the stringified blob.
        assert "arn:aws:ssm:eu-west-1:123456789012:parameter/token" not in blob


def test_services_are_described_in_batches_of_at_most_ten():
    # A fake with exactly one service ARN would make this assertion pass
    # even if _chunks were deleted and describe_services called once with
    # the whole list - the limit is only exercised past ten. Twelve service
    # ARNs force at least two batches.
    service_arns = [
        f"arn:aws:ecs:eu-west-1:123456789012:service/prod/svc-{i}"
        for i in range(12)]
    task_def_arn = ("arn:aws:ecs:eu-west-1:123456789012:"
                    "task-definition/web:7")

    class ManyServicesEcs:
        def __init__(self):
            self.describe_services_batches = []

        def get_paginator(self, op):
            pages = {
                "list_clusters": [{"clusterArns": [
                    "arn:aws:ecs:eu-west-1:123456789012:cluster/prod"]}],
                "list_services": [{"serviceArns": service_arns}],
            }

            class P:
                def paginate(self_inner, **kw):
                    return iter(pages[op])
            return P()

        def describe_clusters(self, clusters):
            return {"clusters": [{
                "clusterArn": clusters[0], "clusterName": "prod",
                "status": "ACTIVE", "activeServicesCount": len(service_arns),
                "runningTasksCount": len(service_arns),
                "registeredContainerInstancesCount": 0}]}

        def describe_services(self, cluster, services):
            self.describe_services_batches.append(len(services))
            return {"services": [{
                "serviceArn": arn, "serviceName": arn.rsplit("/", 1)[-1],
                "status": "ACTIVE", "launchType": "FARGATE",
                "desiredCount": 1, "runningCount": 1,
                "taskDefinition": task_def_arn,
            } for arn in services]}

        def describe_task_definition(self, taskDefinition):
            return {"taskDefinition": {
                "taskDefinitionArn": taskDefinition,
                "family": "web", "revision": 7, "networkMode": "awsvpc",
                "cpu": "256", "memory": "512", "containerDefinitions": []}}

    class ManyServicesSession:
        def __init__(self):
            self.ecs = ManyServicesEcs()

        def client(self, name, **kw):
            assert name == "ecs"
            return self.ecs

    s = ManyServicesSession()
    res = EcsCollector().collect(s, "eu-west-1")
    batches = s.ecs.describe_services_batches
    assert batches
    assert all(n <= 10 for n in batches)
    # More than one batch proves chunking actually happened, not just that
    # the sole batch stayed under the limit.
    assert len(batches) > 1
    # Catches a chunking bug that drops the remainder rather than one that
    # merely oversizes a call.
    assert sum(batches) == len(service_arns)
    services = [r for r in res.resources if r.rtype == "ecs.service"]
    assert len(services) == len(service_arns)


def test_shared_task_definition_is_described_only_once():
    # Only the revision each service actually references is described, and a
    # definition already fetched for one service in the same run is not
    # re-described for another that references the same revision - nor does
    # the second service get marked partial because of it.
    task_def_arn = ("arn:aws:ecs:eu-west-1:123456789012:"
                    "task-definition/web:7")
    service_arns = [
        "arn:aws:ecs:eu-west-1:123456789012:service/prod/web",
        "arn:aws:ecs:eu-west-1:123456789012:service/prod/web-canary"]

    class SharedTaskDefEcs:
        def __init__(self):
            self.describe_task_definition_calls = 0

        def get_paginator(self, op):
            pages = {
                "list_clusters": [{"clusterArns": [
                    "arn:aws:ecs:eu-west-1:123456789012:cluster/prod"]}],
                "list_services": [{"serviceArns": service_arns}],
            }

            class P:
                def paginate(self_inner, **kw):
                    return iter(pages[op])
            return P()

        def describe_clusters(self, clusters):
            return {"clusters": [{
                "clusterArn": clusters[0], "clusterName": "prod",
                "status": "ACTIVE", "activeServicesCount": 2,
                "runningTasksCount": 2,
                "registeredContainerInstancesCount": 0}]}

        def describe_services(self, cluster, services):
            return {"services": [{
                "serviceArn": arn, "serviceName": arn.rsplit("/", 1)[-1],
                "status": "ACTIVE", "launchType": "FARGATE",
                "desiredCount": 1, "runningCount": 1,
                "taskDefinition": task_def_arn,
            } for arn in services]}

        def describe_task_definition(self, taskDefinition):
            self.describe_task_definition_calls += 1
            return {"taskDefinition": {
                "taskDefinitionArn": taskDefinition,
                "family": "web", "revision": 7, "networkMode": "awsvpc",
                "cpu": "256", "memory": "512", "containerDefinitions": []}}

    class SharedTaskDefSession:
        def __init__(self):
            self.ecs = SharedTaskDefEcs()

        def client(self, name, **kw):
            assert name == "ecs"
            return self.ecs

    s = SharedTaskDefSession()
    res = EcsCollector().collect(s, "eu-west-1")
    assert s.ecs.describe_task_definition_calls == 1
    task_defs = [r for r in res.resources if r.rtype == "ecs.task_definition"]
    assert len(task_defs) == 1
    services = [r for r in res.resources if r.rtype == "ecs.service"]
    assert len(services) == 2
    assert all(svc.partial is False for svc in services)


def test_denied_task_definition_read_emits_partial_placeholder_not_omission():
    # Emitting a partial placeholder (rather than nothing) for a denied
    # describe_task_definition is what stops the next diff from reporting a
    # deletion that never happened: the identity ("web:7") is still known
    # from the service's taskDefinition ARN even though the read itself was
    # denied, so the resource can still be carried forward with empty attrs
    # and partial=True instead of silently vanishing.
    res = EcsCollector().collect(Session(deny_task_def=True), "eu-west-1")
    task_defs = [r for r in res.resources if r.rtype == "ecs.task_definition"]
    assert len(task_defs) == 1
    assert task_defs[0].rid == "web:7"
    assert task_defs[0].attrs == {}
    assert task_defs[0].partial is True
    service = [r for r in res.resources if r.rtype == "ecs.service"][0]
    assert service.partial is True
    assert res.denials >= 1


def test_denied_shared_task_definition_marks_every_service_partial():
    # Regression: seen_task_defs (which stops the placeholder from being
    # duplicated) must not be confused with "this service's own read was
    # denied". Only the FIRST service sharing a denied ARN actually triggers
    # the probe; a naive fix that set td_partial only inside the
    # `if td_arn not in seen_task_defs` block left every LATER service
    # sharing that same denied ARN reading back as partial=False, even
    # though its task definition data is exactly as unknown as the first
    # service's. Both services must be partial.
    task_def_arn = ("arn:aws:ecs:eu-west-1:123456789012:"
                    "task-definition/web:7")
    service_arns = [
        "arn:aws:ecs:eu-west-1:123456789012:service/prod/web1",
        "arn:aws:ecs:eu-west-1:123456789012:service/prod/web2"]

    class DeniedSharedTaskDefEcs:
        def __init__(self):
            self.describe_task_definition_calls = 0

        def get_paginator(self, op):
            pages = {
                "list_clusters": [{"clusterArns": [
                    "arn:aws:ecs:eu-west-1:123456789012:cluster/prod"]}],
                "list_services": [{"serviceArns": service_arns}],
            }

            class P:
                def paginate(self_inner, **kw):
                    return iter(pages[op])
            return P()

        def describe_clusters(self, clusters):
            return {"clusters": [{
                "clusterArn": clusters[0], "clusterName": "prod",
                "status": "ACTIVE", "activeServicesCount": 2,
                "runningTasksCount": 2,
                "registeredContainerInstancesCount": 0}]}

        def describe_services(self, cluster, services):
            return {"services": [{
                "serviceArn": arn, "serviceName": arn.rsplit("/", 1)[-1],
                "status": "ACTIVE", "launchType": "FARGATE",
                "desiredCount": 1, "runningCount": 1,
                "taskDefinition": task_def_arn,
            } for arn in services]}

        def describe_task_definition(self, taskDefinition):
            self.describe_task_definition_calls += 1
            raise ClientError({"Error": {"Code": "AccessDeniedException"}},
                              "DescribeTaskDefinition")

    class DeniedSharedTaskDefSession:
        def __init__(self):
            self.ecs = DeniedSharedTaskDefEcs()

        def client(self, name, **kw):
            assert name == "ecs"
            return self.ecs

    s = DeniedSharedTaskDefSession()
    res = EcsCollector().collect(s, "eu-west-1")
    # Exactly one probe attempt, and exactly one placeholder - the denial
    # guard against duplication (I3/C1) must still hold.
    assert s.ecs.describe_task_definition_calls == 1
    task_defs = [r for r in res.resources if r.rtype == "ecs.task_definition"]
    assert len(task_defs) == 1
    assert task_defs[0].attrs == {}
    assert task_defs[0].partial is True
    services = [r for r in res.resources if r.rtype == "ecs.service"]
    assert len(services) == 2
    assert all(svc.partial is True for svc in services), \
        [(svc.rid, svc.partial) for svc in services]


def test_region_is_carried_and_counted():
    res = EcsCollector().collect(Session(), "us-east-1")
    assert all(r.region == "us-east-1" for r in res.resources)
    counts = {m.name: m.value for m in res.metrics}
    assert counts["ecs.cluster_count"] == 1.0
    assert counts["ecs.service_count"] == 1.0


def test_service_task_definition_attribute_keeps_family_and_revision():
    # Task-definition ARNs end ".../task-definition/web:7" - the revision is
    # colon-separated, so a helper that splits on ":" (like the one used for
    # cluster/service ARNs) would return just "7". The service's
    # task_definition attribute must keep "web:7" together so it is joinable
    # to the ecs.task_definition resource this collector emits.
    res = EcsCollector().collect(Session(), "eu-west-1")
    service = [r for r in res.resources if r.rtype == "ecs.service"][0]
    assert service.attrs["task_definition"] == "web:7"
