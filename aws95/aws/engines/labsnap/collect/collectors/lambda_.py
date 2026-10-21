# Upstream: labsnap/labsnap/collect/collectors/lambda_.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws import gate
from decimal import Decimal
from functools import partial
from aws95.aws.engines.labsnap.collect import redact
from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.collect.fanout import map_ordered
from aws95.aws.engines.labsnap.collect.probe import probe
from aws95.aws.engines.labsnap.model import CollectorResult, Metric, Resource



def _provisioned(lam, fn):
    return probe(lam.list_provisioned_concurrency_configs, FunctionName=fn["FunctionName"])


@register
class LambdaCollector:
    name = "lambda"
    scope = "regional"
    timeout_s = 300          # per region (resumable-snapshots spec §3.3)
    volatile_keys = frozenset()

    def collect(self, session, region, cancel=None):
        lam = gate.bind(session, "lambda", region, cancel=cancel)
        resources, denials = [], 0
        # Only the runtimes we actually know are counted - a denied function
        # would otherwise be invisible to the count rather than honestly
        # excluded from it.
        runtime_counts = {}

        for page in lam.pages("list_functions"):
            redact.lambda_functions(page)     # spec §3.2: Environment.Variables never reaches anything else
            functions = page["Functions"]
            # The per-function probes run FANOUT at a time (resumable-snapshots spec §5.3); results keep page order.
            probes = map_ordered(partial(_provisioned, lam), functions, cancel=cancel)
            for fn, outcome in zip(functions, probes):
                if isinstance(outcome, Exception):
                    raise outcome
                name = fn["FunctionName"]
                # Container-image functions carry no "Runtime" key at all -
                # that is a known, meaningful fact (no managed runtime), not
                # an unreadable attribute, so it gets its own bucket rather
                # than crashing the collector or being marked partial.
                runtime = fn.get("Runtime", "(container)")
                attrs = {
                    "runtime": runtime,
                    "memory_mb": fn["MemorySize"],
                    "timeout_s": fn["Timeout"],
                }
                runtime_counts[runtime] = runtime_counts.get(runtime, 0) + 1

                pc, denied = outcome
                denials += denied
                # `not denied` alone is not "success" - an ABSENT_CODES error
                # (e.g. the function deleted mid-scan) also returns
                # (None, False), and must not be subscripted.
                if not denied and pc is not None:
                    attrs["provisioned_concurrency"] = len(
                        pc["ProvisionedConcurrencyConfigs"])

                resources.append(Resource("lambda.function", region, name, attrs,
                                          partial=denied))

        metrics = [Metric("lambda.function_count", Decimal(count),
                          dims={"runtime": runtime, "region": region})
                   for runtime, count in runtime_counts.items()]
        return CollectorResult(resources, metrics, [region], denials)
