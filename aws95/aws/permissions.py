"""One table → probes, policy JSON and wizard text (spec §4.5). Edit capabilities here and only here."""
from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from aws95.aws import gate
from aws95.aws.errors import classify
from aws95.aws.session import CE_REGION, STS_REGION
from aws95.contracts.accounts import CapabilityCheck
from aws95.contracts.common import Status
from aws95.core.cancel import CancelToken

# Reviewed 2026-10-07 against labsnap @ 1b1d437: all read verbs. The collectors redact what three of these can return
# (lambda:ListFunctions Environment.Variables, cognito-idp:DescribeUserPoolClient ClientSecret, ecs:DescribeTaskDefinition
# environment/secrets values) - first-drop spec §3.2.
_LABSNAP_READ = (
    "access-analyzer:ListAnalyzers", "apigateway:GET", "budgets:ViewBudget", "cloudfront:GetDistributionConfig",
    "cloudfront:ListDistributions", "cloudtrail:DescribeTrails",
    "cloudtrail:GetTrailStatus", "cognito-idp:DescribeUserPool", "cognito-idp:DescribeUserPoolClient",
    "cognito-idp:ListUserPoolClients", "cognito-idp:ListUserPools", "dynamodb:DescribeContinuousBackups",
    "dynamodb:DescribeTable", "dynamodb:ListTables", "ec2:DescribeAddresses", "ec2:DescribeInstances",
    "ec2:DescribeNatGateways", "ec2:DescribeRegions", "ec2:DescribeSecurityGroups", "ec2:DescribeSnapshots",
    "ec2:DescribeVolumes", "ecr:DescribeRepositories", "ecr:GetLifecyclePolicy", "ecr:GetRepositoryPolicy",
    "ecs:DescribeClusters", "ecs:DescribeServices", "ecs:DescribeTaskDefinition", "ecs:ListClusters",
    "ecs:ListServices", "elasticloadbalancing:DescribeListeners", "elasticloadbalancing:DescribeLoadBalancers",
    "elasticloadbalancing:DescribeTargetGroups", "elasticloadbalancing:DescribeTargetHealth",
    "freetier:GetFreeTierUsage", "guardduty:ListDetectors", "iam:GetAccessKeyLastUsed",
    "iam:GetAccountPasswordPolicy", "iam:GetAccountSummary", "iam:ListAccessKeys", "iam:ListMFADevices",
    "iam:ListUsers", "kms:DescribeKey", "kms:GetKeyPolicy", "kms:GetKeyRotationStatus", "kms:ListAliases",
    "kms:ListKeys", "lambda:ListFunctions", "lambda:ListProvisionedConcurrencyConfigs", "logs:DescribeLogGroups",
    "rds:DescribeDBClusters", "rds:DescribeDBInstances", "route53:GetDNSSEC", "route53:ListHostedZones",
    "route53:ListQueryLoggingConfigs", "s3:GetBucketPolicyStatus",
    "s3:GetBucketPublicAccessBlock", "s3:GetEncryptionConfiguration", "s3:GetLifecycleConfiguration",
    "s3:ListAllMyBuckets", "secretsmanager:ListSecrets", "securityhub:DescribeHub",
)


SHIPPED_PARTS: frozenset[str] = frozenset({"2a", "2b", "2c", "2d"})     # parts whose windows are live


@dataclass(frozen=True)
class Capability:
    id: str
    label: str
    feature: str
    actions: tuple[str, ...]
    probe: tuple[str, str, dict[str, Any]] | None = field(default=None, hash=False)
    probe_action: str = ""
    available_in: str = "2a"

    @property
    def billed(self) -> bool:
        return self.probe is not None and self.probe[0] in gate.BILLED_SERVICES

    @property
    def shipped(self) -> bool:
        return self.available_in in SHIPPED_PARTS


def _yesterday() -> dict[str, Any]:
    end = datetime.now(timezone.utc).date()
    return {"TimePeriod": {"Start": (end - timedelta(days=1)).isoformat(), "End": end.isoformat()},
            "Granularity": "DAILY", "Metrics": ["UnblendedCost"]}


