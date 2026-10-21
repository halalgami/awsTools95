# Upstream: labsnap/labsnap/rules/builtin/cost.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws.engines.labsnap.model import Finding
from aws95.aws.engines.labsnap.rules.base import register_rule


@register_rule
class FreeTierExceeded:
    id = "freetier.exceeded"
    severity = "low"
    title = "Free tier allowance is at or over its limit"
    description = ("Usage of a free-tier-eligible service has reached or passed "
                   "100% of the free allowance for the current month, so further "
                   "usage is billed.")
    params: dict = {}

    def evaluate(self, snap, params):
        # A metric only exists here when the collector actually read a limit
        # and an actual-usage figure for it; there is no partial/attrs concept
        # for metrics, so "the metric is present" is the whole guard.
        for m in snap.metrics("freetier.usage"):
            if m.value >= 100.0:
                service = m.dims.get("service", "unknown")
                usage_type = m.dims.get("usage_type", "unknown")
                # rid must include usage_type: a service can have more than
                # one usage type over its limit in the same month (e.g. EC2
                # BoxUsage and DataTransfer-Out both over 100%), and keying
                # on service alone collides on the findings primary key
                # (snapshot_id, rule, rtype, region, rid) - INSERT OR REPLACE
                # then silently drops all but the last one written.
                yield Finding(self.id, "freetier.usage", m.dims.get("region", "global"),
                              f"{service}:{usage_type}", self.severity,
                              {"service": service,
                               "usage_type": m.dims.get("usage_type"),
                               "pct_of_limit": m.value})


@register_rule
class BudgetIneffective:
    id = "budget.ineffective"
    severity = "low"
    title = "Budget filters on a cost allocation tag that is not active"
    description = ("The budget scopes itself with a tag filter, but the account "
                   "has zero active cost allocation tags, so the filter can never "
                   "match anything and the budget silently tracks nothing.")
    params: dict = {}

    def evaluate(self, snap, params):
        # The tag-count metric is only present when that collector call
        # succeeded; if it's missing we genuinely don't know the count and
        # must not guess it as zero.
        tag_metrics = snap.metrics("cost.active_allocation_tags")
        if not tag_metrics:
            return
        active_tags = tag_metrics[0].value

        for r in snap.resources("budgets.budget"):
            if "filters_on_tag" not in r.attrs:
                continue
            if not r.attrs["filters_on_tag"]:
                continue
            if active_tags == 0:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity)
