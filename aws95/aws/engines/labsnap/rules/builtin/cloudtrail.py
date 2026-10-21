# Upstream: labsnap/labsnap/rules/builtin/cloudtrail.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws.engines.labsnap.model import Finding
from aws95.aws.engines.labsnap.rules.base import register_rule


@register_rule
class NotLogging:
    id = "cloudtrail.not_logging"
    severity = "critical"
    title = "CloudTrail trail exists but is not logging"
    description = ("The trail is configured but currently stopped, so API "
                   "activity in its scope is not being recorded.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("cloudtrail.trail"):
            if "is_logging" not in r.attrs:
                continue
            if not r.attrs["is_logging"]:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity)
