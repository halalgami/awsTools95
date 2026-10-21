# Upstream: labsnap/labsnap/collect/collectors/iam.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws import gate
from decimal import Decimal
from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.collect.probe import probe
from aws95.aws.engines.labsnap.model import CollectorResult, Metric, Resource


@register
class IamCollector:
    name = "iam"
    scope = "global"
    timeout_s = 300
    volatile_keys = frozenset()

    def collect(self, session, region, cancel=None):
        iam = gate.bind(session, "iam", None, cancel=cancel)
        resources, denials = [], 0
        # Counted only over data actually retrieved - a denied per-user call
        # must never silently understate these, which would be a quieter
        # version of the same lie an absence-vs-denial mixup produces.
        no_mfa, active_keys = 0, 0

        for page in iam.pages("list_users"):
            for u in page["Users"]:
                name = u["UserName"]
                attrs, partial = {}, False

                # `denied is False` covers two cases: success, and an
                # ABSENT_CODES error (e.g. the user was deleted mid-scan) -
                # probe() returns None for both. Only a genuine success has
                # a value to read.
                mfa, denied = probe(iam.list_mfa_devices, UserName=name)
                denials += denied
                partial |= denied
                if not denied and mfa is not None:
                    has_mfa = bool(mfa["MFADevices"])
                    attrs["has_mfa"] = has_mfa
                    no_mfa += not has_mfa

                keys, denied = probe(iam.list_access_keys, UserName=name)
                denials += denied
                partial |= denied
                if not denied and keys is not None:
                    for k in keys["AccessKeyMetadata"]:
                        active_keys += k["Status"] == "Active"
                        key_id = k["AccessKeyId"]
                        key_attrs = {
                            "user": name, "status": k["Status"],
                            "created": k["CreateDate"].strftime("%Y-%m-%dT%H:%M:%SZ")}

                        last_used, lu_denied = probe(
                            iam.get_access_key_last_used, AccessKeyId=key_id)
                        denials += lu_denied
                        if not lu_denied and last_used is not None:
                            # AWS omits LastUsedDate entirely (rather than
                            # erroring) when a key has never been used - that
                            # is a real fact, distinct from a denied call, so
                            # it is stored as an explicit None, never simply
                            # left off the way a denial is.
                            last_used_info = last_used.get("AccessKeyLastUsed", {})
                            last_used_date = last_used_info.get("LastUsedDate")
                            key_attrs["last_used"] = (
                                last_used_date.strftime("%Y-%m-%dT%H:%M:%SZ")
                                if last_used_date else None)

                        resources.append(Resource(
                            "iam.access_key", "global", key_id, key_attrs,
                            partial=lu_denied))

                resources.append(Resource("iam.user", "global", name, attrs, partial))

        metrics = [Metric("iam.users_without_mfa", Decimal(no_mfa)),
                   Metric("iam.active_access_keys", Decimal(active_keys))]
        return CollectorResult(resources, metrics, ["global"], denials)
