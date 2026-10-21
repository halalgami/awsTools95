# Upstream: labsnap/labsnap/collect/collectors/cloudtrail.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws import gate
from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.collect.probe import probe
from aws95.aws.engines.labsnap.model import CollectorResult, Resource


@register
class CloudTrailCollector:
    name = "cloudtrail"
    scope = "regional"
    timeout_s = 60
    # is_logging is deliberately NOT here: it genuinely changing (someone
    # stopped logging) is a real event a diff must surface, not churn to
    # suppress. Do not "fix" this by adding it.
    volatile_keys = frozenset()

    def collect(self, session, region, cancel=None):
        ct = gate.bind(session, "cloudtrail", region, cancel=cancel)
        resources, denials = [], 0

        # describe_trails is the top-level listing call - if denied, whole-
        # collector failure is the honest outcome. get_trail_status is a
        # per-trail follow-up, so it is guarded: a denial there degrades only
        # that trail's is_logging attr, not the trail's existence.
        #
        # includeShadowTrails=False is not optional: the default includes a
        # multi-region trail's shadow copy in every enabled region, so the
        # same trail would otherwise be enumerated (and later flagged by any
        # rule over cloudtrail.trail) once per region instead of once, in
        # its home region.
        for trail in ct.describe_trails(includeShadowTrails=False).get(
                "trailList", []):
            name = trail["Name"]
            attrs = {
                "is_multi_region": bool(trail.get("IsMultiRegionTrail")),
                "s3_bucket": trail.get("S3BucketName"),
            }
            status, denied = probe(ct.get_trail_status, Name=name)
            denials += denied
            # `denied is False` covers both success AND an ABSENT_CODES
            # error (e.g. the trail was deleted between describe_trails and
            # this call) - probe() returns None for both, so status must
            # also be checked before subscripting it. Same shape as the bug
            # that killed the iam collector on a mid-scan deletion.
            if not denied and status is not None:
                attrs["is_logging"] = bool(status["IsLogging"])
            resources.append(Resource("cloudtrail.trail", region, name, attrs,
                                      partial=denied))

        return CollectorResult(resources, [], [region], denials)
