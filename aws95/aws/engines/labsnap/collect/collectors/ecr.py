# Upstream: labsnap/labsnap/collect/collectors/ecr.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws import gate
from decimal import Decimal
import json

from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.collect.probe import probe
from aws95.aws.engines.labsnap.model import CollectorResult, Metric, Resource


def _policy_allows_public(policy_text):
    """True when the repository policy grants to any principal.

    The document itself is never stored - only this boolean. A public
    container repository leaks whatever the images contain, which routinely
    includes configuration, internal hostnames and occasionally credentials
    baked into a layer.
    """
    doc = json.loads(policy_text)
    statements = doc.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    for stmt in statements:
        if stmt.get("Effect") != "Allow":
            continue
        principal = stmt.get("Principal")
        if principal == "*":
            return True
        if isinstance(principal, dict):
            aws = principal.get("AWS")
            values = [aws] if isinstance(aws, str) else list(aws or [])
            if "*" in values:
                return True
    return False


@register
class EcrCollector:
    name = "ecr"
    scope = "regional"
    timeout_s = 180
    volatile_keys = frozenset()

    def collect(self, session, region, cancel=None):
        # NOTE: ECR returns lowerCamelCase keys ("repositories",
        # "repositoryName", "imageScanningConfiguration"), unlike every other
        # collector in this package, which sees PascalCase. Do not "correct"
        # these to PascalCase - they are what the API sends.
        ecr = gate.bind(session, "ecr", region, cancel=cancel)
        resources, denials = [], 0
        count = 0
        for page in ecr.pages("describe_repositories"):
            for repo in page["repositories"]:
                count += 1
                name = repo["repositoryName"]
                attrs = {
                    "scan_on_push": bool(
                        repo.get("imageScanningConfiguration", {}).get("scanOnPush")),
                    "tag_mutability": repo.get("imageTagMutability"),
                    "encryption_type": repo.get(
                        "encryptionConfiguration", {}).get("encryptionType"),
                }
                # Image tags and digests are deliberately not collected: not
                # configuration, not cost, and they change on every push.

                # RepositoryPolicyNotFoundException and
                # LifecyclePolicyNotFoundException are in ABSENT_CODES, so
                # probe() returns (None, False) for a repository that simply
                # has none. "Not configured" is an answer; only a denial is a
                # gap.
                policy, unreadable = probe(ecr.get_repository_policy,
                                           repositoryName=name)
                denials += unreadable
                partial = unreadable
                if not unreadable:
                    attrs["policy_allows_public"] = (
                        _policy_allows_public(policy["policyText"])
                        if policy else False)

                lifecycle, unreadable_lc = probe(ecr.get_lifecycle_policy,
                                                 repositoryName=name)
                denials += unreadable_lc
                partial = partial or unreadable_lc
                if not unreadable_lc:
                    attrs["has_lifecycle_policy"] = lifecycle is not None

                resources.append(Resource("ecr.repository", region, name,
                                          attrs, partial))

        metrics = [Metric("ecr.repository_count", Decimal(count),
                          dims={"region": region})]
        return CollectorResult(resources, metrics, [region], denials)
