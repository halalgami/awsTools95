"""API Gateway rules (edge-dns spec §4)."""
from aws95.aws.engines.labsnap.model import Finding
from aws95.aws.engines.labsnap.rules.base import register_rule


@register_rule
class StageNoAccessLogging:
    id = "apigateway.stage_no_access_logging"
    severity = "medium"
    title = "API Gateway stage has no access logging"
    description = "Requests to this stage aren't written to an access log, so callers can't be traced afterwards."
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("apigateway.stage"):
            if r.attrs.get("access_logging") is False:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity)
