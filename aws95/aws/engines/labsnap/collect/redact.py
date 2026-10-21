"""The three secret-bearing responses (first-drop spec §3.2). Each collector calls its redactor on the raw response
before anything else reads it; each function mutates in place and returns its argument (None passes through)."""
from __future__ import annotations

from typing import Any

REDACTED = "[redacted]"


def lambda_functions(page: dict[str, Any] | None) -> dict[str, Any] | None:
    """lambda:ListFunctions - drop Environment (Variables routinely hold credentials)."""
    for fn in (page or {}).get("Functions") or []:
        fn.pop("Environment", None)
    return page


def user_pool_client(response: dict[str, Any] | None) -> dict[str, Any] | None:
    """cognito-idp:DescribeUserPoolClient - drop ClientSecret."""
    client = (response or {}).get("UserPoolClient")
    if isinstance(client, dict):
        client.pop("ClientSecret", None)
    return response


def _arn_service(value_from: str) -> str:
    """Keep only "arn:<partition>:<service>": enough to say which store a secret lives in, never which secret."""
    parts = (value_from or "").split(":")
    return ":".join(parts[:3]) if len(parts) > 2 and parts[0] == "arn" else ""


def task_definition(response: dict[str, Any] | None) -> dict[str, Any] | None:
    """ecs:DescribeTaskDefinition - environment names and values, and secret references, become placeholders.
    The collector still counts plaintext variables and names the secret store's service."""
    td = (response or {}).get("taskDefinition") or {}
    for c in td.get("containerDefinitions") or []:
        if c.get("environment") is not None:
            c["environment"] = [{"name": REDACTED, "value": REDACTED} for _ in c["environment"]]
        if c.get("secrets") is not None:
            c["secrets"] = [{"name": REDACTED, "valueFrom": _arn_service(s.get("valueFrom", ""))}
                            for s in c["secrets"]]
    return response
