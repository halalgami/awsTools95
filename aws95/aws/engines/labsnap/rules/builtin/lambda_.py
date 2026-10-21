# Upstream: labsnap/labsnap/rules/builtin/lambda_.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws.engines.labsnap.model import Finding
from aws95.aws.engines.labsnap.rules.base import register_rule


@register_rule
class DeprecatedRuntime:
    id = "lambda.deprecated_runtime"
    severity = "high"
    title = "Lambda function uses a deprecated runtime"
    description = ("The function's runtime is past its end-of-support date and no "
                   "longer receives security patches from AWS.")
    params = {"deprecated": [
        "nodejs14.x", "nodejs16.x", "python3.7", "python3.8",
        "python3.9", "ruby2.7", "go1.x", "dotnetcore3.1", "java8",
    ]}

    def evaluate(self, snap, params):
        deprecated = set(params.get("deprecated", []))
        for r in snap.resources("lambda.function"):
            if "runtime" not in r.attrs:
                continue
            if r.attrs["runtime"] in deprecated:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"runtime": r.attrs["runtime"]})
