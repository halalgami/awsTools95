# Upstream: labsnap/labsnap/rules/builtin/__init__.py @ 1b1d437 — adapted for aws95; owned here from now on.
# Importing a rule module registers its rules.
from aws95.aws.engines.labsnap.rules.builtin import (  # noqa: F401
    apigateway, cloudfront, cloudtrail, cognito, cost, dynamodb, ec2, ecr, ecs, elb, guardrails, iam,
    kms, lambda_, logs, rds, route53, s3, secrets,
)
