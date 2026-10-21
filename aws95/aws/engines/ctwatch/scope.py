"""The Activity event scope (first-drop spec §5.3): ONE editable table of what AWS 95 Tools keeps.

Each row is a rule: its lookup pass (one LookupAttributes filter — LookupEvents takes exactly one), the window that
pass uses, and the `keep` test applied to every event the pass returns and to every record an S3 import reads. Rows
without a lookup pass come only from Import History… (S3). A rule's key is also the activity_coverage kind of its
pass. Edit here and only here."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Detail = dict[str, Any]

HUMAN_TYPES = frozenset({"IAMUser", "Root", "SAMLUser"})   # SAMLUser: a person federated through SAML (review I7)
SSO_ROLE_PREFIX = "AWSReservedSSO_"           # IAM Identity Center: a person behind an assumed role
DENIED_CODES = frozenset({"AccessDenied", "AccessDeniedException", "UnauthorizedOperation",
                          "Client.UnauthorizedOperation", "UnauthorizedAccess"})
INSIGHT_EVENT_TYPE = "AwsCloudTrailInsight"


def identity(detail: Detail) -> dict[str, Any]:
    ui = detail.get("userIdentity")
    return ui if isinstance(ui, dict) else {}


def is_sso_session(ui: dict[str, Any]) -> bool:
    if ui.get("type") != "AssumedRole":
        return False
    issuer = (ui.get("sessionContext") or {}).get("sessionIssuer") or {}
    return str(issuer.get("userName", "")).startswith(SSO_ROLE_PREFIX)


def is_human(detail: Detail) -> bool:
    ui = identity(detail)
    return ui.get("type") in HUMAN_TYPES or is_sso_session(ui)


def is_change(detail: Detail) -> bool:
    """A change event. The ReadOnly=false pass returns these; a record without the flag is kept, not dropped."""
    return detail.get("readOnly") is not True


def is_root(detail: Detail) -> bool:
    return identity(detail).get("type") == "Root"


def is_denied(detail: Detail) -> bool:
    return detail.get("errorCode") in DENIED_CODES


def is_insight(detail: Detail) -> bool:
    return detail.get("eventType") == INSIGHT_EVENT_TYPE


@dataclass(frozen=True)
class ScopeRule:
    key: str                               # stable id; also the activity_coverage kind of its lookup pass
    label: str                             # what the window says is covered
    lookup: tuple[str, str] | None         # (AttributeKey, AttributeValue); None = Import History… only
    window_hours: int                      # lookup window length, walked newest first
    keep: Callable[[Detail], bool]


def _human_call(source: str, name: str, also: frozenset[str] = frozenset()) -> Callable[[Detail], bool]:
    """`name` from `source` by a person - or by one of the principal types in `also` (federated callers)."""
    def keep(detail: Detail) -> bool:
        if detail.get("eventSource") != source or detail.get("eventName") != name:
            return False
        return is_human(detail) or identity(detail).get("type") in also
    return keep


def _named(label: str, source: str, name: str, *, also: tuple[str, ...] = ()) -> ScopeRule:
    return ScopeRule(f"name:{name}", label, ("EventName", name), 24, _human_call(source, name, frozenset(also)))


def _console_login(detail: Detail) -> bool:
    return detail.get("eventName") == "ConsoleLogin"


SCOPE: tuple[ScopeRule, ...] = (
    # Base (ctwatch): every change, human role assumption, human secret reads.
    ScopeRule("bulk", "Every change event, including denied change attempts", ("ReadOnly", "false"), 4, is_change),
    _named("Human AssumeRole", "sts.amazonaws.com", "AssumeRole"),
    # These two are made by federated principals (SAMLUser / WebIdentityUser), never by IAM users (review I7).
    _named("AssumeRoleWithSAML by people", "sts.amazonaws.com", "AssumeRoleWithSAML", also=("SAMLUser",)),
    _named("AssumeRoleWithWebIdentity by federated identities", "sts.amazonaws.com", "AssumeRoleWithWebIdentity",
           also=("WebIdentityUser",)),
    _named("Human GetSecretValue", "secretsmanager.amazonaws.com", "GetSecretValue"),
    # Addition 1: all root activity, reads included (root's changes also arrive in the bulk pass).
    ScopeRule("root", "All root activity, reads included", ("Username", "root"), 24, is_root),
    # Addition 3: sensitive human reads.
    _named("Human GetParameter", "ssm.amazonaws.com", "GetParameter"),
    _named("Human GetParameters", "ssm.amazonaws.com", "GetParameters"),
    _named("Human kms:Decrypt", "kms.amazonaws.com", "Decrypt"),
    _named("Human GetSessionToken", "sts.amazonaws.com", "GetSessionToken"),
    _named("Human GetFederationToken", "sts.amazonaws.com", "GetFederationToken"),
    _named("Human ListAccessKeys", "iam.amazonaws.com", "ListAccessKeys"),
    _named("Human GetCredentialReport", "iam.amazonaws.com", "GetCredentialReport"),
    # Addition 4: console sign-ins, failures included, whoever the principal.
    ScopeRule("name:ConsoleLogin", "Console sign-ins, including failures", ("EventName", "ConsoleLogin"), 24,
              _console_login),
    # Addition 2 (reads) and addition 5: only the S3 import sees these.
    ScopeRule("denied", "Denied read attempts (imported history only)", None, 0, is_denied),
    ScopeRule("insights", "CloudTrail Insights events (imported history only)", None, 0, is_insight),
)
LOOKUP_PASSES: tuple[ScopeRule, ...] = tuple(r for r in SCOPE if r.lookup is not None)
TARGETED_PASSES: tuple[ScopeRule, ...] = tuple(r for r in LOOKUP_PASSES if r.key != "bulk")


def keep_imported(detail: Detail) -> bool:
    """An S3 log record is kept when any rule keeps it (the import sees every read, so each rule filters)."""
    return any(rule.keep(detail) for rule in SCOPE)
