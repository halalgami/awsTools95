# Upstream: labsnap/labsnap/collect/collectors/kms.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws import gate
from decimal import Decimal
import json

from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.collect.probe import probe
from aws95.aws.engines.labsnap.model import CollectorResult, Metric, Resource

# Condition keys that narrow an otherwise-open principal back to this account
# or organisation. A wildcard principal guarded by one of these is the normal,
# correct shape for a service-linked key policy - not an external grant.
#
# aws95 (2026-10-15/16): a key narrows only when its VALUES name this account exactly - a condition that names
# another account, or uses a wildcard, is not a narrowing - and a negated operator (StringNotEquals, ...) never
# narrows. Organisation keys don't narrow either: the collector can't tell this account's organisation from another
# one, so it fails closed and the finding asks a person to look.
_ACCOUNT_KEYS = {"kms:calleraccount", "aws:sourceaccount", "aws:principalaccount", "aws:sourceowner"}
_ARN_KEYS = {"aws:sourcearn"}


def _values(raw):
    return [str(v) for v in (raw if isinstance(raw, list) else [raw])]


def _condition_narrows(statement, account):
    for operator, operator_args in (statement.get("Condition") or {}).items():
        if not isinstance(operator_args, dict) or "not" in str(operator).lower():
            continue
        for key, raw in operator_args.items():
            k = key.lower()
            values = _values(raw)
            if any(ch in v for v in values for ch in "*?"):
                continue
            if k in _ACCOUNT_KEYS and values and all(v == account for v in values):
                return True
            if k in _ARN_KEYS and values and all(f":{account}:" in v for v in values):
                return True
    return False


def _principals(statement):
    principal = statement.get("Principal")
    if principal == "*":
        return ["*"]
    if isinstance(principal, str):
        return [principal]
    if isinstance(principal, dict):
        aws = principal.get("AWS", [])
        found = [aws] if isinstance(aws, str) else list(aws)
        # A web or SAML identity provider is outside the account (aws95, 2026-10-15). Service principals stay
        # internal: they are AWS services acting for this account.
        federated = principal.get("Federated", [])
        found += [f"federated:{f}" for f in ([federated] if isinstance(federated, str) else list(federated))]
        return found
    return []


def _policy_allows_external(policy_text, account):
    """True when the policy grants to a principal outside `account`.

    The policy document itself is never stored - only this boolean. A stored
    key policy is a stored list of everyone who can decrypt with the key,
    which is precisely the inventory an attacker would want and precisely
    what §12 of the design forbids keeping.
    """
    doc = json.loads(policy_text)
    statements = doc.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    for stmt in statements:
        if stmt.get("Effect") != "Allow":
            continue
        if _condition_narrows(stmt, account):
            continue
        for principal in _principals(stmt):
            if principal == "*":
                return True
            # An ARN for this account is internal; anything else is not.
            if f":{account}:" not in principal:
                return True
    return False


@register
class KmsCollector:
    name = "kms"
    scope = "regional"
    timeout_s = 300          # fans out per key; the reference account has 44
    volatile_keys = frozenset()

    def collect(self, session, region, cancel=None):
        kms = gate.bind(session, "kms", region, cancel=cancel)
        resources, denials = [], 0

        aliases = {}
        alias_pages, alias_unreadable = probe(
            lambda: list(kms.pages("list_aliases")))
        denials += alias_unreadable
        if not alias_unreadable:
            for page in alias_pages or []:
                for alias in page.get("Aliases", []):
                    target = alias.get("TargetKeyId")
                    if target:
                        aliases[target] = aliases.get(target, 0) + 1

        key_count = 0
        for page in kms.pages("list_keys"):
            for entry in page["Keys"]:
                key_id = entry["KeyId"]
                key_count += 1
                meta, unreadable = probe(kms.describe_key, KeyId=key_id)
                denials += unreadable
                if unreadable:
                    # A denied describe_key means alias_count is unknown too -
                    # recorded only when it is actually known; a denial must
                    # never look like "no aliases".
                    attrs = {}
                    if not alias_unreadable:
                        attrs["alias_count"] = aliases.get(key_id, 0)
                    resources.append(Resource("kms.key", region, key_id,
                                              attrs, True))
                    continue
                md = (meta or {}).get("KeyMetadata", {})
                # Aliases enrich every key with alias_count. A denial there
                # must never look like "zero aliases" - counted only over
                # keys whose alias_count is actually known.
                partial = alias_unreadable
                attrs = {
                    "manager": md.get("KeyManager"),
                    "key_state": md.get("KeyState"),
                    "key_spec": md.get("KeySpec"),
                    "key_usage": md.get("KeyUsage"),
                }
                if not alias_unreadable:
                    attrs["alias_count"] = aliases.get(key_id, 0)
                # Rotation is only asked about for keys where the answer is
                # both meaningful and the operator's to change. Asking about
                # an asymmetric key raises UnsupportedOperationException, and
                # asking about an AWS-managed key produces a fact nobody can
                # act on.
                if (md.get("KeyManager") == "CUSTOMER"
                        and md.get("KeySpec") == "SYMMETRIC_DEFAULT"):
                    rotation, unreadable_rot = probe(
                        kms.get_key_rotation_status, KeyId=key_id)
                    denials += unreadable_rot
                    partial = partial or unreadable_rot
                    if not unreadable_rot:
                        attrs["rotation_enabled"] = bool(
                            (rotation or {}).get("KeyRotationEnabled"))

                policy, unreadable_pol = probe(
                    kms.get_key_policy, KeyId=key_id, PolicyName="default")
                denials += unreadable_pol
                partial = partial or unreadable_pol
                if not unreadable_pol:
                    attrs["policy_allows_external"] = _policy_allows_external(
                        (policy or {}).get("Policy", "{}"), _account_of(md))

                resources.append(Resource("kms.key", region, key_id, attrs, partial))

        metrics = [Metric("kms.key_count", Decimal(key_count),
                          dims={"region": region})]
        return CollectorResult(resources, metrics, [region], denials)


def _account_of(key_metadata):
    """The owning account id, taken from the key's own ARN.

    Reading it from the key rather than from the snapshot keeps
    `_policy_allows_external` correct for a multi-account run without the
    collector needing to know which target it is inside.
    """
    arn = key_metadata.get("Arn", "")
    parts = arn.split(":")
    return parts[4] if len(parts) > 4 else ""
