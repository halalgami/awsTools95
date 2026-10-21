"""The read-only gate: an explicit (service, operation) allowlist. Not a name-prefix rule.

Engine code reaches AWS only through `call`, `paginate`, or `bind(...)`, a thin facade over those two. A pair in
VALIDATED is callable only while a parameter validator is installed (2c's S3 import, first-drop spec §5.4)."""
from __future__ import annotations

import functools
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from functools import cache
from typing import Any

from aws95.core.cancel import CancelToken, raise_if_cancelled

# Capability probes (phase 2a, permissions.CAPABILITIES).
PROBES: frozenset[tuple[str, str]] = frozenset({
    ("sts", "get_caller_identity"),        # caller account id + ARN; no credentials
    ("ec2", "describe_regions"),           # region names
    ("cloudtrail", "lookup_events"),       # management events (who did what)
    ("ce", "get_cost_and_usage"),          # cost figures; billed ($0.01/request)
})

# Every call the labsnap collectors make (first-drop spec §3.2), reviewed 2026-10-07 against labsnap @ 1b1d437,
# collect/collectors/*.py. tests/test_engine_calls.py asserts the copied collectors call exactly these.
LABSNAP: frozenset[tuple[str, str]] = frozenset({
    # account
    ("iam", "get_account_password_policy"),          # password policy flags and lengths; no passwords
    ("iam", "get_account_summary"),                  # account-wide counts and the root-MFA flag
    # budgets
    ("sts", "get_caller_identity"),                  # the account id DescribeBudgets and the cost LINKED_ACCOUNT filter need
    ("budgets", "describe_budgets"),                 # budget names, limits and cost filters
    # apigateway (REST v1 and HTTP/WebSocket v2); stage variables are never stored
    ("apigateway", "get_rest_apis"),                 # REST API ids, names, endpoint types
    ("apigateway", "get_stages"),                    # REST stage logging, tracing and WAF settings
    ("apigatewayv2", "get_apis"),                    # HTTP and WebSocket API ids, names, protocols
    ("apigatewayv2", "get_stages"),                  # HTTP/WebSocket stage access-log settings
    # cloudfront (global, us-east-1); domain names, aliases and origins are never stored
    ("cloudfront", "list_distributions"),            # distribution ids, viewer protocol, certificate, WAF, price class
    ("cloudfront", "get_distribution_config"),       # whether standard access logging is on
    # cloudtrail
    ("cloudtrail", "describe_trails"),               # trail names, home region, bucket name, multi-region flag
    ("cloudtrail", "get_trail_status"),              # IsLogging and delivery times for one trail
    # cognito
    ("cognito-idp", "list_user_pools"),              # user pool ids and names
    ("cognito-idp", "describe_user_pool"),           # pool policies, MFA mode, estimated user count
    ("cognito-idp", "list_user_pool_clients"),       # app client ids and names
    ("cognito-idp", "describe_user_pool_client"),    # client settings; ClientSecret is REDACTED in the collector
    # cost (billed; only for accounts that opted in)
    ("ce", "get_cost_and_usage"),                    # monthly-by-service and daily cost amounts
    ("ce", "get_cost_forecast"),                     # AWS's forecast for the rest of this month
    ("ce", "list_cost_allocation_tags"),             # active cost-allocation tag keys
    # dynamodb
    ("dynamodb", "list_tables"),                     # table names
    ("dynamodb", "describe_table"),                  # table settings, item count and size; never items
    ("dynamodb", "describe_continuous_backups"),     # point-in-time-recovery status
    # ec2
    ("ec2", "describe_instances"),                   # instance ids, state and type
    ("ec2", "describe_volumes"),                     # volume ids, size and attachments
    ("ec2", "describe_security_groups"),             # group ids and ingress rules
    ("ec2", "describe_addresses"),                   # Elastic IPs and their associations
    ("ec2", "describe_nat_gateways"),                # NAT gateway ids, state, VPC and subnet
    ("ec2", "describe_snapshots"),                   # EBS snapshot ids, size and state (OwnerIds=self)
    # ecr
    ("ecr", "describe_repositories"),                # repository names and scan/mutability settings
    ("ecr", "get_repository_policy"),                # policy JSON; only "allows public" is kept
    ("ecr", "get_lifecycle_policy"),                 # whether a lifecycle policy exists
    # ecs
    ("ecs", "list_clusters"),                        # cluster ARNs
    ("ecs", "describe_clusters"),                    # cluster status and counts
    ("ecs", "list_services"),                        # service ARNs per cluster
    ("ecs", "describe_services"),                    # service settings and network config
    ("ecs", "describe_task_definition"),             # container definitions; environment/secrets values REDACTED
    # elb (elbv2)
    ("elbv2", "describe_load_balancers"),            # load balancer names, scheme, type, VPC
    ("elbv2", "describe_listeners"),                 # listener ports, protocols, TLS policies
    ("elbv2", "describe_target_groups"),             # target group settings
    ("elbv2", "describe_target_health"),             # healthy/total target counts
    # route53 (global, us-east-1; hosted zones only, no record sets)
    ("route53", "list_hosted_zones"),                # zone ids, names, private flag, record counts
    ("route53", "get_dnssec"),                       # DNSSEC signing status of a public zone
    ("route53", "list_query_logging_configs"),       # whether a public zone logs DNS queries
    # free tier
    ("freetier", "get_free_tier_usage"),             # free-tier usage, limit and forecast per usage type
    # guardrails
    ("guardduty", "list_detectors"),                 # detector ids (is GuardDuty on)
    ("accessanalyzer", "list_analyzers"),            # analyzers (is Access Analyzer on)
    ("securityhub", "describe_hub"),                 # hub ARN (is Security Hub on)
    # iam
    ("iam", "list_users"),                           # user names and ARNs
    ("iam", "list_mfa_devices"),                     # MFA device serials per user
    ("iam", "list_access_keys"),                     # access key ids, status and creation date; never the key
    ("iam", "get_access_key_last_used"),             # when and where a key was last used
    # kms
    ("kms", "list_aliases"),                         # alias names and target key ids
    ("kms", "list_keys"),                            # key ids
    ("kms", "describe_key"),                         # key metadata (manager, state, spec, usage)
    ("kms", "get_key_rotation_status"),              # rotation flag
    ("kms", "get_key_policy"),                       # policy JSON; only "allows external" is kept
    # lambda
    ("lambda", "list_functions"),                    # function config; Environment.Variables REDACTED
    ("lambda", "list_provisioned_concurrency_configs"),  # provisioned concurrency configs
    # logs
    ("logs", "describe_log_groups"),                 # group names, retention, stored bytes; never log events
    # rds
    ("rds", "describe_db_instances"),                # instance class, engine, public flag, security groups
    ("rds", "describe_db_clusters"),                 # cluster engine, encryption, members, security groups
    # s3 (fast config: an unreachable region costs seconds)
    ("s3", "list_buckets"),                          # bucket names and creation dates
    ("s3", "get_public_access_block"),               # public-access-block flags
    ("s3", "get_bucket_policy_status"),              # IsPublic only; never the policy
    ("s3", "get_bucket_lifecycle_configuration"),    # lifecycle rules (presence is kept)
    ("s3", "get_bucket_encryption"),                 # default encryption config (presence is kept)
    # secrets manager (the value-reading API is never allowed)
    ("secretsmanager", "list_secrets"),              # secret names, rotation and access dates; no values
})

