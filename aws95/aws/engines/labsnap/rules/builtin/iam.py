# Upstream: labsnap/labsnap/rules/builtin/iam.py @ 1b1d437 — adapted for aws95; owned here from now on.
from datetime import UTC, datetime

from aws95.aws.engines.labsnap.model import Finding
from aws95.aws.engines.labsnap.rules.base import register_rule


@register_rule
class RootNoMfa:
    id = "iam.root_no_mfa"
    severity = "critical"
    title = "Root account has no MFA device"
    description = ("Root can do anything in the account, including closing it. "
                   "It is the single highest-value credential to protect.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("account.summary"):
            if "root_mfa_enabled" not in r.attrs:
                continue
            if not r.attrs["root_mfa_enabled"]:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity)


@register_rule
class NoPasswordPolicy:
    id = "iam.no_password_policy"
    severity = "critical"
    title = "Account has no IAM password policy"
    description = "Without one, users may set passwords of any length or complexity."
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("account.summary"):
            if "has_password_policy" not in r.attrs:
                continue
            if not r.attrs["has_password_policy"]:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity)


@register_rule
class UserNoMfa:
    id = "iam.user_no_mfa"
    severity = "high"
    title = "IAM user has no MFA device"
    description = "A long-lived identity protected by a password alone."
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("iam.user"):
            if "has_mfa" not in r.attrs:
                continue
            if r.attrs["has_mfa"]:
                continue
            yield Finding(self.id, r.rtype, r.region, r.rid, self.severity)


@register_rule
class KeyStale:
    id = "iam.key_stale"
    severity = "high"
    title = "Active access key is older than the rotation threshold"
    description = "Long-lived static credentials that have not been rotated."
    params = {"max_age_days": 180}

    def evaluate(self, snap, params):
        cutoff = params.get("max_age_days", 180)
        now = datetime.now(UTC)
        for r in snap.resources("iam.access_key"):
            if "status" not in r.attrs or "created" not in r.attrs:
                continue
            if r.attrs["status"] != "Active":
                continue
            created = datetime.strptime(
                r.attrs["created"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            age = (now - created).days
            if age > cutoff:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"age_days": age, "user": r.attrs.get("user")})


@register_rule
class KeyUnused:
    id = "iam.key_unused"
    severity = "high"
    title = "Active access key has never been used, or not used in a long time"
    description = ("A key that has never authenticated a single call, or has not "
                   "done so in a long time, is unnecessary standing access - a "
                   "different problem from a key that is merely old and rotated "
                   "on schedule (see iam.key_stale).")
    params = {"unused_days": 365}

    def evaluate(self, snap, params):
        cutoff = params.get("unused_days", 365)
        now = datetime.now(UTC)
        for r in snap.resources("iam.access_key"):
            if ("status" not in r.attrs or "created" not in r.attrs
                    or "last_used" not in r.attrs):
                continue
            if r.attrs["status"] != "Active":
                continue

            created = datetime.strptime(
                r.attrs["created"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

            if r.attrs["last_used"] is None:
                # Never used at all - flagged regardless of how new the key
                # is, since "created last week and never touched" is exactly
                # the case this rule exists to catch.
                days_since_created = (now - created).days
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"reason": "never_used",
                               "days_since_created": days_since_created,
                               "user": r.attrs.get("user")})
                continue

            last_used = datetime.strptime(
                r.attrs["last_used"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            age = (now - last_used).days
            if age > cutoff:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"reason": "last_used_stale", "age_days": age,
                               "user": r.attrs.get("user")})
