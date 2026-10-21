# Upstream: labsnap/labsnap/rules/builtin/ecr.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws.engines.labsnap.model import Finding
from aws95.aws.engines.labsnap.rules.base import register_rule


@register_rule
class PublicPolicy:
    id = "ecr.public_policy"
    severity = "high"
    title = "Container repository is readable by any principal"
    description = ("The repository policy grants to a wildcard principal, so "
                   "anyone can pull the images. Image layers routinely carry "
                   "configuration, internal hostnames, and sometimes "
                   "credentials baked in at build time.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("ecr.repository"):
            if "policy_allows_public" not in r.attrs:
                continue
            if r.attrs["policy_allows_public"]:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity)


@register_rule
class ScanOnPushOff:
    id = "ecr.scan_on_push_off"
    severity = "medium"
    title = "Repository does not scan images on push"
    description = ("Images are stored without a vulnerability scan, so a known-"
                   "vulnerable base layer reaches deployment unremarked.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("ecr.repository"):
            if "scan_on_push" not in r.attrs:
                continue
            if not r.attrs["scan_on_push"]:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity)


@register_rule
class MutableTags:
    id = "ecr.mutable_tags"
    severity = "low"
    title = "Repository allows image tags to be overwritten"
    description = ("A tag can be repointed at a different image, so the "
                   "artifact a deployment refers to is not the artifact that "
                   "was tested. Immutable tags make a deployment reproducible.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("ecr.repository"):
            if "tag_mutability" not in r.attrs:
                continue
            if r.attrs["tag_mutability"] == "MUTABLE":
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity)
