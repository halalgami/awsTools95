"""Resource types the labsnap collectors produce: (rtype, collector, domain, label) for the Resources tree."""
from __future__ import annotations

CATALOG: tuple[tuple[str, str, str, str], ...] = (
    ("account.summary", "account", "Identity", "Account summary"),
    ("apigateway.api", "apigateway", "Network", "API Gateway APIs"),
    ("apigateway.stage", "apigateway", "Network", "API Gateway stages"),
    ("budgets.budget", "budgets", "Billing", "Budgets"),
    ("cloudfront.distribution", "cloudfront", "Edge & DNS", "CloudFront distributions"),
    ("cloudtrail.trail", "cloudtrail", "Management", "CloudTrail trails"),
    ("cognito.user_pool", "cognito", "Identity", "Cognito user pools"),
    ("cognito.user_pool_client", "cognito", "Identity", "Cognito app clients"),
    ("dynamodb.table", "dynamodb", "Database", "DynamoDB tables"),
    ("ec2.instance", "ec2", "Compute", "EC2 instances"),
    ("ec2.volume", "ec2", "Storage", "EBS volumes"),
    ("ec2.snapshot", "ec2", "Storage", "EBS snapshots"),
    ("ec2.security_group", "ec2", "Network", "Security groups"),
    ("ec2.elastic_ip", "ec2", "Network", "Elastic IPs"),
    ("ec2.nat_gateway", "ec2", "Network", "NAT gateways"),
    ("ecr.repository", "ecr", "Containers", "ECR repositories"),
    ("ecs.cluster", "ecs", "Containers", "ECS clusters"),
    ("ecs.service", "ecs", "Containers", "ECS services"),
    ("ecs.task_definition", "ecs", "Containers", "ECS task definitions"),
    ("elb.load_balancer", "elb", "Network", "Load balancers"),
    ("elb.target_group", "elb", "Network", "Target groups"),
    ("guardrails.service", "guardrails", "Security", "Security services"),
    ("iam.user", "iam", "Identity", "IAM users"),
    ("iam.access_key", "iam", "Identity", "IAM access keys"),
    ("kms.key", "kms", "Security", "KMS keys"),
    ("lambda.function", "lambda", "Compute", "Lambda functions"),
    ("logs.group", "logs", "Management", "Log groups"),
    ("rds.instance", "rds", "Database", "RDS instances"),
    ("rds.cluster", "rds", "Database", "RDS clusters"),
    ("route53.hosted_zone", "route53", "Edge & DNS", "Route 53 hosted zones"),
    ("s3.bucket", "s3", "Storage", "S3 buckets"),
    ("secrets.secret", "secrets", "Security", "Secrets"),
)
BY_RTYPE = {c[0]: c for c in CATALOG}
# Collectors with scope "global" (their resources carry region "global"; a region filter does not apply).
GLOBAL_COLLECTORS = frozenset({"account", "budgets", "cloudfront", "cost", "freetier", "iam", "route53", "s3"})


def collector_of(rtype: str) -> str:
    return BY_RTYPE[rtype][1] if rtype in BY_RTYPE else rtype.split(".")[0]