# Every call the ctwatch engine makes (first-drop spec §3.2, §5), reviewed 2026-10-07 against ctwatch @ 254ff31 and
# the 2c plan. tests/test_engine_calls.py asserts aws95/aws/engines/ctwatch calls exactly these.
CTWATCH: frozenset[tuple[str, str]] = frozenset({
    ("cloudtrail", "lookup_events"),       # management events, 50 per call (also the Activity capability probe)
    ("cloudtrail", "describe_trails"),     # trail bucket, key prefix, KMS key id, organisation and Insights flags
    ("organizations", "describe_organization"),  # the organisation id (o-…) only; the rest of the reply is ignored
    ("s3", "list_objects_v2"),             # VALIDATED: CloudTrail log keys under the resolved trail's prefix only
    ("s3", "get_object"),                  # VALIDATED: one CloudTrail .json.gz log file under that prefix only
})

# AI Insights (2d part 2, ai-insights spec §3): the one exception to read-only. Converse writes nothing but is billed
# per token; it is VALIDATED, so it runs only inside bedrock.converse with its strict request validator installed.
INSIGHTS: frozenset[tuple[str, str]] = frozenset({
    ("bedrock", "list_foundation_models"),     # the Anthropic text models this account can see (free)
    ("bedrock-runtime", "converse"),           # VALIDATED: one text message of reviewed aggregates, 1,500 tokens out
})

ALLOWED: frozenset[tuple[str, str]] = PROBES | LABSNAP | CTWATCH | INSIGHTS
REVIEWED_EXTRAS: frozenset[tuple[str, str]] = ALLOWED - PROBES    # allowed pairs that are not capability probes
# Cost Explorer bills every API request, whatever the operation; Bedrock Runtime bills per token.
BILLED_SERVICES = frozenset({"ce", "bedrock-runtime"})

# Pairs callable only while a parameter validator is installed (2c: s3 list_objects_v2 and get_object).
VALIDATED: frozenset[tuple[str, str]] = frozenset({("s3", "list_objects_v2"), ("s3", "get_object"),
                                                   ("bedrock-runtime", "converse")})
Validator = Callable[[Mapping[str, Any]], None]
_validators: dict[tuple[str, str], Validator] = {}
_validators_lock = threading.Lock()


class WriteBlockedError(Exception):
    """A (service, operation) outside ALLOWED, or refused by its validator. Raised before any client exists."""


