# Upstream: labsnap/labsnap/collect/collectors/guardrails.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws import gate
from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.collect.probe import DENIED_CODES, error_code
from aws95.aws.engines.labsnap.model import CollectorResult, Resource

# "Not subscribed" is a finding, never an error: "never turned on" and "not allowed to ask" are different facts.
_NOT_ENABLED = {"InvalidAccessException", "SubscriptionRequiredException", "ResourceNotFoundException",
                "BadRequestException"}


def _check(fn, truthy):
    """Return (enabled, denied). enabled is None when denied: never a fabricated False. Unknown codes re-raise."""
    try:
        return bool(truthy(fn())), False
    except Exception as exc:  # noqa: BLE001 - classified below; anything unrecognised is re-raised
        code = error_code(exc)
        if code in _NOT_ENABLED:
            return False, False
        if code in DENIED_CODES:
            return None, True
        raise


@register
class GuardrailsCollector:
    name = "guardrails"
    scope = "regional"
    timeout_s = 60
    volatile_keys = frozenset()

    def collect(self, session, region, cancel=None):
        checks = {
            "guardduty": (gate.bind(session, "guardduty", region, cancel=cancel).list_detectors,
                          lambda r: r.get("DetectorIds")),
            "access_analyzer": (gate.bind(session, "accessanalyzer", region, cancel=cancel).list_analyzers,
                                lambda r: r.get("analyzers")),
            "securityhub": (gate.bind(session, "securityhub", region, cancel=cancel).describe_hub,
                            lambda r: bool(r)),
        }
        resources, denials = [], 0
        # One denied service never loses the other two.
        for name, (fn, truthy) in checks.items():
            enabled, denied = _check(fn, truthy)
            attrs = {} if denied else {"enabled": enabled}
            denials += denied
            resources.append(Resource("guardrails.service", region, name, attrs, partial=denied))
        return CollectorResult(resources, [], [region], denials)
