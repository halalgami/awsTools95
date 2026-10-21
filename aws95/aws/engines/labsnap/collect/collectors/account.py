# Upstream: labsnap/labsnap/collect/collectors/account.py @ 1b1d437 — adapted for aws95; owned here from now on.
from decimal import Decimal

from aws95.aws import gate
from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.collect.probe import error_code, probe
from aws95.aws.engines.labsnap.model import CollectorResult, Metric, Resource


@register
class AccountCollector:
    name = "account"
    scope = "global"
    timeout_s = 60
    volatile_keys = frozenset()

    def collect(self, session, region, cancel=None):
        iam = gate.bind(session, "iam", None, cancel=cancel)
        try:
            iam.get_account_password_policy()
            has_policy = True
        except Exception as exc:  # noqa: BLE001 - only "no policy" is an answer; everything else propagates
            if error_code(exc) != "NoSuchEntity":
                raise
            has_policy = False

        summary, denied = probe(iam.get_account_summary)
        attrs = {"has_password_policy": has_policy}
        metrics = []
        if summary is not None:
            summary_map = summary["SummaryMap"]
            attrs["root_mfa_enabled"] = bool(summary_map.get("AccountMFAEnabled", 0))
            # Only emitted when actually seen: a fabricated zero from a denied call would be worse than none.
            metrics.append(Metric("iam.user_count", Decimal(summary_map.get("Users", 0))))

        return CollectorResult(
            [Resource("account.summary", "global", "self", attrs, partial=denied)],
            metrics, ["global"], denials=int(denied))
