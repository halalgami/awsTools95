# Upstream: labsnap/labsnap/rules/builtin/cognito.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws.engines.labsnap.model import Finding
from aws95.aws.engines.labsnap.rules.base import register_rule


@register_rule
class MfaOff:
    id = "cognito.mfa_off"
    severity = "high"
    title = "User pool does not offer MFA"
    description = ("Multi-factor authentication is switched off entirely for "
                   "this directory, so every account in it is protected by a "
                   "password alone. OPTIONAL is not reported here: the factor "
                   "is at least available to users who enable it.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("cognito.user_pool"):
            if "mfa_configuration" not in r.attrs:
                continue
            if r.attrs["mfa_configuration"] == "OFF":
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"estimated_users": r.attrs.get("estimated_users")})


@register_rule
class WeakPasswordPolicy:
    id = "cognito.weak_password_policy"
    severity = "medium"
    title = "User pool password policy is below the configured floor"
    description = ("The pool accepts passwords shorter or simpler than the "
                   "configured minimum. Every threshold is a parameter, so the "
                   "floor is the operator's policy, not this tool's opinion.")
    params: dict = {
        "min_length": 12,
        "require_uppercase": True,
        "require_lowercase": True,
        "require_numbers": True,
        "require_symbols": True,
    }

    # (param name, attribute name, phrase used in the reason)
    _CLASSES = (
        ("require_uppercase", "password_requires_uppercase", "uppercase"),
        ("require_lowercase", "password_requires_lowercase", "lowercase"),
        ("require_numbers", "password_requires_numbers", "numbers"),
        ("require_symbols", "password_requires_symbols", "symbols"),
    )

    def evaluate(self, snap, params):
        min_length = params.get("min_length", 12)
        for r in snap.resources("cognito.user_pool"):
            if "password_min_length" not in r.attrs:
                continue
            reasons = []
            length = r.attrs["password_min_length"]
            if length is not None and length < min_length:
                reasons.append(f"min_length {length} < {min_length}")
            for param, attr, phrase in self._CLASSES:
                if not params.get(param, True):
                    continue
                if attr not in r.attrs:
                    continue
                if not r.attrs[attr]:
                    reasons.append(f"no {phrase} required")
            if reasons:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"reasons": reasons})


@register_rule
class SelfSignupAllowed:
    id = "cognito.self_signup_allowed"
    severity = "medium"
    title = "User pool permits unauthenticated self-registration"
    description = ("Anyone who can reach the pool can create an account in it. "
                   "This is correct for a public product and wrong for an "
                   "internal directory, so it is reported for a decision "
                   "rather than assumed either way.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("cognito.user_pool"):
            if "self_signup_allowed" not in r.attrs:
                continue
            if r.attrs["self_signup_allowed"]:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"estimated_users": r.attrs.get("estimated_users")})