CAPABILITIES: tuple[Capability, ...] = (
    Capability("identity", "Identity", "All windows", ("sts:GetCallerIdentity",),
               ("sts", "get_caller_identity", {}), "sts:GetCallerIdentity", "2a"),
    Capability("resources", "Resources, Findings, Snapshots", "Resources", _LABSNAP_READ,
               ("ec2", "describe_regions", {}), "ec2:DescribeRegions", "2b"),
    Capability("activity", "Activity Log", "Activity Log", ("cloudtrail:LookupEvents", "cloudtrail:DescribeTrails"),
               ("cloudtrail", "lookup_events", {"MaxResults": 1}), "cloudtrail:LookupEvents", "2c"),
    Capability("billing", "Billing", "Billing", ("ce:GetCostAndUsage", "ce:GetCostForecast",
                                                   "ce:ListCostAllocationTags"),
               ("ce", "get_cost_and_usage", {}), "ce:GetCostAndUsage", "2b"),
    Capability("insights", "AI Insights", "AI Insights", ("bedrock:ListFoundationModels", "bedrock:InvokeModel"),
               ("bedrock", "list_foundation_models", {"byProvider": "Anthropic"}), "bedrock:ListFoundationModels",
               "2d"),
)

# The IAM action behind every allowed gate pair (gate.ALLOWED); tests keep the two in step.
IAM_ACTIONS: dict[tuple[str, str], str] = {
    ("sts", "get_caller_identity"): "sts:GetCallerIdentity",
    ("ec2", "describe_regions"): "ec2:DescribeRegions",
    ("cloudtrail", "lookup_events"): "cloudtrail:LookupEvents",
    ("ce", "get_cost_and_usage"): "ce:GetCostAndUsage",
    ("ce", "get_cost_forecast"): "ce:GetCostForecast",
    ("ce", "list_cost_allocation_tags"): "ce:ListCostAllocationTags",
    ("iam", "get_account_password_policy"): "iam:GetAccountPasswordPolicy",
    ("iam", "get_account_summary"): "iam:GetAccountSummary",
    ("iam", "list_users"): "iam:ListUsers",
    ("iam", "list_mfa_devices"): "iam:ListMFADevices",
    ("iam", "list_access_keys"): "iam:ListAccessKeys",
    ("iam", "get_access_key_last_used"): "iam:GetAccessKeyLastUsed",
    ("budgets", "describe_budgets"): "budgets:ViewBudget",
    ("cloudtrail", "describe_trails"): "cloudtrail:DescribeTrails",
    ("cloudtrail", "get_trail_status"): "cloudtrail:GetTrailStatus",
    ("cognito-idp", "list_user_pools"): "cognito-idp:ListUserPools",
    ("cognito-idp", "describe_user_pool"): "cognito-idp:DescribeUserPool",
    ("cognito-idp", "list_user_pool_clients"): "cognito-idp:ListUserPoolClients",
    ("cognito-idp", "describe_user_pool_client"): "cognito-idp:DescribeUserPoolClient",
    ("dynamodb", "list_tables"): "dynamodb:ListTables",
    ("dynamodb", "describe_table"): "dynamodb:DescribeTable",
    ("dynamodb", "describe_continuous_backups"): "dynamodb:DescribeContinuousBackups",
    ("ec2", "describe_instances"): "ec2:DescribeInstances",
    ("ec2", "describe_volumes"): "ec2:DescribeVolumes",
    ("ec2", "describe_security_groups"): "ec2:DescribeSecurityGroups",
    ("ec2", "describe_addresses"): "ec2:DescribeAddresses",
    ("ec2", "describe_nat_gateways"): "ec2:DescribeNatGateways",
    ("ec2", "describe_snapshots"): "ec2:DescribeSnapshots",
    ("ecr", "describe_repositories"): "ecr:DescribeRepositories",
    ("ecr", "get_repository_policy"): "ecr:GetRepositoryPolicy",
    ("ecr", "get_lifecycle_policy"): "ecr:GetLifecyclePolicy",
    ("ecs", "list_clusters"): "ecs:ListClusters",
    ("ecs", "describe_clusters"): "ecs:DescribeClusters",
    ("ecs", "list_services"): "ecs:ListServices",
    ("ecs", "describe_services"): "ecs:DescribeServices",
    ("ecs", "describe_task_definition"): "ecs:DescribeTaskDefinition",
    ("apigateway", "get_rest_apis"): "apigateway:GET",
    ("apigateway", "get_stages"): "apigateway:GET",
    ("apigatewayv2", "get_apis"): "apigateway:GET",
    ("apigatewayv2", "get_stages"): "apigateway:GET",
    ("cloudfront", "list_distributions"): "cloudfront:ListDistributions",
    ("cloudfront", "get_distribution_config"): "cloudfront:GetDistributionConfig",
    ("elbv2", "describe_load_balancers"): "elasticloadbalancing:DescribeLoadBalancers",
    ("elbv2", "describe_listeners"): "elasticloadbalancing:DescribeListeners",
    ("elbv2", "describe_target_groups"): "elasticloadbalancing:DescribeTargetGroups",
    ("elbv2", "describe_target_health"): "elasticloadbalancing:DescribeTargetHealth",
    ("route53", "list_hosted_zones"): "route53:ListHostedZones",
    ("route53", "get_dnssec"): "route53:GetDNSSEC",
    ("route53", "list_query_logging_configs"): "route53:ListQueryLoggingConfigs",
    ("freetier", "get_free_tier_usage"): "freetier:GetFreeTierUsage",
    ("guardduty", "list_detectors"): "guardduty:ListDetectors",
    ("accessanalyzer", "list_analyzers"): "access-analyzer:ListAnalyzers",
    ("securityhub", "describe_hub"): "securityhub:DescribeHub",
    ("kms", "list_aliases"): "kms:ListAliases",
    ("kms", "list_keys"): "kms:ListKeys",
    ("kms", "describe_key"): "kms:DescribeKey",
    ("kms", "get_key_rotation_status"): "kms:GetKeyRotationStatus",
    ("kms", "get_key_policy"): "kms:GetKeyPolicy",
    ("lambda", "list_functions"): "lambda:ListFunctions",
    ("lambda", "list_provisioned_concurrency_configs"): "lambda:ListProvisionedConcurrencyConfigs",
    ("logs", "describe_log_groups"): "logs:DescribeLogGroups",
    ("rds", "describe_db_instances"): "rds:DescribeDBInstances",
    ("rds", "describe_db_clusters"): "rds:DescribeDBClusters",
    ("s3", "list_buckets"): "s3:ListAllMyBuckets",
    ("s3", "get_public_access_block"): "s3:GetBucketPublicAccessBlock",
    ("s3", "get_bucket_policy_status"): "s3:GetBucketPolicyStatus",
    ("s3", "get_bucket_lifecycle_configuration"): "s3:GetLifecycleConfiguration",
    ("s3", "get_bucket_encryption"): "s3:GetEncryptionConfiguration",
    ("secretsmanager", "list_secrets"): "secretsmanager:ListSecrets",
    # AI Insights (2d part 2)
    ("bedrock", "list_foundation_models"): "bedrock:ListFoundationModels",
    ("bedrock-runtime", "converse"): "bedrock:InvokeModel",
    # import-only pairs: never in policy_json(); import_policy() scopes them
    ("organizations", "describe_organization"): "organizations:DescribeOrganization",
    ("s3", "list_objects_v2"): "s3:ListBucket",
    ("s3", "get_object"): "s3:GetObject",
}


