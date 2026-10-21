"""Remove secrets from a CloudTrail record before anything evaluates or stores it (first-drop review C1).

CloudTrail logs some secrets in clear: the temporary credentials returned by AssumeRole*, GetSessionToken and
GetFederationToken (responseElements.credentials), container and function environment values, and parameters such
as passwords. redact() returns a deep copy with:
  - credentials.sessionToken and credentials.accessKeyId dropped;
  - every value inside an `environment` list ({name, value}) or `environment.variables` map masked;
  - any scalar under a key matching (?i)password|secret|token|privatekey masked, unless AWS already wrote
    HIDDEN_DUE_TO_SECURITY_REASONS; secretId/secretArn/secretName are names, not secrets, and stay.
The caller's own userIdentity.accessKeyId is an identifier (it says who acted) and stays."""
from __future__ import annotations

import re
from typing import Any

REDACTED = "[redacted]"
HIDDEN = "HIDDEN_DUE_TO_SECURITY_REASONS"
SENSITIVE_KEY = re.compile(r"(?i)password|secret|token|privatekey")
KEEP_KEYS = frozenset({"secretId", "secretArn", "secretName"})
DROPPED_CREDENTIALS = frozenset({"sessionToken", "accessKeyId"})


def _masked(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _masked(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_masked(v) for v in value]
    return value if value is None or value == HIDDEN else REDACTED


def _environment(value: Any) -> Any:
    if isinstance(value, list):                    # ECS: [{"name": ..., "value": ...}]
        return [{k: (_masked(v) if k == "value" else _walk(v)) for k, v in item.items()}
                if isinstance(item, dict) else _masked(item) for item in value]
    if isinstance(value, dict):                    # Lambda: {"variables": {...}}; CodeBuild: {"environmentVariables": [...]}
        return {k: (_masked(v) if k == "variables" else _environment(v) if k == "environmentVariables" else _walk(v))
                for k, v in value.items()}
    return _masked(value)


def _walk(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key == "credentials" and isinstance(item, dict):
                item = {k: v for k, v in item.items() if k not in DROPPED_CREDENTIALS}
            if key == "environment":
                out[key] = _environment(item)
            elif (isinstance(key, str) and key not in KEEP_KEYS and SENSITIVE_KEY.search(key)
                  and not isinstance(item, (dict, list))):
                out[key] = item if item is None or item == HIDDEN else REDACTED
            else:
                out[key] = _walk(item)
        return out
    if isinstance(value, list):
        return [_walk(v) for v in value]
    return value


def redact(detail: dict) -> dict:
    """A redacted deep copy of one CloudTrail record; the argument is never changed."""
    return _walk(detail)