def is_allowed(service: str, operation: str) -> bool:
    """A reviewed pair, and - for a VALIDATED pair - only while its validator is installed."""
    pair = (service, operation)
    if pair not in ALLOWED:
        return False
    if pair in VALIDATED:
        with _validators_lock:
            return pair in _validators
    return True


@cache
def client_config():
    from botocore.config import Config
    return Config(connect_timeout=5, read_timeout=20, retries={"mode": "standard", "max_attempts": 3},
                  user_agent_extra="aws95-tools")


@cache
def fast_config():
    """S3 per-bucket calls: one attempt and short timeouts, so an unreachable region costs seconds (labsnap s3.py)."""
    from botocore.config import Config
    return Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 1}, user_agent_extra="aws95-tools")


@contextmanager
def validator(service: str, operation: str, check: Validator) -> Iterator[None]:
    """Install `check` for one VALIDATED pair for the duration of the block (2c: one per import, one bucket)."""
    pair = (service, operation)
    if pair not in VALIDATED:
        raise ValueError(f"{service}.{operation} does not take a validator")
    with _validators_lock:
        if pair in _validators:
            raise RuntimeError(f"a validator for {service}.{operation} is already installed")
        _validators[pair] = check
    try:
        yield
    finally:
        with _validators_lock:
            _validators.pop(pair, None)


def _check(service: str, operation: str, params: Mapping[str, Any] | None = None) -> None:
    if (service, operation) not in ALLOWED:
        raise WriteBlockedError(f"{service}.{operation}")
    pair = (service, operation)
    if pair not in VALIDATED:
        return
    with _validators_lock:
        check = _validators.get(pair)
    if check is None:
        raise WriteBlockedError(f"{service}.{operation} (no validator installed)")
    if params is None:
        return
    try:
        check(params)
    except WriteBlockedError:
        raise
    except Exception:  # noqa: BLE001 - any validator failure is a refusal, never a pass
        raise WriteBlockedError(f"{service}.{operation} (parameters refused)") from None


def _client(session: Any, service: str, region: str | None, fast: bool = False) -> Any:
    return session.client(service, region_name=region or "us-east-1",
                          config=fast_config() if fast else client_config())


def call(session: Any, service: str, operation: str, *, region: str | None = None,
         cancel: CancelToken | None = None, fast: bool = False, **params: Any) -> dict:
    _check(service, operation, params)
    raise_if_cancelled(cancel)
    return getattr(_client(session, service, region, fast), operation)(**params)


def paginate(session: Any, service: str, operation: str, *, region: str | None = None,
             cancel: CancelToken | None = None, fast: bool = False, **params: Any) -> Iterator[dict]:
    _check(service, operation, params)  # eager: a blocked pair raises here, not on the first next()
    raise_if_cancelled(cancel)
    return _pages(session, service, operation, region, cancel, fast, params)


def _pages(session: Any, service: str, operation: str, region: str | None, cancel: CancelToken | None,
           fast: bool, params: dict[str, Any]) -> Iterator[dict]:
    for page in _client(session, service, region, fast).get_paginator(operation).paginate(**params):
        yield page
        raise_if_cancelled(cancel)


class Bound:
    """One AWS service for engine code: `svc.op(**params)` and `svc.pages(op, **params)` check exactly what `call`
    and `paginate` check. Attribute access checks the allowlist eagerly, so a blocked operation fails before any
    client exists. The client is built once per binding (a collector makes hundreds of calls; one client per call
    would cost seconds). A binding belongs to one collector thread; never share one across threads."""
    __slots__ = ("_session", "_service", "_region", "_cancel", "_fast", "_gate_built")

    def __init__(self, session: Any, service: str, region: str | None = None, *,
                 cancel: CancelToken | None = None, fast: bool = False) -> None:
        self._session = session
        self._service = service
        self._region = region
        self._cancel = cancel
        self._fast = fast
        self._gate_built: Any = None

    def _gate_client_once(self) -> Any:
        if self._gate_built is None:
            self._gate_built = _client(self._session, self._service, self._region, self._fast)
        return self._gate_built

    def _invoke(self, operation: str, **params: Any) -> dict:
        _check(self._service, operation, params)
        raise_if_cancelled(self._cancel)
        return getattr(self._gate_client_once(), operation)(**params)

    def __getattr__(self, operation: str) -> Callable[..., dict]:
        if operation.startswith("_"):
            raise AttributeError(operation)
        _check(self._service, operation)
        return functools.partial(self._invoke, operation)

    def pages(self, operation: str, **params: Any) -> Iterator[dict]:
        _check(self._service, operation, params)
        raise_if_cancelled(self._cancel)
        return self._bound_pages(operation, params)

    def _bound_pages(self, operation: str, params: dict[str, Any]) -> Iterator[dict]:
        for page in self._gate_client_once().get_paginator(operation).paginate(**params):
            yield page
            raise_if_cancelled(self._cancel)


def bind(session: Any, service: str, region: str | None = None, *, cancel: CancelToken | None = None,
         fast: bool = False) -> Bound:
    return Bound(session, service, region, cancel=cancel, fast=fast)
