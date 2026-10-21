"""Importing a collector module registers it."""
from aws95.aws.engines.labsnap.collect.collectors import (  # noqa: F401
    account, budgets, cloudtrail, cognito, cost, dynamodb, ec2, ecr, ecs, elb, freetier, guardrails, iam, kms,
    lambda_, logs, rds, s3, secrets,
)
