"""Importing a collector module registers it."""
from aws95.aws.engines.labsnap.collect.collectors import (  # noqa: F401
    account, apigateway, budgets, cloudfront, cloudtrail, cognito, cost, dynamodb, ec2, ecr, ecs, elb, freetier,
    guardrails, iam, kms, lambda_, logs, rds, route53, s3, secrets,
)
