# Upstream: labsnap/labsnap/rules/builtin/guardrails.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws.engines.labsnap.model import Finding
from aws95.aws.engines.labsnap.rules.base import register_rule


@register_rule
class Disabled:
    id = "guardrails.disabled"
    severity = "medium"
    title = "Security guardrail service is not enabled"
    description = ("GuardDuty, IAM Access Analyzer, or Security Hub is not "
                   "switched on in this region, leaving that layer of detective "
                   "or preventive control absent.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("guardrails.service"):
            if "enabled" not in r.attrs:
                continue
            if not r.attrs["enabled"]:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"service": r.rid})
