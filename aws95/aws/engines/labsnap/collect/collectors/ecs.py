# Upstream: labsnap/labsnap/collect/collectors/ecs.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws import gate
from decimal import Decimal
from aws95.aws.engines.labsnap.collect import redact
from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.collect.probe import probe
from aws95.aws.engines.labsnap.model import CollectorResult, Metric, Resource

# describe_services accepts at most 10 service ARNs per call. A single call
# with more silently returns only the first ten, which would read as services
# that do not exist.
_DESCRIBE_SERVICES_BATCH = 10


def _secret_sources(containers):
    """Which secret stores the containers read from, by ARN service prefix.

    The ARN itself is never stored: it names a specific secret or parameter,
    and that name routinely encodes the system it belongs to. The store is
    enough to answer "does this task pull secrets, and from where".
    """
    sources = set()
    for c in containers:
        for secret in c.get("secrets") or []:
            arn = secret.get("valueFrom", "")
            parts = arn.split(":")
            if len(parts) > 2 and parts[0] == "arn":
                sources.add(parts[2])
    return sorted(sources)


def _name_from_arn(arn):
    """The trailing name of an ECS ARN.

    A cluster ARN ends '.../cluster/prod'; a service ARN ends
    '.../service/prod/web'. Everything after the resource type is the
    identifier, and for a service that includes the cluster - which is what
    makes it unique within a region.

    Not used for task-definition ARNs - see _task_def_name_from_arn below.
    """
    tail = arn.split(":")[-1]
    return tail.split("/", 1)[1] if "/" in tail else tail


def _task_def_name_from_arn(arn):
    """The family:revision tail of a task-definition ARN.

    Task-definition ARNs end `.../task-definition/web:7` - the revision is
    colon-separated, so the general `_name_from_arn` (which splits on ":")
    would return just "7". Splitting on "/" keeps family and revision
    together, which is what makes the value joinable to the
    ecs.task_definition resource this collector emits.
    """
    return arn.split("/")[-1]