# AI Insights' one billed, non-read action (ai-insights spec §2). It never joins the "*" read-only statement: it is
# limited to Anthropic foundation models and inference profiles (security review, 2026-10-20).
INVOKE_ACTIONS: frozenset[str] = frozenset({"bedrock:InvokeModel"})
INVOKE_RESOURCES: tuple[str, ...] = ("arn:aws:bedrock:*::foundation-model/anthropic.*",
                                     "arn:aws:bedrock:*:*:inference-profile/*anthropic.*")

# API Gateway authorises reads as apigateway:GET on resource paths, not as List*/Get* actions, so it gets its own
# statement, limited to the API paths the collector reads (edge-dns spec §5).
APIGATEWAY_ACTIONS: frozenset[str] = frozenset({"apigateway:GET"})
APIGATEWAY_RESOURCES: tuple[str, ...] = ("arn:aws:apigateway:*::/restapis", "arn:aws:apigateway:*::/restapis/*",
                                         "arn:aws:apigateway:*::/apis", "arn:aws:apigateway:*::/apis/*")


def policy_json() -> str:
    actions = {a for c in CAPABILITIES for a in c.actions}
    read_only = actions - INVOKE_ACTIONS - APIGATEWAY_ACTIONS
    return json.dumps({"Version": "2012-10-17", "Statement": [
        {"Sid": "AWS95ToolsReadOnly", "Effect": "Allow", "Action": sorted(read_only), "Resource": "*"},
        {"Sid": "AWS95ToolsApiGatewayRead", "Effect": "Allow", "Action": sorted(actions & APIGATEWAY_ACTIONS),
         "Resource": list(APIGATEWAY_RESOURCES)},
        {"Sid": "AWS95ToolsInsightsInvoke", "Effect": "Allow", "Action": sorted(actions & INVOKE_ACTIONS),
         "Resource": list(INVOKE_RESOURCES)}]}, indent=2)


