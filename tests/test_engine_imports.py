import subprocess
import sys


def test_engine_import_does_not_load_botocore():
    code = ("import sys, aws95.store, aws95.aws.engines.labsnap.collect.collectors, "
            "aws95.aws.engines.labsnap.collect.probe, aws95.aws.engines.labsnap.collect.fanout, "
            "aws95.aws.engines.labsnap.rules.base, "
            "aws95.aws.engines.labsnap.analyze.findings, aws95.aws.engines.labsnap.analyze.diff, "
            "aws95.aws.engines.labsnap.service, aws95.aws.insights.bedrock; "
            "print('botocore' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "False"


def test_all_collectors_register():
    import aws95.aws.engines.labsnap.collect.collectors  # noqa: F401
    from aws95.aws.engines.labsnap.collect.base import all_collectors
    assert sorted(all_collectors()) == ["account", "apigateway", "budgets", "cloudfront", "cloudtrail", "cognito", "cost", "dynamodb", "ec2",
                                        "ecr", "ecs", "elb", "freetier", "guardrails", "iam", "kms", "lambda",
                                        "logs", "rds", "route53", "s3", "secrets"]


CTWATCH_MODULES = ["aws95.aws.engines.ctwatch.rules", "aws95.aws.engines.ctwatch.redact",
                   "aws95.aws.engines.ctwatch.scope", "aws95.aws.engines.ctwatch.normalize",
                   "aws95.aws.engines.ctwatch.s3keys", "aws95.aws.engines.ctwatch.fetcher",
                   "aws95.aws.engines.ctwatch.estimate", "aws95.aws.engines.ctwatch.service",
                   "aws95.aws.engines.ctwatch.history"]


def test_ctwatch_import_does_not_load_botocore():
    code = f"import sys, {', '.join(CTWATCH_MODULES)}; print('botocore' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "False"
