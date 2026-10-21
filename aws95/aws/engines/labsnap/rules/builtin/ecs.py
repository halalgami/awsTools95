# Upstream: labsnap/labsnap/rules/builtin/ecs.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws.engines.labsnap.model import Finding
from aws95.aws.engines.labsnap.rules.base import register_rule


@register_rule
class PrivilegedTask:
    id = "ecs.privileged_task"
    severity = "high"
    title = "Task definition runs a privileged container"
    description = ("At least one container runs with elevated host "
                   "privileges, so a compromise inside it is a compromise of "
                   "the host it lands on rather than of one container.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("ecs.task_definition"):
            if "any_privileged" not in r.attrs:
                continue
            if r.attrs["any_privileged"]:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"family": r.attrs.get("family")})


@register_rule
class PublicIpAssigned:
    id = "ecs.public_ip_assigned"
    severity = "medium"
    title = "Service gives its tasks public IP addresses"
    description = ("Tasks are addressable from the internet directly, rather "
                   "than only through a load balancer, so the container's own "
                   "ports are the exposed surface.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("ecs.service"):
            if "assign_public_ip" not in r.attrs:
                continue
            if r.attrs["assign_public_ip"] == "ENABLED":
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"security_groups": r.attrs.get("security_groups", [])})


@register_rule
class PlaintextEnvVars:
    id = "ecs.plaintext_env_vars"
    severity = "low"
    title = "Task definition carries plaintext environment variables"
    description = ("Values set directly in the task definition are visible to "
                   "anyone who can describe it and are recorded in every "
                   "revision forever. Secrets belong in a secret store, "
                   "referenced by ARN. Only the count is reported - this tool "
                   "never reads the values.")
    params: dict = {"max_plain_env": 0}

    def evaluate(self, snap, params):
        limit = params.get("max_plain_env", 0)
        for r in snap.resources("ecs.task_definition"):
            if "plaintext_env_count" not in r.attrs:
                continue
            count = r.attrs["plaintext_env_count"]
            if count > limit:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"plaintext_env_count": count,
                               "secret_sources": r.attrs.get("secret_sources", [])})
