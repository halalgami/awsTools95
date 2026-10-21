"""Which collector covers which Cost Explorer service (Billing ▸ Uncovered spend, Billing → Resources). Qt-free.
Names are the SERVICE keys Cost Explorer returns. An unlisted name reads "No collector": it only changes a label."""
from __future__ import annotations

SERVICE_COLLECTORS: dict[str, tuple[str, ...]] = {
    "Amazon Elastic Compute Cloud - Compute": ("ec2",),
    "EC2 - Other": ("ec2",),
    "Amazon Elastic Load Balancing": ("elb",),
    "Amazon Simple Storage Service": ("s3",),
    "AWS Lambda": ("lambda",),
    "Amazon Relational Database Service": ("rds",),
    "Amazon DynamoDB": ("dynamodb",),
    "AWS Identity and Access Management": ("iam",),
    "AWS Key Management Service": ("kms",),
    "AmazonCloudWatch": ("logs",),
    "AWS CloudTrail": ("cloudtrail",),
    "Amazon EC2 Container Registry (ECR)": ("ecr",),
    "Amazon Elastic Container Service": ("ecs",),
    "Amazon Cognito": ("cognito",),
    "AWS Secrets Manager": ("secrets",),
    "Amazon Bedrock": ("guardrails",),
    "AWS Budgets": ("budgets",),
}
# Collectors that describe the account or its bill rather than a billed service.
META_COLLECTORS = frozenset({"account", "cost", "freetier"})
# Lines on the bill that have no resources to inventory.
NOT_A_RESOURCE = frozenset({"Tax", "AWS Cost Explorer", "AWS Marketplace", "Savings Plans for AWS Compute usage"})
NO_COLLECTOR = "No collector"
NOT_RESOURCE = "Not a resource service"

# Which resource types back a service (Billing → Resources navigation).
SERVICE_RTYPES: dict[str, tuple[str, ...]] = {
    "Amazon Elastic Compute Cloud - Compute": ("ec2.instance",),
    "Amazon Simple Storage Service": ("s3.bucket",),
    "AWS Lambda": ("lambda.function",),
    "Amazon Relational Database Service": ("rds.instance",),
    "Amazon DynamoDB": ("dynamodb.table",),
    "AWS Identity and Access Management": ("iam.user",),
}


def uncovered_reason(service: str) -> str | None:
    """None when a collector inventories the service; else why it is uncovered."""
    if service in SERVICE_COLLECTORS:
        return None
    # Support plans and model usage billed through Bedrock ("Claude … (Amazon Bedrock Edition)") have no resources.
    if service in NOT_A_RESOURCE or service.startswith("AWS Support") or service.endswith("(Amazon Bedrock Edition)"):
        return NOT_RESOURCE
    return NO_COLLECTOR


def rtypes_for_service(service: str) -> tuple[str, ...]:
    return SERVICE_RTYPES.get(service, ())