@register
class EcsCollector:
    name = "ecs"
    scope = "regional"
    timeout_s = 300
    volatile_keys = frozenset({"running_count", "running_task_count"})

    def collect(self, session, region, cancel=None):
        # NOTE: ECS returns lowerCamelCase keys, like ECR and unlike the rest
        # of this package.
        ecs = gate.bind(session, "ecs", region, cancel=cancel)
        resources, denials = [], 0
        cluster_count = service_count = 0
        # seen_task_defs: ARNs already resolved one way or the other (either
        # successfully described, or denied and placeholder-emitted) - this
        # is what stops a shared definition being probed, and its
        # placeholder emitted, more than once.
        seen_task_defs = set()
        # denied_task_defs: the subset of seen_task_defs that were denied.
        # Tracked separately so that the SECOND (and later) service sharing
        # a denied ARN is still marked partial, even though seen_task_defs
        # already contains the ARN and skips the probe/placeholder entirely
        # for it.
        denied_task_defs = set()

        cluster_arns = [arn
                        for page in ecs.pages("list_clusters")
                        for arn in page["clusterArns"]]
        # describe_clusters actually allows up to 100 per call, not 10 - but
        # reusing _DESCRIBE_SERVICES_BATCH here is simply conservative, and
        # one constant is clearer than two.
        for batch in _chunks(cluster_arns, _DESCRIBE_SERVICES_BATCH):
            detail, unreadable = probe(ecs.describe_clusters, clusters=batch)
            denials += unreadable
            if unreadable:
                for arn in batch:
                    cluster_count += 1
                    resources.append(Resource("ecs.cluster", region,
                                              _name_from_arn(arn), {}, True))
                continue
            for cl in (detail or {}).get("clusters", []):
                cluster_count += 1
                resources.append(Resource("ecs.cluster", region,
                                          cl["clusterName"], {
                                              "status": cl.get("status"),
                                              "active_service_count":
                                                  cl.get("activeServicesCount"),
                                              "running_task_count":
                                                  cl.get("runningTasksCount"),
                                              "registered_instance_count":
                                                  cl.get(
                                                      "registeredContainerInstancesCount"),
                                          }))

        for cluster_arn in cluster_arns:
            service_arns = [
                arn
                for page in ecs.pages("list_services", 
                    cluster=cluster_arn)
                for arn in page["serviceArns"]]
            for batch in _chunks(service_arns, _DESCRIBE_SERVICES_BATCH):
                detail, unreadable = probe(ecs.describe_services,
                                           cluster=cluster_arn, services=batch)
                denials += unreadable
                if unreadable:
                    for arn in batch:
                        service_count += 1
                        resources.append(Resource("ecs.service", region,
                                                  _name_from_arn(arn), {}, True))
                    continue
                for svc in (detail or {}).get("services", []):
                    service_count += 1
                    awsvpc = ((svc.get("networkConfiguration") or {})
                              .get("awsvpcConfiguration") or {})
                    attrs = {
                        "cluster": _name_from_arn(cluster_arn),
                        "status": svc.get("status"),
                        "launch_type": svc.get("launchType"),
                        "desired_count": svc.get("desiredCount"),
                        "running_count": svc.get("runningCount"),
                        "assign_public_ip": awsvpc.get("assignPublicIp"),
                        "subnet_count": len(awsvpc.get("subnets") or []),
                        "security_groups": list(awsvpc.get("securityGroups") or []),
                        "task_definition":
                            _task_def_name_from_arn(svc["taskDefinition"]),
                    }
                    # Only the revision this service actually runs is
                    # described. A long-lived account holds thousands of
                    # historical revisions and none of them are deployed.
                    td_arn = svc["taskDefinition"]
                    if td_arn not in seen_task_defs:
                        td, unreadable_td = probe(ecs.describe_task_definition,
                                                  taskDefinition=td_arn)
                        redact.task_definition(td)       # spec §3.2: environment and secret values never reach anything else
                        denials += unreadable_td
                        seen_task_defs.add(td_arn)
                        if not unreadable_td:
                            resources.append(
                                _task_definition_resource(region, td))
                        else:
                            # A denied read must not simply vanish: the task
                            # definition still exists, so omitting it here
                            # would make the next diff report it as a
                            # deletion that never happened (see
                            # describe_clusters / describe_services above for
                            # the same pattern). Emit a partial placeholder
                            # instead, guarded by seen_task_defs so two
                            # services sharing one definition do not each
                            # emit their own copy.
                            denied_task_defs.add(td_arn)
                            resources.append(
                                Resource("ecs.task_definition", region,
                                        _task_def_name_from_arn(td_arn),
                                        {}, True))
                    # Whether THIS service triggered the probe or not, it is
                    # partial if its task definition is a denied one - a
                    # second service sharing an already-resolved, denied ARN
                    # must not read back as fully known just because its own
                    # pass through this loop skipped the probe.
                    td_partial = td_arn in denied_task_defs
                    resources.append(Resource("ecs.service", region,
                                              f"{attrs['cluster']}/"
                                              f"{svc['serviceName']}",
                                              attrs, td_partial))

        metrics = [
            Metric("ecs.cluster_count", Decimal(cluster_count),
                   dims={"region": region}),
            Metric("ecs.service_count", Decimal(service_count),
                   dims={"region": region})]
        return CollectorResult(resources, metrics, [region], denials)


def _task_definition_resource(region, detail):
    td = (detail or {}).get("taskDefinition", {})
    containers = td.get("containerDefinitions") or []
    return Resource("ecs.task_definition", region,
                    f"{td.get('family')}:{td.get('revision')}", {
                        "family": td.get("family"),
                        "revision": td.get("revision"),
                        "network_mode": td.get("networkMode"),
                        "cpu": td.get("cpu"),
                        "memory": td.get("memory"),
                        "container_count": len(containers),
                        # ANY container privileged is a finding for the task.
                        "any_privileged": any(bool(c.get("privileged"))
                                              for c in containers),
                        # ALL containers must be read-only for the task to be.
                        "all_readonly_root": bool(containers) and all(
                            bool(c.get("readonlyRootFilesystem"))
                            for c in containers),
                        # COUNT only. Environment variable values routinely
                        # carry credentials; neither the values nor the names
                        # are stored.
                        "plaintext_env_count": sum(
                            len(c.get("environment") or []) for c in containers),
                        "secret_sources": _secret_sources(containers),
                    })


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]
