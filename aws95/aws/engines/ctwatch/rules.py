# Upstream: ctwatch/ctwatch/rules.py @ 254ff31 — adapted for aws95; owned here from now on.
"""Risk rules (escalation, destructive, exposure, security-config, secret access), unchanged from ctwatch."""
_HIGH_NAMES = {"CreateAccessKey", "CreateUser", "CreateLoginProfile", "UpdateAssumeRolePolicy"}
_DESTRUCTIVE = {"DeleteUserPool", "DeleteUserPoolClient", "DeleteBucket", "DeleteRole",
                "DeleteFunction", "DeleteDBInstance", "DeleteLogGroup"}
_SECCONF = {"PutEventSelectors", "StopLogging", "DeleteTrail", "DisableSecurityHub",
            "DeleteDetector", "UpdateAccountPasswordPolicy", "DeleteAccountPasswordPolicy"}

def _is_admin_attach(name, rp):
    if name in ("AttachUserPolicy", "AttachRolePolicy"):
        return str(rp.get("policyArn", "")).endswith("/AdministratorAccess")
    return False

def _is_star_policy(name, rp):
    if name in ("PutUserPolicy", "PutRolePolicy"):
        doc = str(rp.get("policyDocument", ""))
        return '"Action":"*"' in doc.replace(" ", "") and '"Resource":"*"' in doc.replace(" ", "")
    return False

_ASSUME_NAMES = {"AssumeRole", "AssumeRoleWithSAML", "AssumeRoleWithWebIdentity"}
_ASSUME_HUMAN_TYPES = {"Root", "IAMUser"}


def _is_role_assumption(name, detail):
    if name not in _ASSUME_NAMES or detail.get("eventSource") != "sts.amazonaws.com":
        return False
    return detail.get("userIdentity", {}).get("type") in _ASSUME_HUMAN_TYPES


def evaluate(detail: dict) -> tuple[int, str | None]:
    name = detail.get("eventName", "")
    rp = detail.get("requestParameters") or {}
    # High
    if name in _HIGH_NAMES or _is_admin_attach(name, rp) or _is_star_policy(name, rp):
        return 3, "iam-escalation"
    if name == "PassRole":
        return 3, "iam-escalation"
    if _is_role_assumption(name, detail):
        # Root assuming any role is always alarming; a human IAM user doing it is
        # routine for admins, so it is listed rather than flagged. Role-to-role
        # chaining by service principals stays silent - it is normal automation
        # and would otherwise swamp the watchlist.
        if detail.get("userIdentity", {}).get("type") == "Root":
            return 3, "iam-escalation"
        return 1, "role-assumption"
    # Medium
    if name in _DESTRUCTIVE:
        return 2, "destructive"
    if name in ("PutBucketPolicy", "PutBucketPublicAccessBlock", "PutUserPolicy", "PutRolePolicy"):
        return 2, "exposure"
    # Low
    if name in _SECCONF:
        return 1, "security-config"
    if name == "GetSecretValue":
        return 1, "secret-access"
    return 0, None
