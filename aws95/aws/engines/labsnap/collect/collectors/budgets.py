# Upstream: labsnap/labsnap/collect/collectors/budgets.py @ 1b1d437 — adapted for aws95; owned here from now on.
# The cost-allocation-tag count moved to cost.py: it is a billed Cost Explorer call (plan 2b ruling).
from aws95.aws import gate
from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.model import CollectorResult, Resource


@register
class BudgetsCollector:
    name = "budgets"
    scope = "global"
    timeout_s = 60
    volatile_keys = frozenset()

    def collect(self, session, region, cancel=None):
        budgets = gate.bind(session, "budgets", "us-east-1", cancel=cancel)
        sts = gate.bind(session, "sts", "us-east-1", cancel=cancel)
        # DescribeBudgets requires the AccountId; failing to identify the caller fails the collector, honestly.
        account_id = sts.get_caller_identity()["Account"]
        resources = []
        for page in budgets.pages("describe_budgets", AccountId=account_id):
            for b in page["Budgets"]:
                limit = b.get("BudgetLimit") or {}
                tag_filters = (b.get("CostFilters") or {}).get("TagKeyValue")
                resources.append(Resource("budgets.budget", "global", b["BudgetName"], {
                    "limit_amount": limit.get("Amount"),       # AWS's own decimal string, kept as text
                    "limit_unit": limit.get("Unit"),
                    "filters_on_tag": bool(tag_filters),
                }))
        return CollectorResult(resources, [], ["global"], denials=0)
