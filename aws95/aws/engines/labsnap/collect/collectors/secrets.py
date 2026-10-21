# Upstream: labsnap/labsnap/collect/collectors/secrets.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws import gate
from decimal import Decimal
from datetime import UTC, datetime

from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.model import CollectorResult, Metric, Resource


def _days_since(value, now):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return (now - value).days


@register
class SecretsCollector:
    name = "secrets"
    scope = "regional"
    timeout_s = 120
    # Recomputed against "now" on every run, so it drifts by a day at a time
    # on its own. Diffing it would report every secret as changed daily.
    volatile_keys = frozenset({"days_since_accessed", "last_accessed_date"})

    def collect(self, session, region, cancel=None):
        # list_secrets returns rotation state, LastAccessedDate,
        # LastRotatedDate, KmsKeyId and tags - everything both rules need - so
        # no per-secret follow-up call is made.
        #
        # The secret-value read API is NEVER called. ctwatch scores that call
        # as a level-1 security event, and a scheduled scan that trips its
        # sibling's alarms is a scan people switch off. The permission is
        # absent from docs/iam-policy.json for the same reason.
        sm = gate.bind(session, "secretsmanager", region, cancel=cancel)
        now = datetime.now(UTC)
        resources = []
        for page in sm.pages("list_secrets"):
            for s in page["SecretList"]:
                last_accessed = s.get("LastAccessedDate")
                last_rotated = s.get("LastRotatedDate")
                resources.append(Resource("secrets.secret", region, s["Name"], {
                    "rotation_enabled": bool(s.get("RotationEnabled")),
                    "rotation_days": (s.get("RotationRules") or {}).get(
                        "AutomaticallyAfterDays"),
                    # None means "never accessed" - a real, actionable state,
                    # not a failure to read. A failure to read this call is a
                    # whole-collector failure, handled by the runner.
                    "last_accessed_date": (last_accessed.isoformat()
                                           if last_accessed else None),
                    "last_rotated_date": (last_rotated.isoformat()
                                          if last_rotated else None),
                    "days_since_accessed": _days_since(last_accessed, now),
                    "has_custom_kms_key": bool(s.get("KmsKeyId")),
                    "tag_count": len(s.get("Tags") or []),
                }))
        metrics = [Metric("secrets.secret_count", Decimal(len(resources)),
                          dims={"region": region})]
        return CollectorResult(resources, metrics, [region], 0)
