# Upstream: labsnap/labsnap/rules/builtin/secrets.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws.engines.labsnap.model import Finding
from aws95.aws.engines.labsnap.rules.base import register_rule


@register_rule
class RotationDisabled:
    id = "secrets.rotation_disabled"
    severity = "medium"
    title = "Secret has no automatic rotation"
    description = ("The secret never rotates, so a credential exposed at any "
                   "point stays valid indefinitely. Rotation bounds how long a "
                   "leak is useful.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("secrets.secret"):
            if "rotation_enabled" not in r.attrs:
                continue
            if not r.attrs["rotation_enabled"]:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"last_rotated_date": r.attrs.get("last_rotated_date")})


@register_rule
class Unused:
    id = "secrets.unused"
    severity = "low"
    title = "Secret has not been read recently"
    description = ("Nothing has retrieved this secret within the configured "
                   "window, or it has never been retrieved at all. An unused "
                   "secret is an unmonitored credential and a monthly charge.")
    params: dict = {"max_idle_days": 180}

    def evaluate(self, snap, params):
        limit = params.get("max_idle_days", 180)
        for r in snap.resources("secrets.secret"):
            if "days_since_accessed" not in r.attrs:
                continue
            days = r.attrs["days_since_accessed"]
            # None means never accessed - a stronger version of the same
            # finding, not an unknown. The collector distinguishes the two:
            # unknown is an omitted attribute, caught by the guard above.
            if days is None or days > limit:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"days_since_accessed": days})