def _ok_text(cap: Capability) -> str:
    return "Verified." if cap.shipped else f"Ready for {cap.feature} (arrives in a later update)."


def _gap_text(cap: Capability, message: str, missing: tuple[str, ...]) -> str:
    if missing and not cap.shipped:
        return f"Missing: {', '.join(missing)} (needed by {cap.feature}, arriving later)."
    return message


def check(session: Any, profile_region: str, include_billing: bool, cancel: CancelToken | None,
          only: set[str] | None = None) -> tuple[CapabilityCheck, ...]:
    rows: list[CapabilityCheck] = []
    for cap in CAPABILITIES:
        if only is not None and cap.id not in only:
            continue
        if cap.probe is None:
            rows.append(CapabilityCheck(cap.id, cap.label, Status.NOT_CONNECTED, (), "Arrives in a later update.",
                                        False, False))
            continue
        if cap.billed and not include_billing:
            rows.append(CapabilityCheck(cap.id, cap.label, Status.NOT_CONNECTED, (),
                                        "Not checked (AWS charges $0.01 per check).", True, True))
            continue
        service, op, params = cap.probe
        if cap.id == "billing":
            params = _yesterday()
        region = {"sts": STS_REGION, "ce": CE_REGION}.get(service, profile_region)
        try:
            gate.call(session, service, op, region=region, cancel=cancel, **params)
        except Exception as exc:  # noqa: BLE001 - classified; classify() re-raises CancelledError
            iam_service, iam_op = cap.probe_action.split(":")
            c = classify(exc, iam_service, iam_op)
            missing = (cap.probe_action,) if c.status is Status.DENIED else ()
            rows.append(CapabilityCheck(cap.id, cap.label, c.status, missing, _gap_text(cap, c.message, missing),
                                        cap.billed, True))
            continue
        rows.append(CapabilityCheck(cap.id, cap.label, Status.OK, (), _ok_text(cap), cap.billed, True))
    return tuple(rows)


IMPORT_ONLY: frozenset[tuple[str, str]] = frozenset({("s3", "list_objects_v2"), ("s3", "get_object"),
                                                     ("organizations", "describe_organization")})
IMPORT_NEEDS: tuple[str, ...] = ("s3:ListBucket", "s3:GetObject", "kms:Decrypt")
ORG_NEED = "organizations:DescribeOrganization"


def import_policy(bucket: str, prefix: str, kms_key_arn: str | None,
                  needs: Collection[str] = IMPORT_NEEDS) -> str:
    """The exact statements Import History… needs (spec §5.4), scoped to one trail's log folder - never "*" for S3
    or KMS. DescribeOrganization has no resource level, so its statement is "*" (it returns the organisation id)."""
    root = f"{prefix}/AWSLogs/" if prefix else "AWSLogs/"
    statements: list[dict] = []
    if "s3:ListBucket" in needs:
        statements.append({"Sid": "AWS95ListTrailLogs", "Effect": "Allow", "Action": "s3:ListBucket",
                           "Resource": f"arn:aws:s3:::{bucket}",
                           "Condition": {"StringLike": {"s3:prefix": [f"{root}*"]}}})
    if "s3:GetObject" in needs:
        statements.append({"Sid": "AWS95ReadTrailLogs", "Effect": "Allow", "Action": "s3:GetObject",
                           "Resource": f"arn:aws:s3:::{bucket}/{root}*"})
    if "kms:Decrypt" in needs and kms_key_arn:
        statements.append({"Sid": "AWS95DecryptTrailLogs", "Effect": "Allow", "Action": "kms:Decrypt",
                           "Resource": kms_key_arn})
    if ORG_NEED in needs:
        statements.append({"Sid": "AWS95FindOrganisationFolder", "Effect": "Allow", "Action": ORG_NEED,
                           "Resource": "*"})
    return json.dumps(statements, indent=2)
