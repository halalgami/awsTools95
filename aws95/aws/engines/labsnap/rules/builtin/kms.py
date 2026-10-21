# Upstream: labsnap/labsnap/rules/builtin/kms.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws.engines.labsnap.model import Finding
from aws95.aws.engines.labsnap.rules.base import register_rule


@register_rule
class RotationDisabled:
    id = "kms.rotation_disabled"
    severity = "medium"
    title = "Customer-managed KMS key does not rotate"
    description = ("Automatic annual rotation is off, so one key version "
                   "protects data indefinitely and a compromise has unbounded "
                   "scope. AWS-managed keys are excluded: their rotation is "
                   "not the operator's to change.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("kms.key"):
            if "rotation_enabled" not in r.attrs:
                continue
            if r.attrs.get("manager") != "CUSTOMER":
                continue
            if not r.attrs["rotation_enabled"]:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"alias_count": r.attrs.get("alias_count")})


@register_rule
class PolicyAllowsExternal:
    id = "kms.policy_allows_external"
    severity = "high"
    title = "KMS key policy grants outside this account"
    description = ("The key policy allows a principal from another account, or "
                   "any principal at all, with no condition narrowing it back "
                   "to this account or organisation. Anything that key "
                   "protects is readable by whoever holds that access.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("kms.key"):
            if "policy_allows_external" not in r.attrs:
                continue
            if r.attrs["policy_allows_external"]:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"key_state": r.attrs.get("key_state")})
